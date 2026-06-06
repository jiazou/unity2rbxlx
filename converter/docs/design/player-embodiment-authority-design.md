# Design — host-owned player-embodiment authority (`self.host.player`)

Status: REVISED after dual-voice review (Claude design + Codex adversarial). Open decisions are
now RESOLVED (U1, pre/post camera, A-last, B-demoted, server-authoritative teleport). Ready for the
gstack review pass. Not yet implemented. Supersedes the player-binding *mechanism* of PR #182
(keeps its identity + fail-closed foundation).

## The one goal
A converted Unity FPS plays correctly in GENERIC scene-runtime mode — first-person camera bound to
the character, the character moves (WASD camera-relative + jump), shooting raycasts the player's
view, respawn/teleport moves the player — **for ANY valid AI transpilation of the player
controller**, verifiable at build time, not only by a lucky e2e run.

## The invariant (the test for "is this the last time we fix it")
> Given a unique upstream player identity, **authoritative player embodiment is driven only by
> deterministic runtime code and explicit helper contracts — never by inspecting or rewriting
> emitted methods, and never depending on the *shape* of the emitted output for correctness.**

If a proposed fix reads the transpiled shape to make the binding WORK, it breaks on the next AI
shape. This fails PR #182 (its locators read the shape) and the entire prior lineage; only a runtime
authority passes. **Corollary (both reviewers):** the authority's correctness must NOT secretly
depend on the AI emitting a particular call (e.g. a starvable `GetMouseDelta`) — see §Ordering.

## Why (history: 4 fixes, all the same doomed paradigm)
Camera/player binding has been "fixed" 4×, all by **matching/rewriting the non-deterministic AI
output**: transpile-fidelity → legacy coherence pack → facet lowering + `SceneCameraInput` service →
PR #182 (fixed IDENTITY upstream, but kept fingerprint LOCATORS for the rewrite sites). The design
docs admit the verifier is structural-only with no behavioral oracle (`scene-runtime-contract.md`),
the only behavioral net is the e2e fixtures, and prompt-teaching is "a hint, not enforcement"
(`camera-input-fidelity-plan.md`). So binding "passed" only when the e2e ran on a cached/lucky
shape. PROOF: a COLD (uncached) re-transpile of SimpleFPS factored WASD into a `_getAxis` helper +
drove the rig via `PivotTo` + cached `GetMouseDelta` in `Update` → both PR #182 locators abstained →
fail-closed, NO bind (output/e2e/2026-06-05T23-45-34-cold3a59/conversion/scripts/Player.luau).

Three paradigms: A=match/rewrite (doomed), B=constrain via prompt+verifier (can't guarantee — "hint
not enforcement", one-shot reprompt then fail-closed), C=own at runtime (only one not reading the
shape). **Decision: C is the authority; B is a conflict-reducing backstop; A is deleted LAST.**

## Scope: it's player EMBODIMENT, not just camera+movement (Codex)
The player controller couples game logic to embodiment in ≥4 places, all of which re-decouple the
rig from the character if left to the AI: (1) **look** — writes `CurrentCamera.CFrame`; (2) **move**
— reads WASD + drives the rig/`Humanoid`; (3) **aim-read** — `Shoot` raycasts `self.cam.CFrame`;
(4) **respawn/teleport** — `TakeDamage`/respawn `PivotTo`s the rig. The unit of authority is **player
embodiment** (the rig↔character relationship), exposed as ONE host object `self.host.player`.

## Architecture

### C (load-bearing) — `self.host.player`, a host-owned per-player embodiment authority
For the upstream-identified player component, the host owns an embodiment object driven from `_tick`
**independent of the AI's emitted methods**. NOTE (Codex): this is **NOT a thin wrapper over the
existing `SceneCameraInput` singleton** — that singleton carries sticky `_rig/_followChar/_seeded`
state and a one-acquire lifetime that's the wrong model here. **Reuse** its vetted *math* (pose
composition `composeLook`, `advance`, recoil clamp, the E2E mouse channel, HRP eye-follow) but own a
**clean per-player lifecycle** (init on character spawn, resync on respawn, no leaked singleton
state). Responsibilities:
- **input snapshot** — read mouse delta (+E2E channel) + WASD ONCE per frame, BEFORE the component
  Update pass.
- **camera pose** — world-yaw ∘ local-pitch, eye = character HRP + eyeHeight (the prompt's
  HRP+1.5 anti-bob rule). Written **TWICE** per frame (see §Ordering).
- **locomotion** — WASD → camera-yaw-relative → `Humanoid:Move`/jump on `LocalPlayer.Character`,
  AFTER the component passes.
- **read-only look** — `self.host.player:getLookCFrame()` for raycasts (Shoot).
- **respawn/teleport** — `self.host.player:teleport(cf)` requests a CHARACTER teleport; the
  **server** applies it (see §Server). Yaw resyncs after.
