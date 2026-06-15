"""Phase-1 (relation #8) emit tests: the emit-gate predicate, the baked
standalone ``SceneGravityCorrection`` server script, the ``write_output``
emit subphase, and the producer→consumer stash hop.

Covers AC1-8 / AC5b / AC8b / AC8c / AC8e / AC10 / AC10b / AC11 / AC12 / AC16b.
Force-shape tokens are STRUCTURAL source assertions (FIX A); emit-gate and
zero-gravity survival are pure-Python pytest. Numeric net-accel ⇒ Studio S2.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import config as _config
from converter.autogen import generate_gravity_correction_server_script
from converter.pipeline import Pipeline
from core.roblox_types import RbxPart, RbxPlace, RbxScript
from utils import luau_analyze


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _dynamic_part(name: str = "Crate", **attrs: object) -> RbxPart:
    base = {"_UnityMass": 2.0}
    base.update(attrs)
    return RbxPart(name=name, class_name="Part", anchored=False, attributes=base)


def _make_pipeline(tmp_path: Path) -> Pipeline:
    unity_project = tmp_path / "unity"
    (unity_project / "Assets").mkdir(parents=True)
    output = tmp_path / "out"
    output.mkdir()
    pipeline = Pipeline(str(unity_project), str(output))
    pipeline.state.rbx_place = RbxPlace()
    pipeline.ctx.scene_runtime_mode = "generic"
    pipeline.ctx.scene_runtime = {"gravityDesiredBaseStuds": 35.03}
    return pipeline


# --------------------------------------------------------------------------
# AC11 / AC8e -- emit-gate predicate
# --------------------------------------------------------------------------
class TestEmitGatePredicate:
    def test_fires_on_scene_dynamic_unitymass(self) -> None:
        parts = [_dynamic_part()]
        assert Pipeline._part_tree_has_dynamic_unitymass(parts) is True

    def test_fires_on_nested_dynamic_unitymass_recursively(self) -> None:
        inner = _dynamic_part("Inner")
        container = RbxPart(name="Container", class_name="Model", children=[inner])
        assert Pipeline._part_tree_has_dynamic_unitymass([container]) is True

    def test_no_fire_when_no_dynamic_unitymass(self) -> None:
        static = RbxPart(name="Floor", class_name="Part")
        assert Pipeline._part_tree_has_dynamic_unitymass([static]) is False

    def test_no_fire_on_rigidbody2d_only(self) -> None:
        """AC8e: a 2D-only game (every dynamic part carries _Rigidbody2D) does
        NOT count toward the gate -- Physics2D is OOS."""
        twod = _dynamic_part("Coin2D", _Rigidbody2D=True)
        assert Pipeline._part_tree_has_dynamic_unitymass([twod]) is False

    def test_no_fire_on_mesh_wrapped_2d_body(self) -> None:
        """AC8e: a mesh-wrapped 2D body -- the inner *_Mesh carrier holds BOTH
        _UnityMass and _Rigidbody2D (co-located via the move-list) -- is skipped."""
        inner = _dynamic_part("Coin_Mesh", _Rigidbody2D=True)
        outer = RbxPart(name="Coin", class_name="Model", children=[inner])
        assert Pipeline._part_tree_has_dynamic_unitymass([outer]) is False

    def test_fires_when_3d_present_alongside_2d(self) -> None:
        twod = _dynamic_part("Coin2D", _Rigidbody2D=True)
        threed = _dynamic_part("Crate3D")
        assert Pipeline._part_tree_has_dynamic_unitymass([twod, threed]) is True

    def test_bool_unitymass_is_not_numeric(self) -> None:
        """A bool is an int subclass in Python; it must NOT count as a numeric
        _UnityMass mass."""
        weird = RbxPart(name="X", anchored=False, attributes={"_UnityMass": True})
        assert Pipeline._part_tree_has_dynamic_unitymass([weird]) is False


# --------------------------------------------------------------------------
# AC1-8 / AC10 -- baked server script source
# --------------------------------------------------------------------------
class TestBakedServerScript:
    def test_baked_constant_uses_repr_float(self) -> None:
        src = generate_gravity_correction_server_script(35.03)
        assert "local DESIRED_G_STUDS_BASE = " + repr(35.03) in src

    def test_abs_scalar_default_target_baked(self) -> None:
        g = abs(-9.81) * _config.STUDS_PER_METER
        src = generate_gravity_correction_server_script(g)
        assert "local DESIRED_G_STUDS_BASE = " + repr(g) in src

    def test_zero_gravity_constant_survives(self) -> None:
        """AC10b at the generator: a 0.0 base bakes 0.0 (full-cancel), NOT a
        truthy default."""
        src = generate_gravity_correction_server_script(0.0)
        assert "local DESIRED_G_STUDS_BASE = " + repr(0.0) in src

    def test_tag_literal_and_helper_embedded(self) -> None:
        src = generate_gravity_correction_server_script(35.03)
        assert 'local TAG = "_ScaleGravityCorrected"' in src
        assert "local function correctDynamicAssembly(carrier, desiredBaseStuds)" in src

    def test_boot_sweep_class_agnostic_with_2d_exclusion(self) -> None:
        src = generate_gravity_correction_server_script(35.03)
        assert "for _, d in workspace:GetDescendants() do" in src
        assert (
            'd:GetAttribute("_UnityMass") ~= nil and '
            'd:GetAttribute("_Rigidbody2D") == nil' in src
        )

    def test_descendant_added_hook_deferred_with_2d_exclusion(self) -> None:
        src = generate_gravity_correction_server_script(35.03)
        assert "workspace.DescendantAdded:Connect(function(d)" in src
        assert "task.defer(function() correctDynamicAssembly(d, DESIRED_G_STUDS_BASE) end)" in src

    @pytest.mark.skipif(
        luau_analyze.luau_analyze_path() is None,
        reason="needs luau-analyze for the syntax smoke test",
    )
    def test_emitted_source_is_syntactically_valid(self) -> None:
        """AC10: the emitted Luau LOADS (no SyntaxError). Roblox-API TypeErrors
        are filtered out by syntax_errors_for_source."""
        src = generate_gravity_correction_server_script(35.03)
        errors = luau_analyze.syntax_errors_for_source(src)
        assert errors == [], errors


# --------------------------------------------------------------------------
# AC11 / AC12 -- the write_output emit subphase
# --------------------------------------------------------------------------
class TestEmitSubphase:
    def test_emits_when_scene_has_dynamic(self, tmp_path: Path) -> None:
        p = _make_pipeline(tmp_path)
        p.state.rbx_place.workspace_parts = [_dynamic_part()]
        p._subphase_inject_gravity_correction()
        names = [s.name for s in p.state.rbx_place.scripts]
        assert names.count("SceneGravityCorrection") == 1

    def test_emits_when_only_prefab_template_has_dynamic(self, tmp_path: Path) -> None:
        """AC11: prefab-clone-only game -- dynamic part ONLY in
        replicated_templates, none in workspace_parts."""
        p = _make_pipeline(tmp_path)
        p.state.rbx_place.workspace_parts = [RbxPart(name="Floor")]
        p.state.rbx_place.replicated_templates = [_dynamic_part("CrateTemplate")]
        p._subphase_inject_gravity_correction()
        assert any(
            s.name == "SceneGravityCorrection" for s in p.state.rbx_place.scripts
        )

    def test_no_emit_when_no_dynamic_anywhere(self, tmp_path: Path) -> None:
        p = _make_pipeline(tmp_path)
        p.state.rbx_place.workspace_parts = [RbxPart(name="Floor")]
        p._subphase_inject_gravity_correction()
        assert not any(
            s.name == "SceneGravityCorrection" for s in p.state.rbx_place.scripts
        )

    def test_no_emit_when_not_generic(self, tmp_path: Path) -> None:
        p = _make_pipeline(tmp_path)
        p.ctx.scene_runtime_mode = "legacy"
        p.state.rbx_place.workspace_parts = [_dynamic_part()]
        p._subphase_inject_gravity_correction()
        assert not any(
            s.name == "SceneGravityCorrection" for s in p.state.rbx_place.scripts
        )

    def test_emitted_script_routes_to_server_script_service(self, tmp_path: Path) -> None:
        """AC12: parent_path == ServerScriptService, script_type == Script."""
        p = _make_pipeline(tmp_path)
        p.state.rbx_place.workspace_parts = [_dynamic_part()]
        p._subphase_inject_gravity_correction()
        s = next(
            s for s in p.state.rbx_place.scripts if s.name == "SceneGravityCorrection"
        )
        assert s.parent_path == "ServerScriptService"
        assert s.script_type == "Script"

    def test_idempotent_on_rerun(self, tmp_path: Path) -> None:
        """AC12: re-running write_output (--phase resume) does not duplicate."""
        p = _make_pipeline(tmp_path)
        p.state.rbx_place.workspace_parts = [_dynamic_part()]
        p._subphase_inject_gravity_correction()
        p._subphase_inject_gravity_correction()
        names = [s.name for s in p.state.rbx_place.scripts]
        assert names.count("SceneGravityCorrection") == 1

    def test_does_not_clobber_user_named_script(self, tmp_path: Path) -> None:
        p = _make_pipeline(tmp_path)
        p.state.rbx_place.workspace_parts = [_dynamic_part()]
        user = RbxScript(
            name="SceneGravityCorrection",
            source="-- my own script",
            script_type="Script",
        )
        p.state.rbx_place.scripts.append(user)
        p._subphase_inject_gravity_correction()
        sgc = [
            s for s in p.state.rbx_place.scripts if s.name == "SceneGravityCorrection"
        ]
        assert len(sgc) == 1
        assert sgc[0].source == "-- my own script"


# --------------------------------------------------------------------------
# AC10b -- zero-gravity survives into the emitted constant (Python falsy guard)
# --------------------------------------------------------------------------
class TestZeroGravitySurvives:
    def test_stashed_zero_bakes_zero_not_default(self, tmp_path: Path) -> None:
        p = _make_pipeline(tmp_path)
        p.ctx.scene_runtime = {"gravityDesiredBaseStuds": 0.0}
        p.state.rbx_place.workspace_parts = [_dynamic_part()]
        p._subphase_inject_gravity_correction()
        s = next(
            s for s in p.state.rbx_place.scripts if s.name == "SceneGravityCorrection"
        )
        assert "local DESIRED_G_STUDS_BASE = " + repr(0.0) in s.source
        default = _config.STUDS_PER_METER * 9.81
        assert repr(default) not in s.source

    def test_missing_stash_falls_back_to_default(self, tmp_path: Path) -> None:
        """The is-None default still covers a legacy path that reached
        write_output without plan_scene_runtime populating the key."""
        p = _make_pipeline(tmp_path)
        p.ctx.scene_runtime = {}
        p.state.rbx_place.workspace_parts = [_dynamic_part()]
        p._subphase_inject_gravity_correction()
        s = next(
            s for s in p.state.rbx_place.scripts if s.name == "SceneGravityCorrection"
        )
        default = _config.STUDS_PER_METER * 9.81
        assert "local DESIRED_G_STUDS_BASE = " + repr(default) in s.source


# --------------------------------------------------------------------------
# AC16b -- producer→consumer stash rehydration on the SAME ctx
# --------------------------------------------------------------------------
class TestStashRehydration:
    def test_server_script_bakes_the_stashed_value(self, tmp_path: Path) -> None:
        """The value the server script bakes equals the value the stash carries
        (the 1.1 producer → 1.2 consumer hop), not a re-parse."""
        p = _make_pipeline(tmp_path)
        stashed = 12.5 * _config.STUDS_PER_METER
        p.ctx.scene_runtime = {"gravityDesiredBaseStuds": stashed}
        p.state.rbx_place.workspace_parts = [_dynamic_part()]
        p._subphase_inject_gravity_correction()
        s = next(
            s for s in p.state.rbx_place.scripts if s.name == "SceneGravityCorrection"
        )
        assert "local DESIRED_G_STUDS_BASE = " + repr(stashed) in s.source
