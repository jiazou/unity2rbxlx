"""Phase 4.4 — method-completeness diagnostic for transpiled scripts.

Compares the C# source's method list against the AI-produced Luau
output and flags methods that disappear silently — neither present as a
Luau function nor marked with a ``-- UNCONVERTED`` / ``-- TODO`` comment.

The old regex-based ``luau_validator.py`` was deleted 2026-04-18 and is
NOT resurrected here. This is a pure diagnostic — it emits warnings that
surface in the JSON conversion report, and it neither rewrites nor
validates Luau syntax. Syntax validation stays with ``luau-analyze``
plus the reprompt loop in ``code_transpiler._luau_syntax_check``.
"""

from __future__ import annotations

import re


# C# comments + string literals must be stripped before method-name
# extraction — otherwise ``// public void Foo()`` or ``"public void
# Bar()"`` inside a log string registers as a real method declaration.
_CSHARP_COMMENT_OR_STRING = re.compile(
    r"""
    //[^\n]*
    | /\*.*?\*/
    | @"(?:[^"]|"")*"
    | "(?:\\.|[^"\\])*"
    | '(?:\\.|[^'\\])*'
    """,
    re.DOTALL | re.VERBOSE,
)

# C# method: any access modifier + return type + identifier + ``(``.
# The outer-most capture group is the method name.
_CSHARP_METHOD_RE = re.compile(
    r"(?:public|private|protected|internal|static|override|virtual|abstract|async|sealed"
    r"|new|partial)\s+"
    r"(?:(?:public|private|protected|internal|static|override|virtual|abstract|async|sealed"
    r"|new|partial)\s+)*"
    r"[\w<>\[\],\s]+?\s+(\w+)\s*\(",
    re.MULTILINE,
)

# Luau function definition forms we recognize:
#   function Class:Method(...)
#   function Class.Method(...)
#   function name(...)
#   local function name(...)
_LUAU_FUNC_RE = re.compile(
    r"function\s+(?:\w+[.:])?([\w]+)\s*\(",
    re.MULTILINE,
)


# Unity lifecycle hooks that the transpiler intentionally lowers into
# top-level code / Heartbeat connections rather than named functions.
# Exempt them from missing-method warnings so we don't flood the report.
_LIFECYCLE_EXEMPT: frozenset[str] = frozenset({
    "Awake", "Start", "Update", "FixedUpdate", "LateUpdate",
    "OnEnable", "OnDisable", "OnDestroy", "OnApplicationQuit",
    "OnValidate", "OnDrawGizmos", "OnDrawGizmosSelected",
    "Reset", "OnGUI", "Main",
})


def _strip_comments_and_strings(source: str) -> str:
    """Collapse comments + string/char literals to a space so they don't
    contribute method names. Same technique used by PR 4's dep-graph
    extractor; duplicated here so this module stays standalone.
    """
    return _CSHARP_COMMENT_OR_STRING.sub(" ", source)


def check_method_completeness(
    csharp_source: str,
    luau_source: str,
    source_name: str = "<script>",
) -> list[str]:
    """Return a warning per C# method missing from the Luau output.

    A method is considered "converted" when either:
      - Its exact name appears as a Luau function definition, OR
      - Its name appears in a ``-- UNCONVERTED …`` or ``-- TODO …``
        comment (intentional drop, honoured per the prompt rule).

    Unity lifecycle hooks (Awake/Start/Update/…) are exempt because
    the transpiler idiomatically lowers them into top-level code or
    ``RunService`` connections rather than named Luau functions.

    Pure function, no I/O.
    """
    if not csharp_source or not luau_source:
        return []

    clean_cs = _strip_comments_and_strings(csharp_source)
    csharp_methods: set[str] = set()
    for match in _CSHARP_METHOD_RE.finditer(clean_cs):
        name = match.group(1)
        if name in _LIFECYCLE_EXEMPT:
            continue
        csharp_methods.add(name)
    if not csharp_methods:
        return []

    luau_functions: set[str] = set()
    for match in _LUAU_FUNC_RE.finditer(luau_source):
        luau_functions.add(match.group(1))

    # Methods that the AI explicitly marked as unconverted via comment.
    # Accept both `-- UNCONVERTED: foo` and `-- TODO: foo` idioms.
    commented: set[str] = set()
    for line in luau_source.splitlines():
        stripped = line.strip()
        if not stripped.startswith("--"):
            continue
        upper = stripped.upper()
        if "UNCONVERTED" not in upper and "TODO" not in upper:
            continue
        for method in csharp_methods:
            if method in stripped:
                commented.add(method)

    missing = sorted(csharp_methods - luau_functions - commented)
    return [
        f"[{source_name}] C# method '{m}' missing from Luau output "
        f"(neither a function definition nor an UNCONVERTED / TODO comment)"
        for m in missing
    ]
