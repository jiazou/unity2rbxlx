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
transforms). ``<go>`` is captured as the whole first argument (a name, an
index, or a call -- not just ``self.gameObject``) and preserved verbatim.
Two abstain guards stop a rewrite inside a Lua string/comment so source is
never corrupted: ``_luau_pos_is_code`` skips short quoted strings and ``--``
line comments (line-local), and ``_luau_pos_in_long_bracket`` skips multi-line
long-bracket strings (``[[ ... ]]``/``[=[ ... ]=]``) and block comments
(``--[[ ... ]]``) opened on an earlier line.
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
#   indent = leading indentation on the call's own line (used to find line start)
#   head   = receiver+method+open-paren+<go>+comma, e.g.
#       ``self.host:connectGameObjectSignal(self.gameObject, ``  -- everything
#       up to (and including) the comma+space before the "Touched" literal.
#
# ``<go>`` is captured NON-GREEDILY (``.+?``) as the minimal text up to the
# ``, "Touched",`` separator. The anchor ``,\s*['"]Touched['"]`` forces the
# capture to extend past any internal commas inside the go expression (a call
# ``self:getTriggerPart()`` or an index ``self.parts[1]`` with a ``foo(a, b)``
# subexpression is captured whole), so a local alias ``trigger``, an index,
# or a call -- not just the contract default ``self.gameObject`` -- all match
# and are preserved verbatim in the rewrite. Both ``:`` (method) and ``.``
# (field) call forms are accepted. ``"Touched"`` may use single or double
# quotes. The trailing ``,\s*`` is consumed so the rewrite drops the literal
# AND its separator, leaving the function expression as the (now first) arg.
#
# NO ``re.DOTALL``: ``.`` excludes newlines, so the whole ``connect...(...,
# "Touched",`` match stays on one physical line. The transpiler always emits
# the ``<go>, "Touched",`` head on a single line; keeping ``.`` line-local
# means the non-greedy ``<go>`` can never run past the binding's line to
# swallow a ``"Touched"`` on some later line.
_CONNECT_TOUCHED_RE = re.compile(
    r"""(?P<indent>[ \t]*)
        (?P<head>
            self\.host[:.]connectGameObjectSignal
            \s*\(\s*
            .+?                          # <go> -- minimal text up to ", \"Touched\","
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


def _luau_pos_in_long_bracket(source: str, pos: int) -> bool:
    """True if char index ``pos`` is inside an OPEN Luau long-bracket string
    (``[[ ... ]]``, ``[=[ ... ]=]`` and higher level forms) or long block
    comment (``--[[ ... ]]``) opened earlier in ``source`` and not yet closed.

    ``_luau_pos_is_code`` only scans from the start of ``pos``'s own line, so a
    binding inside a MULTI-LINE long-string/comment payload (opened on an
    earlier line) looks like live code to it and would be corrupted. This scans
    the WHOLE source up to ``pos`` and reports whether we are inside such a
    span, biasing the rewrite to ABSTAIN (the safe degrade -- never corrupt a
    string). Single/double-quoted strings and ``--`` line comments are handled
    by ``_luau_pos_is_code``; this only adds the long-bracket forms.
    """
    i = 0
    n = len(source)
    while i < pos:
        ch = source[i]
        # ``--`` may begin a line comment or, if immediately followed by a long
        # bracket, a long block comment. A long block comment shares the same
        # ``[=*[ ... ]=*]`` delimiters as a long string.
        if ch == "-" and i + 1 < n and source[i + 1] == "-":
            j = i + 2
            level = _long_bracket_open_level(source, j)
            if level is not None:
                # ``--[=*[`` long block comment: skip to its closing ``]=*]``.
                close = source.find("]" + "=" * level + "]", j)
                if close == -1 or close >= pos:
                    return True  # pos is inside this open block comment
                i = close + level + 2
                continue
            # Plain ``--`` line comment: skip to end of line (or pos).
            nl = source.find("\n", j)
            if nl == -1 or nl >= pos:
                return False  # line comment runs through pos -> not a long span
            i = nl + 1
            continue
        if ch in ("'", '"'):
            # Short quoted string: skip to its matching close (honor escapes).
            quote = ch
            i += 1
            while i < n:
                c = source[i]
                if c == "\\":
                    i += 2
                    continue
                if c == quote or c == "\n":
                    break
                i += 1
            i += 1
            continue
        if ch == "[":
            level = _long_bracket_open_level(source, i)
            if level is not None:
                # Long string ``[=*[``: skip to its closing ``]=*]``.
                close = source.find("]" + "=" * level + "]", i + level + 2)
                if close == -1 or close >= pos:
                    return True  # pos is inside this open long string
                i = close + level + 2
                continue
        i += 1
    return False


def _long_bracket_open_level(source: str, i: int) -> int | None:
    """If ``source[i:]`` begins a Luau long-bracket opener ``[=*[``, return the
    number of ``=`` signs (the bracket level: 0 for ``[[``, 1 for ``[=[`` ...).
    Otherwise return ``None``."""
    if i >= len(source) or source[i] != "[":
        return None
    j = i + 1
    while j < len(source) and source[j] == "=":
        j += 1
    if j < len(source) and source[j] == "[":
        return j - (i + 1)
    return None


def _preceding_comment_line(source: str, line_start: int) -> str | None:
    """Return the stripped text of the LITERAL immediately-preceding physical
    line (the one ending at the newline just before ``line_start``), or ``None``
    if there is no preceding line.

    "Immediately preceding" is STRICT: it does NOT skip blank/whitespace-only
    lines. The design contract emits the ``-- OnTriggerStay`` origin comment
    directly above its binding, so requiring the comment on the literal previous
    line is both correct and safe. A blank line OR a non-blank statement between
    the comment and the binding therefore returns that (blank/statement) line --
    not the comment -- and the binding is left as an edge.
    """
    if line_start == 0:
        return None
    # ``line_start - 1`` is the newline terminating the previous physical line.
    # (If the char before the binding's line isn't a newline, ``line_start``
    # wasn't a line start -- but the matcher always anchors on a line start.)
    prev_nl = source.rfind("\n", 0, line_start - 1)
    prev_line = source[prev_nl + 1:line_start - 1]
    return prev_line.strip()


def rewrite_trigger_stay_source(source: str) -> tuple[str, int]:
    """Rewrite each ``connectGameObjectSignal(<go>, "Touched", <fn>)`` binding
    whose immediately-preceding comment is ``-- OnTriggerStay...`` into
    ``connectGameObjectSignalStay(<go>, <fn>)``.

    Returns ``(new_source, count)`` where ``count`` is the number of bindings
    rewritten (0 -> ``source`` returned unchanged)."""
    count = 0

    def _repl(m: "re.Match[str]") -> str:
        nonlocal count
        # Skip matches that live inside a short string literal / ``--`` line
        # comment (scanned line-locally) OR inside a multi-line long-bracket
        # string / block comment opened on an earlier line (scanned from source
        # start). Either => ABSTAIN, never corrupt the payload.
        if not _luau_pos_is_code(source, m.start("head")):
            return m.group(0)
        if _luau_pos_in_long_bracket(source, m.start("head")):
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
