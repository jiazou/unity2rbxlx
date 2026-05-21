# scene-runtime PR6 followups

Tracking items surfaced during PR6 (write_output playability guard
rail) review that are out of PR6 scope but worth revisiting.

## 1. Multi-scene `UNCONVERTED.md` clobbering (codex R2-P1, deferred)

**Surface.** `Pipeline.run_all_scenes` writes one `.rbxlx` per scene
(`<scene>.rbxlx` via a transient `RBXLX_OUTPUT_FILENAME` override),
but `_write_unconverted_md` always targets the shared root
`output_dir/UNCONVERTED.md`. Each scene's `write_output` rewrites the
file; the last scene wins.

**Why deferred.** The clobbering is pre-PR6 (since 2026-04-24) and
affects every category in `UNCONVERTED.md` -- cross-domain edges,
material warnings, dropped components, and now PR6's
`nonplayable_warnings`. The fix is a per-scene file path (e.g.
`UNCONVERTED_<scene>.md`) plus a root summary index. That's a
multi-scene-pipeline refactor, not a PR6 guard-rail change.

**PR6 impact.** Under `--scene all`, if scene A downgrades via
`--allow-nonplayable-output` and scene B is clean, B's clean write
deletes A's UNCONVERTED.md. The PR6 hard-fail path is unaffected
(scene A raises and aborts the whole multi-scene loop before scene
B starts).

**Tracking.** Should land before PR7 makes `auto` the default for
multi-scene projects so the safety signal is durable across
`--scene all` runs. Estimate: small, mechanical -- s/single shared
file/per-scene file/.

## 2. Reversibility of the `--allow-nonplayable-output` opt-in (codex R2 priority answer, deferred)

**Surface.** PR6 plumbs the flag as sticky-opt-in: explicit `True`
overrides persisted; default `False` respects persisted. There is no
way for an operator to TURN OFF a previously opted-in
`conversion_context.json` short of editing the JSON or running
`--clean`. The reviewer flagged this as a gap.

**Why deferred.** Adding `--no-allow-nonplayable-output` (or
equivalent tri-state) is a CLI design decision that should be made
across the sticky-opt-in family of flags (scaffolding has the same
pattern); designing it in isolation here would create surface
inconsistency.

**Recommendation.** Either:
  (a) introduce `--no-X` shadow flags for every sticky-opt-in field
      with a single shared helper, or
  (b) document that `--clean` is the canonical way to reset
      operator opt-ins.

PR6 chose (b) implicitly. Revisit after PR8 retires the FPS
scaffolding sticky-opt-in, which will reduce the design pressure.

## 3. PR7 cross-reference test is unit-level, not end-to-end (codex R2-P3, partially absorbed)

**Surface.** `TestPR7CrossReference::test_auto_clean_routes_to_generic_then_guard_fires`
now drives `_check_auto_fail_closed` before `_check_runtime_playability_guard`
(addresses the round-1 form of the codex finding). It still calls
both subphases directly rather than running the whole
`Pipeline.run_all()` / `write_output()` orchestration.

**Why deferred.** A true end-to-end test needs a fully-stocked
`Pipeline.state` (parsed scene, transpilation result, asset manifest,
etc.) -- closer to an integration fixture than a unit test. The
canary suite that lands with PR5 covers the end-to-end auto routing;
re-implementing it here would duplicate the canary infrastructure.

**Tracking.** When PR7 lands, the canary suite should run the auto-
default flow against a project with incomplete plan + assert the
PR6 guard raises (instead of write_output producing a broken
artifact). This is more naturally PR7's territory than PR6's.

## Recap

| # | Item | Severity (round 2) | Deferred to |
|---|------|--------------------|-------------|
| 1 | Multi-scene `UNCONVERTED.md` clobbering | P1 (pre-existing) | PR7 (before default flip) |
| 2 | Sticky-opt-in reversibility | Design | PR8+ |
| 3 | PR7 cross-reference end-to-end | P3 | PR7 canary |
