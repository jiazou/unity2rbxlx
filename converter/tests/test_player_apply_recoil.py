"""``host.player:applyRecoil`` — the recoil kick the Phase-4 directive advertises.

REGRESSION (Phase-5 cold-Studio verify, 2026-06-10): the directive tells the AI
"recoil kicks go through host.player:applyRecoil", and the cold-transpiled
Player's Shoot calls ``self.host.player:applyRecoil(...)`` — but the host
surface only implemented ``getLookCFrame`` + ``teleport``, so every shot threw
``attempt to call missing method 'applyRecoil'`` inside the component Update
(caller/callee contract gap; caught in Studio Play, invisible to the build-time
suite because nothing executed the advertised method).

Semantic parity with ``SceneCameraInput:applyRecoil`` (the drone/turret recoil
path): additive on the pitch state, clamped to the player's pitch range. The
kick must SURVIVE to the rendered camera: ``_playerWriteCamera`` composes from
``p._pitch``, so a mid-Update kick lands in the same frame's post-write.

UNITS: the surface takes DEGREES (the directive contract) — Unity recoil
constants are degrees and the AI's natural emission of ``camRotation.x -= 2``
is ``applyRecoil(2)``; a radians surface clamp-slams the camera to the sky
(SceneCameraInput:applyRecoil stays radians; its caller is OUR lowering pass).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests._camera_input_harness import (
    CAMERA_INPUT_PATH,
    camera_input_preamble,
    run_camera_scenario,
)
from tests.test_player_authority import _build_authority_runtime

HOST_RUNTIME_PATH = (
    Path(__file__).parent.parent / "runtime" / "scene_runtime.luau"
)


def _luau_available() -> bool:
    return shutil.which("luau") is not None


pytestmark = pytest.mark.skipif(
    not _luau_available()
    or not CAMERA_INPUT_PATH.exists()
    or not HOST_RUNTIME_PATH.exists(),
    reason="needs standalone luau interpreter + runtime files",
)


# ---------------------------------------------------------------------------
# The regression: the AI's exact colon-form call MUST NOT throw, and it kicks
# C's pitch.
# ---------------------------------------------------------------------------

def test_apply_recoil_colon_form_kicks_pitch_no_throw() -> None:
    preamble = camera_input_preamble(mouse_deltas=[])
    body = _build_authority_runtime() + """
        local host = engine:_makeHostSurface({})
        local before = p._pitch
        -- The cold AI shape's exact call form: ``applyRecoil(2)`` —
        -- Unity ``camRotation.x -= 2`` => a 2-DEGREE kick (the directive's
        -- units contract). A radians surface would read 2 rad = 114.6deg and
        -- clamp-slam the camera to the sky (the second Studio-caught bug).
        local ok, err = pcall(function() host.player:applyRecoil(2) end)
        print("OK=" .. tostring(ok) .. " ERR=" .. tostring(err))
        print(string.format("KICKDEG=%.4f", math.deg(p._pitch - before)))
    """
    rc, out, err = run_camera_scenario(preamble, body)
    assert rc == 0, f"scenario failed (rc={rc}): {err}\n{out}"
    assert "OK=true" in out, out
    # 2 DEGREES — small kick, nowhere near the 80-deg clamp.
    assert "KICKDEG=2.0000" in out, out


def test_apply_recoil_dotted_form() -> None:
    preamble = camera_input_preamble(mouse_deltas=[])
    body = _build_authority_runtime() + """
        local host = engine:_makeHostSurface({})
        local before = p._pitch
        host.player.applyRecoil(-2)
        print(string.format("KICKDEG=%.4f", math.deg(p._pitch - before)))
    """
    rc, out, err = run_camera_scenario(preamble, body)
    assert rc == 0, f"scenario failed (rc={rc}): {err}\n{out}"
    assert "KICKDEG=-2.0000" in out, out


def test_apply_recoil_clamps_to_pitch_range() -> None:
    preamble = camera_input_preamble(mouse_deltas=[])
    body = _build_authority_runtime() + """
        local host = engine:_makeHostSurface({})
        host.player:applyRecoil(999)  -- absurd kick (degrees) -> clamp at _maxPitch
        print(string.format("MAXED=%.6f", p._pitch))
        host.player:applyRecoil(-999) -- and the floor
        print(string.format("MINNED=%.6f", p._pitch))
    """
    rc, out, err = run_camera_scenario(preamble, body)
    assert rc == 0, f"scenario failed (rc={rc}): {err}\n{out}"
    assert "MAXED=%.6f" % (1.396263,) in out or "MAXED=1.396263" in out, out
    assert "MINNED=-1.396263" in out, out


def test_apply_recoil_non_number_and_no_arg_are_noops() -> None:
    """Colon-form with NO argument passes the player table as arg1 — the
    number type-guard must no-op (not corrupt the pitch with a table)."""
    preamble = camera_input_preamble(mouse_deltas=[])
    body = _build_authority_runtime() + """
        local host = engine:_makeHostSurface({})
        local before = p._pitch
        local ok1 = pcall(function() host.player:applyRecoil() end)
        local ok2 = pcall(function() host.player:applyRecoil("kick") end)
        print("OK1=" .. tostring(ok1) .. " OK2=" .. tostring(ok2))
        print(string.format("DRIFT=%.6f", p._pitch - before))
        print("PITCHTYPE=" .. type(p._pitch))
    """
    rc, out, err = run_camera_scenario(preamble, body)
    assert rc == 0, f"scenario failed (rc={rc}): {err}\n{out}"
    assert "OK1=true OK2=true" in out, out
    assert "DRIFT=0.000000" in out, out
    assert "PITCHTYPE=number" in out, out


def test_apply_recoil_noop_when_no_player() -> None:
    """Server / pre-boot context (``self._player == nil``) -> safe no-op."""
    preamble = camera_input_preamble(mouse_deltas=[])
    body = _build_authority_runtime() + """
        local host = engine:_makeHostSurface({})
        engine._player = nil
        local ok = pcall(function() host.player:applyRecoil(0.1) end)
        print("OK=" .. tostring(ok))
    """
    rc, out, err = run_camera_scenario(preamble, body)
    assert rc == 0, f"scenario failed (rc={rc}): {err}\n{out}"
    assert "OK=true" in out, out


# ---------------------------------------------------------------------------
# The kick SURVIVES to the rendered camera: a mid-pass applyRecoil lands in the
# same frame's post-write (composeLook reads p._pitch).
# ---------------------------------------------------------------------------

def test_apply_recoil_survives_to_camera_write() -> None:
    preamble = camera_input_preamble(mouse_deltas=[])
    body = _build_authority_runtime() + """
        local host = engine:_makeHostSurface({})
        engine:_playerWriteCamera()
        local pitch0 = workspace.CurrentCamera.CFrame._pitch
        host.player:applyRecoil(10)  -- 10 degrees
        engine:_playerWriteCamera()
        local pitch1 = workspace.CurrentCamera.CFrame._pitch
        print(string.format("DELTADEG=%.4f", math.deg(pitch1 - pitch0)))
    """
    rc, out, err = run_camera_scenario(preamble, body)
    assert rc == 0, f"scenario failed (rc={rc}): {err}\n{out}"
    assert "DELTADEG=10.0000" in out, out


def test_host_player_surface_has_apply_recoil_and_stays_narrow() -> None:
    """The narrow-surface guard, extended: applyRecoil reachable alongside
    getLookCFrame + teleport; internals still unreachable."""
    preamble = camera_input_preamble(mouse_deltas=[])
    body = _build_authority_runtime() + """
        local host = engine:_makeHostSurface({})
        print("HASRECOIL=" .. tostring(type(host.player.applyRecoil)))
        print("HASLOOK=" .. tostring(type(host.player.getLookCFrame)))
        print("HASTP=" .. tostring(type(host.player.teleport)))
        print("SERVICES=" .. tostring(host.player._services))
        print("PLAYER=" .. tostring(host.player._player))
    """
    rc, out, err = run_camera_scenario(preamble, body)
    assert rc == 0, f"scenario failed (rc={rc}): {err}\n{out}"
    assert "HASRECOIL=function" in out, out
    assert "HASLOOK=function" in out, out
    assert "HASTP=function" in out, out
    assert "SERVICES=nil" in out, out
    assert "PLAYER=nil" in out, out
