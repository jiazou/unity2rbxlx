# Refactor Plan — Resolve AI-Hostile Concentrations

Date: 2026-05-21
Companion to `docs/architecture_critique.md`.
Status: **proposal — pending `/plan-eng-review`, then held until scene-runtime-contract 9-PR effort lands upstream.**

## Decisions locked (2026-05-21)

- **Sequencing:** all seven refactor PRs (including PR-A and PR-B) are held until the scene-runtime-contract 9-PR effort merges into ntornow upstream. Rationale: avoid two concurrent multi-PR efforts competing for review attention while the 14-round-converged scene-runtime design is still in draft.
- **Next gate:** this plan goes through `/plan-eng-review` before any PR starts. The review will lock data flow, module boundaries, and the test matrix — the same discipline applied to the scene-runtime design doc.

## Strategy in one paragraph

Seven PRs, mechanical transformations only, sequenced around the in-flight scene-runtime-contract 9-PR effort. Production code splits are accompanied by matching test splits in the same PR — never let the test file lag the production file it covers. Every PR carries a golden-output regression on SimpleFPS so any silent behavior change shows up before merge. Total effort estimate: ~3 engineer-weeks of AI-driven implementation, spread across two phases gated by external work.

---

## Constraints that shape the sequence

1. **Hold on `scene_converter.py`.** Three active worktrees (`unity2rbxlx-pr1/2/3a` per `MEMORY.md`) are touching scene_converter for the scene-runtime-contract effort, which is gated to land as a 9-PR batch (none merged yet). Any refactor of that file would force a 9-way rebase on a design that converged after 14 review rounds. **Anything that touches scene_converter waits.**
2. **`_ctx()` elimination also waits.** It lives entirely in `scene_converter.py:185-200`. Same blocker.
3. **No-Any CI gate.** Per `[[no_any_ci_gate]]` memory, every PR must pass `converter/tools/check_no_any.sh`. Use concrete types and TypedDicts in any new code.
4. **Fork PR base.** Per `[[fork_pr_base_repo]]`, every PR branches off `origin/main` and targets ntornow upstream.
5. **Reviewable size.** Each PR diff stays under ~1500 lines of *real* change. Pure renames (git-tracked file moves) don't count against that budget; logic changes do.

---

## The two phases (both currently held)

### Phase 1 — parallel-safe in principle, held in practice

These PRs touch *only* `converter/CLAUDE.md`, `converter/pipeline.py`, and `converter/script_coherence_packs.py` (plus their tests). None of those files are in the scene-runtime scope, so they *could* run in parallel — but eng-review decision is to **hold all PRs until scene-runtime-contract lands upstream**, to avoid two concurrent multi-PR efforts competing for review attention.

### Phase 2 — gated on scene-runtime landing

`_ctx()` elimination and the `scene_converter.py` split. Cannot start until PR8 of the scene-runtime effort merges into ntornow upstream — three active worktrees touch `scene_converter.py` today.

## Eng-review decisions (2026-05-21)

The plan went through `/plan-eng-review` with codex as outside voice. Nine decisions are locked:

1. **Pipeline dispatch:** PR-D uses a `PHASE_FUNCS: dict[str, Callable]` dispatch table. Per-phase wrapper methods on `Pipeline` are deleted. Only `run_all/run_all_scenes/run_through/resume/_run_phase` plus a new public `run_phase(name)` for tests remain on the class.
2. **PR-E shim:** Explicit submodule imports (`from .packs import fps, doors, pickups, proximity, misc`); no `import *`. Trigger registration as a side effect of import.
3. **Script-assembly split:** PR-D extracts the script-assembly grab bag into 5 themed modules (`script_binding`, `storage_classification`, `rehydration`, `runtime_injection`, `reporting`) — not deferred to a follow-up.
4. **Golden snapshot:** Canonicalize-then-hash. Snapshot includes sha256 of each script's source content (not line count), preserves parent/child hierarchy traversal order (no flat-sort that erases sibling order), excludes mtimes/timestamps/temp paths.
5. **Golden test home:** Extend existing `converter/tests/test_byte_equivalence.py` with a `TestFrozenBaseline` class. No new file.
6. **Baseline projects:** SimpleFPS + Gamekit3D + 3D-Platformer (three sha256 baselines under `tests/golden/`).
7. **Pipeline state shape:** Phase functions sign `(state: PipelineState, ctx: ConversionContext, services: PipelineServices)`. A new `PipelineServices` dataclass holds `output_dir`, `skip_binary_rbxl`, `_context_path`, `_is_resume`, `_fps_artifacts_at_init`, plus bound references to cross-cutting helpers (`classify_storage`, `bind_scripts_to_parts`, `rehydrate_scripts_from_disk`, `inject_runtime_modules`, `generate_prefab_packages`, `collect_all_scripts`, `collect_method_warnings`).
8. **Test API contract:** PR-D rewrites the 16+ existing test call sites that hit `pipeline.extract_assets()`, `pipeline.write_output()`, `pipeline.resolve_assets()` directly. Tests use `pipeline.run_phase('extract_assets')` (new public method) or import the phase function directly.
9. **Pack ordering — PR-E0 prelude:** Before PR-E, audit current pack execution order on `origin/main`. For every pack that detects against post-rewrite shape produced by an earlier pack, add explicit `after=('producer_name',)` edges. After PR-E0 lands, registration order becomes irrelevant — `_topological_order` enforces correctness.

