# Slice 9b handoff

Phase 2a slice 9b: drop `reachability_forced_container` from
`TopologyModuleEntry` (Option C). Stacked on PR #155 (slice 9a).

## Architecture decisions made

- **`reachability_forced_container` dropped from `TopologyModuleEntry`**.
  Verified by dual independent audits (Claude + Codex) that no
  production code uses the field for runtime decisions.
  `reachability_required_container` carries the full semantic.
- **Invariant-10 lockstep check removed** (was tautological — the same
  loop populated both fields from the same source).
- **8-test migration**: 6 PRESERVE-adapted, 2 DELETED (covered only the
  removed lockstep), 2 NEW regression tests for slice 9a R1's
  degenerate-fixture corner.
- **Pipeline degenerate-fixture fix folded in**: `rbx_place.scripts == []`
  case skips the new `assert topology_inputs is not None` rather than
  firing it.

## What slice 10 inherits — the two-ended scope

1. **Switch the read site** at
   `build_topology._build_modules_block:629` from
   `domain_signals["reachability_forced_container"]` (planner-row audit
   signal) to a different source.
   - **Option a:** read from `module_row["container"]` if it captures
     the needed semantic.
   - **Option b:** recompute from `reachability_requirements[sid]`
     (which is the underlying analysis output anyway).
   - Slice 10 arch review will pick one.
2. **Retire the writes** at `module_domain.py:955` and `:1743`.
3. **External tests** at `test_module_domain_prepass.py:1184`,
   `test_scene_runtime_domain.py:347`,
   `test_scene_runtime_domain_v2.py:712` test the planner-row audit
   signal directly; under slice 10 they'll need migration / deletion.

## Process notes for slice 10

- Slice 10's arch review should answer: which source replaces line
  629's read? (option a vs option b above).
- After slice 10, slice 11 is final test sweep + any remaining cleanup.
