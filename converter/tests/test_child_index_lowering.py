"""Tests for the child-index lowering pass (generic allowlist).

The transpiler flattens Unity ``transform.GetChild(n)`` to
``<recv>:GetChildren()[n+1]``. The converter injects an AudioSource->Sound at
child index 0 of Turret-like Parts, so the naive index returns the Sound and a
following ``:GetPivot()`` crashes. ``lower_child_index`` rewrites each such
site to ``__unityChild(recv, N)`` -- the SAME shared helper the legacy
coherence pack (``_fix_unity_transform_child_index``) uses, which resolves the
N-th authored child (prefer the N-th ``_SceneRuntimeId``-stamped child, else
the N-th ``Model``/``BasePart``, else ``nil``).

The rule is GENERAL (keyed on the ``<recv>:GetChildren()[<literal>]`` emission
shape, never ``s.name``): it applies to any GetChild site, never just the
turret. The simple-receiver regex (no ``()``/``[]`` in the receiver) plus
non-overlapping ``re.sub`` structurally avoids nested-chain corruption.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from converter.child_index_lowering import (  # noqa: E402
    _UNITY_CHILD_HELPER,
    lower_child_index,
    rewrite_child_index_source,
)


class _S:
    """Minimal TranspiledScript stand-in (carries ``luau_source``)."""

    def __init__(self, src: str) -> None:
        self.luau_source = src


# The real Turret.luau emission shape (child[0]=injected Sound, child[1]=Base).
_TURRET = textwrap.dedent("""\
    local Turret = {}
    Turret.__index = Turret

    -- transform.GetChild(0)
    function Turret:_tBase()
        return self.gameObject:GetChildren()[1]
    end

    function Turret:_fire()
        local base = self:_tBase()
        if base then
            return base:GetPivot().Position
        end
        return nil
    end