---

## Phase 1 PRs

### PR-A — Trim `converter/CLAUDE.md` (½ day, zero risk)

**Scope:** Cut historical narrative from `converter/CLAUDE.md`:
- Delete: "Autonomous Work Plan", "Recent Session" blocks, "Development History (2026-03-24 through 2026-03-28)", "Full upload test (2026-03-25)" sections.
- Move surviving session highlights to `TODO_archive.md` (preserve git-tracked history).

**Keep verbatim:** Bug fix protocol, Upload semantics, Coordinate System, Test Projects, CLI Commands, Mesh Sizing, Asset Resolution, Inline-over-runtime principle, Roblox API safety rules (Agas Map, scaffolding).

**Target:** 322 lines → ~150 lines. ~1.5K tokens saved per session.

**Risk:** None — markdown change only. No code paths affected.

**Done criteria:** `git diff --stat converter/CLAUDE.md` shows -170 lines. `pytest` not required.

---

### PR-B — Golden baselines via `test_byte_equivalence.py` extension (1.5 days)

**Scope:** Add a frozen-baseline safety net by extending the existing byte-equivalence test rather than creating a new file.

- Extend `converter/tests/test_byte_equivalence.py` with `class TestFrozenBaseline`.
- New: `converter/tests/golden/{simplefps,gamekit3d,platformer}.rbxlx.sha256` — three frozen baselines, one per shape variation.
- New: `converter/tests/golden/canonicalize.py` — canonicalization helper.
- Use existing `@pytest.mark.slow` and `_has_project()` skipif patterns from `_project_paths.py`. No new pytest marker.
- **Canonicalization** (decision #4):
  - JSON document with sorted KEYS but list/sibling order preserved (parent/child hierarchy is load-bearing in the rbxlx writer — see `roblox/rbxlx_writer.py:810` and `core/roblox_types.py:118`).
  - For each script: `(name, sha256(source))` — NOT line count (misses content edits at constant length).
  - Sets are sorted before hashing: `unhandled_components`, asset GUID sets.
  - Excluded: `generated_at` timestamps, `mtime` fields, absolute temp paths, UUID referents (already normalized via existing `_REFERENT_RE`).
- **Determinism guard:** `TestFrozenBaseline` runs the conversion TWICE on the test host and asserts the canonical hash matches between the two runs before comparing to the frozen baseline. Catches new nondeterminism introduced upstream.

**Why this PR before the splits:** the splits *will* introduce subtle behavior changes (registry-ordering, decorator timing, dispatcher cliff). Without frozen baselines, refactor regressions are silent until a real conversion ships broken output.

**Risk:** Low. Extends existing test infrastructure; doesn't modify production code. Worst case: a nondeterminism source exists upstream and the determinism guard flaps. That's actionable signal, not a blocker.

**Done criteria:**
- `pytest -m slow tests/test_byte_equivalence.py::TestFrozenBaseline` passes on `origin/main` HEAD with all three baselines.
- `bash converter/tools/check_no_any.sh` passes.
- Determinism guard runs each baseline twice and asserts match before the comparison.

---

### PR-C — Extract `Pipeline.write_output` subphases + introduce `PipelineServices` (3 days)

**Scope:** The single most cross-cutting region of `pipeline.py` (`pipeline.py:1986–2385`) becomes its own module package. Concurrently, introduce the `PipelineServices` dataclass (decision #7) that the extracted functions need.

- New: `converter/converter/phases/services.py` — `PipelineServices` dataclass (decision #7).
  - Fields: `output_dir`, `skip_binary_rbxl`, `context_path`, `is_resume`, `fps_artifacts_at_init`
  - Helper Callables: `classify_storage`, `bind_scripts_to_parts`, `rehydrate_scripts_from_disk`, `inject_runtime_modules`, `generate_prefab_packages`, `collect_all_scripts`, `collect_method_warnings`, `apply_scaffolding`
- New: `converter/converter/phases/output/`
  - `__init__.py` — exposes `write_output(state, ctx, services)` + 3-line ADR comment on the (state, ctx, services) convention
  - `emit_scripts.py` — `emit_scripts_to_disk(state, ctx, services)`
  - `cohere_scripts.py` — `cohere_scripts(state, ctx, services)`
  - `inject_autogen.py` — `inject_autogen_scripts(state, ctx, services)` (264 lines, includes back-compat migration)
  - `encode_terrain.py` — `encode_terrain(state, ctx, services)`
  - `inject_mesh_loader.py` — `inject_mesh_loader(state, ctx, services)`
  - `patch_setup_sounds.py` — `patch_setup_sounds(state, ctx, services)`
  - `finalize_scripts.py` — `finalize_scripts_to_disk(state, ctx, services)`

- `Pipeline.write_output` becomes a ~30-line orchestrator that builds `services` from `self` and calls the subphase functions in order.
- Pre-scaffolding migration branch (`pipeline.py:2370-2384`) gets a dedicated regression test in `tests/test_pre_scaffolding_resume.py` — currently uncovered.

**Risk:** Medium. These subphases mutate shared state (`state.rbx_place.scripts`, `ctx.uploaded_assets`). PR-B's frozen baseline catches order-dependent regressions.

**Done criteria:**
- `pipeline.py` shrinks from 3897 → ~3400 lines.
- All 1340 tests pass + new pre-scaffolding regression test passes.
- `bash converter/tools/check_no_any.sh` passes.
- Frozen baselines (all 3) unchanged.

---

### PR-D — Split `Pipeline` via dispatch table (4 days)

**Scope:** Extract remaining phase methods into `converter/converter/phases/` siblings to the output package. Per decision #1, this uses a dispatch table — the per-phase wrapper methods are deleted, not preserved.

**Phase modules (10):**
- `phases/parse.py` — `parse(state, ctx, services)`
- `phases/extract_assets.py` — `extract_assets`, `_extract_serialized_field_refs`, `_compute_fbx_bounding_boxes`
- `phases/moderate_assets.py` — `moderate_assets`
- `phases/upload_assets.py` — `upload_assets`, `_audit_new_uploads`
- `phases/convert_materials.py` — `convert_materials`, `_bake_vertex_colors`
- `phases/transpile.py` — `transpile_scripts`
- `phases/convert_animations.py` — `convert_animations`
- `phases/resolve_assets.py` — `resolve_assets` (272 lines)
- `phases/convert_scene.py` — `convert_scene` + `_delete_pruned_script_from_disk`

**Script-assembly themed modules (5 — decision #3):**
- `phases/script_binding.py` — `bind_scripts_to_parts` + `attach_prefab_scoped_animation_scripts_to_templates` + `attach_monobehaviour_scripts_to_templates`
- `phases/storage_classification.py` — `classify_storage` + `load_storage_plan_for_rehydration`
- `phases/rehydration.py` — `rehydrate_scripts_from_disk` + `remove_rehydrated_fps_autogen`
- `phases/runtime_injection.py` — `inject_runtime_modules`
- `phases/reporting.py` — `build_conversion_report` + `build_script_summary` + `collect_method_warnings` + `write_unconverted_md`

**Dispatch (decision #1):**
- New: `phases/__init__.py` defines `PHASE_FUNCS: dict[str, Callable[[PipelineState, ConversionContext, PipelineServices], None]]`.
- `pipeline.py:40`'s `PHASES` list becomes `PHASES = list(PHASE_FUNCS.keys())` — single source of truth (decision: CQ-1 fold-in).
- `Pipeline._run_phase(phase)` becomes `PHASE_FUNCS[phase](self.state, self.ctx, self.services)`.
- New public `Pipeline.run_phase(name: str)` for tests (decision #8) — same dispatch, public-facing name.

**Pipeline public API after PR-D (frozen contract):**
- `__init__`, `apply_scaffolding`, `scaffolding`, `_find_unity_root`, `context` (property)
- `run_all`, `run_all_scenes`, `run_through`, `resume`, `run_phase(name)`, `_run_phase`
- All per-phase methods DELETED (`pipeline.parse()` etc. — no longer callable). Tests use `pipeline.run_phase('parse')` instead.

**Test call-site rewrite (decision #8):** PR-D includes the mechanical rewrite of 16+ call sites:
- `tests/test_resolve_assets_id_contract.py` — 6 sites: `pipeline.resolve_assets()` → `pipeline.run_phase('resolve_assets')`
- `tests/test_sprite_extractor_wiring.py` — 4 sites: `pipeline.extract_assets()` → `pipeline.run_phase('extract_assets')`
- `tests/test_scriptable_object_wiring.py` — 3 sites: `pipeline.write_output()` → `pipeline.run_phase('write_output')`
- `tests/test_pipeline_write_output_subphases.py` — deeper rewrite (currently asserts on `self` access shape; rewrite to assert on `services` shape).

**Risk:** Medium-high. 49 methods → 15 modules is a big move. Mitigations:
- One commit per phase module (15 commits in the PR, reviewable individually).
- Each commit runs `pytest -m "not slow"` + frozen baselines (decision: CQ-3 fold-in).
- `git log --follow` preserves history because we extract+import rather than copy.
- New tests: `tests/test_pipeline_dispatch.py` covers (a) `set(PHASE_FUNCS.keys()) == set(PHASES)`, (b) `pipeline.run_phase('nonexistent')` raises `KeyError`, (c) every `PHASE_FUNCS[name]` is a callable signing `(state, ctx, services) -> None`.

**Done criteria:**
- `pipeline.py` ≤ 800 lines.
- All tests + frozen baselines pass.
- `bash converter/tools/check_no_any.sh` passes.
- `python -c "from converter.pipeline import Pipeline; from converter.phases import PHASE_FUNCS"` succeeds (no circular imports).
- `class Pipeline` method count drops from 49 to ≤ 15.
- Test call-site rewrite complete: zero remaining `pipeline.<phase_name>()` in `converter/tests/`.

---

### PR-E0 — Pack ordering audit + `after=` edges (1 day, prelude to PR-E)

**Scope:** Before splitting, make pack execution order explicit so the split can't reorder behavior (decision #9).

- Run the current pack registry on `origin/main` and dump execution order: `python -c "from converter.script_coherence_packs import PatchPack; [print(p.name) for p in PatchPack._registry]"`. This is the frozen reference.
- For each pack, audit: does it detect against shape produced by an earlier pack? Specifically check the post-rewrite-shape detector at `test_script_coherence_packs.py:894` and the producer/consumer pairings in `TestProducerConsumerBindableEventGuard`.
- For every consumer found, add an explicit `after=('producer_name',)` edge to its `@patch_pack` decorator. After PR-E0 lands, the topological order is enforced from declarations, not file order.
- New test: `TestPackOrderFrozenOnMain` asserts the post-`_topological_order` sequence matches a checked-in fixture (`tests/fixtures/pack_execution_order.txt`). This survives PR-E as a regression check.

**Risk:** Low. Adding `after=` edges is additive — packs without consumers see no behavior change. Tests catch any misaudit.

**Done criteria:**
- Every pack that consumes another pack's output has an explicit `after=` edge.
- `TestPackOrderFrozenOnMain` passes with the checked-in fixture matching `origin/main` order.
- `bash converter/tools/check_no_any.sh` passes.

---

### PR-E — Split `script_coherence_packs.py` by theme (2 days)

**Scope:** Decompose the 4667-line file into a registry + 5 themed pack modules. Requires PR-E0 to have landed.

- New: `converter/converter/coherence/`
  - `__init__.py` — explicit submodule imports (decision #2): `from .packs import fps, doors, pickups, proximity, misc`. Side-effect-triggers `@patch_pack` registration. Re-exports `run_packs`, `PatchPack`, `patch_pack` from `registry`.
  - `registry.py` — `PatchPack`, `patch_pack`, `_topological_order`, `run_packs` (~200 lines)
  - `helpers.py` — shared utilities: `_blank_lua_strings_and_comments`, `_touch_callback_ranges`, `_resolve_touch_callback_param_at`, `_ensure_players_service_binding`, `_LUA_BLOCK_OPEN_RE` and other cross-pack constants (~250 lines)
  - `packs/fps.py` — weapon mount (`_detect_fps_weapon_mount`, `_inject_fps_weapon_mounts`, `WEAPON_MOUNTS`), default controls, camera pitch inversion, bullet physics raycast (~1000 lines). Plus `_PICKUP_REPLACEMENT`, `_PICKUP_TOUCHED_*` constants used by FPS pickup flow.
  - `packs/doors.py` — global player lookup, AI rotation strip, tween open, module player attr (~600 lines). Plus `_DOOR_GLOBAL_PLAYER_*_RE` constants.
  - `packs/pickups.py` — remote event conversion, visual target fix, listener fanout (~900 lines). Plus `_PICKUP_SETATTRIBUTE_RE`, `_PICKUP_HAS_ATTR_INJECTED_RE`, `_PICKUP_REMOTE_ALIAS_RE`, `_GETITEM_SYMBOL_RE`.
  - `packs/proximity.py` — trigger stay polling, proximity fanout (~400 lines)
  - `packs/misc.py` — template clone visibility, LocalScript API shim, BindableEvent producer/consumer guard, self-destroying template guard (~700 lines). Plus `_SELF_DESTROY_RE`, `_TEMPLATE_GUARD_*` constants.
- Constant placement (CQ-2 fold-in): pack-specific module-level constants migrate with their pack; truly shared ones (e.g., `_LUA_BLOCK_OPEN_RE`) move to `coherence/helpers.py`.

**Back-compat shim (decision #2):** Existing `converter/converter/script_coherence_packs.py` becomes ~15 lines:
```python
# Back-compat shim — explicit submodule imports trigger @patch_pack registration.
from converter.coherence.registry import PatchPack, patch_pack, run_packs
from converter.coherence.packs import fps, doors, pickups, proximity, misc  # noqa: F401
__all__ = ['PatchPack', 'patch_pack', 'run_packs']
```
No `import *`. Underscore-prefixed pack functions are NOT re-exported through the shim — tests update to import directly from `coherence.packs.<theme>`.

**Risk:** Medium. PR-E0 mitigates the registration-order risk. The remaining risk is missing a pack module from `coherence/__init__.py`, silently disabling its packs.

**Done criteria:**
- No file in `coherence/` exceeds 1100 lines.
- Registry NAMES assertion (not just count) — `tests/test_script_coherence_packs.py` adds: `assert set(p.name for p in PatchPack._registry) == EXPECTED_PACK_NAMES` (24 specific names).
- `TestPackOrderFrozenOnMain` from PR-E0 still passes (post-split order matches frozen reference).
- All tests pass.
- `bash converter/tools/check_no_any.sh` passes.
- `converter/TODO.md` P1.a / P1.b / P1.c entries rewritten to point at new locations: `coherence/packs/misc.py:_classify_api` (P1.a), `coherence/packs/misc.py:_build_shim_source` (P1.b), `coherence/packs/misc.py:_inject_template_clone_visibility` (P1.c).

---

### PR-F — Split `test_script_coherence_packs.py` to mirror (1 day)

**Scope:** Mechanical test reorganization following PR-E.

- New: `converter/tests/coherence/`
  - `test_registry.py` — `TestRegistry`, `TestTopologicalOrder`, `TestRunPacksGating`
  - `test_packs_fps.py` — FPS-related test classes (5 classes, ~700 lines)
  - `test_packs_doors.py` — door-related (4 classes, ~700 lines)
  - `test_packs_pickups.py` — pickup-related (4 classes, ~1100 lines)
  - `test_packs_proximity.py` — 2 classes
  - `test_packs_misc.py` — remaining

- Delete `test_script_coherence_packs.py`.

**Risk:** Very low. Pure test-file moves, no logic change.

**Done criteria:** `pytest tests/coherence/ -v` collects the same 200+ tests as before; all pass.

---

## Phase 2 PRs — gated on scene-runtime-contract landing

### PR-G — Eliminate `_ctx()` in `scene_converter.py` (1.5 days)

**Scope:** Land the deferred refactor flagged at `scene_converter.py:161-165`.

- Remove module-global `_current_ctx` and `_ctx()` function.
- Every helper that currently calls `_ctx()` takes `ctx: SceneConversionContext` as an explicit parameter.
- 50 call sites updated mechanically.
- `convert_scene()` instantiates `SceneConversionContext` and threads it explicitly through `_convert_node`, `_process_components`, etc.

**Risk:** Medium. Mechanical but high call-site count. PR-B frozen baselines are the safety net.

**Done criteria:**
- `grep -c "_ctx()" converter/converter/scene_converter.py` returns 0.
- `grep -c "_current_ctx" converter/converter/scene_converter.py` returns 0.
- **No-global-state regression test:** `tests/test_scene_converter.py::test_no_module_global_ctx` deletes `scene_converter._current_ctx` attribute (if it still exists) and runs a small `convert_scene()` invocation — must not raise `AttributeError` or `RuntimeError`.
- All 3 frozen baselines unchanged.
- All tests pass.
- `bash converter/tools/check_no_any.sh` passes.

---

### PR-H — Split `scene_converter.py` (3-4 days)

**Scope:** Decompose the 4856-line file (now no longer using `_ctx()`) into thematic modules.

**Import-graph constraint (Codex finding 4):** Today `_process_components()` does NOT call back into prefab code, but prefab helpers call `_process_components()` at `scene_converter.py:4103, 4191, 4533`. Direction is **prefab → components**, never the reverse. PR-H must preserve this — `scene/components.py` MUST NOT import from `scene/prefab.py`. If a future change wants to import that direction, treat it as a design bug.

- New: `converter/converter/scene/`
  - `__init__.py` — re-exports `convert_scene` for back-compat
  - `_context.py` — `SceneConversionContext` dataclass
  - `convert_scene.py` — top-level `convert_scene()` orchestration + `_convert_node` (~600 lines)
  - `components.py` — `_process_components` (591 lines). **Does not import `prefab.py`.**
  - `prefab.py` — `_convert_prefab_instance`, `_convert_prefab_node`, `_convert_fbx_prefab_instance`, `_wrap_geometry_with_children_into_model`. Imports `components.py` (one-way).
  - `mesh_sizing.py` — `_compute_mesh_size`, `_compute_mesh_size_from_fbx_bbox`, `_compute_mesh_size_from_embedded_aabb`, `_compute_mesh_vertical_offset`, `_read_fbx_unit_scale_factors`, `_get_fbx_import_scale`, `_get_fbx_unit_ratio`, `_read_embedded_mesh_aabb` (~700 lines)
  - `mesh_resolution.py` — `_resolve_sub_mesh`, `_resolve_mesh_id`, `_resolve_mesh_texture_id`, `_get_multi_sub_meshes`, `_extract_prefab_material_map`
  - `materials.py` — `_apply_materials`, `_blend_extra_material_colors`, `_apply_prefab_materials`
  - `lighting.py` — `_extract_lighting`, `_apply_directional_light`, `_extract_skybox`
  - `water.py` — `_is_water_node`, `_extract_water_region`, `_extract_water_region_from_prefab`
  - `monobehaviour.py` — `_extract_monobehaviour_attributes`
  - `transforms.py` — `_compose_parts_with_parent_cframe`

- Old `converter/converter/scene_converter.py` becomes a back-compat shim.

**Risk:** High. This is the most invasive PR. Mitigations:
- Land *after* PR-G so the explicit-ctx threading is already in place.
- One commit per extracted module (~12 commits in the PR).
- Each commit runs the full test suite + frozen baselines + a real SimpleFPS end-to-end conversion via `u2r.py convert`.
- New smoke test: `tests/test_scene_split_imports.py::test_no_circular_imports` runs `python -c "from converter.converter.scene_converter import convert_scene"` in a fresh interpreter; asserts no `ImportError`.
- Codex `exec` review on the full diff before merge.

**Done criteria:**
- No file in `scene/` exceeds 1500 lines.
- Top-level `convert_scene` is still importable from `converter.converter.scene_converter` (shim).
- Smoke test asserts no circular imports.
- All 3 frozen baselines unchanged. Full test suite (1340) passes.
- End-to-end SimpleFPS + Gamekit3D + 3D-Platformer conversion produces identical `.rbxlx` (canonical hash match).
- `bash converter/tools/check_no_any.sh` passes.

---

## Phase 3 — out of scope for this plan

These are mentioned for completeness but are separate efforts:

- **Skills hygiene** (global `~/.claude/skills` deduplication + progressive disclosure on monolithic SKILL.md files). Not in this repo, separate plan.
- **`animation_converter.py` and `component_converter.py` splits** (2082 and 1986 lines). Apply same pattern, lower urgency — they're not as cross-cutting as `pipeline.py` and `scene_converter.py`. Defer until Phase 2 lands.
- **`test_animation_converter.py` split** (3517 lines, 15 test classes). Same.

---

## Cumulative expected impact

After Phase 1 (PRs A → F + E0):
- `pipeline.py`: 3897 → ~600 lines (orchestrator only)
- `Pipeline` class: 49 methods → ≤ 15 (only `run_*`, `run_phase`, init, context)
- `script_coherence_packs.py`: 4667 → 15-line shim + 5 pack modules ≤1100 lines each
- `test_script_coherence_packs.py`: 3726 → 5 files ≤1100 lines each
- `converter/CLAUDE.md`: 322 → ~150 lines (auto-loaded savings)
- Pack execution order made explicit via `after=` edges
- Three of the five files-over-3000-lines eliminated.

After Phase 2 (PRs G → H):
- `scene_converter.py`: 4856 → ~10 modules ≤1500 lines each
- Module-global hidden state in scene conversion eliminated (50 `_ctx()` call sites threaded explicitly)
- All five files-over-3000-lines eliminated.

Concentration metric: files ≥1000 lines drop from 18 to ~10. Total LOC essentially unchanged (~77K) — we are reshaping, not deleting.

## PR sequence summary (post-eng-review)

| Order | PR | Phase | Days | Depends on |
|------:|----|------:|----:|----|
| 1 | PR-A — Trim `converter/CLAUDE.md` | 1 | 0.5 | — |
| 2 | PR-B — Frozen baselines via `test_byte_equivalence` | 1 | 1.5 | — |
| 3 | PR-C — `write_output` subphases + `PipelineServices` | 1 | 3 | PR-B |
| 4 | PR-D — `Pipeline` dispatch table + 15 phase modules + test rewrite | 1 | 4 | PR-B, PR-C |
| 5 | PR-E0 — Pack ordering audit + `after=` edges | 1 | 1 | PR-B |
| 6 | PR-E — Split `script_coherence_packs.py` | 1 | 2 | PR-E0 |
| 7 | PR-F — Mirror split of `test_script_coherence_packs.py` | 1 | 1 | PR-E |
| 8 | PR-G — Eliminate `_ctx()` | 2 | 1.5 | PR-B, scene-runtime landed |
| 9 | PR-H — Split `scene_converter.py` | 2 | 4 | PR-G |

**Total: 8 PRs (was 7 before eng-review added PR-E0), ~3.5 engineer-weeks.**

---

## How a single PR executes (template)

For Phase 1 PRs C/D/E/E0 specifically, since they're decorator-sensitive or import-graph-sensitive:

1. Create branch off `origin/main`. New worktree under `/Users/jiazou/workspace/unity2rbxlx-refactor-{letter}/`.
2. Establish baseline: run full `pytest` and frozen-baseline test on the unmodified worktree; capture canonical hash + pass count.
3. **Move first, edit second.** Each commit either (a) creates a new module by copying functions verbatim with `git mv`-aware techniques, or (b) changes call sites. Never mix.
4. After each commit:
   - `pytest -m "not slow"` (≤2 min)
   - `pytest -m slow tests/test_byte_equivalence.py::TestFrozenBaseline` (≤5 min — only if test_projects available)
   - `bash converter/tools/check_no_any.sh` (CI gate per `[[no_any_ci_gate]]`)
5. **Per-commit verification:** before pushing, run `git rebase --exec "pytest -m 'not slow' && bash converter/tools/check_no_any.sh" origin/main` to confirm every intermediate commit is green, not just the tip. Catches "PR is green only because the last commit fixed the previous commit's regression."
6. Codex review on full diff before requesting human review. Use `codex exec` for architecture flavor per `[[codex_cli_quirks]]`.
7. Force-push amendments allowed up to first human review; after that, additive commits only.

---

## Next steps (in order)

1. **`/plan-eng-review` on this document.** Lock module boundaries, data-flow contracts between extracted phase modules, the golden-test snapshot strategy (PR-B), the `_ctx()` threading contract (PR-G), and the per-PR test matrix. Treat the resulting reviewed doc as the spec — no mid-implementation redesign, per `[[scene_runtime_multi_pr]]` working norms.
2. **Hold.** Plan sits dormant until scene-runtime-contract PRs #122/#123/#124 and the stacked PR3b→PR8 chain all merge into ntornow upstream.
3. **Re-confirm before execution.** When scene-runtime lands, re-read this plan, verify the file line counts haven't drifted, and re-baseline the golden test on the new HEAD. Then start with PR-A.

## How to track

This document is the source of truth for the refactor effort. After `/plan-eng-review` (this review):
- Save an auto-memory entry pointing at this file (`refactor_plan.md` lives in the `arch-critique` branch / worktree per `[[worktree_path_trap]]`).
- Add a TODO entry under `converter/TODO.md` blocking on scene-runtime merge.
- No PRs are opened until both gates clear.

---

## NOT in scope (deferred deliberately)

- **`animation_converter.py` and `component_converter.py` splits** (2082 and 1986 lines). Same shape as the in-scope work but lower urgency — neither is as cross-cutting as `pipeline.py` or `scene_converter.py`. Revisit after Phase 2 lands.
- **`test_animation_converter.py` split** (3517 lines, 15 test classes). Couples to the animation_converter decision.
- **Skills hygiene** (global `~/.claude/skills` deduplication and progressive disclosure on monolithic SKILL.md files). Not in this repo — separate plan.
- **Eliminating module-global state outside scene_converter.** The `_ctx()` pattern is unique to scene_converter; other modules don't have equivalent debt.
- **Pre-emptive optimization of conversion performance.** No performance changes in scope. Any speedup is incidental.
- **Renaming legacy public API.** `Pipeline.run_all/run_through/resume` keep their current names.
- **Distribution / packaging changes.** No new artifacts.

## What already exists (re-use, don't rebuild)

- **`converter/tests/test_byte_equivalence.py`** (249 lines) — already hashes normalized rbxlx across three pipeline flows with UUID-referent normalization. PR-B extends this; does not duplicate.
- **`converter/tests/test_pipeline_e2e.py`** (351 lines) — already runs end-to-end against 7+ test projects. Frozen baselines plug into this infrastructure.
- **`converter/tests/_project_paths.py`** — `_has_project(...)` skipif pattern. New baselines reuse it.
- **`converter/converter/scaffolding/`** — existing precedent for `phases/` directory layout.
- **`PHASES: list[str]` at `pipeline.py:40`** — single source of truth for phase ordering. PR-D evolves this to derive from `PHASE_FUNCS`.
- **`@patch_pack` decorator and `_topological_order`** at `script_coherence_packs.py:54, 84` — registry/dependency machinery. PR-E0/E preserve this; only the file boundaries change.
- **`bash converter/tools/check_no_any.sh`** — existing CI gate. Per-PR done criteria reference it; no new tool needed.
- **Prior refactor PR pattern (`PR1 of 4` series)** — repo has a track record of incremental refactor landings.

## Parallelization strategy

The dependency table above shows a mostly-sequential plan. Real parallelism opportunities:

| Lane | PRs | Modules touched | Notes |
|------|-----|----------------|-------|
| A    | PR-A | `converter/CLAUDE.md` | Standalone, can land anytime |
| B    | PR-B | `tests/test_byte_equivalence.py` + `tests/golden/` | Standalone, blocks C/D/E0 |
| C    | PR-C → PR-D | `converter/pipeline.py` + `converter/phases/` + 16 test files | Single lane — both touch `Pipeline` |
| D    | PR-E0 → PR-E → PR-F | `converter/script_coherence_packs.py` + `coherence/` + `tests/coherence/` | Single lane — strict order |

**Possible parallel split after PR-B merges:** Lane C and Lane D can run in parallel worktrees (`unity2rbxlx-refactor-pipeline/` and `unity2rbxlx-refactor-coherence/`) because they touch disjoint modules. Saves ~5 days wall-clock if two AI sessions run concurrently. PR-A is independent of both.

**Phase 2 (PRs G/H) is strictly sequential** — PR-H depends on PR-G's ctx threading. No parallelization opportunity within Phase 2.

## Unresolved decisions

None. All 9 eng-review decision points resolved in this session. Codex provided outside-voice consensus on 4 of them.

## Failure modes audit

| PR | Realistic production failure | Test catches | Severity if silent |
|---|---|---|---|
| PR-C | Order-dependent state mutation in subphase | Frozen baseline | Critical (silent without baseline) |
| PR-D | `phases/X.py` import error | Test collection itself fails | None — loud |
| PR-D | Phase function forgets a `services` field | Type-checker / runtime AttributeError | None — loud |
| PR-D | Test still calls deleted `pipeline.parse()` | Test collection failure | None — loud |
| PR-E0 | Missing `after=` edge — pack runs before its producer | `TestPackOrderFrozenOnMain` | Caught by audit |
| PR-E | Pack module not imported in `coherence/__init__.py` | NAMES assertion (24 specific names) | Critical without names check |
| PR-G | Helper missed in grep, still references `_ctx()` | No-global-state regression test | Loud (RuntimeError) |
| PR-H | Circular import between `scene/` submodules | Smoke test (fresh interpreter) | Loud (ImportError) |

**No critical silent gaps remain after the 6 new tests land.**

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | issues_found (codex) | 5 findings, 3 incorporated as decisions, 2 folded |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (PLAN) | 9 issues, 0 critical gaps, 9 decisions locked |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**CODEX:** 5 findings (services dataclass needed, dispatcher-cliff test breakage, pack ordering risk, golden snapshot blind spots, plan internal contradictions). 3 promoted to locked decisions (#7, #8, #9); 2 folded into plan edits.

**CROSS-MODEL:** Claude eng-review and Codex outside-voice converged on the dispatch-table approach (decision #1) and `_ctx()` elimination (PR-G priority). Codex caught the `(state, ctx)` signature being too narrow — Claude missed it.

**UNRESOLVED:** 0.

**VERDICT:** ENG CLEARED — refactor plan is ready to execute once scene-runtime-contract 9-PR effort lands upstream.
