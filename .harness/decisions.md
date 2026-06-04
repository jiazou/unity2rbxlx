
## Run main-20260604-201520 — scene-runtime recut + refactor execution (2026-06-04)

# Decisions — drive run main-20260604-201520

Task: scene-runtime recut + mega-file refactor → execution (validate-and-re-baseline, NOT redesign).
Design of record: PR #177 (`docs/scene-runtime-refactor-execution-plan`). 9 locked decisions stand.

## Plan stage

- **Track-0 re-baseline (2026-06-04):** LOC figures (6495/5553/5373) confirmed exact. Refreshes:
  packs 27→29 registered (24 after PR8, NOT 22); `_ctx()` 58→66 (grep-driven, harmless);
  PR-D phase table confirmed stale (missing `plan_scene_runtime` + `materialize_and_classify`,
  both live). **No locked decision invalidated.** Figure refreshes land in their follow-on runs.

- **D1 — Run scope = PR5 canary (Slice T + Slice H) only**, one PR. Follow-ons: PR-B, PR6/7/8,
  Track 2 (PR-G/PR-H), Track 3 (PR-C/D, PR-E0/E/F). Classification: User-Challenge → Gate A.

- **D2 — Slice T ‖ Slice H** (disjoint owns: `contract_pipeline.py`+new lowering file vs
  `module_domain.py`; no data dep). Classification: Mechanical.

- **D3 — autoplan reduced to validate-against-locked-decisions** per task directive; dual-voice
  design review (Claude + codex, the owed second voice) is the substantive gate. Classification: Taste.

- **D4 — Slice T = GENERAL structure-gated child-index rule** (skip injected non-spatial children
  for any GetChild), not turret-name-gated (no-hardcoding). Classification: Mechanical.

## Empirical re-baseline (the load-bearing finding)
Fresh `u2r.py convert SimpleFPS --scene-runtime generic --no-upload --clean` @ current main:
- **Slice H STALE:** HudControl already `domain="client"`, `ModuleScript`, `ReplicatedStorage`,
  `runtime_bearing`, `requireable`, no fail-close → classifier rule is a no-op. → **D5: drop Slice H.**
- **Slice T LIVE:** Turret/Turret(1)/Turret(2) Parts have Sound at child[0], Base at child[1];
  Turret.luau does `GetChildren()[1]:GetPivot()` → Sound:GetPivot() crash. → fix as designed.
- **D6: contract_verifier in SHADOW mode** (1 info-level dead-module violation; no HudControl trip).

Design review: round 1 FINDINGS (both voices flagged Slice H stale) → round 2 CONVERGED
(empirical repro resolved all P1s by removing the no-op slice). Codex = the owed second voice,
obtained this run (266k tokens, repo-grounded).

## Slice review round 1 (codex caught what the passive reviewer missed)
- Claude reviewer: CONVERGED (missed all of codex's findings — asserted receiver-parse + string-strip
  "hold up"; codex produced counterexamples to both). Adversarial voice is load-bearing.
- codex: 2 BLOCKING (over-broad scope clobbers camera weaponSlot; nested-chain span corruption) + 1 MAJOR
  (backtick strings unstripped) + 1 MINOR (depth-0 whitespace receiver parse).
- **D7 — Reuse the existing legacy pack `_fix_unity_transform_child_index`, don't rebuild.** It already
  solves this (the `__unityChild` helper + simple-receiver regex + `_luau_pos_is_code`), is in the frozen
  legacy baseline, and its simple-receiver regex + non-overlapping `re.sub` structurally avoid codex's
  nested-chain corruption (BLOCKING #2) and the whitespace mis-parse (MINOR #4). codex BLOCKING #1 is MOOT:
  legacy already rewrites all `GetChildren()[n]` incl. weaponSlot (proven, in-baseline) → generic parity is
  correct. Extract the shared logic into a helper both the pack + generic path call (owns += script_coherence_packs.py).
  Classification: Mechanical (forced by "reuse don't rebuild" + the existing-pack-search project rule).

## Slice 1.1 fix round 1 (implementer)
- Extracted shared lowering logic (`_UNITY_CHILD_HELPER`, `_GETCHILDREN_INDEX_RE`,
  `_luau_pos_is_code`, `rewrite_child_index_source`, `source_has_child_index`) into
  `child_index_lowering.py`; `script_coherence_packs.py` re-imports + re-exports them, legacy
  pack behavior byte-identical (test_unity_transform_child_index.py + full fast suite green).
- `contract_pipeline.py` in owns but UNCHANGED: the existing `lower_child_index(transpilation.scripts)`
  wiring (generic-only) already works with the rewritten helper on `luau_source`; no edit needed.
