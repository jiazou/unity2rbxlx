# Slice 5 handoff → Slice 6

## Architecture decisions made (with provenance)

- **Pipeline ordering**: NOT reordered. Original phase order (`classify_storage` → `classify_scene_runtime_domains` → `build_topology`) preserved.
  - Provenance: arch synthesis (`/tmp/topology/slice-5-arch-synth.md`) verified the cycle is real; Option 3 (revert + intrinsic field) chosen because Option 1 (move `classify_scene_runtime_domains` ahead of `classify_storage`) is blocked by `module_domain.py:536-537` requiring `parent_path` from classify_storage.

- **Immutable intrinsic field**: `RbxScript.intrinsic_script_type: Optional[str]` is stamped at transpile time and never mutated. `derive_intrinsic_script_class` reads it.
  - Provenance: arch round 1 (Claude+Codex parallel). Stamp added at 3 transpile-emit sites + 3 coherence-pack/prefab-packages sites + 1 topology-override site.

- **StoragePlan persistence**: `intrinsic_script_type` persisted on `StoragePlan.decisions[]`. Backward-compat via `.get(..., None)`.
  - Provenance: code review round 2 Codex P2 + round-3 fix.

- **Stem-fallback collision exclusion**: `build_script_id_by_name` honors its docstring contract via `_compute_stem_collisions`.
  - Provenance: code review round 2 Codex P3.

- **Pre-classifier mutator gates documented**: `derive_intrinsic_script_class` docstring names the two pre-classifier mutators it is robust against, and the gates that keep them off the topology consumer today: (a) `classify_storage`'s `Script→LocalScript` routing coercion (gated by build_topology's consumer design), (b) `_subphase_cohere_scripts`'s `fix_require_classifications` `Script→ModuleScript` rewrite (gated by generic-mode early-return at `pipeline.py:2837-2843`; build_topology consumption gated to generic-mode at `pipeline.py:4057-4058`).
  - Provenance: code review round 3 Codex P1 (REFUTED on verification — chain empty by construction; mitigated by documenting the gates rather than rewriting working code).

## What slice 5 deliberately deferred to slice 6

- `_decide_script_container` is NOT rewritten. `storage_classifier.py` retains the six-rule sequence and the regex-API detectors (`_scripts_with_client_apis`, `_scripts_with_server_apis`).
- `_apply_reachability_rule` and `_stamp_container_and_path` are NOT removed.
- `_classify_storage` is NOT moved out of `write_output`.
- Server-only ModuleScript routing default (ReplicatedStorage per design doc vs preserve ServerStorage per Codex) — defer decision to slice 6.

These remain on the design doc's Phase 2a deliverable list.

## Open architectural questions for slice 6

1. **Server-only ModuleScript routing default**: Claude said ReplicatedStorage (design doc); Codex said preserve legacy ServerStorage. Slice 6 implementer must decide at start.

2. **Decision tree input set**: TopologyModuleEntry fields slice 6's `_decide_script_container` should consume — `lifecycle_role` (gated decision), `character_attached` (raw planner hint, NOT for placement), `reachability_required_container` (slice-4 triple — must be honored), `script_class` (now intrinsic), `domain`, caller_graph via `callers_of()`. Phantom field `requires_server_storage_isolation` must NOT be re-introduced.

3. **Test-migration map**: see `/tmp/topology/slice-5-arch-claude.md` Section "test-migration map" (preserve / adapt / delete / add per test in test_storage_classifier.py).

## Slice 5 final state

- PR: #151 (https://github.com/ntornow/unity2rbxlx/pull/151) — STACKED on PR #150.
- HEAD: `bf87ba9` (slice 5 round 3 docstring addendum, on top of `9fe1b70`).
- Tests: 2204 passed, no-Any clean.

## Process notes for slice 6

- Slice 5 took 3 implementer rounds + 3 review rounds. Memory cap is 4 rounds. Slice 6 is the biggest behavior change in Phase 2a (the design-doc warned ≥4 rounds). Plan for the full budget.
- Codex flagged a P1 in round 3 that turned out to be wrong (gated off by mode). Verification step matters; don't reflexively believe either reviewer.
- The parallel Claude + Codex pattern caught issues each individually missed (pipeline cycle, rehydration cycle). Keep using it.
