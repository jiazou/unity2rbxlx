# Phase 4c.1: Runtime Emission (spawners)

> **Last verified:** 2026-04-16. Cross-check `api_mappings.py` and `code_transpiler.py` before acting on prescriptions.

Write spawn/cleanup on top of transpiled managers. Inputs: `templates_manifest` and `animation_plan` from 4a, transpiled modules from 4b.

## Why this matters

Unity games generate gameplay content at runtime — spawned enemies, level chunks, procedural terrain, collectibles. **#1 system that doesn't survive transpilation**: it depends on Inspector-serialized prefab refs, Addressables, and object pooling — none have Roblox equivalents. Transpiled code keeps scoring/movement; spawn methods become empty shells with nil refs.

**Diagnostic:** game runs (score ticks) but the world is empty? Spawning wasn't ported. Check the manager's spawn arrays.

## Porting pattern

1. **Use templates from the plan.** `templates_manifest` maps each name to a Model in the container 4a.5 picked. Never auto-discover by substring.
2. **Use plan metadata.** `templates_manifest.metadata` has dimensions, sub-object positions, sub-template slots — extracted from prefab YAML by 4a.3.
3. **Replace `Instantiate()` with `:Clone()` + `Parent = workspace`.** Resolve ScriptableObject GUID refs to Template names via data modules.
4. **Cleanup.** Past a threshold, `:Destroy()` it. No object pooling needed — Roblox instance creation is fast.
5. **Wire into Heartbeat.** Spawning checks per frame, not one-time setup.
6. **Create ground only if Unity's is genuinely missing.** If Unity generates ground at runtime, port the system. Faithful port over workarounds.

## Animation on clones

Skeletal / character animation is **not supported** (see `docs/UNSUPPORTED.md`):
a Unity skinned mesh becomes a single rigid `MeshPart`, and there is no
animation runtime. Transform-only `.anim` clips are converted to inline
TweenService `Anim_*` scripts by `animation_converter.py` and need no
per-clone wiring. Humanoid clips are surfaced to `UNCONVERTED.md`.

## Particle emission

For systems flagged `burst` in `animation_plan.particle_systems`, wire `emitter:Emit(burstCount)` at gameplay moments (collection sparkles, death effects). Sub-emitter wiring: `runtime/sub_emitter_runtime.luau`.

## Scene object classification

Pipeline places non-prefab scene objects into Workspace. Prefab instances are auto-excluded (they live in Templates).

- **Menu/UI scene objects** (title backdrops, menu cameras, preview platforms) → hide via `SetActive(obj, false)`. Identify by name patterns ("Menu", "UI", "Background", "Title"); confirm against `.unity` YAML.
- **Gameplay environment** → keep visible. Decoration positions are baked.
- **Broken visual artifacts** (white boxes, gray rectangles) → remove from Workspace and Templates.

Hide by **known name list**, not broad patterns that could catch gameplay objects.