""")


def test_turret_getchild_resolves_to_spatial_child_not_sound() -> None:
    """Acceptance 1: the flattened GetChild(0) no longer returns
    ``GetChildren()[1]`` (the injected Sound); it resolves via ``__unityChild``,
    which skips non-spatial children, so a following :GetPivot() targets a
    Part, not the Sound."""
    s = _S(_TURRET)
    n = lower_child_index([s])
    assert n == 1
    # The naive index that hit the Sound is gone...
    assert "self.gameObject:GetChildren()[1]" not in s.luau_source
    # ...replaced by the __unityChild helper call, receiver + index preserved.
    assert "__unityChild(self.gameObject, 1)" in s.luau_source
    # The helper (which skips non-spatial children) is injected once.
    assert "local function __unityChild(" in s.luau_source
    assert s.luau_source.count("local function __unityChild(") == 1
    assert "_SceneRuntimeId" in s.luau_source
    assert 'IsA("BasePart")' in s.luau_source
    assert 'IsA("Model")' in s.luau_source


def test_helper_skips_sound_at_index_0_picks_base() -> None:
    """Acceptance 1/2: the injected helper counts only authored children
    (_SceneRuntimeId-stamped, else Model/BasePart), so child[0]=Sound is
    skipped, and returns nil when there is no N-th match (abstain, no crash)."""
    s = _S(_TURRET)
    lower_child_index([s])
    # Two-tier resolution, terminal abstain.
    assert "if n == i then return c end" in s.luau_source
    assert s.luau_source.rstrip().endswith("end") or "return nil" in s.luau_source
    assert "return nil" in _UNITY_CHILD_HELPER


def test_general_non_turret_getchild_site_is_lowered() -> None:
    """Acceptance 2: the rule is structure-gated, not turret-name-gated. A
    script with no turret identity but a GetChild emission is still lowered."""
    src = textwrap.dedent("""\
        local Elevator = {}
        function Elevator:platform()
            return self.gameObject:GetChildren()[2]
        end
    """)
    s = _S(src)
    n = lower_child_index([s])
    assert n == 1
    assert "self.gameObject:GetChildren()[2]" not in s.luau_source
    # Receiver + index (the spatial-child ordinal) preserved.
    assert "__unityChild(self.gameObject, 2)" in s.luau_source


def test_variable_index_is_not_lowered() -> None:
    """A genuine dynamic lookup ``GetChildren()[i]`` (not a flattened constant
    GetChild) must NOT be rewritten -- only integer-literal indices."""
    src = textwrap.dedent("""\
        function M:pick(i)
            return self.gameObject:GetChildren()[i]
        end
    """)
    s = _S(src)
    before = s.luau_source
    n = lower_child_index([s])
    assert n == 0
    assert s.luau_source == before


def test_index_inside_string_or_comment_is_not_lowered() -> None:
    """Acceptance: structure-gate on CODE only -- a GetChildren()[1] inside a
    string literal or comment is never a signal."""
    src = textwrap.dedent("""\
        -- self.gameObject:GetChildren()[1] is the historical shape
        local doc = "call container:GetChildren()[1] to fetch the base"
        function M:noop()
            return nil
        end
    """)
    s = _S(src)
    before = s.luau_source
    n = lower_child_index([s])
    assert n == 0
    assert s.luau_source == before
    assert "__unityChild" not in s.luau_source


def test_code_match_rewritten_string_match_on_same_line_preserved() -> None:
    """A real code match is rewritten; a same-line comment occurrence is left
    verbatim (matches the legacy pack's _luau_pos_is_code semantics)."""
    src = "local b = container:GetChildren()[1] -- cf x:GetChildren()[9]\n"
    s = _S(src)
    n = lower_child_index([s])
    assert n == 1
    assert "__unityChild(container, 1)" in s.luau_source
    assert "x:GetChildren()[9]" in s.luau_source  # comment preserved


def test_nested_chain_no_corruption_inner_rewritten() -> None:
    """codex BLOCKING #2 (resolved by reuse): a flattened nested
    ``transform.GetChild(0).GetChild(0)`` ->
    ``a:GetChildren()[1]:GetChildren()[1]``. The simple-receiver regex cannot
    match a receiver containing ``()``/``[]`` and ``re.sub`` is non-overlapping,
    so ONLY the inner site rewrites; the source is not corrupted."""
    src = "local x = a:GetChildren()[1]:GetChildren()[1]\n"
    s = _S(src)
    n = lower_child_index([s])
    assert n == 1
    # Inner site rewritten; the outer ``:GetChildren()[1]`` is left intact
    # (its receiver __unityChild(a, 1) contains ()/[] so it cannot match).
    assert "__unityChild(a, 1):GetChildren()[1]" in s.luau_source
    # No double-rewrite / garbled output: exactly one rewritten call site
    # (``__unityChild(a, 1)``); the un-rewritten outer index is left intact.
    assert s.luau_source.count("__unityChild(a, 1)") == 1
    assert s.luau_source.count(":GetChildren()[1]") == 1


def test_abstain_returns_nil_not_crash() -> None:
    """Edge case 1: fewer real spatial children than the index -> the helper
    returns nil (the existing ``if base then`` guards handle it), it does not
    crash. The injected helper ends with a terminal ``return nil``."""
    s = _S(_TURRET)
    lower_child_index([s])
    assert "return nil\nend" in s.luau_source


def test_idempotent_twice_applied() -> None:
    """Edge case 5 / acceptance: re-running the pass yields identical output
    (the GetChildren()[literal] fingerprint is gone after the first pass; the
    rewritten ``__unityChild(...)`` receivers contain ()/[] so re-running the
    simple-receiver regex finds nothing)."""
    s = _S(_TURRET)
    n1 = lower_child_index([s])
    after_first = s.luau_source
    n2 = lower_child_index([s])
    assert n1 == 1
    assert n2 == 0
    assert s.luau_source == after_first
    assert s.luau_source.count("local function __unityChild(") == 1


def test_multiple_getchild_sites_in_one_script() -> None:
    """All GetChild emissions in a script are lowered, with their distinct
    receivers and indices preserved; the helper is injected exactly once."""
    src = textwrap.dedent("""\
        function Turret:_tBase()
            return self.gameObject:GetChildren()[1]
        end
        function Turret:_tThird()
            return self.gameObject:GetChildren()[3]
        end
    """)
    s = _S(src)
    n = lower_child_index([s])
    assert n == 1
    assert "__unityChild(self.gameObject, 1)" in s.luau_source
    assert "__unityChild(self.gameObject, 3)" in s.luau_source
    assert s.luau_source.count("local function __unityChild(") == 1


def test_empty_and_no_match_scripts() -> None:
    """No GetChild sites -> no change, count 0; empty source is safe."""
    a = _S("")
    b = _S("function M:f() return self.x end")
    n = lower_child_index([a, b])
    assert n == 0
    assert a.luau_source == ""
    assert b.luau_source == "function M:f() return self.x end"


def test_rewrite_core_matches_legacy_shape() -> None:
    """The shared core is the SAME logic the legacy pack runs: a plain
    ``recv:GetChildren()[N]`` becomes ``__unityChild(recv, N)`` and the helper
    is prepended once."""
    new_source, count = rewrite_child_index_source(
        "local b = container:GetChildren()[1]\n"
    )
    assert count == 1
    assert "__unityChild(container, 1)" in new_source
    assert new_source.startswith(_UNITY_CHILD_HELPER)
