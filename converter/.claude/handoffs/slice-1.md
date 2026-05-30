# Phase 2b Slice 1 handoff → Slice 2

First slice of Phase 2b (the `code_transpiler` bridge-emission phase per
the design doc). Phase 2a's slice numbering ran 1-11; Phase 2b restarts
at slice 1. This handoff captures everything slice 2-onwards needs.

---

## What slice 1 shipped

- **Extended `CrossDomainEdge` schema** (`cross_domain_edges.py`) with
  the Phase 2b fields slice 3's bridge emitter will consume:
  - `kind: "attribute_write"` (closed-enum discriminator; today's only
    value, slice 2+ may add more).
  - `resolution` (strategy + event_name). Slice 1 always emits
    `strategy = "remote_event_bridge"` and derives a stable
    `event_name` from owner+field; slice 2 may revise/downgrade.
  - `bridge_member_scripts: list[BridgeMember]` (EMPTY in slice 1;
    slice 2 enrichment populates it).
  - `payload` (attribute_name + schema). For component-ref edges the
    attribute_name is the serialized field name; for shared-attribute
    candidates it is a per-instance template (`has<itemName>`).

- **Two-bucket topology artifact** (R1 fix from codex P1, 2026-05-30).
  `build_topology` now routes the two producers to SEPARATE buckets,
  not a concatenated list:
  - `artifact["cross_domain_edges"]` — fully resolved component-ref
    edges from `compute_cross_domain_edges`. Every row has runtime
    `from_domain` and `to_domain`; passes `_enforce_invariants`
    invariant 2.
  - `artifact["cross_domain_edge_candidates"]` — fan-out shape from
    `compute_shared_attribute_candidates`. Empty `to_*` until slice 2
    enrichment resolves consumers. Invariant 2 does NOT iterate this
    bucket.

- **`SHARED_ATTRIBUTE_SEEDS` table** with one row: `producer_class_name
  = "Pickup"`, `remote_event_name = "PickupItemEvent"`,
  `attribute_template = "has<itemName>"`. Walks scene/prefab instances
  and emits one candidate per Pickup-classed instance.

---

## Mitigation α — LOCKED `PickupItemEvent`

The Pickup seed's `remote_event_name` MUST stay the literal
`"PickupItemEvent"`. Three downstream sites continue to hardcode this
string through Phase 2b and would break if it changed:

- `pickup_remote_event_client` regex at `script_coherence_packs.py:780-783`
- `pickup_visual_target` template at `script_coherence_packs.py:1167-1189`
- listener pack at `script_coherence_packs.py:1032-1079`

The design doc explicitly locks the name (Phase 2b deliverable 4 at
`converter/docs/design/scene-runtime-architecture-ir.md` L818-825). Slice
3 re-produces the same byte shape; the three sites continue to match
without migration. Future cleanup migrates those sites to consume the
edge artifact directly; Phase 2b does NOT.

---

## Codex R2 carry-forward — the slice 3 coverage question

Codex R2 (2026-05-30) flagged that the slice 1 seed table is NARROWER
than the prompt it eventually replaces:

- The prompt at `code_transpiler.py:1271-1289` is UNBOUNDED — it
  instructs the AI to fire `PlayerSetSharedFlag` for ANY MonoBehaviour
  writing shared state (`GetItem(itemName)`-style methods,
  `RecoverHealth`, `gotWeapon = true`, etc.).
- The slice 1 seed only models `producer_class_name = "Pickup"` — which
  matches the EXISTING `pickup_remote_event_server` pack's class-name
  detection scope, NOT the broader prompt scope.

**Slice 3 must decide between:**

(a) Require this table to be COMPREHENSIVE before deleting the prompt
    path — i.e. statically enumerate every MonoBehaviour class that
    writes shared player state across the converter's supported
    corpus, and add a seed row per class. High up-front cost; fully
    data-driven afterwards.

