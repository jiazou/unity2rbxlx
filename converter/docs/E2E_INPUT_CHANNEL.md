# E2E test input channel — workspace attributes

The `/e2e-test` skill (see `converter/.claude/skills/e2e-test/SKILL.md`)
drives gameplay verification by sending Luau to a running Roblox Studio
via the MCP `execute_luau` tool. Studio MCP's `user_mouse_input` tool
synthesises mouse motion with `Delta = (0, 0)`, which is the value the
client receives via `UserInputService:GetMouseDelta()` and
`InputChanged`'s `input.Delta` — so mouse-look code that polls
`GetMouseDelta()` (the canonical FPS pattern) cannot be exercised
through MCP at all without an alternate input source.

This document defines that alternate source. It is **not a debug-only
test seam.** It is a documented runtime input channel that always reads
nil / zero when nothing is setting it, and reads as additive mouse
delta when a test is.

## Contract

| Attribute | Owner | Type | Semantics |
|---|---|---|---|
| `workspace.E2EMouseSeq` | test sets, client reads | `number` (monotonic int) | A monotonically increasing sequence number. The client tracks its own `lastSeenSeq` and only consumes a delta when `seq > lastSeenSeq`. Re-using the same seq number is a no-op. |
| `workspace.E2EMouseDeltaX` | test sets, client reads | `number` (pixels) | Horizontal mouse delta. Added to `UserInputService:GetMouseDelta().X` for one frame. |
| `workspace.E2EMouseDeltaY` | test sets, client reads | `number` (pixels) | Vertical mouse delta. Added to `UserInputService:GetMouseDelta().Y` for one frame. |

The seq number is what makes the channel **a queue, not a mailbox**.
Without it, two consecutive identical writes (e.g.
`E2EMouseDeltaX = 400` twice) would collapse into one — Roblox
attributes coalesce repeated identical values. The client guards on
`seq > lastSeenSeq`, so:

- One bump of `seq` = one frame of injected delta consumed.
- Re-bumping `seq` with new delta values = a second injection.
- Setting deltas without bumping seq = no effect (defends against the
  mailbox-collapse risk Codex flagged).
- Production has nothing setting `seq`, so it stays nil → 0 → no path
  through the seq guard → identical behavior to the pre-channel code.

## Where it's read

Two call sites in the converter — both must stay in sync:

1. `converter/converter/scaffolding/fps.py` — the auto-generated FPS
   scaffolding's `updateCamera()`. This script ships verbatim into
   projects that match the FPS heuristic (e.g. SimpleFPS) when no
   user-authored Player controller exists.

2. `converter/converter/code_transpiler.py` — the AI system prompt's
   "Mouse look (canonical FPS pattern)" snippet. When the AI transpiles
   a Unity FPS controller into Player.luau, the emitted code follows
   this pattern and includes the seq-guarded E2E read.

If you change one site, change the other. The grep regression test in
`tests/test_fps_scaffolding_e2e_channel.py` pins the scaffolding side;
the prompt-invariant test in `tests/test_ai_system_prompt.py` pins the
AI side.

## Where it's written

By the `/e2e-test` skill's `setup_luau` blocks in
`tests/fixtures/upload_snapshots/<project>.behavior.json`. Example fixture:

```json
{
  "id": "mouse_yaw_rotates_camera",
  "setup_luau": "_G._state.look0 = cam.CFrame.LookVector; local s = (workspace:GetAttribute('E2EMouseSeq') or 0) + 1; workspace:SetAttribute('E2EMouseDeltaX', 400); workspace:SetAttribute('E2EMouseDeltaY', 0); workspace:SetAttribute('E2EMouseSeq', s)",
  "wait_seconds": 0.5,
  "assert_luau": "...",
  ...
}
```

The fixture's preamble (`_schema.preamble`) exposes a `_pumpMouse(dx, dy)`
helper that encapsulates the seq-bump-and-set so fixtures stay terse.

## Why not...

- **A debug-only build flag.** That recreates the production/test
  divergence we deleted the previous test seam to avoid. The point of
  this channel is that there is no production-vs-test code path; both
  modes flow through the same `updateCamera`.
- **Per-player attributes (`Players.LocalPlayer:GetAttribute`).** The
  client-side `updateCamera` runs in the LocalPlayer's context already,
  so per-player is technically more correct. But `workspace` attributes
  are observably more reliable across the MCP sandbox boundary, and
  mouse-look is single-player-by-construction in the SimpleFPS test
  project. If a multi-player project ever needs this, switch to
  per-player at that point.
- **A BindableEvent or RemoteEvent.** Heavier than necessary. Attributes
  are sufficient because the only consumer is the single-frame additive
  read, and the seq number provides the queue semantics events would
  give us.

## Failure modes the seq guard prevents

- **Mailbox collapse:** two consecutive identical writes silently
  becoming one. Seq forces each consumption to be a distinct frame.
- **Frame-rate races:** test sets delta then waits 0.5s; client may
  have run RenderStepped twice in that window. Without seq, the second
  RenderStepped would re-consume the same value. With seq, the second
  RenderStepped sees `seq == lastSeenSeq` and ignores it.
- **Test author errors:** forgetting to bump seq while setting deltas
  is a silent no-op rather than a confusing intermittent failure.

## The seq is session-monotonic — never reset it

`E2EMouseSeq` must climb monotonically for the **whole Play session**.
The client's `_lastE2ESeq` only ever increases and never resets, so if
a fixture's reset helper clears `E2EMouseSeq` back to 0/nil, the next
`_pumpMouse` produces `seq = 1` again — which the client has already
seen (`1 > _lastE2ESeq` is false), so the injection is silently
dropped. This bit the second mouse-look fixture in the first live run:
yaw passed (seq 0→1) but pitch failed (reset → seq back to 1, client
already at 1, ignored).

The fix: a fixture's `_reset()` may zero `E2EMouseDeltaX/Y` but must
**not** touch `E2EMouseSeq`. `_pumpMouse` then increments from the
current value, so the sequence keeps climbing 1, 2, 3, … across
fixtures and every pump is consumed exactly once.
