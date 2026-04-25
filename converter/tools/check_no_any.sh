#!/usr/bin/env bash
# check_no_any.sh — Block new `Any` annotations outside boundary files.
#
# PRINCIPLE: `Any` is allowed only where untyped external data crosses the
# system boundary (YAML parsers, untyped library seams). Inside the typed
# core, `Any` is a bug — either the type exists and was skipped, or the
# boundary was pushed too far inside.
#
# This gate runs against the PR diff and only fails on ADDED lines. Existing
# `Any` in non-boundary files is recognized debt, tracked as cleanup work.
# A refactor that moves a legacy `Any` line will trip the gate (boy-scout
# rule: clean it while you're there).
#
# Usage: bash check_no_any.sh [<base_ref>]
#   default base_ref = origin/main
#
# Allowlist lives at tools/no-any-allowlist.txt.

set -euo pipefail

BASE="${1:-origin/main}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ALLOWLIST_FILE="$SCRIPT_DIR/no-any-allowlist.txt"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ ! -f "$ALLOWLIST_FILE" ]; then
  echo "ERROR: allowlist file not found: $ALLOWLIST_FILE" >&2
  exit 2
fi

# Verify $BASE is reachable. If checkout fetch-depth is too small or the base
# ref was never fetched, fail loudly here — silently passing the gate when
# the diff can't be computed is exactly the failure mode we don't want.
if ! (cd "$REPO_ROOT" && git rev-parse --verify --quiet "$BASE" >/dev/null); then
  echo "ERROR: base ref '$BASE' not reachable locally." >&2
  echo "Fetch it before running the gate, e.g.:" >&2
  echo "  git fetch origin '${BASE#origin/}' --depth=200" >&2
  exit 2
fi

# Build the allowed-files set (paths only, strip the | reason).
allowed_paths=$(grep -v '^\s*#' "$ALLOWLIST_FILE" | grep -v '^\s*$' | awk -F' \\| ' '{print $1}' | sed 's/[[:space:]]*$//')

# Get the diff. Only added lines (start with `+`, not `+++`).
# Restrict to .py files in core/, converter/, unity/, roblox/ under converter/.
# No `|| true` here — if git diff itself errors, we want the gate to fail.
diff_output=$(cd "$REPO_ROOT" && git diff "$BASE"...HEAD --unified=0 -- \
  ':(glob)converter/core/**/*.py' \
  ':(glob)converter/converter/**/*.py' \
  ':(glob)converter/unity/**/*.py' \
  ':(glob)converter/roblox/**/*.py')

if [ -z "$diff_output" ]; then
  echo "no-any-gate: no relevant Python changes in diff."
  exit 0
fi

# Walk the diff. Track current file (from `+++ b/<path>` headers).
# For each added line, check if it introduces an `Any` annotation.
# Annotation patterns we flag:
#   `: Any`            param/var/field annotation
#   `-> Any`           return annotation
#   `Any |`  / `| Any` union member
#   `[Any]` / `[Any,`  type argument (list[Any], dict[X, Any], etc.)
# We do NOT flag:
#   `from typing import Any`     (import line, no `:` before)
#   `# ... Any ...`              (comment-only)
#   string literals containing Any
#   the word in identifiers like `MyAny`, `Anything`, `anyone` (word-boundary regex below)

violations=$(echo "$diff_output" | awk '
  /^\+\+\+ b\// {
    sub(/^\+\+\+ b\//, "")
    file=$0
    next
  }
  /^\+/ && !/^\+\+\+/ {
    line=substr($0, 2)
    # Strip comments (everything from the first # not inside a string).
    # Approximate: drop from the first # to end of line. Loses Any inside
    # f-strings with #, but those are rare and not annotations anyway.
    sub(/[ \t]*#.*$/, "", line)

    # Skip pure import lines.
    if (line ~ /^[ \t]*from[ \t]+typing[ \t]+import/) next
    if (line ~ /^[ \t]*import[ \t]+typing/) next

    # Match Any (or typing.Any) with annotation-context delimiter on the
    # left side and a permitted token on the right.
    if (match(line, /(:[ \t]*|->[ \t]*|\[[ \t]*|,[ \t]*|\|[ \t]*)(typing\.)?Any([ \t]*[],|)>:]|[ \t]*$)/)) {
      print file ": " line
    }
  }
')

if [ -z "$violations" ]; then
  echo "no-any-gate: pass (no new Any annotations in diff)."
  exit 0
fi

# Filter out allowed files. Allowed entries match the START of the diff path.
# (i.e. allowlist `converter/unity/yaml_parser.py` matches that exact path.)
real_violations=""
while IFS= read -r line; do
  [ -z "$line" ] && continue
  # Extract just the file path (everything before ": ")
  vfile="${line%%: *}"
  is_allowed=0
  while IFS= read -r allowed; do
    [ -z "$allowed" ] && continue
    if [ "$vfile" = "$allowed" ]; then
      is_allowed=1
      break
    fi
  done <<< "$allowed_paths"
  if [ $is_allowed -eq 0 ]; then
    real_violations="${real_violations}${line}"$'\n'
  fi
done <<< "$violations"

if [ -z "${real_violations//[$'\n\t ']/}" ]; then
  echo "no-any-gate: pass (all Any additions were in allowlisted boundary files)."
  exit 0
fi

cat >&2 <<EOF

ERROR: new \`Any\` annotations introduced outside the boundary allowlist.

PRINCIPLE: \`Any\` is allowed only where untyped external data crosses the
system boundary. Inside the typed core, \`Any\` is a bug.

Offending additions:
$(echo "$real_violations" | sed 's/^/  /')

Fix options:
  1. Replace \`Any\` with the real type. The dest type system lives at
     converter/core/roblox_types.py and converter/core/unity_types.py.
  2. If this code is genuinely a typed/untyped boundary (YAML parser,
     untyped library seam), add the file to converter/tools/no-any-allowlist.txt
     with a one-line architectural justification.

EOF
exit 1
