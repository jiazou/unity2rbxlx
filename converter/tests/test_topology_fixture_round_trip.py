"""Phase 2a slice 1 — schema-compat frozen fixture + round-trip test.

Design doc ref:
`converter/docs/design/scene-runtime-architecture-ir.md` §"Phase 2a —
script_storage.py becomes bound consumer (placement-only)" → §"Slice plan
(post eng-review, 2026-05-28)" → slice 1.

What this test guards:
  - The on-disk frozen artifact at
    `tests/fixtures/topology/simplefps_minimal.json` is identical to what
    `build_topology` produces from the SimpleFPS-minimal-shape synthetic
    inputs `_build_simplefps_minimal_scene_runtime()` defines.
    Drift detection: any added/dropped/renamed field in a future slice
    that did NOT extend the fixture in the same commit fails this test.
  - The fixture parses against the TopologyArtifact schema (enum
    membership for `routing_status`, `lifecycle_role`, `domain`,
    `script_class`).
  - The current downstream consumer surface (the animation_drivers
    application logic mirrored from pipeline.py:4232-4276) accepts the
    fixture without error. The `script_storage` half of the brief's
    "drives animation_converter + script_storage from disk" wiring lands
    in slice 5 (storage_classifier becomes a topology consumer).

Migration discipline (codex 2026-05-28): slices 2/3/4/5 extend the
fixture in the SAME commit they add fields to `build_topology`'s
output. There is NO separate "extend the fixture" slice — deferring it
would leave slice 1 incompatible with slices 2-5.

To regenerate the on-disk fixture after an intentional schema change,
run this module as a script:

    cd converter && python -m tests.test_topology_fixture_round_trip --regen

`--regen` is NOT a safe default (codex pass 1 2026-05-28): it overwrites
the on-disk fixture with whatever `build_topology` currently emits, so a
regression in the builder will be blessed if you regenerate before
diffing. Inspect the fixture diff against HEAD first; if the change is
unexpected, fix the builder. The build_topology unit-test corpus at
`tests/test_scene_runtime_topology.py` is the semantic-regression
backstop that catches accidental drift independently of this fixture.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.roblox_types import RbxScript, ScriptType  # noqa: E402
from converter.scene_runtime_planner import (  # noqa: E402
    SceneRuntimeArtifact,
)
from converter.scene_runtime_topology.animation_routing import (  # noqa: E402
    compute_stable_id,
)
from converter.scene_runtime_topology.build_topology import (  # noqa: E402
    EmittedAnimation,
    TopologyArtifact,
    build_topology,
)
from converter.scene_runtime_topology.lifecycle_roles import (  # noqa: E402
    LIFECYCLE_ROLES,
)


# ---------------------------------------------------------------------------
# Canonical inputs — the SimpleFPS-minimal shape. Changing these is a
# schema-affecting change and must travel with a fixture regen.
# ---------------------------------------------------------------------------

FIXTURE_PATH: Path = (
    Path(__file__).parent / "fixtures" / "topology" / "simplefps_minimal.json"
)

# The Door scenario from `test_scene_runtime_topology._door_shape_artifact`.
# Kept inline (not imported) so this test stays the single source of truth
# for the on-disk fixture's input contract — a refactor of the upstream
# helper cannot silently invalidate the fixture.
_DOOR_SCRIPT_ID = "guid-door"
_ANIMATOR_SCRIPT_ID = "guid-animator-target"
_HUD_SCRIPT_ID = "guid-hud"
_SPAWN_SCRIPT_ID = "guid-spawn"
_DOOR_PREFAB_ID = "guid-door-prefab:Assets/Prefabs/Door.prefab"
_DOOR_MB_INSTANCE = "P:1"
_ANIMATOR_INSTANCE = "P:2"


def _build_simplefps_minimal_scene_runtime() -> SceneRuntimeArtifact:
    """Inputs the fixture was generated from.

    Door (client driver) + AnimatorTarget (client, animated target) +
    HudControl (client, unrelated module) + SpawnManager (server,
    unrelated module). One prefab carries the Door MonoBehaviour and the
    AnimatorTarget peer, with an `animator` field cross-reference (which
    is intra-domain — both are client — so it does NOT produce a
    cross_domain_edges row; the empty edges list is the fixture's
    invariant 2 anchor).
    """
    # Phase 2a slice 2: every runtime_bearing row carries
    # `character_attached` + `is_loader` (build_topology invariant 7).
    # All four modules: character_attached=False (none are bound to the
    # player-character prefab in this minimal scenario), is_loader=False
    # (none of the stems — Door / AnimatorTarget / HudControl /
    # SpawnManager — match REPLICATED_FIRST_HINTS).
    return cast(SceneRuntimeArtifact, {
        "modules": {
            _DOOR_SCRIPT_ID: {
                "stem": "Door",
                "class_name": "Door",
                "runtime_bearing": True,
                "domain": "client",
                "character_attached": False,
                "is_loader": False,
            },
            _ANIMATOR_SCRIPT_ID: {
                "stem": "AnimatorTarget",
                "class_name": "AnimatorTarget",
                "runtime_bearing": True,
                "domain": "client",
                "character_attached": False,
                "is_loader": False,
            },
            _HUD_SCRIPT_ID: {
                "stem": "HudControl",
                "class_name": "HudControl",
                "runtime_bearing": True,
                "domain": "client",
                "character_attached": False,
                "is_loader": False,
            },
            _SPAWN_SCRIPT_ID: {
                "stem": "SpawnManager",
                "class_name": "SpawnManager",
                "runtime_bearing": True,
                "domain": "server",
                "character_attached": False,
                "is_loader": False,
            },
        },
        "scenes": {},
        "prefabs": {
            _DOOR_PREFAB_ID: {
                "name": "Door",
                "template_name": "Door",
                "instances": [
                    {
                        "instance_id": _DOOR_MB_INSTANCE,
                        "script_id": _DOOR_SCRIPT_ID,
                        "game_object_id": "P:go-1",
                        "active": True, "enabled": True, "config": {},
                    },
                    {
                        "instance_id": _ANIMATOR_INSTANCE,
                        "script_id": _ANIMATOR_SCRIPT_ID,
                        "game_object_id": "P:go-2",
                        "active": True, "enabled": True, "config": {},
                    },
                ],
                "references": [
                    {
                        "from": _DOOR_MB_INSTANCE,
                        "field": "animator",
                        "index": None,
                        "target_kind": "component",
                        "target_ref": _ANIMATOR_INSTANCE,
                        "target_is_ui": False,
                        "target_component_type": "Animator",
                    },
                ],
                "lifecycle_order": [],
            },
        },
        "domain_overrides": {},
    })


def _build_simplefps_minimal_emissions() -> list[EmittedAnimation]:
    """One door-open animation emitted from the Door prefab. Resolves to
    the Door MonoBehaviour as driver (driver_module_guid=_DOOR_SCRIPT_ID)
    via `resolve_driver`'s same-scope prefab lookup."""
    return [
        cast(EmittedAnimation, {
            "scope_kind": "prefab",
            "scope_ref": _DOOR_PREFAB_ID,
            "scope_display": "Door",
            "ctrl_key": "Door",
            "clip_disp": "door_open",
            "script_name": "Anim_Door_door_open",
            "observed_attribute": "Open",
            "curve_paths": [],
            "prefab_scoped": True,
        }),
    ]


