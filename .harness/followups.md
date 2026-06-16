
## Run main-20260604-201520 (2026-06-04)

# Follow-ups — drive run main-20260604-201520

- **[recut docs stale] Update `scene-runtime-pr5-8-recut-plan.md` + `scene-runtime-and-refactor-execution.md`:**
  Slice H's premise ("HudControl `domain="excluded"` → dead HUD; fix the `module_domain.py` rule;
  output LocalScript") is STALE. Empirically (current main, fresh generic SimpleFPS), HudControl
  already classifies `domain="client"`, `script_class="ModuleScript"`, `container="ReplicatedStorage"`,
  `runtime_bearing`, `requireable`, no fail-close. The require-fallback strip + `instance_owner_is_ui`
  strong-client signal already landed. Mark Slice H done; the "LocalScript" requirement is wrong
  (host require()s it as a ModuleScript).

- **[contract_verifier shadow → fail-closed]** The task assumed Slice H "must pass the fail-closed
  contract_verifier," but the verifier runs in SHADOW mode on current main (1 info-level
  consumer_compliance violation: dead water module `Displace`). Flipping it to fail-closed is its
  own decision/PR (likely part of PR6/PR8 territory). Track separately.

- **[possible HUD runtime-binding]** IF the Studio canary shows the HUD is dead at runtime despite
  correct client classification, the real root cause is runtime boot/binding (SceneRuntimeClient
  not constructing the requireable client module, or `self.gameObject` not binding to the HUD
  ScreenGui) — a distinct, smaller fix than the doc's classifier change. Capture with its real
  root cause; do NOT fix blind in this run.

- **[fixture hygiene]** `tests/fixtures/topology/simplefps_minimal.json` encodes HudControl=client
  (now the correct production behavior). Ensure Slice-adjacent tests exercise the real classifier,
  not just assert against this fixture (green-test-for-the-wrong-reason guard).

- **[Pause panel]** `HUD.Pause` not converted (`_pauseMenu()` nil) — UI-conversion completeness,
  pre-existing, out of scope.

## Run hudbind-20260604-223428 (2026-06-05)

# Followups (out of scope for the run)

## Slice 1.1 — UI-host deferred resolution
- **Respawn re-clone rebind (ResetOnSpawn=true GUIs).** Slice 1.1 binds
  `instance.gameObject` once at initial boot. The confirmed HUD is
  ResetOnSpawn=false so its clone persists across respawns and no rebind is
  needed. A ResetOnSpawn=true GUI gets a NEW PlayerGui clone on respawn that
  no component rebinds to today (true regardless of this slice). Rebinding
  deferred UI components on respawn is a separate concern — out of scope here.
  (Marked with a code comment in `scene_runtime.luau:_resolveDeferredUiInstances`.)