- **boot** — default-controls-off + avatar-hide + Scriptable camera (folds `_ensureInit`), gated
  `RunService:IsClient()`.

### Ordering (the headline risk — failure-#5 vector; both reviewers)
`_tick` iterates components UNORDERED (scene_runtime.luau:2656) and the cold player runs
`Shoot→Rotate→Move` inside one `Update`. The prior design's Risk #1 is the in-band ordering
(yaw-before-move, recoil-before-raycast same frame). So:
- The host runs OUTSIDE the component loop: `readInput()` + **camera write** BEFORE the Update pass;
  `driveCamera()` (re-assert) + `driveLocomotion()` AFTER the LateUpdate pass. **TWO camera writes
  per frame** (pre + post), both idempotent (last-writer-wins).
- **Why pre-write (the fix both voices required):** `Shoot` reads raw `self.cam.CFrame` during the
  Update pass; a post-only host write leaves aim 1 frame stale. The pre-Update write makes
  same-frame raw reads correct. The post-LateUpdate write folds in this frame's locomotion-driven
  eye position.
- **This removes the `GetMouseDelta`-drain dependence from CORRECTNESS.** The host overwrites the
  camera both pre and post, so the AI's vestigial `Rotate` write is dominated **regardless** of
  whether the engine drains the AI's `GetMouseDelta` to ~0. The drain is a *nicety* (the AI's write
  is also a near-no-op if drained), not load-bearing — it must NOT be a correctness assumption
  (Gate 0 proves the multi-read semantics empirically before we lean on it for anything).
- **Recoil-before-raycast** is preserved ONLY if `Shoot` cooperates via `applyRecoil`/`getLookCFrame`
  (paradigm B). Without B: recoil is cosmetic-lost (acceptable; the shot still raycasts the correct
  pre-write look). State this as a known fidelity floor, not a blocker.

### Rig↔character unification — U1 (shadow-sync). RESOLVED: reject U2.
**U1 (chosen):** each frame, BEFORE the component Update pass, the host sets `rig.CFrame :=
character HRP CFrame`. The component still lives on the authored rig object, so all host identity
machinery is preserved: `GetComponent` (closed over the original `gameObjectId`/instance in `_meta`,
scene_runtime.luau:668/712/774), `_SceneRuntimeId` child lookup, active/registry maps,
`CameraRigFollower` (follows `CurrentCamera`, not the rig). The AI's `gameObject:GetPivot()` reads
see the character; its `PivotTo` writes are overwritten next frame (vestigial). **Caveat (Claude,
accepted):** the rig's *yaw* is no longer authoritative — but nothing authoritative reads it (the
host owns yaw via the camera and drives locomotion from camera-yaw, not rig-yaw; the AI's rig reads
are vestigial because the host does not consume them). The rig is a **positional shadow of the one
body (the character)**, NOT a second game-state store — so it is not the cross-script-state
anti-pattern.
**U2 (rejected, Codex):** rebinding `self.gameObject` to the character splits identity —
`self.gameObject` would point at the Roblox-spawned character while `GetComponent`/registries/
`_SceneRuntimeId` services still target the rig instance captured in `_meta`. Unifying *all* of those
is a large, hazardous blast radius for no gain over U1.

### B (backstop, NOT load-bearing) — keep the AI out of the way
Keyed on the upstream player identity (known at transpile time). Demoted per both reviewers:
- **per-script prompt directive**: "This script is the player controller. The host owns camera,
  movement, aim, and respawn via `self.host.player`. Do NOT write `workspace.CurrentCamera`, do NOT
  call `Humanoid:Move`. For aim use `self.host.player:getLookCFrame()`; for recoil `applyRecoil`; to
  teleport use `self.host.player:teleport(cf)`. Keep your game logic (shoot decision, ammo, pickups,
  pause)." (Requires NEW per-script prompt-context plumbing — the prompt is binary today.)
- **negative verifier rule (ONLY the lexically-decidable rejects)**: for the identified player
  script, reject a direct `workspace.CurrentCamera.CFrame =` write or a direct `Humanoid:Move(`
  call → one-shot reprompt. **DROP the "movement `PivotTo`" clause** — `PivotTo` is used for yaw,
  translate, AND respawn in the same script and is NOT lexically separable (both reviewers); the
  verifier is conservative-lexical, not semantic. (Requires wiring per-script identity into
  `verify_module`, which today takes only `source`.)
- B is a *conflict reducer + a cleaner-output nicety*, NEVER correctness. **C holds the binding even
  if the AI ignores B entirely** — because the body write-surface (camera pre/post, Humanoid on the
  real character, rig shadow-synced) is host-owned structurally.

