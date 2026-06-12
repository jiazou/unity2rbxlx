# Converter TODO

Active work items only. Completed work + PR execution logs live in `TODO_archive.md`.

Priority: **P0** = blocks gameplay, **P1** = significant quality, **P2** = nice to have.

Citation rule: reference code by grep-target (`file.py` + symbol/string), never by line
number — line refs rot (the 2026-06-11 review found every CLAUDE.md/TODO.md line ref stale).

---

## Architecture & platform currency (2026-06-11 review)

From a full repo audit + external fact-check of platform claims against Roblox Creator Docs
(2024-2026 changes). Verified CORRECT and needing no action: coordinate/quaternion math, stud
scale (0.28 m), 2048-stud part cap, FBX-7500 diagnosis, task-library usage, two-tier publish
cache. The items below are where code or docs are stale or wrong.

- [ ] **P1 — Architecture docs describe a retired pipeline.** ARCHITECTURE.md/CLAUDE.md list 10
  phases; `pipeline.py:PHASES` has 12 (`plan_scene_runtime`, `materialize_and_classify` and the
  whole scene-runtime-topology / contract-verifier / `contract_pipeline.py` subsystem are
  undocumented). The "Asset Resolution (Critical)" section is obsolete twice: textures upload as
  `Image` assetType (`cloud_api.upload_image` — no Decal→Image resolution needed) and mesh
  resolution is headless via the official Open Cloud Luau Execution API (`execute_luau` =
  `luau-execution-session-tasks`) — not "Studio-required". Also fix: runtime-module list
  (animator retired; `scene_runtime.luau`/`scene_camera_input.luau` undocumented), AI backend
  (Claude CLI preferred + Anthropic API fallback), `--api-key` not `--api-key-file` (incl. a
  stale error msg in `u2r.py`), 6 undocumented u2r subcommands, stale test counts, and convert
  all hardcoded `file:line` citations to grep-targets.

- [ ] **P1 — Mesh face cap is stale (10k) and the quality floor ignores it.** Roblox's limit has
  been **20,000** triangles since ~2021 (creator-docs mesh specifications); `config.py
  MESH_ROBLOX_MAX_FACES = 10_000` over-decimates large meshes ~2×, AND `mesh_processor.py`
  applies `MESH_QUALITY_FLOOR` with no cap clamp (50k faces × floor 0.6 → 30k > cap → upload
  fails). Fix both: constant → 20_000, clamp after the floor. Open Cloud may also silently
  decimate server-side (DevForum 2026-04) — enforce locally, don't rely on the server.

- [ ] **P1 — Workspace gravity comment is false; physics diverges 5.6×.** `rbxlx_writer.py`
  writes `Gravity = 196.2` commented "(9.81 m/s²)" — at the converter's own 0.28 m/stud scale
  that is 54.9 m/s² (196.2 = 20×9.81 is the pre-2019 5 cm/stud convention). Unity-tuned
  ballistics/falls/jumps land ~5.6× heavy. DECIDE: (a) fidelity `9.81 × STUDS_PER_METER ≈ 35.0`,
  (b) keep 196.2 for Roblox-native avatar feel, or (c) config knob. Fix the comment regardless;
  playtest (a) before committing — it changes feel globally.

- [ ] **P1 — Contract verifier ships shadow-mode against a fail-closed contract.**
  `scene-runtime-contract.md` promises fail-closed; `contract_verifier.py` "never fails the
  build" (only `FAIL_CLOSED_CHECKS` members can trip), and the status lives only in
  `.harness/followups.md`. Flip to enforced (PR6/PR8 territory per the recut plan) — now also
  gate (b) of legacy retirement below; until it lands, note shadow status in KNOWN_ISSUES.