- **Inbound references to a deferred UI component.** RESOLVED in fix-round 1
  (codex BLOCKING #2). The synchronous `_wireReferences` pass now records
  inbound refs whose target is a deferred instance (`_inboundRefsToDeferred`);
  `_completeDeferredBatch` back-patches the stored field on each source once the
  deferred target is built (including prefab-side `externalRefs`). Inherent
  residual: a source that already CACHED the value in its own Awake won't
  re-read it — the field is populated, but a consumer that snapshotted nil in
  Awake keeps nil. Not fixable without a re-Awake of the source (out of scope).
- **awaitUiHost connect-vs-scan gap (MAJOR #5) is only structurally tested.**
  The connect-first fix is exercised by a coroutine test (initial-scan hit,
  DescendantAdded-after-miss, timeout→nil), but the microscopic real-Roblox
  window where a clone lands between connect and scan cannot be reproduced
  deterministically under standalone luau. The Studio canary (acceptance 4) is
  the real-environment guard for that window.

## --- Run: generic-converter-step1-player-20260607T091314 (Phase 1 / Gate 0) ---
# Followups — generic-converter Step 1 (player-embodiment authority)

- Propagate D7+D8 into the authoritative converter/docs/design/player-embodiment-authority-design.md IN THE WORKTREE before Phase 2/3 detailed-design runs (update-design-doc-before-implementation).
- Phase 2 /drive-design: pin the EXACT after-LateUpdate scheduling slot for driveLocomotion vs the post-camera re-assert (tail of _tick / later heartbeat priority / RenderStepped), and Gate 0 should test that exact slot (codex r3 MINOR + claude r3 MINOR).
- Phase 2/3 /drive-design: state recoil-on-A-hit is knowingly degraded until A deleted in Phase 5; the A-hit Studio fixture asserts look/move, NOT recoil (claude r3 MINOR).
- codex r3 MINOR "verify_hook.py/REQUIRE_PLAYER_BIND not found" was a FALSE ALARM (codex read the pre-#184 working tree). Files exist at base ref 2cbed06. No action; noted so it isn't re-raised.
- Phase 2 /drive-design: the E2E single-read must fire at the HEAD of _tick (before the pairs() Update loop at scene_runtime.luau:2794) — tail-of-_tick won't do for the PRE read. Confirm nothing else advances E2EMouseAckSeq earlier in the frame (claude r4 MINOR).
- Phase 1 Gate-0 (d): assert C's snapshot read HAPPENS-BEFORE an A-style in-band _readDelta (ordering), not merely "consumed once" — so a future refactor moving C's read into the component loop FAILS Gate 0 rather than passing on lucky pairs() order (claude r4 MINOR).

## From Step-1b (player-embodiment, 2026-06-10)
- **door-visual cold-e2e (PRE-EXISTING, documented pr148-followups)** — `door_opens_with_key` logic passes (`open` attribute flips) but the visual tween never plays: Door is `domain=client` and sets `open` client-side, while `Anim_Door` ships as a server `Script` (the animation_routing "safe fallback" for an UNRESOLVED driver — Door uses dynamic `transform.parent.Find("door").GetComponent<Animator>()`, no serialized Animator ref). Client→server attribute writes don't replicate → no tween. Latent on `main`; orthogonal to player-embodiment. FIX = the deferred "Phase-2 source-narrowing" (resolve the driver by which MB writes the clip's `observed_attribute` → route Anim client-side) OR widen the PlayerSetSharedFlag RemoteEvent bridge to generic Animator-attribute writes. **To be its own focused PR (per jiazou).**
- **turret damage (PRE-EXISTING, PR #145)** — turrets fire but TurretBullet damage no-ops because runtime-spawned prefab clones lack `_SceneRuntimeId`. Out of any Step-1b scope.
- **recoil cross-surface sign** — `host.player:applyRecoil(deg)` kicks UP for +deg (Studio-verified); the drone/turret `SceneCameraInput:applyRecoil(-math.rad(2))` lowering uses the opposite sign for the same Unity shape. Audit the drone/turret recoil direction in Studio; pinned pre-existing, non-player.
- **claude_cli-backend reject E2E seam** + the Phase-2/3/4 P2/P3 test-strength followups remain (see the RUN_DIR followups.md).

## Run addressables-unit1-20260615T133903 (Addressables Unit 1) — 2026-06-15T10:05:21Z

## For the per-phase design (phase 1) — review pointers (not blocking)
- Real collision locus: `autogen.py:_resolveTemplate` keys instantiatePrefab on the
  unique prefab_id but COLLAPSES to bare `template_name` for the Templates lookup. The
  unique-key fix must re-key BOTH the lookup AND the on-disk Templates child name as a
  coupled pair (the one seam where the fix can diverge).
- [P3] Stale comment `autogen.py:825` claims the prefab_id path already disambiguates
  colliding names — it doesn't. Fix the comment when re-keying.
- Resolver return shape (verified): `by_address: dict[str, list[str]]`; tests prove
  duplicate addresses -> multi-element lists, and `"Trash Cat" -> ["<catguid>:.../Cat/character.prefab"]`
  (a singleton resolving to Cat).

## Out-of-scope discoveries (from phase-1 detailed design, 2026-06-15)
- **[design.md correction] `by_guid` is NOT in the plan `addressables` block.** design.md
  lists `by_address`/`by_label`/`by_guid`, but the resolver's `PrefabAddressables` has no
  `by_guid` (only the raw guid-keyed `AddressablesIndex` does, which the host never reads).
  Detailed design drops it (decision D7). Reconcile design.md if it's re-read downstream.
- **[Unit 2] `by_label` is emitted into the plan but not consumed by `instantiatePrefab`.**
  Unit 1 wires only the address path (the character spike is address-only). Unit 2's
  `LoadAssetsAsync<GameObject>("characters")` host path must consume `addressables.by_label`.
- **[low-prob, deferred] guid6 suffix collision.** Two distinct prefab guids whose first 6
  hex chars match AND same base name → identical Templates child name → real on-disk
  collision + wrong-template clone. Slice 1.3 logs a WARNING on post-suffix collision among
  emitted templates (fail-loud); full resolution (widen to guid8/full guid on collision) is
  deferred — not load-bearing for the Cat spike (Cat/Raccoon guids differ in the first 6).
- **[prefab_id format parity] resolver vs planner separator.** Planner uses `.as_posix()`;
  resolver used `str(Path)` (OS-native). Slice 1.2 normalizes the resolver to `.as_posix()`
  so the two prefab_id producers are byte-identical (latent Windows-only mismatch otherwise).
- **[verify checkpoint, NOT a slice] TrackManager/Character/CharacterCollider domain.** If the
  regenerated generic output mis-routes any of these server-side, the spawned client collider
  never boots — that's a domain-classifier fix OUTSIDE Unit 1's boundary; scope it as its own
  phase, don't bolt it into a Slice 1.x.
## Carry-forward for slices 1.2/1.3 (from 1.1 implement)
- `select_emitted_prefab_ids` gained an optional `guid_index: GuidIndex | None = None` kwarg
  (PrefabLibrary carries no guid_index). The planner (1.2) + emitter (1.3) MUST pass
  `guid_index=` so the derived prefab_ids match `artifact["prefabs"]` keys; omitting it falls
  back to guid-less posix paths (won't match). `resolve_template_child_names` is collision-
  conditional over the EMITTED input set only.
## HARDEN targets (phase 1) — from slice 1.1 review (P2, non-blocking)
- _guid6_of (prefab_packages.py): harden against a leading path segment that is accidentally
  all-hex before a colon (e.g. drive letter / "face:") — only treat as guid if it matches a
  real Unity guid shape. Add a test for the path-with-colon case.
- Add a selector test for the duplicate-base referenced case: a bare ref to "character"
  selects BOTH Cat and Raccoon prefab_ids (pins D14 emitted-set behavior).
## HARDEN target (phase 1) — from slice 1.2 r2 (P2)
- AC14 parity tests: project_root=None leg uses a SimpleNamespace stub for the resolver and
  the no-guid leg skips the resolver. Use a REAL GuidIndex in every leg so the "3-way via real
  GuidIndex" claim is literal.
## HARDEN targets (phase 1) — from slice 1.3 (P2)
- D13 variant-parent WARN (prefab_packages.py:496): narrow `_colliding_variant_parents` to
  emitted variant CHILDREN only (currently warns even when variant_chains is {}).
- guid-less colliding animation bypass: now covered by the D12 colliding-base-membership fix.
- `_colliding_emitted_bare_bases` duplicates the emitter's collision count (dedupe/perf).
- manifest `addressable_referenced` can exceed `emitted_count` (cosmetic stat clarity).
## HARDEN target (phase 1) — from phase-integration review (P2)
- Stale comment in test_prefab_packages.py:1380-1389 ("xfail markers below / ids drift today")
  now contradicts the unified code (markers removed). Fix/remove the comment.

## slop (deferred to finalize)
converter/converter/prefab_packages.py:135-223 — helper docstrings read like pasted design-doc prose (design fact / D14 rationale blocks)
converter/converter/pipeline.py:653-660 — long process-history comment in implementation
converter/converter/pipeline.py:1081-1108 — long process-history comment in implementation
converter/converter/scene_runtime_planner.py — verify _prefab_stable_id docstring not overlong (harden audit area)
converter/tests/test_prefab_packages.py:1541-1740 — test docstrings mirror the spec/decision log verbatim

## Run addressables-unit2-20260615T193738 (2026-06-16) — Unit 2 followups

# Unit 2 — out-of-scope discoveries / follow-ups

- **Theme registration is dead (Unit 3).** `ThemeDatabase.Register` never called;
  themeData SOs carry `prefabList`/`collectiblePrefab`/`cloudPrefabs` as unresolved
  refs. The Unit-2 primitive is the dependency; the registration rewrite is Unit 3.

- **Consumable prototype materialization + CharacterDatabase roster (Unit 4).**
  by_label["characters"] roster load and Consumable serialized-field materialization
  are reserved for Unit 4. by_label is emitted in the plan but NOT consumed at runtime
  yet (runtime/scene_runtime.luau:2029-2030) — Unit 4 will wire it.

- **SO emitter drops ALL non-m_Script object refs to nil** (scriptable_object_converter.py
  _value_to_lua lines 82-87). Unit 2 fixes the prefab-ref subset; sprite/audio/material
  refs in SO data remain nil (acceptable fail-soft; revisit if a unit needs them).

- **Stripped prefab-instance component refs are a general gap, not just missionPopup.**
  The planner emits `target_kind="component"` for any scene ref to a stripped MB on a
  prefab instance whose owning GO/instance_id isn't registered. Trash Dash likely has
  more than the 2 missionPopup rows. The repro fix should be general (resolve stripped
  prefab-instance refs), not missionPopup-specific. Audit the full count of unresolvable
  `component`-kind rows at phase-design time.

- **Respawn re-clone rebind out of scope** (runtime/scene_runtime.luau:2507-2509 notes
  refs bound once at boot). Not a Unit-2 concern.

## From Unit 2 plan review
- **Sub-asset fileID disambiguation** (D3 limitation): a `{guid,fileID}` ref pointing INSIDE
  a prefab (not the root) resolves to the prefab root because the primitive ignores fileID.
  Out of Unit-2 scope; Units 3-4 must watch for this if any themeData/consumable ref is a
  sub-asset ref.
- **Other stripped prefab-instance component refs**: there are more `target_kind=component`
  unresolvable rows than just the two `missionPopup` rows; Phase 3's general fix should cover
  the class, but enumerate them during Phase 3 design to confirm coverage.

## slop (deferred to finalize)
converter/unity/prefab_ref.py:27 — review GuidIndexLike Protocol docstring/comment verbosity
converter/unity/prefab_ref.py:46 — review comment/docstring density
converter/unity/prefab_ref.py:69 — review comment/docstring density
converter/tests/test_prefab_ref.py:1 — review module-docstring/comment verbosity
converter/tests/test_prefab_ref.py:74 — review comment verbosity
converter/tests/test_prefab_ref.py:93 — review comment verbosity
converter/unity/prefab_ref.py — docstring says "never raises" but does not type-validate a malformed GuidIndexLike; scope the claim honestly (do NOT add defensive code — keep byte-identical to the original closure)
converter/tests/test_scene_runtime_planner.py:1483,1526 — stale `prefab_id_for` docstring mentions (nested closure renamed to module-level prefab_id_for_guid)

## From Unit 2 phase-1 harden (P2, non-blocking)
- converter/unity/addressables_resolver.py:155-158 — add a targeted by_label regression test for a label with mixed prefab+non-prefab guids and a label where nothing resolves (key disappears). Non-criterion; rewire is byte-identical so low risk.

## slop (deferred to finalize) — phase 2 harden
converter/tests/test_scriptable_object_converter.py:306 — over-explanatory helper docstring (internal narrative: "edge case 9b", "would pass for the wrong reason")
converter/tests/test_scriptable_object_converter.py:444 — arm-number/rhetorical test commentary ("takes the arm (:120)", "Genuinely...")
converter/tests/test_scriptable_object_converter.py:496 — "belt-and-suspenders" banner + author-machine-specific absolute path (~:501) in the real-project test

## Phase 3 follow-ups (out of scope; missionPopup repro IS fixed by Phase 3)
- Binary-scene stripped-MB resolution (no YAML → `stripped_components` stays empty → fail-soft).
- `Awake`-time reads of stripped refs still see nil (pre-placement); needs placement-before-scene-
  Awake boot reorder, a larger rework.
- Sub-asset fileID disambiguation beyond `m_CorrespondingSourceObject.fileID` (Unit-1 keys one id
  per `.prefab` file).
converter/unity/yaml_parser.py — soften the 'len(docs)==len(doc_headers)+1' pairing comment (overstated for bare-trailing-separator files; else-fallback covers it)
converter/runtime/scene_runtime.luau — pre-existing _inboundRefsToDeferred drain (~:2664) does not re-check cross-domain: a cross-domain ref to a UI-deferred SCENE-LOCAL target could rebind. Predates Unit 2; not a Phase-3 regression. Followup ticket.
converter/tests/test_scene_runtime_stripped_refs.py — add coverage for a SOURCE component that is itself UI-deferred (Phase-3 stripped refs have scene-boot sources, so currently out-of-class); + tidy imprecise test comment ~:629

## From Unit 2 phase-3 integration (codex P1 — generality, deferred)
- converter/scene_runtime_topology/cross_domain_edges.py — compute_cross_domain_edges() indexes only raw instance_ids; a cross-domain STRIPPED ref's placement-scoped target_ref (<placement_id>:<prefab_id>:<src_fid>) produces no cross-domain edge → no build-time RemoteEvent bridge. Extend it to recognize placement-scoped stripped refs (resolve target domain via the stamped target_script_id) so a cross-domain stripped ref gets a bridge. NOT a regression (pre-Phase-3 stripped refs were unresolvable → also no edge) and no such ref in Trash-Dash (all 3 are client→client); runtime fail-safes (nil + edge recorded). Out of Unit-2 scope.
- Add an automated Python-plan→Luau-runtime e2e for the stripped-ref path (planner output → runtime bind), complementing the Studio e2e.

## slop (deferred to finalize) — phase 3 harden
converter/tests/test_scene_runtime_stripped_refs.py:1 — module docstring/narrative verbosity
converter/tests/test_scene_runtime_stripped_refs.py:46 — review comment verbosity
converter/tests/test_scene_runtime_stripped_refs.py:184 — "EMPIRICAL ANSWER"/round-rationale narration in test
converter/tests/test_scene_runtime_stripped_refs.py:486 — duplicated codex-round rationale comment
converter/tests/test_scene_runtime_planner.py:1891 — review-narration comment
converter/tests/test_yaml_parser.py:311 — comment verbosity
converter/runtime/scene_runtime.luau (queue site ~:1300) — self-contradictory/over-explanatory comment at the pending-queue site

## From Unit 2 phase-3 harden (WARN-timing residual — reverted net-negative patch)
- converter/runtime/scene_runtime.luau — the residual "stripped ref never resolved" WARN has imperfect timing across MULTIPLE async UI-deferred host groups: it can fire PREMATURELY (a ref that a later group will bind) — that's the last-good behavior we kept. A harden attempt to gate it on a _deferredGroupsPending counter + a final drain was REVERTED because it introduced the opposite bug (WARN SUPPRESSED forever when the last group completes as an empty batch — codex). The WARN is DIAGNOSTIC-ONLY; binding is correct either way (cross-domain refs never queued; same-domain incl. missionPopup bind via the original post-placement + per-batch drains). Getting the WARN's exact multi-group async timing right is deferred (no real game exercises it: Trash-Dash's 3 stripped refs are client→client and their targets register). Fix when a real case appears.
