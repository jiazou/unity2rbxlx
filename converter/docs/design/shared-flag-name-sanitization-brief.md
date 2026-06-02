# Design brief — P1: shared-flag attribute-name sanitization

**Worktree:** `unity2rbxlx-flagname`, branch `fix/shared-flag-name-sanitization`, off `upstream/main`.

## ONE goal
Make the cross-script shared-flag attribute name `[%w_]`-safe at a SINGLE canonical
source so the writer, the dynamic reader, the `PlayerSetSharedFlag` funnel listener,
and Roblox's own `SetAttribute` all agree. Generic — must hold for ANY Unity game,
not just SimpleFPS (whose item names happen to be clean identifiers).

## The bug (generic correctness)
The shared-flag name is built at runtime as `"has" .. itemName`. `itemName` derives
from the prefab/item name with NO sanitization. For an item like `"Red Key"`:
- writer builds `"hasRed Key"` → **`SetAttribute("hasRed Key", …)` errors** (Roblox
  rejects spaces/hyphens in attribute names);
- the funnel listener drops it: `if … not string.match(flagName, "^[%w_]+$") then return end`
  (`autogen.py:190`);
- server-domain readers then read `nil` → silent gameplay break.
SimpleFPS dodges this only because its items are `Key`/`Rifle` (already `[%w_]`).

## Grounding facts (verify before reasoning)
- **GF1 — runtime construction sites** (`"has" .. itemName`): the generic funnel-writer
  prompt example `code_transpiler._GENERIC_RUNTIME_PROMPT` (~L1262-1276); legacy packs
  `script_coherence_packs.py:389, 832, 959` (writers); `:3029` (DYNAMIC reader
  `GetAttribute("has" .. name)`). Literal readers (`GetAttribute("hasKey")`) also exist
  but their literal is a clean code identifier.
- **GF2 — funnel listener** enforces `#flagName<=64 and flagName matches "^[%w_]+$"`
  (`autogen.py:186-197`). Roblox `SetAttribute` enforces the same name charset.
- **GF3 — Python source** `scene_converter._apply_gameplay_attributes` (L3932-3953)
  derives `ItemType` from the raw prefab name (`name.replace('Pickup','').strip()`).
  `ItemType` is NOT used for display anywhere (grep clean) → safe to sanitize.
- **GF4 — scan** `scene_runtime_topology/shared_flag_channels.py` captures read names with
  `\w+` (== `^[%w_]+$` minus length), deliberately matching the funnel allowlist; once
  names are sanitized, the scan is correct by construction.
- **GF5 — prompt not frozen**: only `_AI_SYSTEM_PROMPT` is byte-frozen
  (`code_transpiler.py:1104` + `tests/test_ai_system_prompt.py`); `_GENERIC_RUNTIME_PROMPT`
  is editable (no cache-key test), but editing it changes generic-mode AI output.

## The canonical-contract requirement
Writer and DYNAMIC reader BOTH build `"has" .. name` from the same raw `name`, so they
must apply the IDENTICAL sanitizer or they silently disagree (the Door-bug class:
cross-script state needs ONE canonical derivation). Literal readers are clean by nature.