- [ ] **P1 — Output boundary doesn't sanitize foreign strings.** (a) `rbxlx_writer.py`
  `_add_string`/`_add_float` pass control chars (U+0000–001F) and inf/nan into XML — one NaN
  transform poisons the whole `.rbxlx`; (b) `luau_place_builder._luau_str` +
  `place_publisher` collision-fixup splice untrusted names/JSON into long-bracket Luau
  literals — `]]`-shaped content in an asset name breaks (or injects into) the builder script
  executed via `execute_luau`. Escape-encode at both boundaries.

- [ ] **P1 — Transpile gate: silent pass when luau-analyze is missing; cheap semantic upgrade
  available.** `utils/luau_analyze.py` returns `[]` when the binary is absent — "syntax-gated"
  is silently false (likely incl. CI ubuntu jobs). Fail loud / stamp the report. Then: the
  KNOWN_ISSUES "validator catches syntax, not Roblox API semantics" gap is not a research
  project — `luau-lsp analyze --definitions:@roblox=globalTypes.d.luau`
  (JohnnyMorganz/luau-lsp) ships maintained Roblox API types; wire into the existing
  lint+reprompt loop.

- [ ] **P2 — AI truncation guard.** `code_transpiler` never checks
  `response.stop_reason == "max_tokens"`; truncated-but-syntactically-valid Luau ships
  incomplete logic. (Promoted from KNOWN_ISSUES.)

- [ ] **P2 — `conversion_context.json` save is not atomic.** `ConversionContext.save` is a
  single `write_text`; a crash/Ctrl+C mid-write corrupts the only cross-CLI state file (no
  KeyboardInterrupt handling either — KNOWN_ISSUES). Write-tmp-then-rename.

- [ ] **P2 — CI gate gaps.** (a) `nightly-summary` echoes `cold-e2e` result but never gates on
  it — a nightly cold-e2e failure reports green (`test.yml`). (b) smoke/cold-e2e convert a
  hardcoded local clone (`UNITY_SIMPLEFPS_DIR`), not the pinned submodule — the gate can drift
  from repo state; one self-hosted Mac = bus factor 1. (c) Consider: required gate on the
  cached AI shape + advisory fresh-AI run, so pre-merge red isn't a function of model variance.

- [ ] **P2 — Attribute sanitizer: raise cap, guard RBX prefix.** Real SetAttribute rules:
  alphanumeric + `. - / _`, max **100** chars, reserved `RBX` name prefix. Our `[A-Za-z0-9_]`
  charset is safe-but-lossy, the 64 cap can go to 100, and nothing guards `RBX*` (throws at
  runtime). `core/flag_names.py` + serialized-field attribute emission.

- [ ] **P2 — ".rbxlx → HTTP 400" is folklore; fix the comments.** Official place-publishing
  docs accept both `application/xml` (.rbxlx) and `application/octet-stream` (.rbxl), and
  `cloud_api.publish_place_file` already sets Content-Type by extension. Verify one real
  `.rbxlx` publish, then rewrite the three "Open Cloud rejects XML with 400" comments
  (`convert_interactive.py` upload, `u2r.py`) — the on-error fallback that deletes the `.rbxl`
  and publishes the `.rbxlx` is probably fine; the comments steer changes wrong.
  (Replaces the former xml_to_binary P2.c item.)

- [ ] **P2 — Document Open Cloud quotas.** 100 audio uploads/month (10 if not ID-verified),
  video 20/day, 20 MB/call, 120 req/min. A sound-heavy game can exhaust the monthly audio
  quota in one conversion. Add to UNSUPPORTED.md + surface in the upload report.

- [ ] **P2 — Unit/easing sweep for component conversion.** ParticleSystem `startSpeed` is
  emitted raw m/s (force-over-lifetime and light range ARE scaled) → particles ~3.6× slow;
  audit all unit-bearing props in `component_converter.py` once. Animation easing is
  hardcoded Quad/InOut at all four TweenInfo emit sites in `animation_converter.py` — Unity
  AnimationCurve easing is parsed but dropped.

## Legacy retirement (strategic direction, 2026-06-11)

