"""Canonical shared-flag name sanitization.

The cross-script shared-flag attribute name is built at runtime as
``"has" .. itemName``. ``itemName`` derives from a Unity prefab/item name
with no sanitization, so a name like ``"Red Key"`` produces ``"hasRed Key"``
— which Roblox's ``SetAttribute`` rejects (spaces/hyphens are not valid
attribute-name characters) and the ``PlayerSetSharedFlag`` funnel listener
drops (``^[%w_]+$``). The result is a silent cross-domain gameplay break.

This module is the SINGLE canonical definition of the sanitizer so the
Python source path (``scene_converter`` ItemType / itemName) and the
emitted Luau runtime path (transpiler prompt + coherence packs) produce
byte-identical tokens for ASCII input.

**Sanitizer spec (ASCII-explicit, Python + Luau byte-identical for ASCII):**
Replace each contiguous run of ``[^A-Za-z0-9_]`` with a single ``_``.

- Python MUST use ``re.sub(r"[^A-Za-z0-9_]+", "_", name)`` — NOT a
  ``\\w`` charset, because Python 3 ``\\w`` is Unicode-aware and would
  diverge from Lua's byte-oriented ``%w``.
- Luau (emitted): ``(<expr>:gsub("[^%w_]+", "_"))``.
- No case change. MUST be a no-op on clean identifiers
  (``sanitize("Key") == "Key"``) so SimpleFPS's existing literal
  ``GetAttribute("hasKey")`` readers keep matching.
"""
from __future__ import annotations

import re

# Single canonical Python sanitizer regex. ASCII-explicit by design: a
# Unicode-aware ``\w`` would diverge from Lua's byte-oriented ``%w``.
_FLAG_TOKEN_RE = re.compile(r"[^A-Za-z0-9_]+")

# Mirror the ``PlayerSetSharedFlag`` funnel listener's length cap
# (``#flagName > 64``). ``"has" + stem`` must fit, so the stem budget is
# 64 minus the 3-char ``"has"`` prefix.
_HAS_PREFIX = "has"
_MAX_FLAG_NAME_LEN = 64

# The ONE canonical inline Luau sanitizer expression, as a ``str.format``
# template. Every emitted runtime site (transpiler prompt + coherence
# packs writers + dynamic reader) MUST build its sanitized name from this
# constant so all emitted call sites are byte-identical. ``%%`` is NOT
# needed because this is a ``.format`` template (plain ``%`` is a literal),
# but Lua-pattern ``%w`` is a literal ``%w`` here regardless.
#
# Lua mirror of the Python regex: ``gsub("[^%w_]+", "_")`` replaces each
# run of non-(alphanumeric-or-underscore) with a single ``_``, matching
# ``re.sub(r"[^A-Za-z0-9_]+", "_", ...)`` byte-for-byte on ASCII input.
# The OUTER parens truncate ``gsub``'s second return value (the match
# count) so the expression yields exactly one string.
_LUAU_FLAG_SANITIZE = '({expr}:gsub("[^%w_]+", "_"))'


def canonical_flag_token(name: str) -> str | None:
    """Return the sanitized shared-flag stem for ``name``, or ``None`` to skip.

    Replaces each contiguous run of ``[^A-Za-z0-9_]`` with a single ``_``.
    No case change. A no-op on clean identifiers.

    Returns ``None`` (caller skips the mirror) when:
      - ``name`` is empty,
      - ``name`` contains no original ASCII alphanumeric (sanitizes to
        only underscores), or
      - ``"has" + stem`` would exceed the funnel's 64-char cap.
    """
    if not name:
        return None
    stem = _FLAG_TOKEN_RE.sub("_", name)
    # No original ASCII alphanumeric → token carries no identity; skip.
    if not any(c.isascii() and c.isalnum() for c in name):
        return None
    if len(_HAS_PREFIX) + len(stem) > _MAX_FLAG_NAME_LEN:
        return None
    return stem


def luau_flag_sanitize_expr(expr: str) -> str:
    """Return the canonical inline Luau ``gsub`` sanitizer wrapping ``expr``.

    ``expr`` is the Luau expression yielding the raw name (e.g. ``itemName``
    or ``name``). The emitted shape is ``(<expr>):gsub("[^%w_]+", "_")`` —
    the byte-identical mirror of :func:`canonical_flag_token` for ASCII.
    """
    return _LUAU_FLAG_SANITIZE.format(expr=expr)
