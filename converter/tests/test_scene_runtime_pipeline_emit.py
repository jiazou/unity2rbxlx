"""PR4: pipeline ``_subphase_inject_scene_runtime`` integration tests.

Asserts the conversion-time wiring:
  * Subphase runs ONLY under ``ctx.scene_runtime_mode == "generic"``.
  * Subphase is a no-op when no runtime-bearing modules exist.
  * Under generic with runtime-bearing modules, the four scripts land
    (SceneRuntime, SceneRuntimePlan, SceneRuntimeClient,
    SceneRuntimeServer) with correct parent paths.
  * Re-running the subphase replaces existing copies rather than
    duplicating (idempotency for ``--phase write_output`` resumes).
  * Cross-domain edges are stamped onto ``ctx.scene_runtime`` and
    appended to ``UNCONVERTED.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.conversion_context import ConversionContext  # noqa: E402
from core.roblox_types import RbxPlace, RbxScript  # noqa: E402
from converter.pipeline import Pipeline  # noqa: E402


def _make_pipeline_with_ctx(
    tmp_path: Path,
    scene_runtime_mode: str,
    scene_runtime: dict,
) -> Pipeline:
    """Build a Pipeline with the minimum state ``_subphase_inject_scene_runtime``
    reads. We don't run the full Pipeline.__init__; the subphase touches
    only ``self.ctx`` and ``self.state.rbx_place``.
    """
    p = Pipeline.__new__(Pipeline)
    p.ctx = ConversionContext(unity_project_path=str(tmp_path / "project"))
    p.ctx.scene_runtime_mode = scene_runtime_mode
    p.ctx.scene_runtime = scene_runtime
    p.output_dir = tmp_path
    p.output_dir.mkdir(parents=True, exist_ok=True)

    state = MagicMock()
    state.rbx_place = RbxPlace()
    state.rbx_place.scripts = []
    p.state = state
    return p


def _runtime_bearing_plan() -> dict:
    return {
        "modules": {
            "guid-foo": {
                "stem": "Foo",
                "runtime_bearing": True,
                "domain": "client",
                "module_path": "ReplicatedStorage/Foo",
            },
        },
        "scenes": {},
        "prefabs": {},
        "domain_overrides": {},
    }


class TestSubphaseGating:

    def test_legacy_mode_is_noop(self, tmp_path):
        p = _make_pipeline_with_ctx(
            tmp_path, "legacy", _runtime_bearing_plan(),
        )
        p._subphase_inject_scene_runtime()
        assert p.state.rbx_place.scripts == [], (
            "legacy mode must skip the subphase entirely"
        )

    def test_generic_no_runtime_bearing_is_noop(self, tmp_path):
        plan = {
            "modules": {
                "guid-helper": {
                    "stem": "Helper",
                    "runtime_bearing": False,
                },
            },
            "scenes": {}, "prefabs": {}, "domain_overrides": {},
        }
        p = _make_pipeline_with_ctx(tmp_path, "generic", plan)
        p._subphase_inject_scene_runtime()
        assert p.state.rbx_place.scripts == [], (
            "generic mode with no runtime-bearing modules must skip emit"
        )

    def test_generic_with_runtime_bearing_emits_four_scripts(self, tmp_path):
        p = _make_pipeline_with_ctx(
            tmp_path, "generic", _runtime_bearing_plan(),
        )
        p._subphase_inject_scene_runtime()
        names = sorted(s.name for s in p.state.rbx_place.scripts)
        assert names == [
            "SceneRuntime",
            "SceneRuntimeClient",
            "SceneRuntimePlan",
            "SceneRuntimeServer",
        ]

    def test_parent_paths_set_correctly(self, tmp_path):
        p = _make_pipeline_with_ctx(
            tmp_path, "generic", _runtime_bearing_plan(),
        )
        p._subphase_inject_scene_runtime()
        by_name = {s.name: s for s in p.state.rbx_place.scripts}
        assert by_name["SceneRuntime"].parent_path == "ReplicatedStorage"
        assert by_name["SceneRuntimePlan"].parent_path == "ReplicatedStorage"
        assert by_name["SceneRuntimeClient"].parent_path == (
            "StarterPlayer.StarterPlayerScripts"
        )
        assert by_name["SceneRuntimeServer"].parent_path == (
            "ServerScriptService"
        )
        assert by_name["SceneRuntime"].script_type == "ModuleScript"
        assert by_name["SceneRuntimePlan"].script_type == "ModuleScript"
        assert by_name["SceneRuntimeClient"].script_type == "LocalScript"
        assert by_name["SceneRuntimeServer"].script_type == "Script"


class TestIdempotency:

    def test_rerun_does_not_duplicate(self, tmp_path):
        p = _make_pipeline_with_ctx(
            tmp_path, "generic", _runtime_bearing_plan(),
        )
        p._subphase_inject_scene_runtime()
        names_first = sorted(s.name for s in p.state.rbx_place.scripts)
        # Pre-seed an old "edited" version to ensure replacement, not
        # append.
        p.state.rbx_place.scripts.append(RbxScript(
            name="UnrelatedAutogen",
            source="-- another script",
            script_type="Script",
        ))
        p._subphase_inject_scene_runtime()
        names_second = sorted(s.name for s in p.state.rbx_place.scripts)
        # Unrelated script survives, SceneRuntime* are not duplicated.
        assert names_second == names_first + ["UnrelatedAutogen"]
        runtime_count = sum(
            1 for s in p.state.rbx_place.scripts if s.name == "SceneRuntime"
        )
        assert runtime_count == 1


class TestCrossDomainReport:

    def test_no_edges_no_unconverted_write(self, tmp_path):
        # Plan has runtime-bearing but no cross-domain refs -> no
        # UNCONVERTED.md should be created.
        p = _make_pipeline_with_ctx(
            tmp_path, "generic", _runtime_bearing_plan(),
        )
        p._subphase_inject_scene_runtime()
        assert not (tmp_path / "UNCONVERTED.md").exists()
        assert p.ctx.scene_runtime.get("cross_domain_edges") == []

    def test_edges_written_to_unconverted_md(self, tmp_path):
        plan = {
            "modules": {
                "src": {"stem": "Src", "runtime_bearing": True,
                        "domain": "client", "module_path": "RS/Src"},
                "tgt": {"stem": "Tgt", "runtime_bearing": True,
                        "domain": "server", "module_path": "RS/Tgt"},
            },
            "scenes": {
                "A.unity": {
                    "instances": [
                        {"instance_id": "A.unity:1", "script_id": "src",
                         "game_object_id": "A.unity:1", "active": True,
                         "enabled": True, "config": {}},
                        {"instance_id": "A.unity:2", "script_id": "tgt",
                         "game_object_id": "A.unity:2", "active": True,
                         "enabled": True, "config": {}},
                    ],
                    "references": [{
                        "from": "A.unity:1",
                        "field": "peer",
                        "index": None,
                        "target_kind": "component",
                        "target_ref": "A.unity:2",
                        "target_is_ui": False,
                    }],
                    "lifecycle_order": ["A.unity:1", "A.unity:2"],
                },
            },
            "prefabs": {},
            "domain_overrides": {},
        }
        p = _make_pipeline_with_ctx(tmp_path, "generic", plan)
        p._subphase_inject_scene_runtime()

        edges = p.ctx.scene_runtime["cross_domain_edges"]
        assert len(edges) == 1
        assert edges[0]["from_script"] == "src"
        assert edges[0]["to_script"] == "tgt"

        report = (tmp_path / "UNCONVERTED.md").read_text(encoding="utf-8")
        assert "Cross-domain references" in report
        # The report renders the canonical script_id (planner key), not
        # the stem -- the script_id is stable across renames whereas
        # stems can collide.
        assert "| src | client |" in report
        assert "| tgt | server |" in report

    def test_rerun_replaces_cross_domain_block(self, tmp_path):
        # First run writes the report; second run with different edges
        # should replace, not duplicate.
        plan_v1 = {
            "modules": {
                "src": {"stem": "Src", "runtime_bearing": True,
                        "domain": "client", "module_path": "RS/Src"},
                "tgt": {"stem": "Tgt", "runtime_bearing": True,
                        "domain": "server", "module_path": "RS/Tgt"},
            },
            "scenes": {
                "A.unity": {
                    "instances": [
                        {"instance_id": "A.unity:1", "script_id": "src",
                         "game_object_id": "A.unity:1", "active": True,
                         "enabled": True, "config": {}},
                        {"instance_id": "A.unity:2", "script_id": "tgt",
                         "game_object_id": "A.unity:2", "active": True,
                         "enabled": True, "config": {}},
                    ],
                    "references": [{
                        "from": "A.unity:1", "field": "p", "index": None,
                        "target_kind": "component", "target_ref": "A.unity:2",
                        "target_is_ui": False,
                    }],
                    "lifecycle_order": ["A.unity:1", "A.unity:2"],
                },
            },
            "prefabs": {}, "domain_overrides": {},
        }
        p = _make_pipeline_with_ctx(tmp_path, "generic", plan_v1)
        p._subphase_inject_scene_runtime()
        first = (tmp_path / "UNCONVERTED.md").read_text(encoding="utf-8")
        # Re-run with same plan; report content should be byte-stable.
        p._subphase_inject_scene_runtime()
        second = (tmp_path / "UNCONVERTED.md").read_text(encoding="utf-8")
        assert first == second
        # Only one cross-domain section.
        assert second.count("## Cross-domain references") == 1

    def test_unrelated_unconverted_content_preserved(self, tmp_path):
        # If something else wrote UNCONVERTED.md before us (e.g. a
        # different "## X" section), our cross-domain block appends.
        (tmp_path / "UNCONVERTED.md").write_text(
            "## Unrelated\n\nthis stays\n", encoding="utf-8",
        )
        plan = {
            "modules": {
                "src": {"stem": "Src", "runtime_bearing": True,
                        "domain": "client", "module_path": "RS/Src"},
                "tgt": {"stem": "Tgt", "runtime_bearing": True,
                        "domain": "server", "module_path": "RS/Tgt"},
            },
            "scenes": {
                "A.unity": {
                    "instances": [
                        {"instance_id": "a:1", "script_id": "src",
                         "game_object_id": "a:1", "active": True,
                         "enabled": True, "config": {}},
                        {"instance_id": "a:2", "script_id": "tgt",
                         "game_object_id": "a:2", "active": True,
                         "enabled": True, "config": {}},
                    ],
                    "references": [{
                        "from": "a:1", "field": "p", "index": None,
                        "target_kind": "component", "target_ref": "a:2",
                        "target_is_ui": False,
                    }],
                    "lifecycle_order": ["a:1", "a:2"],
                },
            },
            "prefabs": {}, "domain_overrides": {},
        }
        p = _make_pipeline_with_ctx(tmp_path, "generic", plan)
        p._subphase_inject_scene_runtime()
        contents = (tmp_path / "UNCONVERTED.md").read_text(encoding="utf-8")
        assert "## Unrelated" in contents
        assert "this stays" in contents
        assert "## Cross-domain references" in contents