### A (delete — LAST, after C dominates)
Delete `movement_facet_lowering` + the player path of `camera_facet_lowering`. **Only after** U1 +
aim-read + respawn land and the cold-Studio checks pass (else slice-1 ships the raw cold-shape
`Shoot`/`TakeDamage` hazards with no fallback — both reviewers flagged the original A-first ordering).
**Non-player cameras (drone/turret rigs): KEEP the strict `camera_facet` path** — out of scope for
the player authority (resolved boundary, not an open question).

## Server / multiplayer boundary (RESOLVED — Codex)
`self.host.player` owns **client** embodiment: camera, local look, baseline locomotion (gated
`IsClient()`). The **server stays authoritative** for spawn/respawn/**teleport application** and shot
validation — the autogen `GameServer` already spawns on `CharacterAdded` and validates shot origin
near the character before the server raycast (autogen.py:104/216). So `teleport(cf)` is a client
**request**; the server applies it. Do NOT specify teleport as purely client-owned. No parallel
server *movement* authority is built (matches Roblox defaults; documented boundary, not gold-plated).

## Movement fidelity (RESOLVED)
Host-driven WASD→`Humanoid:Move` discards game-specific movement (dash/double-jump/var-speed). The
current `movement_facet` ALREADY discards it (KNOWN_ISSUES.md) — **no regression**; generic
locomotion is the accepted permanent generic-mode contract. A future opt-in
`self.host.player:setMoveIntent(vel)/jump()` hook could preserve more intent — a SEPARATE fidelity
layer, not this fix.

## Don't widen the CharacterController dual-map
`scene_runtime` maps CharacterController→BasePart; `TYPE_MAP`→Humanoid; `physics_bridge` is a third
path. `self.host.player` is the SINGLE authority for "the player's body"; do NOT add a 4th
per-instance `GetComponent` override. `self.control` reads (if any survive) route through the helper.

## PR #182 disposition
KEEP: upstream identity (`has_character_controller`) — the host needs it; the require-service fix;
the fail-closed *infrastructure* (re-purposed to "host couldn't find a character", which shouldn't
fire once C is authoritative). DELETE (in the final slice): the locators + their `player_move/look_
unbound` rows. #182 is the identity FOUNDATION, not the destination.

## Slicing — reordered per both reviewers (each slice verified on a FRESH cold-transpile in Studio)
- **Gate 0 — prove the primitives.** A luau-harness/Studio check of (a) same-frame multi-read
  `GetMouseDelta` behavior across contexts; (b) pre-Update + post-LateUpdate camera-write semantics
  (does a pre-write survive to `Shoot`'s read; does the post-write win the frame). NO product code
  rides on an unproven assumption.
- **Slice 1 — host authority, WITH A still present as fallback.** `self.host.player`: input snapshot
  + pre/post camera + host locomotion + boot, from `_tick` outside the component loop, keyed on the
  upstream identity carried into the runtime plan. Cold-Studio: camera + WASD on the cold shape.
  (A NOT deleted yet.)
- **Slice 2 — U1 + aim-read + respawn/teleport.** Shadow-sync rig; `getLookCFrame()` for Shoot;
  `teleport()` request→server-apply. Cold-Studio: Shoot hits the crosshair, TakeDamage moves the
  CHARACTER.
- **Slice 3 — B backstop.** Per-script prompt directive + the two lexical verifier rejects (camera
  write / `Humanoid:Move`). Verify: a clean AI player script (no camera/move) + a hand-broken script
  STILL binds (C dominates).
- **Slice 4 — delete A + retire dead tests; full cold e2e (turrets/doors/etc.) green.**

## Verification discipline (the lesson this episode taught)
NEVER call this done off a cached assemble. Every slice: cold (uncached) transpile → real-mesh
conversion → Studio play → the camera/movement/shoot/respawn fixtures, on a FRESH AI shape.
Build-time signal = host-harness unit tests for `self.host.player` (deterministic) + the lexical
verifier rejects; e2e is the final net, not the only one.

## Residual risks (named, ranked)
1. **Frame ordering / same-frame aim** — THE failure-#5 vector. Mitigated by pre+post camera writes
   + Gate 0 proof. Must be Studio-verified uncached, not asserted.
2. **U1 yaw non-authority** — acceptable iff the host owns yaw+locomotion (it does); add a test that
   nothing authoritative reads rig yaw.
3. **B undecidability** — mitigated by demoting B to non-load-bearing + dropping the PivotTo clause.
4. **Server teleport coupling** — keep teleport a client-request/server-apply to avoid client/server
   desync.

## Engineering-review additions (gstack plan-eng-review)

### Scope / minimal surface (Step-0 scope challenge)
The plan spans 5 files + a new authority + 2 deletions — over the 8-file/2-class "smell" line. Verdict:
the innovation token is WARRANTED (4 prior attempts in the cheaper paradigm all failed), BUT pin the
MINIMAL surface so this doesn't grow into a greenfield subsystem. **New code is exactly:** (1) a
WASD→`Humanoid:Move` driver, (2) the rig shadow-sync (U1), (3) pre/post camera *scheduling* in `_tick`,
(4) carrying the upstream identity into the runtime plan. **Everything else REUSES** `SceneCameraInput`'s
already-tested pose composition / recoil / E2E-channel / HRP-eye math (wrapped in a clean per-player
lifecycle, not the sticky singleton). If a slice starts adding camera-pose MATH, stop — that's a
regression into rebuilding what exists.

### Test strategy — the load-bearing addition (eng-review)
Build-time signal must catch the #1 failure mode (frame ordering) WITHOUT a Studio round-trip:
1. **Shape-variance regression corpus (NEW, mandatory).** Check in the TWO real `Player.luau` shapes
   that broke the locators — the cached `dde248` (helper `_axis` + extra-yaw camera) AND the cold
   `cold3a59` (helper `_getAxis` + rig-`PivotTo` move + `Update`-cached `GetMouseDelta`) — as host-harness
   fixtures, asserted to BIND under the authority (camera follows, WASD drives the Humanoid, Shoot reads
   the live look). Every future "it broke on a new shape" adds its shape to the corpus. This is the
   regression guard the whole effort lacked (the e2e was the only net, run on lucky shapes).
2. **Pre+post camera-write assertion (host harness).** Assert the authority writes `CurrentCamera.CFrame`
   BEFORE the component pass AND re-asserts AFTER — so a regression that drops the pre-write (re-opening
   the stale-aim hole) fails a unit test, not just a cold-Studio aim check.
3. **`getLookCFrame()` stale-aim guard.** A test that Shoot's raw `self.cam.CFrame` read returns the
   SAME-frame look after the pre-write.
4. Cold-Studio e2e remains the FINAL net (camera/move/shoot/respawn on a FRESH AI shape), never the only one.

### Sequencing (strangler-fig — confirmed)
A-as-fallback in slice 1 + delete-A-last is the correct Fowler strangler-fig: C proves it dominates on a
cold shape with A still present, then A is removed. Slices are SEQUENTIAL by dependency (Gate 0 → 1 → 2 →
3 → 4); no parallelization (each slice depends on the prior's structural fact). State it: no worktree
fan-out for this effort.

### Failure modes (critical-gap audit)
- **Frame ordering** — silent if wrong, no existing test catches it. MITIGATED by Gate 0 (prove
  GetMouseDelta multi-read + pre/post camera) PRODUCING A REUSABLE host-harness test, + the pre+post
  assertion above. This is the one critical gap; it must be build-time-testable before slice 1 ships.
- **U1 rig-yaw non-authority** — add a test asserting nothing authoritative reads rig yaw.
- **Server teleport desync** — teleport is client-request/server-apply; test the round-trip.

## NOT in scope (explicitly deferred)
- Preserving game-specific movement (dash/double-jump/var-speed) — current code already discards it; an
  opt-in `setMoveIntent` hook is a separate future fidelity layer.
- Non-player cameras (drone/turret) — keep the existing strict `camera_facet` path; untouched.
- Server-authoritative MOVEMENT validation — no Roblox-default game has it; out of scope.
- A semantic (AST) verifier for "movement PivotTo" — undecidable lexically; B stays the two lexical
  rejects only.

## What already exists (reuse, don't rebuild)
- `SceneCameraInput` (runtime/scene_camera_input.luau): pose composition, recoil, E2E channel, HRP eye-
  follow, controls-off/avatar-hide, respawn-resync, `getLookCFrame`. REUSE the math; replace only the
  sticky-singleton lifetime with a per-player lifecycle.
- `has_character_controller` upstream identity (PR #182) + the autogen `GameServer` (CharacterAdded spawn,
  shot-origin validation). KEEP and consume.
- The `_tick` loop (scene_runtime.luau:2656). EXTEND with the pre/post host-player hooks.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Design (dual-voice) | Claude + Codex | Architecture adversarial | 1 | CONVERGED | 3 P1 fixed (pre/post camera, A-last, U-decision) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 0 new P1; +regression corpus, +pre/post unit assertion, scope pinned |

- **CROSS-MODEL:** U1 vs U2 — Codex (U1, preserves GetComponent/registry closure) beat Claude (U2);
  folded in. "extend vs greenfield SceneCameraInput" — reconciled (reuse math, new lifetime).
- **UNRESOLVED:** none — all six original open questions are now resolved decisions.
- **VERDICT:** DESIGN CLEARED — durable iff the frame-ordering Gate 0 produces a reusable host-harness
  test before slice 1. Ready to implement as a SEPARATE /drive run off a clean tree.