def _build_simplefps_minimal_scripts_by_class() -> dict[str, RbxScript]:
    """RbxScript-shaped objects mirroring the modules block. Each script's
    `script_type` is the placement target that produces a non-trivial
    lifecycle_role + script_class in the artifact (HudControl as
    LocalScript flips its lifecycle_role to auto_run; SpawnManager as
    Script keeps it auto_run as well; the Door scenario's
    script_class drops out of the animation_drivers entry, not the
    module).
    """
    def _mk(name: str, script_type: ScriptType) -> RbxScript:
        return RbxScript(name=name, source="-- placeholder", script_type=script_type)
    return {
        "Door": _mk("Door", "LocalScript"),
        "AnimatorTarget": _mk("AnimatorTarget", "LocalScript"),
        "HudControl": _mk("HudControl", "LocalScript"),
        "SpawnManager": _mk("SpawnManager", "Script"),
    }


def _build_expected_artifact() -> TopologyArtifact:
    """The single source of truth for what the fixture must equal.

    Calls `build_topology` on the canonical inputs. The on-disk fixture
    is the JSON-serialized form of this artifact.

    Note for slice 2+ authors: `guid_index=None` keeps every module's
    `provenance` block empty (`{}`) in the slice-1 fixture. Slices that
    add tests requiring populated `source_path` provenance should plumb
    a fake `GuidIndex` here in the SAME commit they add the field-using
    assertion.
    """
    return build_topology(
        scene_runtime=_build_simplefps_minimal_scene_runtime(),
        emitted_animations=_build_simplefps_minimal_emissions(),
        scripts_by_class=_build_simplefps_minimal_scripts_by_class(),
        guid_index=None,  # provenance.source_path omitted in the minimal fixture
    )


