# Phase 4a.5: Storage Classification

> **Last verified:** 2026-04-16. Cross-check `converter/converter/storage_classifier.py` before acting on prescriptions.

Unity has no networking model. Roblox replicates between server and client — every module and template needs an explicit container. Read after 4a.1–4a.4; this phase consumes their outputs.

## Three questions, per script and per template

1. Does the **server** need this?
2. Does the **client** need this?
3. Do **both** need this?

| Container | Server | Client | Typical contents |
|---|---|---|---|
| `ServerScriptService` | runs | — | Authoritative game logic |
| `ServerStorage` | ✓ | — | Server-only modules / templates |
| `ReplicatedStorage` | ✓ | ✓ | Shared modules, templates, RemoteEvents, `_Data` |
| `ReplicatedFirst` | ✓ | ✓ (pre-replication) | Loading screen, splash |
| `StarterPlayerScripts` | — | runs (per player) | Client controllers, UI |
| `StarterCharacterScripts` | — | runs (per character) | Character-attached LocalScripts |
| `StarterGui` | — | cloned to PlayerGui | ScreenGuis (but prefer ReplicatedStorage with Enabled=false) |

## Rules (first match wins)

### Scripts

| Signal | Container |
|---|---|
| Module `require`'d by any LocalScript | `ReplicatedStorage` |
| Module `require`'d only by Scripts | `ServerStorage` |
| Module `require`'d by both | `ReplicatedStorage` |
| Client-only API (`UserInputService`, `LocalPlayer`, `Camera.CurrentCamera`, `Mouse`) | `StarterPlayerScripts` |
| Attached to player character (per scene wiring) | `StarterCharacterScripts` |
| Name hint `*Loading*` / `*Boot*` / `*Splash*` AND runs pre-replication | `ReplicatedFirst` |
| Forced client-side by 4a.2 divergence | `StarterPlayerScripts` |
| Otherwise | `ServerScriptService` |

### Prefab templates

| Signal | Container |
|---|---|
| `:Clone()`'d by any LocalScript | `ReplicatedStorage/Templates` |
| Referenced only by server scripts | `ReplicatedStorage/Templates` (default) |
| Name hint `Admin*` / `Secret*` / `Server*`, OR referenced only by `ServerStorage` modules | `ServerStorage/Templates` |
| UI template (Canvas prefab) | `ReplicatedStorage/UITemplates` |

**Why default server-spawned prefabs to ReplicatedStorage:** Roblox replicates server-parented clones automatically, so ServerStorage isn't required for server-only spawning. ReplicatedStorage doesn't break later when client code wants the template for prediction or UI. Use ServerStorage only when the template must be hidden from clients.

### Remotes / Bindables / Assets

- RemoteEvents, RemoteFunctions → **always** `ReplicatedStorage`.
- BindableEvents → co-located with their callers.
- Assets (meshes, textures, audio) are referenced by URL, not parented. The instances that reference them (SurfaceAppearance, Decal, Sound) follow their parent.

## Ambiguity rule

**When in doubt, prefer `ReplicatedStorage` over `ServerStorage`.** Misplacing into ReplicatedStorage degrades security (a client sees something it shouldn't); misplacing into ServerStorage breaks the game (`:WaitForChild` hangs forever).

## Review

The classifier (`converter/converter/storage_classifier.py`) emits a proposed `storage_plan`. Review:

- **ServerStorage modules** — real security reason? If not, move to ReplicatedStorage.
- **Mixed-API scripts** — call graph is lying; split into two modules before transpile.
- **Templates with mixed-trust callers** — pick the most permissive container.

## Structured overrides (per-project)

`scene_runtime.domain_overrides` is the single override surface the contract pipeline (`--scene-runtime=generic|auto`) currently honors round-trip. Use it instead of hand-editing transpiled `.luau` between 4b and 4c — entries survive resume/rebuild.

### `scene_runtime.domain_overrides`

Per-MonoBehaviour-class `client`/`server` pins for the contract pipeline (Piece 4). Shape: `{ script_id: "client" | "server" }`. **Sticky** — the `plan_scene_runtime` phase's merge logic preserves operator-set overrides across re-runs (see `Pipeline._classify_storage`). Use to:

- Resolve an intra-class instance-domain conflict (multi-context class that the domain classifier fails-closed on without an override).
- Pin a low-confidence-classified class flagged in the `scene_runtime.low_confidence_modules` list.

The displaced-instance report (`scene_runtime.displaced_instances`) enumerates which instance hosts moved as a consequence — review before signing off.

### `storage_plan.overrides_applied` (NOT yet sticky)

`StoragePlan` declares `overrides_applied: [{script, from, to, reason}]` and the classifier emits it as a *report* of moves it applied, but the pipeline does NOT yet ingest operator-edited entries — `_classify_storage` rebuilds the plan from scratch on every `write_output` pass and overwrites `conversion_plan.json`. The wiring gap is tracked in `converter/TODO.md` (P1, "Phase 4a.5 agent-override ingestion is unimplemented"). Until that lands, expect manual `storage_plan` edits to be discarded by the next `assemble`; prefer the `scene_runtime.domain_overrides` path for any override that has a domain-classification equivalent.

## Output

```
storage_plan:
  server_scripts:           [-> ServerScriptService]
  client_scripts:           [-> StarterPlayerScripts]
  character_scripts:        [-> StarterCharacterScripts]
  replicated_first_scripts: [-> ReplicatedFirst]
  shared_modules:           [-> ReplicatedStorage]
  server_modules:           [-> ServerStorage]
  replicated_templates:     [-> ReplicatedStorage/Templates]
  server_templates:         [-> ServerStorage/Templates]
  ui_templates:             [-> ReplicatedStorage/UITemplates]
  remote_events:            [-> ReplicatedStorage]
  overrides_applied:        [{script, from, to, reason}]

scene_runtime:
  modules:                  {script_id -> {domain, container, module_path}}
  domain_overrides:         {script_id -> "client" | "server"}  # sticky
  low_confidence_modules:   [script_id, ...]
  displaced_instances:      [{class, instance, displaced_side}, ...]
```

Phase 4b reads `storage_plan` and emits each script with a `parent_path` hint. `rbxlx_writer.py` routes by `parent_path` when present, else by `script_type`. The contract pipeline's domain classifier reads `scene_runtime.modules` and applies `domain_overrides` over its inference.