(b) Keep a FALLBACK that scans AI output for
    `PlayerSetSharedFlag:FireServer` calls when no topology candidate
    covers the producing script. Lower cost; preserves coverage for
    non-Pickup shared-flag writers (e.g. a `Player.cs` controller that
    records `has<X>` without routing through a Pickup instance) that
    the slice 1 seed misses.

Deleting the prompt path without picking one regresses any non-Pickup
shared-flag writer in real projects. This is slice 3's design concern,
not a slice 1 implementation bug — slice 1's seed correctly matches
the EXISTING PACK's structural scope. The docstring in
`cross_domain_edges.py` above `SHARED_ATTRIBUTE_SEEDS` carries the same
warning in-source.

---

## Slice 2 known inputs

Slice 2's enrichment pass consumes `cross_domain_edge_candidates` from
slice 1. Each row arrives with:

- Fully populated `from_*` (producer instance + script_id + field).
- Empty / sentinel `to_*` (consumers not yet resolved).
- Empty `bridge_member_scripts`.
- `resolution.strategy = "remote_event_bridge"` and a structural
  `event_name` derived from owner+field.
- `payload.attribute_name` as a template (e.g. `has<itemName>`), NOT
  a resolved per-instance name.

Slice 2 enrichment is expected to:

1. Walk consumers — any module that reads `has<X>` attributes — and
   populate `bridge_member_scripts` with the 4-script bridge unit
   (`client_caller`, `server_listener`, `remote_event`, `anim_listener`).
2. Resolve `from_domain` / `to_domain` for the candidates.
3. Decide whether to promote enriched candidates into
   `cross_domain_edges` (single bucket) or keep the two-bucket split
   indefinitely. Either is acceptable per slice 1's contract — the two
   producer functions in `cross_domain_edges.py` are pure and remain
   unaware of the bucket layout; wiring lives in `build_topology`.

---

## P3 deferrals (slice 2 scope)

Three R1-review items classified as slice 2 work:

- **Determinism inconsistency.** `compute_cross_domain_edges` iterates
  producers in dict-insertion order; `compute_shared_attribute_candidates`
  iterates in sorted-key order. Pick one and harmonize. Slice 1
  intentionally left the two as-is because the artifact buckets are
  separate and downstream consumers don't yet cross-reference order.
- **`_derive_event_name_from_owner_field` permissive on empty field.**
  Slice 1 derives an event name even when `field == ""`. Slice 2
  enrichment should reject empty-field rows (or normalize them before
  duplicate-name dedupe).
- **Duplicate-`event_name` invariant lives in slice 2.** Real event
  names aren't known until enrichment resolves them. Slice 1 cannot
  enforce uniqueness; slice 2 must.

---

## Architectural rationale

Phase 2b's two-stage edge derivation (structural pre-transpile +
enrichment post-transpile) is the **Path C** decision from the design
doc, reached unanimously by Claude+Codex parallel adversarial review on
2026-05-30. See `converter/docs/design/scene-runtime-architecture-ir.md`
L763-836 ("Phase 2b — bridge emission (post-transpile rewriter,
edge-driven)") for the full deliverable list and the Path A/B/C
rationale. Brief recap:

- Path A (post-AI rewriter only) is too narrow to cover the pickup
  case.
- Path B (move all derivation pre-transpile) is structurally blocked
  by the `_classify_storage` → `convert_scene` →
  `_subphase_emit_scripts_to_disk` → `_subphase_cohere_scripts`
  ordering (`pipeline.py:2566-2618`).
- Path C splits derivation: structural candidates pre-transpile (this
  slice), enrichment post-transpile (slice 2), emission post-transpile
  pre-pack (slice 3).

Slice 1 implements the structural-candidate half. Slice 2 adds
enrichment; slice 3 adds the bridge emitter; deliverables 3-5 follow.

---

## Slice 1 final state

- Branch: `feat/scene-runtime-phase-2b-slice-1`
- Tests: 2296 passed (no test changes from R2).
- no-Any gate: clean.
- Schema extension is additive; existing consumers continue to read
  `from_*` / `to_*` unchanged.
