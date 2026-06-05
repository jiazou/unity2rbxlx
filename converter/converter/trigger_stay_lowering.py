"""trigger_stay_lowering.py -- deterministic OnTriggerStay->Stay lowering.

The generic transpiler collapses Unity ``OnTriggerEnter``/``OnTriggerExit``/
``OnTriggerStay`` (and the collision variants) all onto a single ``.Touched``
**edge** signal:

    -- OnTriggerStay(other): ...
    self.host:connectGameObjectSignal(self.gameObject, "Touched", function(other)
        ...
    end)

That is faithful for the *Enter* phase, but ``OnTriggerStay`` in Unity fires
**every physics frame** the collider overlaps -- a player standing inside a
turret's sight volume (no fresh ``.Touched`` edge) is never detected. Slice 1.1
added a host primitive ``connectGameObjectSignalStay(comp, go, fn)`` (a throttled
``GetPartsInPart`` poll, the per-frame STAY analog). This pass rewrites the
*specific* ``connectGameObjectSignal(<go>, "Touched", <fn>)`` binding whose
immediately-preceding origin comment marks ``OnTriggerStay`` into

    self.host:connectGameObjectSignalStay(self.gameObject, function(other)
        ...
    end)

dropping the ``"Touched", `` argument and preserving ``<go>`` + the function
expression and everything after.

This runs in GENERIC mode only, inside ``contract_pipeline.transpile_with_contract``
on the RAW ``TranspiledScript.luau_source`` BEFORE comment-stripping -- which is
why the ``-- OnTriggerStay`` origin comment (mandated by the generic contract
doc) is still present and usable as the lowering key. Generic mode deliberately
skips the legacy coherence packs, so this is generic's own re-expression of the
turret STAY-poll behaviour.

EXACT, BINDING-LOCAL match (BLOCKING design requirement):

  * The key is the comment on the line **immediately preceding** the specific
    ``connectGameObjectSignal(...,"Touched",...)`` binding. Not any "Stay"
    substring anywhere in the method; not an earlier method comment.
  * The token is the **exact** ``OnTriggerStay`` -- it must NOT match
    ``OnCollisionStay`` (which also maps to ``.Touched``) nor ``OnTriggerEnter``.
  * In an ``Awake`` with multiple bindings (turret ``OnTriggerStay``->Touched
    AND ``OnTriggerExit``->TouchEnded, or a body mixing Enter+Stay), only the
    binding whose own immediately-preceding comment is ``OnTriggerStay`` is
    rewritten.
  * Robust to ``self.host:`` and ``self.host.`` forms and to leading whitespace.
  * Idempotent: the rewritten call has no ``"Touched"`` literal and no longer
    matches, so a second pass is a no-op.

The matcher is string-span / structural (consistent with the other facet
transforms), and ``_luau_pos_is_code`` skips matches inside string literals or
``--`` comments so the rewrite never fires on a binding that lives inside a Lua
string.
"""

from __future__ import annotations

import re
from typing import Protocol


class _HasLuauSource(Protocol):
    luau_source: str


# Match the call head + first ``"Touched"`` argument of a GameObject-signal
# binding:  ``self.host:connectGameObjectSignal(<go>, "Touched",``
#
# Groups:
#   1 = leading indentation on the call's own line (used to find the line start)
#   2 = receiver+method+open-paren+<go>+comma, e.g.
#       ``self.host:connectGameObjectSignal(self.gameObject, ``  -- everything
#       up to (and including) the comma+space before the "Touched" literal.
#
# ``<go>`` is a simple dotted name (no ``()``/``[]``/``,``) -- the contract
# emits ``self.gameObject`` here. Both ``:`` (method) and ``.`` (field) call
# forms are accepted. ``"Touched"`` may use single or double quotes. The
# trailing ``,\s*`` is consumed so the rewrite drops the literal AND its
# separator, leaving the function expression as the (now first) argument.
_CONNECT_TOUCHED_RE = re.compile(
    r"""(?P<indent>[ \t]*)
        (?P<head>
            self\.host[:.]connectGameObjectSignal
            \s*\(\s*
            [A-Za-z_][A-Za-z0-9_.]*      # <go> -- simple dotted name
            \s*,\s*
        )
        ['"]Touched['"]\s*,\s*           # the "Touched" arg + separator (dropped)
    """,
    re.VERBOSE,
)