def _artifact_to_json_compatible(artifact: TopologyArtifact) -> dict:
    """Round-trip the artifact through JSON to normalize TypedDict ↔ dict
    plus `None` ↔ null. Returns a plain-dict shape suitable for
    on-disk persistence + deep-equality comparison.
    """
    return cast(dict, json.loads(json.dumps(artifact, sort_keys=True)))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFixtureRoundTrip:
    """The on-disk fixture is byte-stable against the live artifact."""

    def test_fixture_exists(self) -> None:
        assert FIXTURE_PATH.is_file(), (
            f"Fixture missing at {FIXTURE_PATH}. Regenerate via "
            f"`python -m tests.test_topology_fixture_round_trip --regen`."
        )

    def test_fixture_equals_live_artifact(self) -> None:
        """The on-disk fixture matches `build_topology(canonical_inputs)`
        exactly. ANY drift — added field, dropped field, renamed key,
        re-ordered enum value — fails this test. The expected fix is to
        update the fixture in the SAME commit that introduces the
        schema change (slices 2-5 of Phase 2a; codex 2026-05-28).

        The exact-equality check is the SINGLE drift detector for this
        slice. An earlier draft also walked the fixture's recursive key
        set as a separate "subset" check, but Claude review 2026-05-28
        flagged it as blind to empty lists (the slice-1 fixture has
        `cross_domain_edges: []`, so a new field added inside an edge
        row would not surface through key-walking until the list was
        also populated). Equality catches every drift case the subset
        check would have caught, with a more actionable diff."""
        expected = _artifact_to_json_compatible(_build_expected_artifact())
        loaded = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        assert loaded == expected, (
            "On-disk fixture diverges from live artifact. INSPECT THE DIFF "
            "first — if the change is intentional (a slice 2-5 schema "
            "extension), regenerate via `python -m "
            "tests.test_topology_fixture_round_trip --regen` and commit "
            "alongside the schema change. If the change is unexpected, "
            "the live artifact has a regression — fix that instead of "
            "regenerating; the fixture is frozen on purpose (codex pass 1 "
            "2026-05-28 — regen is NOT a safe default)."
        )


class TestFixtureSchema:
    """The fixture's enum-valued fields are members of their closed
    enums. Decouples enum drift from value drift — a `routing_status`
    typo in the fixture surfaces here even if no consumer reads it."""

    def test_lifecycle_role_in_closed_enum(self) -> None:
        loaded = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        valid_roles = set(LIFECYCLE_ROLES)
        for guid, entry in loaded.get("modules", {}).items():
            role = entry.get("lifecycle_role", "")
            assert role in valid_roles, (
                f"modules[{guid!r}].lifecycle_role={role!r} not in "
                f"{sorted(valid_roles)!r}"
            )
        for sid, entry in loaded.get("animation_drivers", {}).items():
            role = entry.get("lifecycle_role", "")
            assert role in valid_roles, (
                f"animation_drivers[{sid!r}].lifecycle_role={role!r} not in "
                f"{sorted(valid_roles)!r}"
            )

    def test_routing_status_in_closed_set(self) -> None:
        loaded = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        valid_statuses = {"resolved", "unresolved", "orphan"}
        for sid, entry in loaded.get("animation_drivers", {}).items():
            status = entry.get("routing_status", "")
            assert status in valid_statuses, (
                f"animation_drivers[{sid!r}].routing_status={status!r} not "
                f"in {sorted(valid_statuses)!r}"
            )

    def test_module_domain_in_closed_set(self) -> None:
        loaded = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        valid_domains = {"client", "server", "helper", "excluded"}
        for guid, entry in loaded.get("modules", {}).items():
            domain = entry.get("domain", "")
            assert domain in valid_domains, (
                f"modules[{guid!r}].domain={domain!r} not in "
                f"{sorted(valid_domains)!r}"
            )

    def test_script_class_in_closed_set(self) -> None:
        """`script_class` field on modules is one of `Script` /
        `LocalScript` / `ModuleScript`; on animation_drivers it's a
        narrower 2-value enum (`Script` / `LocalScript`) per
        `animation_routing.AnimationScriptClass`. Validate both."""
        loaded = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        module_valid = {"Script", "LocalScript", "ModuleScript"}
        anim_valid = {"Script", "LocalScript"}
        for guid, entry in loaded.get("modules", {}).items():
            sc = entry.get("script_class", "")
            assert sc in module_valid, (
                f"modules[{guid!r}].script_class={sc!r} not in "
                f"{sorted(module_valid)!r}"
            )
        for sid, entry in loaded.get("animation_drivers", {}).items():
            sc = entry.get("script_class", "")
            assert sc in anim_valid, (
                f"animation_drivers[{sid!r}].script_class={sc!r} not in "
                f"{sorted(anim_valid)!r}"
            )


