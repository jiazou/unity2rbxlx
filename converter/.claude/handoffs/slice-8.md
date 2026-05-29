# Slice 8 handoff (Phase 2a, scene-runtime-topology)

Slice 8 introduced the `materialize_and_classify` phase between `convert_scene` and `write_output`. This handoff captures the architecture decisions slice 9+ must inherit, what slice 8 deferred, and process notes for slice 9.

## Architecture decisions made

- **New `materialize_and_classify` phase** between `convert_scene` and `write_output`. Lifts `_subphase_emit_scripts_to_disk`, `_subphase_cohere_scripts`, `_classify_storage`. (Source: design doc revised plan + Claude arch.)
- **Option (b) safety-net** `_classify_late_appended_scripts` for autogen / runtime / scene-runtime scripts that get appended AFTER `materialize_and_classify`. Mirrors rbxlx_writer fallback routing. (Source: slice 8 R1 implementer + Codex.)
- **`materialize_and_classify` added to ESSENTIAL_PHASES** so `--phase=write_output` resumes re-run it upstream.
- **`run_all_scenes` invokes `materialize_and_classify` per scene** (R2 fix). Multi-scene `--scene all` runs now include the phase.
- **GOLDEN_PARENT_PATHS golden table** in `test_slice8_autogen_classify_gap.py` enforces ZERO drift on autogen / runtime / scene-runtime parent_paths. Hardened R2 to drive real `_subphase_inject_*` paths (not synthetic stubs).

## What slice 9 inherits

- Stable upstream classify boundary (Phase 2b prerequisite).
- StoragePlan persisted by classify in the new phase; write_output is now a pure consumer.
- The 14 well-known autogen / runtime / scene-runtime scripts pinned in GOLDEN_PARENT_PATHS.

## What slice 8 deferred to slice 9+

- Original slice 9 in revised plan: `build_topology._build_modules_block` recomputes `module_path` / `reachability_forced_container` (currently reads mutated `scene_runtime.modules[*]` rows).
- Original slice 10 in revised plan: delete `_stamp_container_and_path` placement mutations at `module_domain.py:880,886,891`. Depends on slice 9.
- Original slice 11: final test migration.
- Followup #10 (class_name-collision fix at build_topology._build_modules_block:529) should fold into slice 9.

## Reviewer-flagged polish (not blocking, address opportunistically)

- Drift loop in acceptance gate test only iterates `GOLDEN_PARENT_PATHS.items()` — new factory absent from table is NOT caught. Could enhance with "real script list / golden table" diff.
- Multi-scene loop in `run_all_scenes` hardcodes phase order (could read from PHASES).
- GOLDEN_PARENT_PATHS preconditions undocumented; would aid future contributors.

## Process notes for slice 9

- Slice 9's diff will primarily touch `build_topology._build_modules_block` (currently 567-602 per slice 8 arch reviews). Codex's recommendation: recompute from canonical helpers + raw inputs instead of reading mutated rows.
- Slice 9 has fewer load-bearing structural risks than slice 8 (one function refactor + tests). Expected review-round count: 2-3.
- Slice 7 pattern: tests passing with pre-stamped fixtures can mask producer/consumer mismatches. Same care applies to slice 9.
