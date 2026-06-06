# Scene-runtime topology authority

**Scope:** this is the **deployment-topology authority** — the implementation-of-record for relations
#5/#6/#7 (domain, script class, container, cross-domain edges, shared-flag channels) in the durability
architecture. Deliberately NOT a general semantic IR (see §Goal/Non-goals). The whole-architecture map
and the gameplay-semantic relation catalog live in the umbrella
[`generic-converter-architecture.md`](generic-converter-architecture.md).

**Status (git truth, upstream/main as of 2026-06-03):** Phases 1, 2a, and the Phase 2b *core*
(`shared_flag_channels.py`, `cross_domain_edges.py`, `edge_enrichment.py`) merged; the Phase 3 **contract
verifier** (`contract_verifier.py`, checks A/B/C fail-closed) merged. **Pending:** the Phase 2b *tail* —
retiring the legacy `pickup_remote_event_server` pack + dependents. Per-slice status, the full phase plan,
testing, sequencing, and the revision history live in
[`scene-runtime-execution-log.md`](scene-runtime-execution-log.md).
**Owner:** unity2rbxlx converter team.
**Related:** [`generic-converter-architecture.md`](generic-converter-architecture.md) (the umbrella /
durability north star — read it first), `scene-runtime-contract.md` (the runtime mechanism this layer
implements), `scene-runtime-domain-signals.md` (the domain classifier = `module_domain`),
[`scene-runtime-execution-log.md`](scene-runtime-execution-log.md) (phase ledger + history), GitHub
issue #146, and the three live-playtest bugs that motivated this plan (Player.Move on Part / HudControl
missing service-getter / door visual not opening).

## Goal

**Make `scene_runtime_topology/` the sole authority over deployment-affecting
decisions for every script the converter emits. Make every downstream emitter
a structurally bound consumer with no independent decision authority over
those fields.**

Today the converter independently decides:

- which domain a module runs in (`scene_runtime_domain.py`)
- where that module's script lands (`storage_classifier.py`, but also
  `scene_runtime_domain._stamp_container_and_path` /
  `_apply_reachability_rule`, and a re-derivation inside
  `pipeline._classify_storage`) — historical context as of pre-Phase-2a;
  slice 6 split `_apply_reachability_rule` into
  `derive_reachability_requirements` + `finalize_topology_containers`,
  and slice 11 deleted the now-dead original implementation
- what class the script gets (Script / LocalScript / ModuleScript — split
  across `storage_classifier`, `animation_converter`, and
  `code_transpiler._classify_script_type` plus a generic-mode override)