class TestFixtureConsumers:
    """Slice 1 drives the ONE current consumer of the topology artifact:
    the `animation_drivers` application logic from
    `pipeline.py:_classify_storage` (lines 4202-4276), which flips
    `Anim_*` RbxScripts' `script_type` + `parent_path` based on the
    driver-routed domain.

    `script_storage.py` is NOT yet a consumer of the topology artifact
    — that wiring is slice 5. The brief's "drives animation_converter +
    script_storage from disk" language reflects the slice-5 endpoint,
    not slice 1's current consumer surface.

    Maintenance note: the flip logic below is a deliberate mirror of
    `pipeline.py:_classify_storage` lines 4232-4276. If that block adds
    a new branch (e.g. a `lifecycle_role == "loader"` placement override
    in Phase 2b), this mirror will silently rot. Slice 7's regex retire
    + storage decision-tree extraction is the natural point to fold
    this mirror into a shared callable; until then a code-change in
    pipeline.py:4232+ should also touch this method."""

    def test_animation_drivers_flip_client_anim_script(self) -> None:
        """Apply the fixture's animation_drivers to a synthetic
        Anim_Door_door_open RbxScript. Expect the driver-routed flip:
        script_type → LocalScript, parent_path →
        StarterPlayer.StarterPlayerScripts (matches pipeline.py:4238-4240).

        Looks up the Door row by its stable_id (computed from the same
        canonical inputs the fixture was generated from) rather than
        asserting fixture cardinality — slices 2-5 may add additional
        resolved entries without invalidating the Door scenario this
        test exists to pin (codex pass 1 2026-05-28).
        """
        loaded = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        drivers = loaded.get("animation_drivers", {})

        # Compute the same stable_id the fixture-generator uses for the
        # door_open emission. compute_stable_id is the public escape-
        # encoded keyer per animation_routing.py.
        door_emission = _build_simplefps_minimal_emissions()[0]
        door_stable_id = compute_stable_id(
            door_emission["scope_ref"],
            door_emission["ctrl_key"] or None,
            door_emission["clip_disp"],
        )
        entry = drivers.get(door_stable_id)
        assert entry is not None, (
            f"fixture missing the canonical Door animation_drivers row "
            f"under stable_id={door_stable_id!r}; got keys {sorted(drivers)!r}"
        )
        assert entry.get("routing_status") == "resolved", (
            f"canonical Door row is not resolved (got "
            f"routing_status={entry.get('routing_status')!r}); the Door "
            f"prefab carries the MonoBehaviour driver, so resolve_driver "
            f"must find it"
        )

        script_name = "Anim_Door_door_open"
        # Synthesize the RbxScript that animation_converter would emit
        # for this stable_id — same name as the fixture's emission row.
        anim_script = RbxScript(
            name=script_name,
            source="-- generated Anim_*",
            script_type="Script",
            parent_path="ServerScriptService",
        )

        # Apply the topology decision (mirror of pipeline.py:4232-4276).
        script_class = entry.get("script_class", "")
        domain = entry.get("domain", "")
        if script_class == "LocalScript" and domain == "client":
            anim_script.script_type = "LocalScript"
            anim_script.parent_path = "StarterPlayer.StarterPlayerScripts"

        assert anim_script.script_type == "LocalScript", (
            f"animation_drivers application did not flip script_type "
            f"(entry={entry})"
        )
        assert anim_script.parent_path == "StarterPlayer.StarterPlayerScripts"


# ---------------------------------------------------------------------------
# Regen entry point
# ---------------------------------------------------------------------------

def _regen() -> None:
    """Write the on-disk fixture from the canonical inputs. Invoked via
    `python -m tests.test_topology_fixture_round_trip --regen`."""
    artifact = _artifact_to_json_compatible(_build_expected_artifact())
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote fixture: {FIXTURE_PATH}")


if __name__ == "__main__":
    if "--regen" in sys.argv:
        _regen()
    else:
        pytest.main([__file__, "-v"])