## Decision forks for reviewers
1. **Where to sanitize (placement).**
   - A (runtime, necessary): a shared Luau sanitizer used at every dynamic `"has" .. name`
     site (prompt + packs writer + packs dynamic reader). Handles ALL `name` sources
     (ItemType attribute, C# literal arg, etc.) uniformly.
   - B (python source): sanitize `ItemType` in `scene_converter` so the attribute value
     is clean. Alone insufficient (misses C#-literal `GetItem("Red Key")` args).
   - C = A + B (defense in depth; B is cheap).
   Recommend **C** (A is the load-bearing half).
2. **Helper vs inline.** A shared Luau helper (`sharedFlagName(name)` injected once by
   autogen, referenced everywhere) is DRY but adds a require/global dependency to every
   writer/reader; inline sanitize (a small `(name:gsub("[^%w]","_"))` expression at each
   site) is self-contained and robust for AI-emitted prompt code (no dependency the AI can
   forget to require). Recommend **inline in the prompt example** (AI reliability) +
   **shared helper for the deterministic packs**, OR inline everywhere for uniformity.
3. **Sanitize strategy.** Replace each non-`[%w_]` run with a single `_`
   (`"Red Key"`→`"hasRed_Key"`) vs strip them (`→"hasRedKey"`). Replace-with-`_` preserves
   uniqueness (avoids `"Red Key"` vs `"RedKey"` collision). Leading digit is allowed by
   `^[%w_]+$` (the `has` prefix guarantees a leading letter anyway). Recommend **replace
   non-`[%w_]` → `_`, collapse runs**. Empty/all-stripped → skip the mirror (no-op).

## Genericity guardrails
- One sanitizer definition; identical semantics in Python (B) and Luau (A) so a name
  sanitized in scene_converter and one sanitized at runtime produce the SAME token.
- No hardcoded item names.
- Don't change the funnel listener's `^[%w_]+$` (it's the canonical gate; sanitized names
  must PASS it, not relax it).

## LOCKED DECISIONS (after Claude+Codex parallel review, 2026-06-02 — both converged)

**Canonical sanitizer spec (ONE definition, Python + Luau must produce byte-identical
tokens for ASCII):**
- Replace each contiguous run of `[^A-Za-z0-9_]` with a single `_`. ASCII-EXPLICIT —
  Python MUST use `re.sub(r"[^A-Za-z0-9_]+", "_", name)`, NOT `\w` (Python 3 `\w` is
  Unicode-aware and would diverge from Lua `%w`). Luau: `(name:gsub("[^%w_]+", "_"))`.
- No case change. **No-op on clean identifiers** → `sanitize("Key")=="Key"`,
  `sanitize("Rifle")=="Rifle"` (SimpleFPS byte-identity; existing literal
  `GetAttribute("hasKey")` readers must keep matching).
- Skip the mirror entirely when: name is empty, sanitizes to no original alphanumeric,
  or `"has"+stem` would exceed 64 chars (the funnel's cap). Don't relax the funnel gate.

**Placement: A only (B REJECTED in code review — see correction below).** A (runtime) is
load-bearing AND sufficient.

> **CORRECTION (code-review, 2026-06-02):** the design pair voted C (A+B), but the code
> review (Codex P1×2) showed **B is harmful**: `itemName`/`ItemType` are GAMEPLAY PAYLOADS
> forwarded raw to `GetItem(itemName)` (`packs.py:402/850` → client listener) and
> `pickup_runtime.luau`'s `SetAttribute("GetItem", itemType)`. Sanitizing them at the
> python source corrupts dispatch for dirty-name items (`"Red Key"` → `GetItem("Red_Key")`).
> The runtime gsub at the `"has" .. name` concat (A) sanitizes the FLAG only, leaving the
> raw value for gameplay — necessary and sufficient. **B was reverted.** The Python
> `sanitize_flag_stem` is kept ONLY as the parity-test reference mirror of the emitted Luau
> (no production caller); its former skip-rules (the P2 asymmetry) were removed — the
> runtime gsub can't skip, so the funnel gate is the overlong backstop and a symbol-only
> name → consistent `has_` (documented degenerate edge, not guarded).
- **A (runtime, inline from ONE Python constant):** `code_transpiler._GENERIC_RUNTIME_PROMPT`
  example (`:1265`) + add a sentence telling the AI to sanitize any name before
  concatenating; pack writers `script_coherence_packs.py:389/832/959`; pack DYNAMIC reader
  `:3029` (Machine `GetAttribute("has"..name)`). All pack sites emit the IDENTICAL inline
  `gsub` from one shared Python string constant.
- **B (python source):** sanitize `ItemType` at `scene_converter.py:3936` AND serialized
  `itemName` at `scene_converter.py:3131` (Codex: pickups read raw `itemName` at
  `packs.py:305`). One shared Python sanitizer util used by both.

**Idempotency:** update the pickup guard regex `script_coherence_packs.py:737/747` to match
the new `"has" .. (itemName:gsub(...))` emitted shape so a twice-`run_packs()` pass does
NOT double-wrap (coherence-pack twice-call rule).

**Scan correctness-by-construction:** fix `shared_flag_channels.py:107` comment (Python
`\w` ≠ ASCII) and make `_GET_ATTR_RE` ASCII-explicit (`[A-Za-z0-9_]+`) so the literal-read
capture matches the funnel's ASCII `^[%w_]+$` exactly.

**Do NOT touch:** Door literal-suffix readers (`packs.py:2058/2114`, suffix already
`(\w+)`-constrained → clean); the funnel allowlist `autogen.py:186-190` (canonical gate
sanitized names must PASS).

**Out of scope (noted):** the scan is blind to DYNAMIC readers (`_GET_ATTR_RE` matches
only quoted literals) — a game whose ONLY server reader is dynamic can still get
`present=False`. Pre-existing scan limitation, separate from name sanitization; do not
expand scope here.

**Optional hygiene (low priority):** `pickup_runtime.luau` writes `SetAttribute("GetItem",
itemType)` unsanitized but nothing reads "GetItem" (dead-end) — sanitize for hygiene if
trivial, else leave + comment.

## Questions for reviewers
- Is runtime sanitization (A) truly necessary, or does python-source (B) cover every path?
- Any writer/reader-disagreement edge a single shared sanitizer still misses?
- Inline-vs-helper for the AI prompt: which is more reliable in emitted output?