- where animation scripts live (always server `Script` via
  `animation_converter.py`, with no consultation of the driver's domain)
- cross-domain edges (`scene_runtime_domain.compute_cross_domain_edges`
  records them, but no consumer enforces communication shape)

Codex's review made the live split-brain concrete: `classify_scene_runtime_domains()`
decides `domain`, then `_apply_reachability_rule()` mutates
`parent_path/container/module_path` again; `_subphase_inject_scene_runtime()`
recomputes `cross_domain_edges`; `convert_animations()` independently decides
prefab-vs-scene animation placement. *Multiple authorities deciding
overlapping facts is non-reproducible builds, hidden divergence between
emitted Luau and the embedded plan, and host/runtime mismatches that contract
checks catch too late.*

The result: silent contract violations between layers. The user-visible
symptom is the door-visual bug, but the same architectural shape produces
the Anim_HostilePlane, Anim_PlaneHolder ×3, and the duplicated Anim_*
emissions — and will produce more bugs of the same family for any future
client-driven Animator pattern.

The fix is structural: ONE authority decides the topology; every other
phase is bound by it.

### Non-goals

- Owning C#→Luau translation semantics. That stays in `code_transpiler.py`
  + `_AI_SYSTEM_PROMPT`.
- Owning scene-graph hierarchy. That stays in `scene_converter.py`.
- Owning structural facts about which modules exist + their dependencies.
  That stays in `scene_runtime_planner.py` (which the topology layer
  consumes as read-only input).
- Becoming a general semantic IR. Codex's earlier review explicitly warned:
  *"if you let it become a giant omnibus IR, you risk freezing bad
  abstractions too early."* Scope is **deployment topology only**: domain,
  script class, container, lifecycle role, cross-domain edges, animation
  routing. Nothing else.
- **Owning every runtime cross-domain WRITE.** (Scoping clarified 2026-06-01
  after the Phase 2b empirical finding — see `scene-runtime-execution-log.md` §Phase 2b.) Topology
  owns the cross-domain *channel contract*: for STATIC couplings (a
  serialized component reference), the per-edge bridge; for DYNAMIC shared
  state (attribute names computed at runtime, e.g. `"has" .. itemName`,
  routed through one funnel RemoteEvent), the *channel* (its presence, the
  set of literal names READ across the boundary, and the canonical store the
  funnel writes). Topology CANNOT own each individual runtime write — those
  names don't exist until runtime. The funnel is an irreducible runtime
  mechanism topology GATES + RECORDS and Phase 3 VERIFIES; it does not
  emit it per-write.

## Background: storage ≠ domain (concrete cases)

Storage classification is NOT a 1:1 derivation from domain. The mapping is
multi-input and deterministic, but not trivial. Concrete cases from this
codebase:

1. **ModuleScripts in `ReplicatedStorage` are requireable by either side.**
   Storage is neutral; execution context is determined by the caller. Today
   `Player.luau` is client-domain but lives in `ReplicatedStorage` because
   `SceneRuntimeClient` (the LocalScript in `StarterPlayer.StarterPlayerScripts`)
   requires it. Same container, different execution context.

2. **`HudControl.luau` and `GameManager.luau` both live in `ReplicatedStorage`.**
   Two client-domain ModuleScripts share a container. The choice of
   `ReplicatedStorage` vs `ServerStorage` depends on who needs cross-process
   reach, not on domain alone.

3. **Server-domain ModuleScript exposing shared types/constants.** Domain =
   server (lifecycle owned by server), but container = `ReplicatedStorage`
   so client code can also require for type references. Domain ≠ container
   by design.

4. **`ReplicatedFirst` is a placement HINT based on intent (loader/splash).**
   A client-domain LocalScript that loads bootstrap assets → `ReplicatedFirst`.
   A client-domain LocalScript that isn't a loader → `StarterPlayerScripts`.
   Same domain, different container, driven by intent.

5. **`StarterCharacterScripts` vs `StarterPlayerScripts`** are both
   client-domain. The split depends on scene wiring (was the script
   attached to the player character prefab in Unity?). Not derivable from
   domain.

6. **Cross-domain bridge = 4 coordinated scripts in 4 containers.** ONE
   semantic decision (server-authoritative state for X) → FOUR storage
   placements: client-side caller in `ReplicatedStorage`, server-side
   bridge listener in `ServerScriptService`, the RemoteEvent in
   `ReplicatedStorage`, and an animation listener somewhere. Per-script
   placement is the storage layer's job; the four-script grouping is a
   topology fact.

**So the storage mapping is many-to-many between domain and container, and
multi-input.** `script_storage.py` retains genuine multi-input decision
logic — not a dictionary lookup. What it does NOT do is re-classify domain
or re-derive what topology already decided.

## Architecture

### Producer/consumer boundary

```
scene_runtime_planner.py        ← STRUCTURAL TRUTH; runs first
                                  Produces: modules, instances, references,
                                  prefab/scene ownership, normalized
                                  module dependency graph.
                                  STAYS SEPARATE from topology.
        │
        ▼  (planner output is read-only input to topology)
        │
scene_runtime_topology/         ← TOPOLOGY AUTHORITY; runs during transpile
├── build_topology.py           ← coordinator: assembles + validates artifact;
│                                 single orchestration entry point
├── module_domain.py            ← C# signal detection + per-module domain
│                                 classification (today's scene_runtime_domain
│                                 minus the misplaced storage mutations)
├── animation_routing.py        ← per-animation driver-edge resolution +
│                                 domain inheritance from driver
├── cross_domain_edges.py       ← edge enumeration + bridge_group_id
│                                 assignment + bridge resolution metadata
└── lifecycle_roles.py          ← derive lifecycle_role (closed enum) per
                                  module from domain + class + intent hints

ARTIFACT EMITTED per module:    {domain, script_class, lifecycle_role,
                                 bridge_group_id?, animation_driver_ref?}
ARTIFACT EMITTED per anim:      {stable_id, driver_module_guid, domain,
                                 lifecycle_role, observed_attribute,
                                 bridge_group_id?}
ARTIFACT EMITTED globally:      cross_domain_edges (FLAT Class-1 bridges) +
                                 shared_flag_channels (Class-2 funnel fact) + caller_graph
        │
        ▼  (topology artifact is read-only input to Phase 4B)
        │
Phase 4B consumers (structurally bound; NO independent topology decisions):
├── script_storage.py           ← BOUND DETERMINISTIC MAPPER; consumes
│                                 topology + caller_graph + character_attached
│                                 + name_hints; outputs specific Roblox
│                                 container per script. Retains real
│                                 multi-input decision logic. DOES NOT
│                                 re-classify domain.
├── animation_converter.py      ← rewrite to read animation_routing for
│                                 placement + emission shape
├── code_transpiler.py          ← Class-1 (component-ref) only: domain +
│                                 cross_domain_edges. Class-2 (shared-flag)
│                                 is NOT a transpiler rewrite — the funnel
│                                 stays (see 2026-06-01 reframe); topology
│                                 gates+records it.
└── contract_verifier.py        ← Phase 3 verifier (NEW module; runs as a POST-
                                  MATERIALIZE hook after _build_and_apply_topology —
                                  NOT contract_pipeline.py, which is the transpile-time
                                  orchestrator); enforces consumers obeyed the artifact

DELETED (logic absorbed by topology):
- storage_classifier._scripts_with_client_apis  (NB: slice 7 R3 RESTORED
  these as FALLBACK-PATH-ONLY infrastructure; the topology decision tree
  at _decide_script_container_from_topology consumes TopologyInputs.domains
  exclusively and never reads the regex sets. They are reached only when
  topology_inputs is None / sid lookup misses / the slice-6
  unconstrained-helper gate sends a ModuleScript to legacy fallback.)
- storage_classifier._scripts_with_server_apis  (same fallback-only contract
  as the client counterpart above)
- pipeline._classify_storage's classifier block (mis-layered re-derivation)
- scene_runtime_domain._stamp_container_and_path's AUDIT-SIGNAL WRITES
  (the parallel ``signals["reachability_forced_container"] = ...`` write
  was retired in slice 10; the function itself is retained as the canonical
  container + module_path writer for finalize_topology_containers --
  ``_stamp_container_and_path`` is still LIVE at module_domain.py:880/886/891)
- animation_converter's hardcoded ServerScriptService routing
- code_transpiler._classify_script_type's generic-mode override
```

### The topology artifact

Single artifact persisted into `conversion_plan.json`'s `scene_runtime`
block. Frozen and schema-checked at emit time.

```python
{
  "modules": {
    "<guid>": {
      "stem": "Door",
      "domain": "client" | "server" | "helper" | "excluded",
      "script_class": "ModuleScript" | "Script" | "LocalScript",
      "lifecycle_role": "auto_run" | "requireable" | "loader"
                        | "character_attached" | "bridge_listener"
                        | "scene_entrypoint",          # closed enum
      "bridge_group_id": "<edge_id>" | null,           # set when this module
                                                       # is part of a bridge
      "provenance": {
        "source_path": ".../Door.cs",
        "source_span": [12, 87]
      }
    }
  },

  "animation_drivers": {
    "<stable_id>": {
      "stable_id": "Door:door:open",  # <prefab>:<target_name>:<clip_name>
      "driver_module_guid": "<door.cs guid>",
      "domain": "client",              # INHERITED from driver
      "script_class": "Script",        # or LocalScript depending on domain
      "lifecycle_role": "auto_run",
      "observed_attribute": "open",
      "observed_target": {
        "kind": "sibling" | "child" | "descendant",
        "name": "door",
        "scope": "self.gameObject.Parent"
      },
      "bridge_group_id": "<edge_id>" | null
    }
  },

  # Phase 2b Class-1 (static component-ref). SHIPPED SHAPE IS FLAT — the nested
  # producer{}/consumer{} restructure once sketched here was deferred INDEFINITELY
  # (cross_domain_edges.py docstring: "every consumer reads from_*/to_* today").
  "cross_domain_edges": [
    {
      "id": "<deterministic_edge_id>",            # also the bridge_group_id
      "kind": "attribute_write",
      "from_instance": "...", "to_instance": "...",
      "from_script": "Door", "to_script": "Anim_Door_door_open",
      "from_domain": "client", "to_domain": "server",
      "field": "open",
      "owner_kind": "...", "owner_ref": "...",
      "resolution": { "strategy": "remote_event_bridge", "event_name": "Door_SetOpen" },
      "bridge_member_scripts": [ /* caller / server_listener / anim_listener */ ],
      "payload": { "attribute_name": "open", "schema": "bool" }
    }
  ],

  # Phase 2b Class-2 (dynamic shared-flag funnel) — ONE channel fact, recompute-only
  # (produced fresh from the reader scan each run; never read back as authoritative).
  "shared_flag_channels": {
    "PlayerSetSharedFlag": {
      "read_names": ["hasKey", "..."],            # literal flags read cross-domain
      "reader_domains": ["server"],
      "canonical_stores": ["Player", "Character"],# CONSTANT — what the funnel writes
      "present": true                             # the gate: funnel injected iff true
    }
  },

  # Phase 2a slice 3 — curated INCOMING dependency view (script_id → its requirers).
  "caller_graph": { "<script_id>": ["<requirer_script_id>"] }
}
```

> **Precise schema = the code, not this sketch** (thin-index discipline). The authoritative
> field-by-field shapes are the TypedDicts `TopologyArtifact` (`scene_runtime_topology/build_topology.py`)
> and `CrossDomainEdge` / `SharedFlagChannels` (`scene_runtime_topology/cross_domain_edges.py`,
> `shared_flag_channels.py`). This block is illustrative; if it disagrees with those TypedDicts, the
> TypedDicts win.

**Invariants enforced by `build_topology.py` at emit time:**

1. Every `animation_drivers[*].driver_module_guid` resolves to a `modules`
   entry; the animation's `domain` matches the driver's.
2. Every `cross_domain_edges[*]` has defined `from_domain` + `to_domain`, and
   `resolution.strategy ∈ {remote_event_bridge, same_domain_no_bridge, excluded}`.
3. Every `Anim_*` script in the planned output corresponds to exactly ONE
   `animation_drivers` entry (no duplicates; structural via `stable_id`).
4. Every `lifecycle_role` is in the closed enum.
5. Every `bridge_group_id` in `modules` or `animation_drivers` refers to
   an existing `cross_domain_edges[*].id`.

Failures here ABORT the build with the offending input row + the
violated invariant. No warnings.

### `script_storage.py` — bound deterministic mapper

Consumes topology artifact + structural inputs. Decides specific Roblox
container per script. Hard constraints enforced AFTER decision (not mixed
into scoring).

**Inputs:**
- From topology: `domain`, `script_class`, `lifecycle_role`,
  `bridge_group_id`
- From planner: normalized caller graph (module dependency edges) — topology
  exposes this as a curated view; `script_storage` does NOT re-derive
  graph shape
- From scene_converter: `character_attached` flag (which scripts are
  attached to the player character prefab)
- From `code_transpiler`: ReplicatedFirst-name hints (loader/splash naming
  conventions)
- From topology: `entrypoint_kind` / `autorun_origin` flag (top-level scene
  entrypoint vs incidental auto-run script — added per Codex round-4)

**Decision tree (deterministic, first-match-wins, no re-classification):**

This ordering was reconciled across Claude + Codex review during the slice 6/7
arch-review cycle (2026-05-29). The previous draft of this tree referenced a
phantom `requires_server_storage_isolation` field on `TopologyModuleEntry`
that does not exist — under the literal-as-drafted tree, NO server-only
ModuleScript would have ever landed in `ServerStorage`, making the SS branch
dead code and the data-flow analysis cosmetic. The corrected rule is: trust
the analysis. If `topology_inputs` says a ModuleScript has only server-domain
callers, place it in `ServerStorage`. The classifier respects its inputs.

```python
def assign_container(script, topology_facts, structural_inputs):
    # 1. character_attached wins outright — Unity-character-prefab scripts
    #    only make sense in StarterCharacterScripts.
    if topology_facts.lifecycle_role == "character_attached":
        return STARTER_CHARACTER_SCRIPTS

    # 2. Loader intent → ReplicatedFirst. Never inferred from client-domain
    #    alone; only when topology stamped lifecycle_role == "loader".
    if topology_facts.lifecycle_role == "loader":
        return REPLICATED_FIRST

    # 3. Reachability-required container (e.g. a Player module reachable from
    #    StarterPlayerScripts must live in ReplicatedStorage). The sentinel
    #    "__excluded__" routes excluded-domain modules to ReplicatedStorage
    #    so they stay parseable but never auto-run.
    if topology_facts.reachability_required_container is not None:
        return topology_facts.reachability_required_container

    # 4. ModuleScript by caller-domain (consumes topology_inputs.caller_graph
    #    via topology_facts).
    if script.script_class == "ModuleScript":
        caller_domains = topology_facts.caller_domains  # already resolved
        if "client" in caller_domains:
            # Any client caller → ReplicatedStorage (cross-process reach).
            return REPLICATED_STORAGE
        if caller_domains == {"server"}:
            # Server-only ModuleScripts → ServerStorage. Faithful to the
            # domain analysis. NO phantom guard; if topology says
            # server-only, we trust it.
            return SERVER_STORAGE
        # Orphan / unknown → conservative default.
        return REPLICATED_STORAGE

    # 5. Auto-run script classes.
    if script.script_class == "LocalScript":
        return STARTER_PLAYER_SCRIPTS
    # script.script_class == "Script"
    return SERVER_SCRIPT_SERVICE

def enforce_constraints(script, container):
    # Hard constraints AFTER decision (not mixed into the tree).
    if script.script_class == "LocalScript" and container == SERVER_SCRIPT_SERVICE:
        raise ConstraintViolation(...)  # would never run
    if container == REPLICATED_FIRST and script.script_class == "ModuleScript":
        raise ConstraintViolation(...)  # ReplicatedFirst is for executable scripts
    # ... and other invariants ...
```

**Note on `character_attached`:** previously modeled as a `structural_inputs`
flag. Slice 5 moved it onto `lifecycle_role` (closed enum value
`"character_attached"`), so the decision tree consults topology only — no
out-of-band structural flag needed for this branch.

What MOVES OUT to topology:
- `_scripts_with_client_apis` regex (duplicates `module_domain` signal
  detection) — **restricted to fallback path only** (slice 7 R3). The
  topology decision tree never reads it; it is consumed only by
  `_decide_script_container_legacy` when the slice-6 unconstrained-
  helper gate (`storage_classifier.py:575-587`) sends a ModuleScript
  to the legacy six-rule path on no-transpile resume.
- `_scripts_with_server_apis` regex — same fallback-only contract
- Implicit re-derivation of domain via API analysis — deleted

What STAYS in `script_storage.py`:
- The decision tree above
- Caller-graph traversal (using the curated dependency graph topology exposes)
- Constraint enforcement (LocalScript-in-SSS validation, ReplicatedFirst
  class check, etc.)
- Name-hint loader detection (combined with topology's lifecycle_role hint)

## Roblox-dead module handling (TODO #8)

Some Unity modules are **Roblox-dead**: rendering / shader / camera-effect
helpers (the SimpleFPS water cluster — `WaterBase`, `Displace`,
`PlanarReflection`, `WaterTile`, `SpecularLighting`, `GerstnerDisplace`, …)
whose transpiled body does nothing executable in Roblox. Left alone they were
misrouted into `ServerStorage` by the caller-domain rule (their only callers are
server-default leaf Scripts → `caller_domains == {"server"}`), shipping dead
code in the wrong container. See the grounding facts + LOCKED DECISIONS in
[`roblox-dead-module-routing-brief.md`](roblox-dead-module-routing-brief.md).

**Detector (generic, no game-specific names) — `converter/roblox_dead_modules.py`.**
Definition **D3 (both-agree) + HARD VETO**:
- *Input prior (weak):* the fraction of the module's C# API references that
  resolve to a REAL (non-stub) mapping in `API_CALL_MAP` ∪ `TYPE_MAP`
  (`-- no equivalent` comment stubs AND absent entries both count as unmapped).
  "Dominated by unmapped" (real coverage ≤ ~0.49) ⇒ dead-leaning. The surface
  excludes `using`/`namespace` directives, the module's own class name, and
  structural/lifecycle tokens, so a trivial empty MonoBehaviour abstains.
- *Output confirmation (decisive):* the **post-coherence** Luau body is inert —
  only class-table boilerplate / comments / `print` / empty lifecycle handlers /
  `require`s, with no genuine Roblox effect. Uses the converter's own
  deterministic stub markers as strong signals (structural, not regex-on-AI).
- *Hard veto:* any single genuine Roblox effect (`Instance.new`, `.Parent =`, a
  real property write, a RemoteEvent/BindableEvent fire, a DataStore/service
  mutation, a genuinely-mapped call) ⇒ NOT dead, regardless of fraction. The
  converter-injected `PrimaryPart`/`script.Parent` guard idiom is excluded.

A module is dead iff input agrees AND output inert AND no veto.

**Pass placement.** `pipeline._subphase_analyze_dead_modules` runs **between
`_subphase_cohere_scripts` and `_classify_storage`** (the post-coherence Luau —
the decisive output signal + the injected require edges — only exists there). It
is eligible only for ModuleScripts whose transpile strategy is `ai`/`stub`
(deterministic): a `rule_based`/`hybrid` fallback can emit an inert TODO-skeleton
for a REAL gameplay module, so its inertness is not trusted. The verdict is a
TRANSIENT `PipelineState.dead_modules` (never persisted; abstains entirely on a
no-transpile resume, where the storage plan was already computed by the
transpiling run — honoring the recompute-only rule below).

**Routing consumer (B).** `classify_storage(dead_modules=…)` routes a dead
ModuleScript to `ReplicatedStorage` regardless of caller-domain, in **both** the
topology path (`_decide_script_container_from_topology`) and the legacy path
(`_decide_script_container_legacy`, whose `"…server-side callers"` reason text is
the cached symptom). The dead body is already an inert stub, so SceneRuntime
applies no effect; RS keeps it reachable for any surviving requirer.

**Prune consumer (A) — closure safety.**
`pipeline._subphase_prune_dead_module_closures` DROPS a dead module from
`rbx_place.scripts` (+ `_delete_pruned_script_from_disk`) **only when its entire
require-closure is also dead** — no live (non-dead) module requires it. The
closure is computed from the **FINAL EMITTED LUAU** injected-require edges
(`RS:FindFirstChild(name) or SS:FindFirstChild(name)`), NOT `dependency_map`
(which misses post-transpile injected requires). A dead-but-live-required module
stays emitted and falls back to the B reroute (so no surviving `require()`
becomes `require(nil)`, per the brief's GF8). Runtime-bearing generic-mode
components are never pruned (would dangle a `SceneRuntimePlan` row) — they stay
inert. Geometry is untouched (built in separate scene_converter branches), so
pruning the scripts causes no visual regression.

## Persistence rule: save raw facts; always recompute conclusions

This rule was adopted during the slice 6 review cycle (2026-05-29) after four
rounds of whack-a-mole staleness bugs — operator input changes between runs
silently invalidated a persisted derived artifact, and the consumer had no way
to detect it.

**The rule:**

- **PERSIST raw facts.** Things that come from outside the converter and cannot
  be recomputed from current inputs, or things that come from an expensive
  one-shot process that runs at a specific pipeline stage:
  - `RbxScript.intrinsic_script_type` (slice 5) — stamped at transpile time.
  - Uploaded asset IDs.
  - Mesh hierarchies (from Roblox `LoadAsset`).
  - Transpile cache (C# → Luau).
  - `caller_graph` — **explicit exception, see below.**
- **DO NOT PERSIST derived conclusions.** Anything that is a deterministic
  function of current inputs + raw facts:
  - `topology_inputs` (all of: `domains`, `reachability_requirements`,
    `lifecycle_roles`, `script_id_by_name`).
- **ALWAYS RECOMPUTE on resume.** `topology_inputs` is recomputed from current
  operator inputs + intrinsic facts on every pipeline entry. The
  "assemble-no-retranspile" case yields an empty `reachability_requirements`
  — this is the same trade slice 3 already accepts, and the
  unconstrained-helper fallback (below) handles it gracefully.

**Why `caller_graph` is the explicit exception.** `caller_graph` depends on the
transpile-time dependency_map. On a no-transpile resume that map is absent, so
recomputing would yield an empty graph. Preservation yields strictly better
fidelity than always-recompute for this one structure. The rule allows it as a
named exception, not as a precedent: any future "persist this derived value
because it's expensive" proposal must clear the same bar (cannot be recomputed
without a transpile-stage input, and preservation strictly dominates
recomputation in fidelity).

### `TopologyInputs` shape — `transpile_ran: bool`

`TopologyInputs` carries a `transpile_ran: bool` raw fact set from
`state.transpilation_result is not None`. This is rule-safe (it's a raw fact
about pipeline execution, not a derived conclusion) and lets the consumer
distinguish two structurally-identical "empty `reachability_requirements`"
cases:

1. `transpile_ran is False` (no-transpile resume): empty is expected. Fall
   back to legacy six-rule path for any script not covered by topology.
2. `transpile_ran is True`: empty is a real classification bug. Raise/log.

### Unconstrained-helper fallback contract

When `topology_inputs.reachability_requirements[sid]` is absent **AND**
`topology_inputs.transpile_ran is False`, fall back to the legacy six-rule
path for **that script only** — not the whole pipeline. This is the Codex
amendment from the slice 6 arch review: per-script fallback keeps the rest of
the consumer wired through topology, so we don't lose the fix surface for
scripts that ARE covered.

When `transpile_ran is True` and a script is missing from
`reachability_requirements`, the consumer raises — the legacy fallback is
NOT engaged, because empty under those conditions indicates a real bug we
want loud.

## Implementation status, phase plan, testing, sequencing & revision history

**Moved 2026-06-06 to [`scene-runtime-execution-log.md`](scene-runtime-execution-log.md)** so this doc
stays the architecture-of-record (the DESIGN substance above), not a changelog. The execution log holds:
the high-priority followup TODOs, the full phase plan (Phase 1 / 2a slices 1–11 / 2b reframe / Phase 3
verifier slices 0–7), migration discipline, per-phase testing specs, sequencing + dependencies, and the
chronological revision history. Slice/round references in the sections above (e.g. "slice 7 R3",
"Phase 2b reframe") resolve to that log.

**Shipped status (git truth, upstream/main as of 2026-06-03):** Phases 1, 2a, and the Phase 2b *core*
(`shared_flag_channels.py`, `cross_domain_edges.py`, `edge_enrichment.py`) are merged; the Phase 3
**contract verifier** (`contract_verifier.py`, checks A/B/C fail-closed) is merged. **Pending:** the
Phase 2b *tail* — retiring the legacy `pickup_remote_event_server` pack + its dependents. See the
execution log for the per-slice breakdown and the umbrella [`generic-converter-architecture.md`](generic-converter-architecture.md)
for where this effort (Workstream 2) sits in the overall durability architecture.

## Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Topology artifact schema proves insufficient mid-migration | medium | high | Lightweight invariant checks (Codex round-1) catch contradictions early; Phase 1 ships with a SINGLE concrete consumer (animation_converter) before promising the schema to other phases |
| **Planner/consumer skew during phased rollout** (Codex round-3) | medium | high | Schema-compat test cut + frozen-fixture round-trip test prevents drift; topology version field in artifact so consumers can detect mismatches |
| Phase 2b's `pickup_remote_event_server` pack migration regresses Pickup (legacy mode only) | medium | medium | Canonical-form (not literal-byte) equivalence regression with documented diff allowlist; walk-up `GetAttribute` compensation extracted to `pickup_attribute_walkup` BEFORE pack deletion. NOTE (2026-06-01 reframe): the pack + its `PickupItemEvent` name are LEGACY-mode-only; the generic-mode shared-flag bridge is the funnel, gated+recorded by topology (no `PickupItemEvent` lock in the generic path). |
| Phase 2b's edge derivation reordering (Path B) is structurally blocked | n/a | n/a | (resolved 2026-05-30) Path C decision in deliverable 1 splits derivation into structural-candidate (pre-transpile) + enrichment (post-transpile); rewriter is post-transpile, pre-pack. Cited evidence: `pipeline.py:2566-2618`, `scene-runtime-pr4-followups.md:600-626` |
| Phase 3 fail-closed mode breaks newly-converted external projects | medium | high | Shadow-mode metrics first; corpus audit across bundled projects; one-release escape hatch (env var to revert to warnings) |
| `lifecycle_role` enum proves insufficient for future cases | low | low | Closed enum + optional metadata bag (Codex round-4) — non-placement-affecting hints go in the bag, structural roles go in the enum; future enum extensions are backward-compatible |
| Cross-domain bridge RemoteEvent naming collisions across prefabs | low | medium | Deterministic `event_name = <prefab>_Set<Attribute>` scheme; topology invariant rejects collisions at emit time |
| `bridge_group_id` not unique under prefab nesting | low | low | Use deterministic edge id including provenance; topology emit-time check catches duplicates |
| Decision-tree branches in `script_storage` accidentally re-introduce regex-based domain re-derivation | low | medium | Phase 3 verifier checks all storage decisions are reproducible from topology + structural inputs (no source-scan inputs) |

## Open questions

1. **Storage location of the artifact:** new top-level key in
   `conversion_plan.json`, or a sibling `topology_plan.json`? Defer to
   Phase 1 implementation; either is fine if the consumers read through
   a single accessor.
2. **`bridge_member_scripts` schema for non-4-script bridges** (e.g.
   read-only RemoteFunction patterns or 2-script bridges): not in scope
   for Phase 1-2; add when a concrete case demands it.
3. **`entrypoint_kind` enum values:** Phase 1 starts with the minimum
   set needed for SimpleFPS; expand as test projects demand.
4. **Where does today's `_attach_monobehaviour_scripts_to_templates`
   logic go?** Probably becomes part of script_storage's bound consumer
   logic. Defer to Phase 2a.