- [ ] **P1 — Retire legacy mode + the coherence-pack layer; generic is the only path forward.**
  Decision 2026-06-11: stop hardening legacy. Pack bug-fix/fixture work (door/weapon packs,
  `localscript_api_shim`, `template_clone_visibility`, genre-negative pack fixtures — the
  PR #96/#121 follow-ups formerly tracked here) is dropped; semantic-fidelity jobs relocate to
  the deterministic lowering layer / host-runtime services instead (the Turret/HudControl
  pattern — KNOWN_ISSUES § "Scene-runtime generic mode"). Gates before deleting legacy:
  - (a) generic parity on the e2e fixture set — player-bind Phase 2 (`self.host.player`
    authority, then `REQUIRE_PLAYER_BIND` 0→1 in `test.yml`);
  - (b) contract verifier flipped fail-closed (Architecture section above);
  - (c) deterministic-lowering homes for the known pack-covered gaps (Turret child-index,
    HudControl binding) verified on a generic conversion.
  Then delete: `script_coherence_packs.py` + its test file, `--scaffolding=fps` +
  `converter/scaffolding/` + `_fps_artifacts_*` back-compat in `pipeline.py`, the
  `detect_fps_game` autogen heuristic, and the legacy `scene_runtime_mode` branch. NOTE: this
  moots the refactor plan's pack-split lane (PR-E0 → PR-E → PR-F in `docs/refactor_plan.md`)
  — update that plan rather than splitting a file slated for deletion.

## Materials & meshes

- [ ] **P1 — `read_fbx` rejects FBX ≥ 7500 (64-bit node headers).** FBX 2016+ — the Autodesk-SDK
  default and the dominant modern asset version — raises `NotImplementedError`;
  `mirror_fbx_handedness` then returns False and the pipeline uploads the raw file: no
  handedness mirror, no bbox, no sub-mesh resolution (trash-dash 2026-05-18: rigged 7500 files
  also rejected by Open Cloud). Fix: 64-bit EndOffset/NumProperties/PropertyListLen + 25-byte
  NULL sentinel, keyed on `version >= 7500` (assimp PR #1354 is the reference); keep the
  32-bit path — Blender still exports 7400. Alternative worth a spike: Open Cloud now accepts
  `.gltf/.glb` for Model uploads (Oct 2025), which may bypass FBX patching entirely.

- [ ] **P2 — Pre-filter undeliverable FBX before upload.** Zero-vertex animation-only FBX and
  skinned (Deformer/Skin/Cluster) character FBX both upload and get rejected (24 files in
  trash-dash). Detect pre-upload, skip, surface to UNCONVERTED.md. Note: EditableMesh gained
  programmatic skinning APIs (Studio beta, 2025-06) — a possible future skinned-mesh path; the
  UNSUPPORTED.md stance stands for now.

- [ ] **P1 — Embedded-mesh resolver warns on bad sub-mesh count, then ships arbitrary
  geometry.** The "exactly one sub-mesh" invariant for synthesized embedded FBX is enforced by
  `log.warning` while `_resolve_sub_mesh` still returns `sub_meshes[0]`. Quarantine the key
  (drop from `mesh_hierarchies`/`mesh_native_sizes`, append to `asset_upload_errors`) so the
  face-decal fallback takes over. (Codex sketched the ~12-line diff in the PR #121 review.)

- [ ] **P2 — Centralize the multi-sub-mesh scale chain.** ~9 sites in `scene_converter.py`
  inline `_get_fbx_import_scale × _get_fbx_unit_ratio × STUDS_PER_METER` (grep the chain) while
  single-mesh sizing goes through `_native_meters_from_roblox_size`. No live numeric bug, but
  this is exactly where scale drift re-entered before. Extract one helper; route all sites.

- [ ] **P2 — Embedded-mesh FBX template: silent degrade + non-determinism.** No-7.x-template →
  `log.warning` and face-decal fallback with a clean-looking report (nothing in
  `asset_upload_errors`); template selection takes the first manifest FBX with a Geometry node
  (filesystem-order-dependent, and its `UpAxis` leaks into every synthesized mesh). Sort
  candidates, filter/normalize to Y-up, and record degraded keys as upload errors.

- [ ] **P2 — Full SurfaceAppearance round-trip through templates** on a real-upload run (PR 5
  deferred; smoke ran `--no-upload`).

## Infrastructure

- [ ] **P1 — ScreenGui enable/disable not wired into the state machine.** All converted
  ScreenGuis ship `Enabled=true` and render stacked (trash-dash: opaque white wall).
  `RbxScreenGui` has no `enabled` field and neither writer serializes it — plumb the field
  through the type + both writers, set non-initial canvases disabled at build time, and map
  `Canvas`/`SetActive` state wiring to `ScreenGui.Enabled` toggles.

- [ ] **P1 — Phase 4a.5 agent-override ingestion is unimplemented.** The skill's storage-plan
  review loop is one-way: `classify_storage()` takes no prior plan, `overrides_applied` is
  never populated, and `_classify_storage` rewrites `conversion_plan.json` on every
  `write_output` — agent edits are silently discarded (trash-dash: 8 overrides → 0 after
  assemble). Fix: persist explicit manual overrides separately (name → container map), keep
  fresh classification, overlay only the explicit overrides. Do NOT make the whole prior plan
  sticky.

- [ ] **P1 — Storage classifier's ModuleScript path is fragile and under-tested.**
  Script/LocalScript route by simple type rules; ModuleScripts route by a regex-scanned
  caller-graph heuristic that ignores the module's own client/server API surface and is
  poisoned by the synthesized ServerStorage require-fallback string. SimpleFPS (76% Script)
  routes around it; module-heavy trash-dash (88% ModuleScript) fell over. Fix direction
  (generic-first): route modules from topology `module_domain` evidence + the module's own
  API surface, not the legacy caller-graph regex; evaluate `Script.RunContext=Client` (runs
  from anywhere replicated, incl. ReplicatedStorage — purpose-built for world-attached client
  behavior, the path's hardest case; never in Starter containers — double-execution); add a
  module-heavy fixture project.

- [ ] **P2 — Persistent prefab/asset cache.** In-memory only today; needs a cache-schema design
  pass first — see FUTURE_IMPROVEMENTS § "Persistent prefab/asset cache".

- [ ] **P2 — Three-flow byte-equivalence:** `test_three_flows_produce_identical_rbxlx` is
  xfailed — the in-memory u2r.py path inlines scripts via `_convert_prefab_node` while the
  interactive path goes through `rehydration_plan.py`. Harmonize; flip to xpass.

- [ ] **P2 — Standalone `.rbxm` per prefab** (toolbox convenience — design notes in
  FUTURE_IMPROVEMENTS).

- [ ] **P2 — Visual-compare baseline screenshot:** CI step is wired but gated on
  `eval_baseline_screenshots/SimpleFPS_main.png` existing; commit a known-good baseline to
  activate the SSIM 0.85 gate.

- [ ] **P2 — Real-upload smoke secrets:** `real-upload-smoke` needs `ROBLOX_API_KEY` /
  `ROBLOX_UNIVERSE_ID` / `ROBLOX_PLACE_ID` / `ROBLOX_CREATOR_ID`; `ai-convert-matrix` needs
  `ANTHROPIC_API_KEY`. Wire when CI billing allows.

---

Type-strictness debt: forward-only no-Any gate landed; no tracked remaining cleanup items.

For platform limitations see [`docs/UNSUPPORTED.md`](docs/UNSUPPORTED.md); architectural debt
and bug-shaped gaps, [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md); long-horizon work,
[`docs/FUTURE_IMPROVEMENTS.md`](docs/FUTURE_IMPROVEMENTS.md).
