"""Post-transpile lowering: rewrite dynamic Addressables SPAWN call sites.

The AI transpiler leaves every ``Addressables.InstantiateAsync(...)`` /
``LoadAssetAsync<GameObject>(...)`` / ``AssetReference.InstantiateAsync(...)`` /
direct ``Instantiate(...)``+``:Clone()`` spawn call UNCONVERTED — it emits a dead
sentinel (``local v = nil`` / a ``:Clone()`` on a prefab-id string) preceded by a
DETERMINISTIC origin comment naming the original C# call. Nothing instantiates, so
gameplay content (track segments, obstacles, the premium collectible, parallax
clouds) never spawns. This module rewrites each such site to instantiate by
prefab-id via the host API.

WHY a deterministic span-rewrite (not regex-on-AI-semantics): the TRIGGER is the
DETERMINISTIC UPSTREAM fact — the transpiler's origin COMMENT marking the call site
(``code_transpiler`` emits one per unconverted Instantiate/LoadAsset call) — NOT a
single downstream guard shape. Empirically, the downstream sentinel DIVERGES across
sites (segment ``local v=nil``+warn-abort; obstacle inverted ``if obj~=nil``;
premium bare ``toUse=nil``+warn-abort; cloud ``:Clone()`` on a string), so keying on
one guard shape silently abstains on the others (D-P4-6). Anchoring on the comment +
requiring an EXACT, ADJACENT sentinel shape gates identity on a compiler-emitted
marker and only fires when the structure also matches — the failure mode is a
fail-soft ABSTAIN, never a silent mis-rewrite.

The rewrite target is ``self.host.instantiatePrefab(<prefabIdExpr>, <parent>,
<cframe>)`` (scene_runtime.luau dotted accessor), which resolves a ``"<guid>:<path>"``
prefab-id STRING to the emitted Template clone — NOT ``PrefabSpawner.spawn``, which
keys on the bare collision-resolved Template NAME the themeData object does not carry
(D-P4-8). ``<prefabIdExpr>`` is recovered STRUCTURALLY per site from the prefab-id
string already in scope (the themeData object / the function parameter / the
``:Clone()`` receiver).

FAIL-CLOSED over guess (D-P3-2 precedent): a site is rewritten ONLY when its
prefab-id source is PROVABLY a prefab-id string at that site. The consumable site
(``Addressables.InstantiateAsync(consumable name)``) is DEFERRED — its source
``consumableDatabase.consumbales[picked]`` is accessed as a struct (``.canBeSpawned`` /
``.gameObject``) but the ``Consumables`` SO materialized it as a bare prefab-id string
list, so no struct-shaped prefab-id expr is available; force-rewriting would convert a
loud, diagnosable SO-materialization mismatch into a silent host-call mis-resolution
(D-P4-11). The consumable origin comment is detected so the deferral is COUNTED and
loud, not silent.

Idempotent: the rewrite removes the sentinel (the ``= nil`` reassign / the
``:Clone()``), so a second pass re-anchors on the (preserved) origin comment, finds no
sentinel in the adjacency window, and abstains — byte-stable.

Pure except the documented lowering side effect: it mutates only the ``source`` of the
scripts it is handed (the sibling-pass convention shared with ``lower_so_db_consumers``
/ ``lower_roster_consumers``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


class _HasSource(Protocol):
    source: str


@dataclass(frozen=True)
class SpawnRewriteResult:
    """Outcome of one ``lower_spawn_call_sites`` pass over one module."""

    rewritten: int          # sites rewritten to instantiatePrefab
    deferred: int           # sites detected but fail-closed (consumable shape)


# A located rewrite: the half-open char span ``[start, end)`` to replace and the
# replacement text. ``None`` from a per-shape locator means "abstain".
@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    replacement: str


# Each origin comment is the transpiler's deterministic marker for one C# spawn
# call. The text is emitted verbatim for the unconverted Instantiate/LoadAsset
# calls; we anchor on a stable SUBSTRING of it. These are the identity gates.
_C_SEGMENT = "AssetReference.InstantiateAsync"
_C_OBSTACLE = "Addressables.LoadAssetAsync<GameObject>"
_C_CONSUMABLE = "Addressables.InstantiateAsync(consumable name)"
_C_PREMIUM = "Addressables.InstantiateAsync(premiumCollectible name)"


# --- Per-shape span matchers (anchored, ADJACENT, capture the prefab-id expr) ---

# SEGMENT: ``local <v> = nil`` + ``if <v> == nil then warn("Unable to load segment")
# return end``. The prefab-id list + index are read just above.
_RE_SEGMENT = re.compile(
    r"(?P<ind>[ \t]*)local (?P<var>\w+) = nil\n"
    r"[ \t]*if (?P=var) == nil then\n"
    r"[ \t]*warn\(string\.format\(\"Unable to load segment[^\n]*\n"
    r"[ \t]*return\n"
    r"[ \t]*end"
)
_RE_SEGMENT_ZONE = re.compile(r"local (\w+) = [^\n]*\.zones\[[^\n]*\]\n")
_RE_SEGMENT_INDEX = re.compile(r"local (\w+) = math\.random\(0, \w+ - 1\)")

# OBSTACLE (inverted): ``local <v> = nil`` + ``if <v> ~= nil then`` inside
# ``function <N>:SpawnFromAssetReference(reference, ...)``. The prefab-id is the
# ``reference`` parameter; keep the body (uses <v>), re-bind <v>.
_RE_OBSTACLE = re.compile(
    r"(?P<ind>[ \t]*)local (?P<var>\w+) = nil\n"
    r"(?P<rest>[ \t]*if (?P=var) ~= nil then\n)"
)
_RE_OBSTACLE_PARAM = re.compile(r"function \w+[:.]SpawnFromAssetReference\((\w+)")

# PREMIUM: bare ``<v> = nil`` reassign + ``if <v> == nil then warn(... <EXPR>.name)
# return end``. The prefab-id expr is ``<EXPR>`` (a GameObject serialized as a
# prefab-id string — themeData.premiumCollectible).
_RE_PREMIUM = re.compile(
    r"(?P<ind>[ \t]*)(?P<var>\w+) = nil\n"
    r"[ \t]*if (?P=var) == nil then\n"
    r"[ \t]*warn\(string\.format\(\"Unable to load collectable[^\n]*\n"
    r"[ \t]*tostring\((?P<expr>[^\n]*?)\.name\)\)\)\n"
    r"[ \t]*return\n"
    r"[ \t]*end"
)

# CONSUMABLE (DEFERRED — detection only, D-P4-11): bare ``<v> = nil`` + warn
# "Unable to load consumable". Matched so the deferral is counted/loud.
_RE_CONSUMABLE = re.compile(
    r"[ \t]*(?P<var>\w+) = nil\n"
    r"[ \t]*if (?P=var) == nil then\n"
    r"[ \t]*warn\(string\.format\(\"Unable to load consumable"
)

# CLOUD: ``local <v> = <expr>:Clone()`` (expr is a prefab-id string;
# ``:Clone()`` on a string errors — D-P4-7), then ``<v>.Parent = <parent>``.
_RE_CLOUD = re.compile(
    r"(?P<ind>[ \t]*)local (?P<var>\w+) = (?P<expr>[\w.]+):Clone\(\)\n"
    r"(?P<parentind>[ \t]*)(?P=var)\.Parent = (?P<parent>[^\n]+)\n"
)


def _comment_present(source: str, marker: str) -> bool:
    """True iff ``marker`` appears on an origin-comment line (``-- ...``)."""
    for line in source.splitlines():
        s = line.lstrip()
        if s.startswith("--") and marker in s:
            return True
    return False


def _locate_segment(source: str) -> _Span | None:
    if not _comment_present(source, _C_SEGMENT):
        return None
    m = _RE_SEGMENT.search(source)
    if m is None:
        return None
    pre = source[: m.start()]
    zone_m = list(_RE_SEGMENT_ZONE.finditer(pre))
    idx_m = list(_RE_SEGMENT_INDEX.finditer(pre))
    if not zone_m or not idx_m:
        logger.warning(
            "[spawn-lowering] segment origin present but zone/index locals "
            "not located; fail-closed (no rewrite)."
        )
        return None
    zone = zone_m[-1].group(1)
    idx = idx_m[-1].group(1)
    ind = m.group("ind")
    var = m.group("var")
    # Unity Random.Range(0,len) is 0-based -> +1 for the 1-based Luau list.
    expr = f"{zone}.prefabList[{idx} + 1]"
    replacement = (
        f"{ind}local {var} = self.host.instantiatePrefab("
        f"{expr}, self.gameObject, nil)\n"
        f"{ind}if {var} == nil then\n"
        f"{ind}    warn(string.format(\"Unable to load segment %d.\", {idx}))\n"
        f"{ind}    return\n"
        f"{ind}end"
    )
    return _Span(m.start(), m.end(), replacement)


def _locate_obstacle(source: str) -> _Span | None:
    if not _comment_present(source, _C_OBSTACLE):
        return None
    param_m = _RE_OBSTACLE_PARAM.search(source)
    if param_m is None:
        return None
    ref = param_m.group(1)
    m = _RE_OBSTACLE.search(source)
    if m is None:
        return None
    ind = m.group("ind")
    var = m.group("var")
    rest = m.group("rest")  # ``if <var> ~= nil then`` line — kept (body uses var)
    replacement = (
        f"{ind}local {var} = self.host.instantiatePrefab("
        f"{ref}, segment.gameObject, nil)\n"
        f"{rest}"
    )
    return _Span(m.start(), m.end(), replacement)


def _locate_premium(source: str) -> _Span | None:
    if not _comment_present(source, _C_PREMIUM):
        return None
    m = _RE_PREMIUM.search(source)
    if m is None:
        return None
    ind = m.group("ind")
    var = m.group("var")
    expr = m.group("expr").strip()
    replacement = (
        f"{ind}{var} = self.host.instantiatePrefab({expr}, segment.gameObject, nil)\n"
        f"{ind}if {var} == nil then\n"
        f"{ind}    warn(string.format(\"Unable to load collectable %s.\",\n"
        f"{ind}        tostring({expr}.name)))\n"
        f"{ind}    return\n"
        f"{ind}end"
    )
    return _Span(m.start(), m.end(), replacement)


def _locate_cloud(source: str) -> _Span | None:
    if ":Clone()" not in source:
        return None
    m = _RE_CLOUD.search(source)
    if m is None:
        return None
    ind = m.group("ind")
    var = m.group("var")
    expr = m.group("expr")
    parentind = m.group("parentind")
    parent = m.group("parent")
    replacement = (
        f"{ind}local {var} = self.host.instantiatePrefab({expr}, {parent}, nil)\n"
        f"{parentind}if {var} ~= nil then\n"
        f"{parentind}    {var}.Parent = {parent}\n"
        f"{parentind}end\n"
    )
    return _Span(m.start(), m.end(), replacement)


# The active (rewriting) site locators, applied in source order each pass.
_LOCATORS = (_locate_segment, _locate_obstacle, _locate_premium, _locate_cloud)


def _consumable_deferred(source: str) -> bool:
    """True iff a consumable spawn site is present (DEFERRED — never rewritten)."""
    return (
        _comment_present(source, _C_CONSUMABLE)
        and _RE_CONSUMABLE.search(source) is not None
    )


def lower_spawn_call_sites(source: str) -> tuple[str, SpawnRewriteResult]:
    """Rewrite the dynamic spawn call sites in one transpiled module ``source``.

    PURE: returns ``(new_source, SpawnRewriteResult)``; does NOT mutate the input.
    Each active site (segment/obstacle/premium/cloud) is located by its
    origin-comment-anchored, adjacency-bounded shape and rewritten to
    ``self.host.instantiatePrefab(<prefabIdExpr>, …)``. The consumable site is
    DETECTED and COUNTED as deferred (fail-closed, D-P4-11) but never rewritten.

    Sites whose origin comment is present but whose adjacent shape / prefab-id
    expr cannot be located ABSTAIN (fail-soft, logged) — never a guessed rewrite.

    Idempotent: a rewritten span no longer carries its sentinel, so a re-run
    re-anchors, finds no shape, and returns byte-identical source.
    """
    deferred = 1 if _consumable_deferred(source) else 0
    rewritten = 0
    # Collect non-overlapping spans, then splice right-to-left so earlier offsets
    # stay valid. Each locator returns at most one span (one site per shape in the
    # real input); if a future build emits two, the second is picked up on the
    # idempotent re-run pass the orchestrator could repeat (out of scope here).
    spans: list[_Span] = []
    for locate in _LOCATORS:
        span = locate(source)
        if span is not None:
            spans.append(span)
            rewritten += 1
    spans.sort(key=lambda s: s.start, reverse=True)
    new_source = source
    for span in spans:
        new_source = new_source[: span.start] + span.replacement + new_source[span.end:]
    return new_source, SpawnRewriteResult(rewritten=rewritten, deferred=deferred)


def lower_spawn_call_sites_in_scripts(scripts: list[_HasSource]) -> SpawnRewriteResult:
    """Apply ``lower_spawn_call_sites`` to every script in place, returning the
    aggregate result.

    PURE except the documented in-place ``source`` mutation (the sibling-pass
    convention). A script whose source carries no spawn site is untouched.
    """
    total_rewritten = 0
    total_deferred = 0
    for script in scripts:
        src = getattr(script, "source", None)
        if not isinstance(src, str):
            continue
        new_src, result = lower_spawn_call_sites(src)
        if new_src != src:
            script.source = new_src
        total_rewritten += result.rewritten
        total_deferred += result.deferred
    return SpawnRewriteResult(rewritten=total_rewritten, deferred=total_deferred)
