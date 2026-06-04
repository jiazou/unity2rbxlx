# Camera/input fidelity plan — generic-mode first-person, by construction

## The one goal
Make converted Unity first-person games behave correctly **in generic
scene-runtime mode** (the path meant to replace legacy) — concretely, the
camera must yaw *and* pitch — **without** an ad-hoc coherence pack, by giving
the first-person camera/input cluster a deterministic home: a small
scope-capped **host-runtime camera/input service** reached by a deterministic
**camera-facet lowering pass** on the generic allowlist.

## Why this shape (condensed; full reasoning in the session + design doc)
- The bug (FPS can pitch, not yaw) is a **semantic-fidelity** gap: the
  transpiled controller rebuilds `CurrentCamera.CFrame` pitch-only (yaw=0).
  Roblox's camera is a **singleton** not in the workspace tree, so the Unity
  camera-child-of-player yaw inheritance can't be expressed as nesting and is
  re-derived (wrongly) in emitted code.
- The generic verifier is **structural only** — no behavioral oracle — so this
  bug is structurally invisible; "compliance by construction" cannot catch it.
- Generic deliberately runs **no coherence packs** (`run_packs`). But it *does*
  run a permanent **deterministic lowering layer** (asset-rewrite,
  require-resolution). The boundary is *ad-hoc identity-gated repair* (a pack)
  vs *deterministic spec-owned lowering* (allowlisted) — **not** "touches
  emitted Luau." (Design doc updated to record this.)
- Therefore the fix is a deterministic, generic (structural-fingerprint-gated,
  never per-game) lowering pass that routes the camera facet to a hand-written
  runtime service — the same category as the existing allowlist passes, not a
  coherence pack. Reviewed by Claude (3 threads) + Codex (3 rounds); converged.

## Scope cap (load-bearing — do NOT cross)
The runtime service owns ONLY: camera pose composition (world-yaw ∘
local-pitch), pitch clamp, recoil/pitch API, default-controls-off + body-hide
+ spawn-snap, rig/viewmodel slot anchoring, the E2E mouse channel, and
**read-only** `CurrentCamera.CFrame` for game logic. It must **not** own WASD
translation, weapon logic, CharacterController emulation, or shooting. "Once it
owns locomotion, you're rebuilding Unity." Clean line: the service owns
**turning** (look yaw/pitch, and yawing the player rig so the body faces the
view); the controller keeps **translation** (WASD), reading the service's yaw
basis.

## Components

### C1 — Camera/input runtime service (`runtime/`, hand-written Luau)
A deterministic module the host wires on the client. Responsibilities per the
scope cap. Controller↔service API (to finalize in the slice):
- `service:configure({sensitivity, minPitch, maxPitch})`
- `service:applyRecoil(deltaPitch)`  ← recoil from Shoot
- `service:getYaw()` / `service:getLookCFrame()`  ← movement basis / raycast
- service runs its own input→camera loop (reads mouse delta + E2E channel,
  composes `CurrentCamera.CFrame`, yaws the player rig), like Roblox's own
  camera scripts. Production-safe E2E channel (no-op when unset).
The existing `CameraRigFollower` (rig→camera pivot) stays / folds in.

### C2 — Camera-facet lowering pass (generic allowlist, `contract_pipeline.py`)
Deterministic, structural-fingerprint-gated (NOT `s.name`). Method-scoped
recognize-and-splice (Codex's third seam — not whole-class replace, not a bare
host API the AI may misuse):
- Detect the camera-controller facet: binds `workspace.CurrentCamera` + the
  yaw-only `PivotTo` body turn + the pitch-only `CurrentCamera.CFrame` rebuild
  + pitch-state field.
- Splice: replace the `Awake` camera bind + the `Rotate` look-math + the
  pitch-state mutations (incl. recoil writes) with calls into the service.
  Leave `Move`, `Shoot`, raycasts, ammo, events untouched.
- Idempotent; twice-call + fires-on-real-shape + negative unit tests.

## Slices
- **A — Doc + decisions (this).** Design-doc edits (done: lowering-layer
  principle + PR8 timing resolution) + this plan + legacy-pack decision +
  scope-cap sign-off. **Gate A: Codex + Claude plan review, then user.**
- **B — Runtime service (C1).** Build + host-side tests; finalize the
  controller↔service API and the turn-vs-translate seam.
- **C — Lowering pass (C2).** Structural detection + method-scoped splice +
  unit tests; wire into the generic allowlist in `contract_pipeline.py`.
- **D — Integration + verify.** Generic SimpleFPS routes through the service;
  `/e2e-test SimpleFPS --generic` drives `mouse_yaw_rotates_camera` +
  `mouse_pitch_rotates_camera` and they PASS. Cache-based assemble — no API
  auth needed (verified workable this session). Update memory
  [[converted-fps-camera-yaw-lost]].
- **E — Retirement (= PR8 scope, likely a separate effort).** Retire the 3 FPS
  packs + `converter/scaffolding/` + `detect_fps_game`/`is_fps_game` *into* the
  service so legacy and generic converge; rewrite skill 4a/4c as plan overrides.

This effort = **A–D** (fix generic properly + the service + the pass). **E** is
the larger roadmap retirement, gated on A–D proving the seam on SimpleFPS.

## Decision: the committed legacy pack
`fps_camera_yaw_from_player_pivot` (branch `fix/fps-camera-yaw`, commits
`ef62ed7`+`1c1be36`) is a **legacy-mode** coherence pack — generic-gated by
structural fingerprint (the good kind), but in the `run_packs` layer.
- **Recommendation: keep it for legacy** (legacy is the default until PR7 and
  the post-PR7 escape hatch; it's the only thing fixing yaw for users *today*),
  **but fix its stale docstring** — the "transpiler flattens the hierarchy"
  claim is wrong (Codex-flagged); the cause is the singleton-camera re-derivation.
  Mark it explicitly "legacy-only; superseded in generic by the camera/input
  service (Slice E retires it)."
- Alternative: drop it now and accept legacy yaw stays broken until retirement.

## Verification & risks
- **Oracle:** the e2e gameplay fixtures are the behavioral net (no build-time
  behavioral check exists by design). `mouse_yaw` / `mouse_pitch` are the gates.
- **Auth:** the lowering pass + service are cache-/auth-neutral (deterministic,
  post-transpile + hand-written runtime); cache-based `assemble` builds + e2e
  verifies without the (currently 403) cold-transpile API. Only prompt-teaching
  would need auth — and we are deliberately not relying on prompt for this.
- **Risk — seam creep:** the service must not grow into locomotion/weapons.
  Guard: the scope cap above + a test asserting the service module's public API
  surface stays within the capped list.
- **Risk — detection generality:** the lowering pass must fire across
  child-camera FPS shapes without false-positives on non-FPS scripts; gate on
  the multi-signal structural fingerprint, lexer-blanked, never `s.name`.