# The exact origin-comment token that gates the rewrite. Anchored to the start
# of the comment body so ``OnTriggerStay`` matches but ``OnCollisionStay`` and
# ``OnTriggerEnter`` do not. A trailing word-boundary stops it from matching a
# hypothetical ``OnTriggerStayLonger``.
_STAY_COMMENT_RE = re.compile(r"^--\s*OnTriggerStay\b")


def _luau_pos_is_code(source: str, pos: int) -> bool:
    """True if char index ``pos`` is real code, not inside a string or a
    ``--`` comment.

    Scans from the start of ``pos``'s line, tracking single/double-quoted
    strings (with backslash escapes) and ``--`` line comments -- the only forms
    the transpiler emits. Mirrors ``child_index_lowering._luau_pos_is_code``.
    """
    i = source.rfind("\n", 0, pos) + 1
    quote: str | None = None
    while i < pos:
        ch = source[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "-" and i + 1 < pos and source[i + 1] == "-":
            return False  # rest of the line (incl. pos) is a comment
        i += 1
    return quote is None


def _preceding_comment_line(source: str, line_start: int) -> str | None:
    """Return the stripped text of the non-blank line IMMEDIATELY preceding the
    line that begins at ``line_start``, or ``None`` if there is no such line.

    "Immediately preceding" skips over blank/whitespace-only lines so a binding
    annotated with a comment and a blank line still keys off that comment, but
    does NOT skip a non-blank intervening line (e.g. another statement) -- in
    that case the preceding line is that statement, not a comment, and the
    binding is left as an edge.
    """
    # ``line_start`` is the index of the first char of the binding's line. Walk
    # backwards over preceding physical lines.
    end = line_start  # exclusive upper bound of the previous line's newline
    while end > 0:
        # ``end - 1`` is the newline terminating the previous line.
        prev_nl = source.rfind("\n", 0, end - 1)
        prev_line = source[prev_nl + 1:end - 1]
        if prev_line.strip() == "":
            # Blank line -- keep walking to the line above it.
            end = prev_nl + 1
            continue
        return prev_line.strip()
    return None


def rewrite_trigger_stay_source(source: str) -> tuple[str, int]:
    """Rewrite each ``connectGameObjectSignal(<go>, "Touched", <fn>)`` binding
    whose immediately-preceding comment is ``-- OnTriggerStay...`` into
    ``connectGameObjectSignalStay(<go>, <fn>)``.

    Returns ``(new_source, count)`` where ``count`` is the number of bindings
    rewritten (0 -> ``source`` returned unchanged)."""
    count = 0

    def _repl(m: "re.Match[str]") -> str:
        nonlocal count
        # Skip matches that live inside a string literal / comment.
        if not _luau_pos_is_code(source, m.start("head")):
            return m.group(0)
        # The binding's own line begins after the captured indent.
        line_start = m.start("indent")
        comment = _preceding_comment_line(source, line_start)
        if comment is None or not _STAY_COMMENT_RE.match(comment):
            return m.group(0)
        count += 1
        # Drop ``"Touched", `` and rename the method to the Stay variant; the
        # function expression that followed becomes the (now first) trailing
        # argument, preserved verbatim by the regex consuming only up to it.
        head = m.group("head").replace(
            "connectGameObjectSignal", "connectGameObjectSignalStay", 1,
        )
        return m.group("indent") + head

    new_source = _CONNECT_TOUCHED_RE.sub(_repl, source)
    if count == 0:
        return source, 0
    return new_source, count


def lower_trigger_stay(scripts: list[_HasLuauSource]) -> int:
    """Rewrite each ``OnTriggerStay``-origin ``connectGameObjectSignal(go,
    "Touched", fn)`` binding on every script's ``luau_source`` to the Stay-poll
    host method ``connectGameObjectSignalStay(go, fn)``. Returns the number of
    scripts modified.

    GENERAL rule: keyed on the guaranteed ``-- OnTriggerStay`` origin comment
    immediately above the binding, NEVER on ``s.name`` -- it applies to any
    OnTriggerStay binding, not just the turret. OnTriggerEnter/Exit and the
    OnCollision* variants keep their ``.Touched`` edge semantics (their origin
    comments don't match)."""
    changed = 0
    for s in scripts:
        src = s.luau_source or ""
        new_src, count = rewrite_trigger_stay_source(src)
        if count and new_src != src:
            s.luau_source = new_src
            changed += 1
    return changed
