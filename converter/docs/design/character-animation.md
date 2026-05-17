# Character / Skeletal Animation — Closed

**Status:** Closed 2026-05-17. Skeletal/character animation is **not feasible**
for an automated Unity→Roblox converter; the work was retired. This document
records *why*, so the dead end is not re-attempted blind. For the user-facing
limitation see [`../UNSUPPORTED.md`](../UNSUPPORTED.md) § "Skeletal / character
animation — NOT supported".

---

## Outcome

A four-PR plan to finish skeletal/character animation was scoped, started, and
then abandoned when a platform investigation showed the goal is unreachable
through any automated path:

- **PR1 — landed** (merged, PR #94). Renamed `animator_runtime.luau` →
  `character_animator.luau` and retired the redundant
  `generate_state_machine_script`. Cleanup.
- **PR2 — landed** (merged, PR #95). Wired the `CharacterAnimator` tween
  backend end-to-end — working code, but it animates an *invisible* skeleton
  (see below).
- **PR3 / PR4 — cancelled**, never started.
- **Retirement** (PR #98). The `CharacterAnimator` runtime, the
  `AnimationData_*` module emission, the per-controller bootstrap codegen, the
  runtime-module injection, and the imperative-`Animator.*` dispatch were all
  deleted. Humanoid/skeletal animation clips are now surfaced to
  `UNCONVERTED.md`.

What survives and works, untouched: **transform/property animation** — doors,
moving platforms, rotating props — via `generate_tween_script` → inline
`Anim_*` `TweenService` scripts.

---

## Why it is not feasible

Two independent blockers, established from Roblox documentation and a codebase
audit.

### 1. The converted rig cannot deform

A Unity `SkinnedMeshRenderer` is a single mesh skinned to a bone hierarchy.
The converter turns it into:

- one **rigid `MeshPart`** — the whole mesh, undeformable; and
- a skeleton of **invisible transparent `Part`s + `Motor6D`s**
  (`component_converter.py:convert_skinned_mesh_renderer`,
  `rbxlx_writer.py:_make_bone_parts_and_motor6ds`)

which is **not bound to the mesh geometry**. Animating that skeleton — by any
backend — moves invisible Parts; the visible character never deforms. The
converter has no Roblox `Bone`-based skinning path and never had one.

### 2. There is no automated way to build a skinned mesh

A mesh that deforms via `Bone` instances needs skin weights baked into the
`MeshPart` asset. As of 2026, Roblox produces those **only** through the
interactive **Studio 3D Importer**:

- Open Cloud asset upload of `.fbx`/`.glb` yields a `Model` of plain rigid
  `MeshPart`s.
- `AssetService:CreateMeshPartAsync` exposes only fidelity options — no rig.
- `EditableMesh` has skinning-shaped APIs (`AddBone`, `SetVertexBoneWeights`)
  but no documented path to a reusable skinned `MeshPart` asset.
- The only adjacent automation (`AvatarCreationService:AutoSetupAvatarAsync`,
  `WrapLayer.AutoSkin`) is avatar-specific, not generic mesh authoring.

A batch converter (`u2r.py`) cannot drive the Studio 3D Importer, so there is
no automated path to a deformable skinned character. Native vs. tween playback
was therefore moot: with a rigid mesh, neither backend produces visible
character animation.

---

## What would reopen this

Only a Roblox platform change would — specifically, one of:

- an Open Cloud / Luau API that imports a rigged `.fbx`/`.glb` into a skinned
  `MeshPart` with bones and skin weights; or
- a runtime API that binds `Bone` instances to an existing mesh's vertices
  (e.g. `CreateMeshPartAsync` preserving an `EditableMesh` rig).

Until then, skeletal-character animation stays unsupported. Revisit only with
a working demo of automated skinned-`MeshPart` creation as the hard gate.

---

## Review trail

- `/plan-eng-review` + Codex outside voice on the original 4-PR plan.
- Codex review of PR2 (shaped the registry/attribute design; surfaced 2 bugs
  in the implementation).
- Codex passes on PR3 scope, on the "skinned-MeshPart pipeline" option, and a
  research spike on automated skinned-mesh creation — all concluding no
  automated path exists.
- Codex review of the retirement.

The original problem statement and the abandoned 4-PR plan are preserved in
this file's earlier git revisions for anyone investigating the platform gap.
