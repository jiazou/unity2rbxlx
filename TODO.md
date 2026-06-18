## /drive run rifle-mount-diag-20260617T215229 — architectural follow-ups (2026-06-18)

- **Generic Unity camera-mounts → Roblox player/character-mounts (replicated).** [DEFERRED feature,
  product-approved 2026-06-18.] Unity mounts FPS weapons/held-tools/HUD anchors under the
  main-camera transform (`Camera.main.transform.GetChild(n)`); these should convert to a
  **server-replicated CHARACTER mount** (others see it; first-person shows it) — NOT a client-only
  camera viewmodel. Prior infra EXISTS (`converter/child_ref_resolver.py` fact +
  `converter/rifle_rig_retarget_lowering.py` resolver-injection + `camera_facet_lowering.py`) but it
  targets a CLIENT rig lookup (camera-viewmodel direction) and did NOT fire for SimpleFPS (the AI
  flattened `GetRifle` to `getLookCFrame()`, dropping the `self.weaponSlot` read the retarget keys on;
  `output Player.luau` has no `_resolveWeaponSlot`). Future fix must drive from the upstream C# fact
  (AI-independent) + add a server-side weld-to-RightHand equip handler + a client→server equip signal
  (the existing unconsumed `PlayerSetSharedFlag:FireServer` seam). Full design + open questions:
  see the run's DESIGN-camera-mount-to-player-mount.md. Own /drive run (premises→design→build→live-verify).
