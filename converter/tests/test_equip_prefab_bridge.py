"""Phase 2 (camera-mount -> player-mount equip) acceptance criteria 10-11.

The field-name -> prefab_id bridge (D13b) + its reach into the EMITTED Plan.

  10. (LOAD-BEARING) the post-transpile bridge populates
      ``ctx.scene_runtime["equip_prefabs"]`` from the equip-emitting script's
      prefab reference rows, AND that map reaches the EMITTED SceneRuntimePlan
      Luau via ``_PLAN_KEYS_FOR_HOST``. Asserts:
        (a) the in-memory map: equip_prefabs["riflePrefab"] == the rifle prefab_id;
        (b) the EMITTED Luau (generate_scene_runtime_plan_module) carries it
            (NOT just the Python dict — guards the dead-live-path failure mode);
        (c) removing "equip_prefabs" from _PLAN_KEYS_FOR_HOST makes (b) RED;
        (d) a same-field-name-different-prefab pair across equip scripts triggers
            the build-time fail-close (RuntimeError).
  11. a serialize -> rehydrate round-trip of the Plan preserves equip_prefabs
      (a no-retranspile ``assemble`` resume keeps the bridge).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from converter import autogen  # noqa: E402
from converter.autogen import (  # noqa: E402
    generate_scene_runtime_plan_module,
)
from converter.code_transpiler import TranspiledScript  # noqa: E402
from converter.pipeline import build_equip_prefabs_bridge  # noqa: E402
from core.unity_types import GuidIndex  # noqa: E402


# ---------------------------------------------------------------------------
# Builders — a realistic equip-emitting script + scene_runtime reference rows.
# ---------------------------------------------------------------------------

_PLAYER_CS = "/proj/Assets/Scripts/Player.cs"
_PLAYER_SCRIPT_ID = "guid_player_aaaa"
_RIFLE_PREFAB_ID = "Assets/Prefabs/Rifle.prefab::guid_rifle_bbbb"


def _guid_index(paths_to_ids: dict[str, str]) -> GuidIndex:
    gi = GuidIndex(project_root=Path("/proj"))
    for path, script_id in paths_to_ids.items():
        gi.path_to_guid[Path(path).resolve()] = script_id
    return gi


def _equip_script(
    source_path: str = _PLAYER_CS,
    prefab_field: str = "riflePrefab",
) -> TranspiledScript:
    return TranspiledScript(
        source_path=source_path,
        output_filename=Path(source_path).stem + ".luau",
        csharp_source="",
        luau_source="",
        strategy="ai",
        confidence=1.0,
        equip_binding={
            "prefab": prefab_field,
            "method": "GetRifle",
            "remote": "equipWeaponRemote",
            "present": True,
        },
    )


def _scene_runtime_with_ref(
    script_id: str = _PLAYER_SCRIPT_ID,
    field: str = "riflePrefab",
    prefab_id: str = _RIFLE_PREFAB_ID,
) -> dict[str, object]:
    """A minimal scene_runtime with one equip script's instance + its prefab
    reference row (the planner-seeded SceneRuntimeReference)."""
    return {
        "scenes": {
            "Game.unity": {
                "instances": [{
                    "instance_id": "Game.unity:1",
                    "script_id": script_id,
                    "game_object_id": "Game.unity:99",
                    "active": True,
                    "enabled": True,
                    "config": {},
                }],
                "references": [{
                    "from": "Game.unity:1",
                    "field": field,
                    "index": None,
                    "target_kind": "prefab",
                    "target_ref": prefab_id,
                    "target_is_ui": False,
                }],
                "lifecycle_order": ["Game.unity:1"],
            },
        },
        "prefabs": {},
    }


# ---------------------------------------------------------------------------
# Criterion 10 (a) — the in-memory bridge
# ---------------------------------------------------------------------------

class TestBridgeInMemory:

    def test_field_resolves_to_prefab_id(self):
        scene_runtime = _scene_runtime_with_ref()
        gi = _guid_index({_PLAYER_CS: _PLAYER_SCRIPT_ID})
        equip_prefabs = build_equip_prefabs_bridge(
            [_equip_script()], scene_runtime, gi,
        )
        assert equip_prefabs == {"riflePrefab": _RIFLE_PREFAB_ID}

    def test_no_equip_script_yields_empty_map(self):
        # A non-equip game: no equip_binding anywhere -> empty bridge, no crash.
        ts = TranspiledScript(
            source_path=_PLAYER_CS, output_filename="Player.luau",
            csharp_source="", luau_source="", strategy="ai", confidence=1.0,
            equip_binding=None,
        )
        equip_prefabs = build_equip_prefabs_bridge(
            [ts], _scene_runtime_with_ref(), _guid_index({}),
        )
        assert equip_prefabs == {}

    def test_unresolvable_field_abstains(self):
        # The reference row's field does not match the equip field -> the field
        # stays unresolved (runtime abstain), NOT a crash.
        scene_runtime = _scene_runtime_with_ref(field="otherField")
        equip_prefabs = build_equip_prefabs_bridge(
            [_equip_script(prefab_field="riflePrefab")],
            scene_runtime,
            _guid_index({_PLAYER_CS: _PLAYER_SCRIPT_ID}),
        )
        assert equip_prefabs == {}

    def test_prefab_reference_row_in_prefabs_block(self):
        # The reference row may live under the prefabs block (a prefab-hosted
        # equip script), not just scenes — the bridge scans both.
        scene_runtime: dict[str, object] = {
            "scenes": {},
            "prefabs": {
                "some_prefab": {
                    "name": "P",
                    "template_name": "P",
                    "instances": [{
                        "instance_id": "P:1",
                        "script_id": _PLAYER_SCRIPT_ID,
                        "game_object_id": "P:9",
                        "active": True, "enabled": True, "config": {},
                    }],
                    "references": [{
                        "from": "P:1",
                        "field": "riflePrefab",
                        "index": None,
                        "target_kind": "prefab",
                        "target_ref": _RIFLE_PREFAB_ID,
                        "target_is_ui": False,
                    }],
                    "lifecycle_order": ["P:1"],
                },
            },
        }
        equip_prefabs = build_equip_prefabs_bridge(
            [_equip_script()], scene_runtime,
            _guid_index({_PLAYER_CS: _PLAYER_SCRIPT_ID}),
        )
        assert equip_prefabs == {"riflePrefab": _RIFLE_PREFAB_ID}


# ---------------------------------------------------------------------------
# Criterion 10 (b/c) — the bridge REACHES the emitted Plan + the RED proof
# ---------------------------------------------------------------------------

class TestBridgeReachesEmittedPlan:

    def test_emitted_plan_carries_equip_prefabs(self):
        scene_runtime = _scene_runtime_with_ref()
        equip_prefabs = build_equip_prefabs_bridge(
            [_equip_script()], scene_runtime,
            _guid_index({_PLAYER_CS: _PLAYER_SCRIPT_ID}),
        )
        # Mirror the pipeline: the bridge result is stashed on scene_runtime
        # before the Plan module is emitted.
        scene_runtime["equip_prefabs"] = equip_prefabs
        plan_source = generate_scene_runtime_plan_module(scene_runtime).source
        # The EMITTED Luau (not the Python dict) must carry the entry.
        assert "equip_prefabs" in plan_source
        assert "riflePrefab" in plan_source
        assert _RIFLE_PREFAB_ID in plan_source

    def test_removing_plan_key_from_allowlist_makes_emit_red(self):
        # The load-bearing guard against the dead-live-path: if "equip_prefabs"
        # is NOT in _PLAN_KEYS_FOR_HOST, the emit elides it (the live handler
        # then reads nil). Prove that omission would RED this test.
        scene_runtime = _scene_runtime_with_ref()
        scene_runtime["equip_prefabs"] = {"riflePrefab": _RIFLE_PREFAB_ID}

        original = autogen._PLAN_KEYS_FOR_HOST
        try:
            autogen._PLAN_KEYS_FOR_HOST = tuple(
                k for k in original if k != "equip_prefabs"
            )
            plan_source = generate_scene_runtime_plan_module(
                scene_runtime
            ).source
            # With the key removed from the allowlist, the emit elides it.
            assert "equip_prefabs" not in plan_source, (
                "removing 'equip_prefabs' from _PLAN_KEYS_FOR_HOST must elide "
                "it from the emitted plan (this is the dead-live-path the live "
                "allowlist entry guards against)"
            )
        finally:
            autogen._PLAN_KEYS_FOR_HOST = original

        # And the live allowlist DOES include it (regression guard).
        assert "equip_prefabs" in autogen._PLAN_KEYS_FOR_HOST


# ---------------------------------------------------------------------------
# Criterion 10 (d) — build-time collision fail-close (RISK-1 / codex AVOID-B)
# ---------------------------------------------------------------------------

class TestCollisionFailClose:

    def test_same_field_different_prefab_across_scripts_fails_closed(self):
        # Two equip scripts, each with field "riflePrefab" but DIFFERENT prefab
        # targets -> a flat map would silently misbind one. The bridge must fail
        # CLOSED at build time instead.
        cs_a = "/proj/Assets/Scripts/PlayerA.cs"
        cs_b = "/proj/Assets/Scripts/PlayerB.cs"
        sid_a, sid_b = "guid_a", "guid_b"
        scene_runtime: dict[str, object] = {
            "scenes": {
                "Game.unity": {
                    "instances": [
                        {"instance_id": "G:1", "script_id": sid_a,
                         "game_object_id": "G:91", "active": True,
                         "enabled": True, "config": {}},
                        {"instance_id": "G:2", "script_id": sid_b,
                         "game_object_id": "G:92", "active": True,
                         "enabled": True, "config": {}},
                    ],
                    "references": [
                        {"from": "G:1", "field": "riflePrefab", "index": None,
                         "target_kind": "prefab", "target_ref": "prefab_one",
                         "target_is_ui": False},
                        {"from": "G:2", "field": "riflePrefab", "index": None,
                         "target_kind": "prefab", "target_ref": "prefab_two",
                         "target_is_ui": False},
                    ],
                    "lifecycle_order": ["G:1", "G:2"],
                },
            },
            "prefabs": {},
        }
        gi = _guid_index({cs_a: sid_a, cs_b: sid_b})
        with pytest.raises(RuntimeError, match="maps to two different prefabs"):
            build_equip_prefabs_bridge(
                [_equip_script(cs_a), _equip_script(cs_b)],
                scene_runtime, gi,
            )

    def test_same_field_same_prefab_across_scripts_is_fine(self):
        # Two equip scripts sharing the field name AND the SAME prefab is NOT a
        # collision (no silent misbinding) -> one entry, no raise.
        cs_a = "/proj/Assets/Scripts/PlayerA.cs"
        cs_b = "/proj/Assets/Scripts/PlayerB.cs"
        sid_a, sid_b = "guid_a", "guid_b"
        scene_runtime: dict[str, object] = {
            "scenes": {
                "Game.unity": {
                    "instances": [
                        {"instance_id": "G:1", "script_id": sid_a,
                         "game_object_id": "G:91", "active": True,
                         "enabled": True, "config": {}},
                        {"instance_id": "G:2", "script_id": sid_b,
                         "game_object_id": "G:92", "active": True,
                         "enabled": True, "config": {}},
                    ],
                    "references": [
                        {"from": "G:1", "field": "riflePrefab", "index": None,
                         "target_kind": "prefab", "target_ref": "prefab_same",
                         "target_is_ui": False},
                        {"from": "G:2", "field": "riflePrefab", "index": None,
                         "target_kind": "prefab", "target_ref": "prefab_same",
                         "target_is_ui": False},
                    ],
                    "lifecycle_order": ["G:1", "G:2"],
                },
            },
            "prefabs": {},
        }
        gi = _guid_index({cs_a: sid_a, cs_b: sid_b})
        equip_prefabs = build_equip_prefabs_bridge(
            [_equip_script(cs_a), _equip_script(cs_b)], scene_runtime, gi,
        )
        assert equip_prefabs == {"riflePrefab": "prefab_same"}

    def test_same_script_multi_instance_different_prefab_fails_closed(self):
        # P1-B: ONE script class with TWO authored instances sharing the equip
        # field name but referencing DIFFERENT prefabs. The pre-fix code took the
        # first matching row and never raised (silent misbinding); the bridge
        # must now collect ALL target_refs for (script_id, field) and fail CLOSED
        # because the resolved set has size > 1.
        scene_runtime: dict[str, object] = {
            "scenes": {
                "Game.unity": {
                    "instances": [
                        {"instance_id": "G:1", "script_id": _PLAYER_SCRIPT_ID,
                         "game_object_id": "G:91", "active": True,
                         "enabled": True, "config": {}},
                        {"instance_id": "G:2", "script_id": _PLAYER_SCRIPT_ID,
                         "game_object_id": "G:92", "active": True,
                         "enabled": True, "config": {}},
                    ],
                    "references": [
                        {"from": "G:1", "field": "riflePrefab", "index": None,
                         "target_kind": "prefab", "target_ref": "prefab_one",
                         "target_is_ui": False},
                        {"from": "G:2", "field": "riflePrefab", "index": None,
                         "target_kind": "prefab", "target_ref": "prefab_two",
                         "target_is_ui": False},
                    ],
                    "lifecycle_order": ["G:1", "G:2"],
                },
            },
            "prefabs": {},
        }
        gi = _guid_index({_PLAYER_CS: _PLAYER_SCRIPT_ID})
        with pytest.raises(
            RuntimeError, match="maps to multiple different prefabs",
        ):
            build_equip_prefabs_bridge([_equip_script()], scene_runtime, gi)

    def test_same_script_multi_instance_same_prefab_is_fine(self):
        # The benign mirror: ONE script class, two instances, same field AND the
        # SAME prefab -> the resolved set collapses to one entry, no raise.
        scene_runtime: dict[str, object] = {
            "scenes": {
                "Game.unity": {
                    "instances": [
                        {"instance_id": "G:1", "script_id": _PLAYER_SCRIPT_ID,
                         "game_object_id": "G:91", "active": True,
                         "enabled": True, "config": {}},
                        {"instance_id": "G:2", "script_id": _PLAYER_SCRIPT_ID,
                         "game_object_id": "G:92", "active": True,
                         "enabled": True, "config": {}},
                    ],
                    "references": [
                        {"from": "G:1", "field": "riflePrefab", "index": None,
                         "target_kind": "prefab", "target_ref": _RIFLE_PREFAB_ID,
                         "target_is_ui": False},
                        {"from": "G:2", "field": "riflePrefab", "index": None,
                         "target_kind": "prefab", "target_ref": _RIFLE_PREFAB_ID,
                         "target_is_ui": False},
                    ],
                    "lifecycle_order": ["G:1", "G:2"],
                },
            },
            "prefabs": {},
        }
        gi = _guid_index({_PLAYER_CS: _PLAYER_SCRIPT_ID})
        equip_prefabs = build_equip_prefabs_bridge(
            [_equip_script()], scene_runtime, gi,
        )
        assert equip_prefabs == {"riflePrefab": _RIFLE_PREFAB_ID}


# ---------------------------------------------------------------------------
# Criterion 11 — Plan serialize/rehydrate round-trip preserves equip_prefabs
# ---------------------------------------------------------------------------

class TestPlanRoundTrip:

    def test_equip_prefabs_survives_serialize_rehydrate(self):
        # The whole scene_runtime dict is persisted in conversion_plan.json and
        # rehydrated wholesale; equip_prefabs (a top-level key) rides along like
        # prefabs/references. Prove a JSON round-trip preserves it.
        scene_runtime = _scene_runtime_with_ref()
        scene_runtime["equip_prefabs"] = {"riflePrefab": _RIFLE_PREFAB_ID}

        serialized = json.dumps({"scene_runtime": scene_runtime})
        rehydrated = json.loads(serialized)["scene_runtime"]
        assert rehydrated["equip_prefabs"] == {"riflePrefab": _RIFLE_PREFAB_ID}

        # And the rehydrated map still emits into the Plan module.
        plan_source = generate_scene_runtime_plan_module(rehydrated).source
        assert "equip_prefabs" in plan_source
        assert _RIFLE_PREFAB_ID in plan_source
