
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

## Run hudbind-20260604-223428 — generic UI gameObject boot-race / HUD fix (2026-06-05)

# Decisions — drive run hudbind-20260604-223428

Task: generic-mode UI-controller gameObject binding (HUD fix). Branched off upstream/main 519a965
(has the merged turret fix PR #178).

## Root-cause spike (plan stage)
HudControl `self.gameObject` nil → dead HUD. NOT classifier (correct), NOT turret fix. 3-link gap:
1. planner doesn't link UI-controller → host UI GameObject (`game_object_id: None`).
2. UI hosts not SRI-stamped (0/32 UI items carry `_SceneRuntimeId`; ui_translator stamp path gated, not firing).
3. host `workspaceFind` (autogen.py:676) already scans PlayerGui by SRI — has nothing to hit until 1+2.

## Scoping checkpoint (waiting on user)
Fix spans scene_runtime_planner/topology + ui_translator + autogen/host — a full design→review→
implement→harden→Studio cycle, not the one-liner the task implied. Surfaced for direction.

## Slice 1.1 implementation deviations
- **Deferral, not in-loop staging.** The design offered two options (stage UI
  resolution before Start arms, OR defer construction out of the synchronous
  pass). I chose full deferral: a UI-owned instance whose `workspaceFind`
  misses is NOT built during the synchronous boot at all — it is collected and
  completed in its own late lifecycle batch from a spawned coroutine after the
  synchronous boot finishes (after `self._crossDomainEdges` is set). This is
  the only option that both (a) keeps the `start()`-never-yields invariant for
  every synchronously-built component intact and (b) is compatible with an
  event-driven (yielding) `awaitUiHost`. An "in-loop stage that completes
  before Start arms" is incompatible with an event-driven wait, since the wait
  is inherently async and Start is armed via `task.defer` at end of the
  synchronous frame.
- **Shared `_applyPlannerFlagsAndTag` helper.** Extracted the duplicated
  active/enabled/tag registration from the scene loop and prefab loop into one
  method so the synchronous and deferred-UI paths stay byte-identical (no
  behavior change for the existing paths; existing tests stay green).
- **`awaitUiHost` host-surface helper** added to the generic CLIENT entrypoint
  only (`autogen.py`). Server entrypoint + legacy emit untouched. Engine guards
  `if self._services.awaitUiHost` so the server partition (no PlayerGui, no UI
  instances in its domain) is unaffected. Event-driven via
  `PlayerGui.DescendantAdded` + initial scan; 10s timeout = fail-closed
  diagnostics (engine `warn`s the id, never binds nil).

## Slice 1.1 fix-round 1 (dual-voice review: 2 BLOCKING + 2 MAJOR + Claude MAJOR)
Reworked the per-component deferral into a correct BATCHED deferral. Changes:
- **Batched lifecycle (BLOCKING #1).** Replaced the per-entry
  `_runAwakeEnableStart({comp})`-as-host-resolves with a barrier: spawn one
  resolver coroutine per deferred entry (event-driven waits run concurrently),
  and when ALL hosts have resolved, run `_completeDeferredBatch` — build/inject/
  wire the whole resolved set ordered by the planner `lifecycle_order`, then run
  `_runAwakeEnableStart` over the WHOLE batch once. The batch's Awake/OnEnable
  all precede its Start; intra-batch `lifecycle_order` and same-GO GetComponent
  in Awake hold. Lateness vs the synchronous batch is inherent + accepted.
- **Inbound-ref back-patch (BLOCKING #2).** `_wireReferences` now records refs
  whose component-kind target is a deferred (not-yet-built) instance into
  `_inboundRefsToDeferred`; `_completeDeferredBatch` sets the stored field on the
  source once the target builds (incl. prefab `externalRefs`). New
  `_deferredInstanceIds` set tells the sync pass which targets are deferred.
- **Server/no-resolver safety (MAJOR #3).** `_resolveDeferredUiInstances` now
  gates on `services.awaitUiHost`: absent → one-shot `workspaceFind` per entry
  then `_completeDeferredBatch` builds even with nil gameObject (pre-slice
  behaviour), NEVER defers-then-never-builds.
- **Runtime-prefab-spawn orphan (Claude MAJOR).** Added a `deferUiMisses` param
  to `_constructPrefabClone`: only the scene/prefab BOOT placement loop passes
  `true`; `instantiatePrefab` (runtime spawn) leaves it false so a runtime-
  spawned UI host resolves one-shot against its LOCAL clone (the right path —
  it doesn't race PlayerGui) instead of being deferred into a queue that
  `instantiatePrefab` never drains.
- **`awaitUiHost` connect-first (MAJOR #5).** Connect `DescendantAdded` BEFORE
  the initial `GetDescendants` scan so a clone landing in the gap can't be
  missed; scan-hit returns inline (thread not yet suspended), event/timeout
  resume the yielded waiter.
- **Tests (#6).** Added batched-deferral + inbound-back-patch test (FAILS
  pre-fix), server/no-resolver one-shot test (FAILS pre-fix), and a
  coroutine-driven `awaitUiHost` test (initial-scan hit / DescendantAdded-after-
  miss / timeout→nil) driving the REAL emitted resolver body.

## Slice 1.1 r3 (commit 3f799c1)
- **Registry key is placement-scoped (BLOCKING).** `_componentByInstanceId`,
  `_deferredInstanceIds`, and the inbound back-patch `target_ref` all key by
  `_idWithPlacement(placement_id, instance_id)` so multi-placed prefab clones
  don't collide. Scene ids stay raw (already globally unique). `_unregister`
  clears the EXACT key (`meta.registryKey`) only when it still points at this
  comp (a later placement/runtime-spawn legitimately overwrites the key).
- **Pass 3b back-patch now resolves against the engine union map** (scoped)
  instead of the batch-local `builtByInstanceId` (raw) — so a cross-host
  inbound ref binds the correct placement's clone and resolves on whichever
  batch builds the target, not only the current one. Safe because the only
  reader runs once at end of `start()` (boot-only).
- **Dependency-aware deferred batching (MAJOR).** Inter-group dependency edges
  (a group's outbound `component`-ref to a deferred component on another host)
  drive (1) a Kahn topological spawn order (deps first) and (2) a per-group
  `waitForDeps` before `_completeDeferredBatch`. KEY: a group waits ONLY on
  groups it references — unrelated never-resolving hosts never gate it (r2
  BLOCKING preserved).
- **TRADEOFF — cycle / never-resolving dependency resolution.** A dependency
  cycle is broken by appending the cycle remnant to the topo order in stable
  order; the runtime `waitForDeps` loop then times out on the unresolved dep
  and proceeds with nil + warn (fail-closed, no hang). The wait is a bounded
  poll: real-time `DEP_TIMEOUT=10s` via `now()` for production PLUS a
  `DEP_MAX_ITERS=200000` hard cap (the unit harness' `task.wait` returns
  without advancing the mock clock, so a clock-only deadline would spin
  forever there — the iteration cap guarantees termination). Polling (vs a
  fully event-driven dependency wait) was chosen as the pragmatic, testable
  shape; an event-driven rewrite is a possible future refinement, not needed
  for correctness.
