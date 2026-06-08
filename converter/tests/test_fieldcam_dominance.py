"""Slice 2.6 (AC2.6.4) — C dominates the field-aliased-camera cold shape's RAW
``self.cam.CFrame`` write, BY EXECUTION.

The slice-2.6 cold-Studio finding: a fresh AI transpile aliased the camera to a
FIELD (``self.cam = workspace.CurrentCamera`` in ``Awake``; ``self.cam.CFrame =
…`` in the look body), so paradigm A's camera-facet look-locator ABSTAINS — no
``self._cam:step(`` is spliced and the RAW ``self.cam.CFrame`` write SURVIVES.
The conversion-time ``player_look_unbound`` fail-closed (keyed on that A-locator
fingerprint) used to abort the build before paradigm C ever ran; slice 2.6
removed it because C binds the camera deterministically for every CC-identified
player.

This module is the build-time PROOF that C drives THIS exact cold shape. It is
the §3 raw-``CurrentCamera``-survives case driven by a REAL captured fixture
(vs the synthetic raw-writer of ``test_player_corpus_dominance.py``'s AC4b):

  1. Lower ``fieldcam_player.luau`` THE PRODUCTION WAY
     (``follow_character_paths=[the script]``) and ASSERT the pre-condition
     ``camera == 0`` (A's look-locator abstained → the raw ``self.cam.CFrame``
     write SURVIVES) so a future locator change that silently starts lowering
     this shape fails LOUDLY rather than passing vacuously.

  2. ``loadstring`` + RUN the lowered fixture under the bus-backed corpus mocks
     (reused from ``test_player_corpus_dominance``): ``Awake`` aliases
     ``self.cam`` to the recording ``workspace.CurrentCamera`` proxy, then
     ``Rotate`` (mapped onto the component ``Update`` pass) writes the raw
     ``self.cam.CFrame`` mid-pass — bracketed by the REAL ``_playerPreTick`` /
     ``_playerPostTick`` the runtime runs around the ``pairs()`` loop.

  3. Assert C is the LAST writer of ``workspace.CurrentCamera.CFrame`` BY READING
     the ordered write log — ``self.cam`` aliases ``workspace.CurrentCamera``
     (the SAME object), so C's post-bracket ``_playerWriteCamera`` overwrites the
     very cell A's raw write stomped. NON-VACUOUS: A's raw write (its distinctive
     pivot-yaw) is present in the log mid-pass, and the FINAL entry is C's
     E2E-advanced yaw.

  4. Mutation guard: removing ``_playerPostTick`` from the driven tick makes the
     final write A's raw value → the dominance assertion goes RED (so the proof
     is load-bearing, not coincidental).
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from converter.camera_facet_lowering import lower_camera_facet
from converter.movement_facet_lowering import lower_movement_facet
from tests._camera_input_harness import (
    CAMERA_INPUT_PATH,
    camera_input_preamble,
    run_camera_scenario,
)
from tests.test_player_corpus_dominance import (
    HOST_RUNTIME_PATH,
    _Script,
    _dominance_extra_setup,
    _embed,
    _grab,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "player_shapes"


def _luau_available() -> bool:
    return shutil.which("luau") is not None


pytestmark = pytest.mark.skipif(
    not _luau_available()
    or not CAMERA_INPUT_PATH.exists()
    or not HOST_RUNTIME_PATH.exists(),
    reason="needs standalone luau interpreter + runtime files",
)


def _lower_fieldcam_production() -> tuple[str, int, int]:
    """Lower the fieldcam fixture THE PRODUCTION WAY and return
    ``(lowered_source, camera_count, move_count)``. The camera count MUST be 0
    here (A's look-locator abstains on the field-aliased camera → the raw
    ``self.cam.CFrame`` write survives) — that abstention is the whole point of
    this shape."""
    src = (_FIXTURES / "fieldcam_player.luau").read_text(encoding="utf-8")
    s = _Script(src)
    camera = lower_camera_facet([s], follow_character_paths=[s])
    move = lower_movement_facet([s])
    return s.luau_source, camera, move


def _fieldcam_body(*, lowered_source: str, post_tick: bool) -> str:
    """Scenario body: load the lowered fieldcam fixture, alias self.cam to the
    recording CurrentCamera via its Awake, map Rotate onto the component Update
    pass, build the real authority, drive one tick.

    ``post_tick`` controls whether the post-LateUpdate bracket
    (``_playerPostTick``) runs — set False for the mutation kill (proves the
    dominance is C's POST write, not coincidence)."""
    fixture_lit = _embed(lowered_source, "fixture")
    # When post_tick is False we override _playerPostTick to a no-op AFTER the
    # authority is built, simulating its removal (the mutation kill).
    mutation = (
        ""
        if post_tick
        else "function engine:_playerPostTick(_dt) end\n"
    )
    template = textwrap.dedent("""\
        -- require() on the SceneCameraInput placeholder returns the REAL module
        -- (the lazy-acquire the movement lowerer injected into Move uses it).
        local _origRequire = require
        require = function(target)
            if type(target) == "table" and target.__sceneCameraInputModule then
                return SceneCameraInput
            end
            return _origRequire(target)
        end

        local PlayerChunk = assert(loadstring(
            "return (function() " .. __FIXTURE_LIT__ .. " end)()",
            "lowered_fieldcam"))
        local LoweredPlayer = PlayerChunk()
        assert(type(LoweredPlayer) == "table",
            "lowered fixture must return its module table")

        -- The lowered A controller instance. It carries the rig + uis + the
        -- look-math fields the raw Rotate reads (sensitivity / min/max pitch).
        local rig = {}
        function rig:GetPivot() return CFrame.new(Vector3.new(0, 0, 0)) end
        function rig:PivotTo(_cf) end
        local aComp = setmetatable({
            gameObject = rig,
            uis = game:GetService("UserInputService"),
            sensitivity = 1.0,
            minAngle = -80.0,
            maxAngle = 80.0,
        }, LoweredPlayer)
        -- GetComponent is a host-injected method the runtime provides on a real
        -- component; stub it so the fixture's Awake (which fetches AudioSource /
        -- CharacterController) runs. The look path only needs self.cam.
        function aComp:GetComponent(_kind) return nil end

        -- Awake aliases self.cam = workspace.CurrentCamera (the RECORDING proxy),
        -- so the raw ``self.cam.CFrame = …`` write in Rotate lands in the ordered
        -- camera-write log — the SAME cell C writes via workspace.CurrentCamera.
        LoweredPlayer.Awake(aComp)
        assert(aComp.cam ~= nil,
            "Awake must alias self.cam to workspace.CurrentCamera")

        -- Map the lowered fixture's Rotate (the raw self.cam.CFrame competitor)
        -- onto the component Update pass so the real _tick pairs() loop drives it
        -- bracketed by the REAL _playerPreTick / _playerPostTick.
        local classTable = {}
        function classTable.Update(self, dt)
            LoweredPlayer.Rotate(self, dt)
        end

        local plan = {modules = {
            player = {stem = "Player", runtime_bearing = true,
                      has_character_controller = true},
        }}
        local services = servicesFor(plan, {}, {})
        services.isClient = true
        services.userInputService = game:GetService("UserInputService")
        services.players = game:GetService("Players")
        services.cameraAdvance = SceneCameraInput._advance
        services.cameraComposeLook = SceneCameraInput._composeLook
        local engine = SceneRuntime.new(services, plan)
        engine:_initPlayerAuthority()
        assert(engine._player ~= nil, "authority must bind on the CC module")
        __MUTATION__
        engine._meta[aComp] = {
            classTable = classTable, scriptId = "player",
            activeInHierarchy = true, enabled = true,
        }

        -- Push the E2E channel so C's yaw advances to a distinctive non-zero
        -- value (C acks first; the raw A write uses the rig pivot yaw = 0).
        workspace:SetAttribute("E2EMouseSeq", 1)
        workspace:SetAttribute("E2EMouseDeltaX", 1000.0)
        workspace:SetAttribute("E2EMouseDeltaY", 0.0)
        engine:_tick(0.016)

        local cYaw = engine._player._yaw
        local camWrites = workspace._camWrites
        local lastCamYaw, sawRawWrite, anyCamWrite = nil, false, false
        for _, w in ipairs(camWrites) do
            if w.key == "CFrame" then
                anyCamWrite = true
                lastCamYaw = w.value._yaw
                -- A's raw write uses the rig pivot (yaw 0), distinct from C's.
                if w.value._yaw ~= cYaw then sawRawWrite = true end
            end
        end
        print("CYAW=" .. tostring(cYaw))
        print("ANYCAMWRITE=" .. tostring(anyCamWrite))
        print("SAWRAWWRITE=" .. tostring(sawRawWrite))
        print("LASTCAMYAW=" .. tostring(lastCamYaw))
    """)
    return (template
            .replace("__FIXTURE_LIT__", fixture_lit)
            .replace("__MUTATION__", mutation))


def _run_fieldcam(*, post_tick: bool):
    lowered_source, camera, move = _lower_fieldcam_production()
    preamble = camera_input_preamble(
        mouse_deltas=[(0.0, 0.0)] * 3,
        # Drive forward so the raw Rotate's clamp path executes; no keys needed
        # for the camera-write competitor itself.
        extra_mock_setup=_dominance_extra_setup(keys_down=[]),
    )
    body = _fieldcam_body(lowered_source=lowered_source, post_tick=post_tick)
    rc, out, err = run_camera_scenario(preamble, body)
    return rc, out, err, camera, move


class TestFieldcamDominance:

    def test_lower_count_precondition(self) -> None:
        # AC2.6.4 precondition: A's camera look-locator ABSTAINS on the
        # field-aliased shape (camera == 0 → the raw self.cam.CFrame write
        # survives). Move still lowers (1). A future locator change that starts
        # lowering this shape fails HERE, loudly.
        _src, camera, move = _lower_fieldcam_production()
        assert camera == 0, (
            f"fieldcam: A's look-locator must ABSTAIN (raw self.cam.CFrame "
            f"survives); got camera={camera}"
        )
        assert move == 1, f"fieldcam: WASD body should still lower; got move={move}"
        assert "self.cam.CFrame =" in _src, (
            "the raw field-aliased camera write must survive lowering"
        )

    def test_C_dominates_raw_fieldcam_camera_write(self) -> None:
        # AC2.6.4: C is the LAST writer of workspace.CurrentCamera.CFrame even
        # though the field-aliased A Rotate stomped self.cam.CFrame (same object)
        # mid-Update. Read off the ordered write log, NOT a string match.
        rc, out, err, camera, move = _run_fieldcam(post_tick=True)
        assert camera == 0, "fieldcam: precondition drift (look-locator lowered)"
        assert rc == 0, f"luau failed: {err}\n{out}"

        # Non-vacuity: the raw A camera write ACTUALLY landed mid-pass (a yaw
        # that is NOT C's), AND C's E2E-advanced yaw is distinctive (non-zero).
        assert "ANYCAMWRITE=true" in out, f"no camera write logged\n{out}"
        assert "SAWRAWWRITE=true" in out, (
            f"the raw self.cam.CFrame write (yaw != C's) must land mid-pass — "
            f"proves A is ACTIVE + dominance non-vacuous\n{out}"
        )
        cyaw = _grab(out, "CYAW=")
        assert float(cyaw) != 0.0, (
            f"C's yaw must advance via the E2E channel C acks first; got {cyaw}\n{out}"
        )
        # C dominates: the FINAL CurrentCamera.CFrame is C's post-write.
        last = _grab(out, "LASTCAMYAW=")
        assert float(last) == float(cyaw), (
            f"final CurrentCamera.CFrame must be C's post-write (yaw={cyaw}); "
            f"got last-writer yaw={last}\n{out}"
        )

    def test_dominance_is_load_bearing_mutation(self) -> None:
        # Mutation kill: WITHOUT the post-LateUpdate bracket, the raw A
        # self.cam.CFrame write is the LAST writer → the dominance assertion
        # would go RED. Proves the proof above is C's POST write, not coincidence.
        rc, out, err, camera, _move = _run_fieldcam(post_tick=False)
        assert camera == 0, "fieldcam: precondition drift"
        assert rc == 0, f"luau failed: {err}\n{out}"
        cyaw = _grab(out, "CYAW=")
        last = _grab(out, "LASTCAMYAW=")
        # With _playerPostTick neutralized, C is NOT the last writer — the raw A
        # write (yaw != C's) wins. This is the kill the real test guards against.
        assert float(last) != float(cyaw), (
            f"mutation: with no _playerPostTick the raw A write must be last "
            f"(last yaw {last} should NOT equal C's {cyaw})\n{out}"
        )
        assert "SAWRAWWRITE=true" in out, (
            f"mutation: the raw A write must still land mid-pass\n{out}"
        )
