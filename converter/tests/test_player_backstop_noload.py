"""Slice 4.3 — the NON-LOAD-BEARING invariant proof for paradigm B (AC4).

This is THE proof the whole effort rests on: paradigm C holds the player
binding even if the AI ignores paradigm B (the per-script directive + the two
lexical verifier rejects) entirely. B is a conflict-reducer / cleaner-output
nicety — NEVER correctness (decisions.md §B, D-P4-reject-fail-open).

It does NOT re-prove the lowered-A competitor (that is Slice 2.4,
``test_player_corpus_dominance.py``, which this module reuses for its real
authority-bracket harness). It proves, BY EXECUTION under the REAL
``_playerPreTick`` / ``_playerPostTick`` brackets ``SceneRuntime:_tick`` runs
around the component ``pairs()`` loop, that on:

  * **AC4(a) — a CLEAN player component** that does NOT write the camera and does
    NOT call ``Humanoid:Move`` (it relies entirely on C / the host, exactly the
    shape B's directive asks the AI to emit) — C is the SOLE / LAST writer of
    ``CurrentCamera.CFrame`` / ``CameraType`` AND of the ``Humanoid:Move``
    intent. The binding holds with B's directive FOLLOWED.

  * **AC4(b) — a HAND-BROKEN player component** that IGNORES B entirely: its
    ``Update`` writes a raw ``workspace.CurrentCamera.CFrame = <junk>`` +
    ``CameraType = <junk>`` AND calls a raw ``Humanoid:Move(<junk>)`` mid-frame
    (the EXACT three things B's p1/p2 rejects target) — C STILL is the LAST
    writer of all three by last-writer ordering (its post-bracket runs after the
    component pass). This is THE non-load-bearing proof: C dominates a player
    that does precisely what B forbids, the verifier reject having "survived"
    (B fully off). No production change is needed for this to hold — if one
    were, B would be load-bearing (a real REDESIGN finding).

Non-vacuity (AC4(b)) is mutation-proven: with C's post-bracket
(``_playerPostTick``) neutralized to a no-op, the hand-broken script's raw
writes WIN — the final camera yaw is the junk sentinel and the final
``Humanoid:Move`` is the junk vector — so the dominance assertions would go RED.
Each test asserts both the live (C-dominant) AND the mutated (junk-dominant)
outcome, so the proof cannot pass vacuously.

The luau-sim classes skip cleanly without a standalone ``luau`` interpreter
(same gate as the corpus harness).
"""

from __future__ import annotations

import textwrap

import pytest

# Reuse the Slice 2.4 corpus-dominance harness wholesale: the recording
# CurrentCamera proxy + recording Humanoid (ordered :Move / .Jump logs) + the
# real SceneCameraInput wiring all live in ``_dominance_extra_setup``; the
# subprocess runner + base-mock preamble in the camera-input harness; ``_grab``
# reads a printed sentinel. The ``pytestmark`` skip gate (luau + runtime files)
# is inherited by importing the same modules and re-declaring it.
from tests._camera_input_harness import (
    CAMERA_INPUT_PATH,
    camera_input_preamble,
    run_camera_scenario,
)
from tests.test_player_corpus_dominance import (
    HOST_RUNTIME_PATH,
    _dominance_extra_setup,
    _grab,
    _luau_available,
)

pytestmark = pytest.mark.skipif(
    not _luau_available()
    or not CAMERA_INPUT_PATH.exists()
    or not HOST_RUNTIME_PATH.exists(),
    reason="needs standalone luau interpreter + runtime files",
)


# The junk sentinel the HAND-BROKEN script writes. Distinct from any value C
# produces (C's yaw is the E2E-advanced delta; C's Scriptable CameraType; C's
# move basis yaw is the non-zero C yaw) so "who wrote last" is unambiguous.
_JUNK_CAM_YAW = 9.0
_JUNK_CAMERA_TYPE = "JUNK_HANDBROKEN_CAMERA_TYPE"
_JUNK_MOVE_YAW = 9.0  # the _srcYaw the junk move vector carries


def _build_engine_snippet(*, mutate_postbracket: bool) -> str:
    """Return the luau that builds the REAL SceneRuntime over a one-CC-module
    plan, binds the player authority, and (optionally) neutralizes the C
    post-bracket to prove the dominance assertions are non-vacuous.

    When ``mutate_postbracket`` is true, ``engine._playerPostTick`` is replaced
    by a no-op on the instance (``_tick`` dispatches ``self:_playerPostTick()``,
    so an instance-level override takes effect) — C's last-writer post bracket
    is gone, so the hand-broken mid-pass writes WIN.
    """
    mutate = (
        "engine._playerPostTick = function(_self, _dt) end\n"
        if mutate_postbracket
        else ""
    )
    return textwrap.dedent("""\
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
        __MUTATE__
    """).replace("__MUTATE__", mutate)


def _drive_and_report_snippet() -> str:
    """Return the luau that pushes one E2E frame, drives the REAL ``_tick``, and
    prints the camera + move sentinels the Python side reads.

    Shared by AC4(a) (clean) and AC4(b) (hand-broken): both read the SAME
    ordered ``CurrentCamera`` write log + ``Humanoid:Move`` log, so the only
    difference between the two scenarios is the component registered into
    ``engine._meta`` (none vs the hand-broken stomper).
    """
    return textwrap.dedent("""\
        -- Push the E2E channel so C's _playerReadInput acks the seq and C's yaw
        -- advances to a clearly non-zero, non-junk value.
        workspace:SetAttribute("E2EMouseSeq", 1)
        workspace:SetAttribute("E2EMouseDeltaX", 600.0)
        workspace:SetAttribute("E2EMouseDeltaY", 0.0)
        engine:_tick(0.016)

        local cYaw = engine._player._yaw
        local camWrites = workspace._camWrites
        local lastCamYaw, lastCameraType = nil, nil
        local anyCamWrite = false
        for _, w in ipairs(camWrites) do
            if w.key == "CFrame" then
                anyCamWrite = true
                lastCamYaw = w.value._yaw
            elseif w.key == "CameraType" then
                lastCameraType = w.value
            end
        end

        local hum = workspace._humanoid
        local moveLog = hum._moveLog
        local lastMove = moveLog[#moveLog]

        print("CYAW=" .. tostring(cYaw))
        print("ANYCAMWRITE=" .. tostring(anyCamWrite))
        print("LASTCAMYAW=" .. tostring(lastCamYaw))
        print("LASTCAMERATYPE=" .. tostring(lastCameraType))
        print("MOVES=" .. tostring(#moveLog))
        if lastMove then
            print("LASTMOVESRCYAW=" .. tostring(lastMove.srcYaw))
        end
    """)


def _run_clean(*, mutate_postbracket: bool):
    """AC4(a): drive the real authority with a CLEAN player component (no camera
    write, no Humanoid:Move) registered into the tick loop. WASD held (W+D) so
    C's locomotion produces a real move; mouse delta so C's camera yaw advances.
    """
    preamble = camera_input_preamble(
        mouse_deltas=[(0.0, 0.0)] * 3,
        extra_mock_setup=_dominance_extra_setup(keys_down=["W", "D"]),
    )
    body = textwrap.dedent("""\
        __BUILD__

        -- A CLEAN player component: it follows B's directive (the host owns
        -- camera + movement). Its Update/LateUpdate touch NEITHER the camera NOR
        -- the Humanoid -- exactly the shape B asks the AI to emit. C alone must
        -- drive camera + move.
        local Clean = {}
        function Clean.Update(_self, _dt) end
        function Clean.LateUpdate(_self, _dt) end
        local comp = setmetatable({}, {__index = Clean})
        engine._meta[comp] = {
            classTable = Clean, scriptId = "player",
            activeInHierarchy = true, enabled = true,
        }

        __DRIVE__
    """).replace("__BUILD__", _build_engine_snippet(
        mutate_postbracket=mutate_postbracket,
    )).replace("__DRIVE__", _drive_and_report_snippet())
    return run_camera_scenario(preamble, body)


def _run_handbroken(*, mutate_postbracket: bool):
    """AC4(b): drive the real authority with a HAND-BROKEN player component that
    IGNORES B entirely — raw ``workspace.CurrentCamera.CFrame =`` +
    ``CameraType =`` AND a raw ``Humanoid:Move(`` mid-frame (the EXACT things
    B's p1/p2 rejects target). C must STILL dominate all three.

    WASD held (W+D) so C's locomotion produces a Move whose basis yaw == C's
    (non-zero) yaw — numerically DISTINCT from the hand-broken junk move's
    sentinel ``_srcYaw`` — so "the final :Move is C's, not the junk" is a
    value comparison, not just a count.
    """
    preamble = camera_input_preamble(
        mouse_deltas=[(0.0, 0.0)] * 3,
        extra_mock_setup=_dominance_extra_setup(keys_down=["W", "D"]),
    )
    body = textwrap.dedent("""\
        __BUILD__

        -- A HAND-BROKEN player component that IGNORES B: it does the EXACT three
        -- things B's p1/p2 rejects forbid -- a raw CurrentCamera.CFrame write, a
        -- raw CurrentCamera.CameraType write, and a raw Humanoid:Move( call --
        -- mid-frame (Update + LateUpdate). The verifier reject "survived" (B
        -- fully off). The junk camera CFrame carries a sentinel yaw; the junk
        -- move vector a sentinel _srcYaw -- both distinct from anything C writes.
        local JUNK_TYPE = "__JUNK_TYPE__"
        local function handBreak()
            local junkCF = CFrame.new(Vector3.new(0, 0, 0))
            junkCF._yaw = __JUNK_CAM_YAW__
            -- raw `workspace.CurrentCamera.CFrame =` (B's p1 target)
            workspace.CurrentCamera.CFrame = junkCF
            -- raw `workspace.CurrentCamera.CameraType =` (B's p1 target)
            workspace.CurrentCamera.CameraType = JUNK_TYPE
            -- raw `Humanoid:Move(` (B's p2 target) on the SAME Humanoid C drives
            -- (resolved via the SAME LocalPlayer.Character path), so its write
            -- lands in the same ordered :Move log C writes to.
            local lp = game:GetService("Players").LocalPlayer
            local char = lp.Character
            local hum = char and char:FindFirstChildOfClass("Humanoid")
            if hum then
                local junkMove = Vector3.new(0, 0, 0)
                junkMove._srcYaw = __JUNK_MOVE_YAW__
                hum:Move(junkMove, false)
            end
        end
        local Broken = {}
        function Broken.Update(_self, _dt) handBreak() end
        function Broken.LateUpdate(_self, _dt) handBreak() end
        local comp = setmetatable({}, {__index = Broken})
        engine._meta[comp] = {
            classTable = Broken, scriptId = "player",
            activeInHierarchy = true, enabled = true,
        }

        __DRIVE__
    """).replace("__BUILD__", _build_engine_snippet(
        mutate_postbracket=mutate_postbracket,
    )).replace("__DRIVE__", _drive_and_report_snippet()
    ).replace("__JUNK_TYPE__", _JUNK_CAMERA_TYPE
    ).replace("__JUNK_CAM_YAW__", repr(_JUNK_CAM_YAW)
    ).replace("__JUNK_MOVE_YAW__", repr(_JUNK_MOVE_YAW))
    return run_camera_scenario(preamble, body)


# --------------------------------------------------------------------------- #
# AC4(a) — a CLEAN player script (B's directive FOLLOWED) binds: C drives.
# --------------------------------------------------------------------------- #


class TestCleanPlayerBindsAC4a:

    def test_clean_player_C_drives_camera_and_move(self) -> None:
        # AC4(a): a clean player component (no camera write, no Humanoid:Move)
        # relies entirely on C. C must be the writer of CurrentCamera.CFrame /
        # CameraType AND the Humanoid:Move intent.
        rc, out, err = _run_clean(mutate_postbracket=False)
        assert rc == 0, f"luau failed: {err}\n{out}"

        cyaw = _grab(out, "CYAW=")
        # C's yaw is the E2E-advanced delta (non-zero) so the camera assertion is
        # non-vacuous (not 0==0 with a coincidental default).
        assert float(cyaw) != 0.0, (
            f"C's yaw must advance via the E2E channel; got {cyaw}\n{out}"
        )
        # C wrote the camera (the clean component never does) and the final yaw
        # is C's; the final CameraType is C's Scriptable.
        assert "ANYCAMWRITE=true" in out, f"C must write the camera\n{out}"
        assert float(_grab(out, "LASTCAMYAW=")) == float(cyaw), (
            f"final CurrentCamera.CFrame must be C's (yaw={cyaw})\n{out}"
        )
        assert _grab(out, "LASTCAMERATYPE=") == "Scriptable", (
            f"final CameraType must be C's Scriptable\n{out}"
        )
        # C drove the ONLY Humanoid:Move (the clean component makes none), so the
        # last move is C's (with C's yaw basis).
        assert int(_grab(out, "MOVES=")) >= 1, (
            f"C must drive at least one Humanoid:Move\n{out}"
        )
        assert float(_grab(out, "LASTMOVESRCYAW=")) == float(cyaw), (
            f"final Humanoid:Move must be C's (basis yaw={cyaw})\n{out}"
        )


# --------------------------------------------------------------------------- #
# AC4(b) — THE non-load-bearing proof: a HAND-BROKEN player script that does the
# EXACT things B forbids (raw camera write + Humanoid:Move) STILL binds; C
# dominates by last-writer ordering, no production change needed.
# --------------------------------------------------------------------------- #


class TestHandBrokenPlayerBindsAC4b:

    def test_handbroken_player_C_still_dominates(self) -> None:
        # AC4(b): the hand-broken component stomps the camera (junk CFrame +
        # junk CameraType) and calls a raw Humanoid:Move(junk) mid-frame -- the
        # verifier reject "survived" (B fully off). C's post-bracket runs AFTER
        # the component pass, so C is the LAST writer of all three.
        rc, out, err = _run_handbroken(mutate_postbracket=False)
        assert rc == 0, f"luau failed: {err}\n{out}"

        cyaw = _grab(out, "CYAW=")
        # Non-vacuity: C's yaw is non-zero AND not the junk sentinel, so a
        # "last == C" pass cannot coincide with the junk value.
        assert float(cyaw) != 0.0 and float(cyaw) != _JUNK_CAM_YAW, (
            f"C's yaw must be a distinctive E2E-advanced value (not the junk "
            f"{_JUNK_CAM_YAW}); got {cyaw}\n{out}"
        )

        # The hand-broken raw writes ACTUALLY landed mid-pass (non-vacuous: the
        # junk really competed). The :Move log carries BOTH the junk move (the
        # hand-broken mid-pass) and C's post move, so >= 2 entries.
        assert int(_grab(out, "MOVES=")) >= 2, (
            f"both the hand-broken raw Move and C's post Move must be logged; "
            f"the raw write must actually compete\n{out}"
        )

        # C DOMINATES all three despite the hand-broken raw writes:
        #   * final CurrentCamera.CFrame is C's E2E yaw, NOT the junk 9.0;
        #   * final CameraType is C's Scriptable, NOT the junk string;
        #   * final Humanoid:Move is C's (basis yaw == C's yaw), NOT the junk
        #     (_srcYaw == 9.0). C's no-keys move is Vector3.zero, but it is still
        #     the LAST writer -- the intent the host owns.
        assert float(_grab(out, "LASTCAMYAW=")) == float(cyaw), (
            f"C must be the last CFrame writer (yaw={cyaw}), NOT the junk "
            f"{_JUNK_CAM_YAW}\n{out}"
        )
        assert _grab(out, "LASTCAMERATYPE=") == "Scriptable", (
            f"C must be the last CameraType writer (Scriptable), NOT the junk "
            f"{_JUNK_CAMERA_TYPE!r}\n{out}"
        )
        last_move_yaw = float(_grab(out, "LASTMOVESRCYAW="))
        assert last_move_yaw == float(cyaw), (
            f"final Humanoid:Move must be C's post-bracket move (basis "
            f"yaw={cyaw}), NOT the hand-broken raw Move (basis yaw="
            f"{_JUNK_MOVE_YAW}); got {last_move_yaw}\n{out}"
        )
        assert last_move_yaw != _JUNK_MOVE_YAW, (
            f"final Humanoid:Move must NOT be the hand-broken junk move "
            f"(_srcYaw={_JUNK_MOVE_YAW}); got {last_move_yaw}\n{out}"
        )

    def test_handbroken_dominance_is_non_vacuous_mutation(self) -> None:
        # MUTATION (non-vacuity): with C's post-bracket (_playerPostTick)
        # neutralized to a no-op, the hand-broken mid-pass raw writes are the
        # LAST writers -- the camera yaw becomes the junk 9.0 and the final
        # Humanoid:Move is the junk vector. This PROVES the AC4(b) assertions
        # above are load-bearing: remove C's post bracket and they go RED.
        rc, out, err = _run_handbroken(mutate_postbracket=True)
        assert rc == 0, f"luau failed: {err}\n{out}"

        # The camera's last writer is now the hand-broken junk (no C post-write).
        assert float(_grab(out, "LASTCAMYAW=")) == _JUNK_CAM_YAW, (
            f"MUTATION: without C's post-bracket the hand-broken raw CFrame "
            f"(junk yaw {_JUNK_CAM_YAW}) MUST win -- proving the live assertion "
            f"is non-vacuous\n{out}"
        )
        assert _grab(out, "LASTCAMERATYPE=") == _JUNK_CAMERA_TYPE, (
            f"MUTATION: without C's post-bracket the hand-broken raw CameraType "
            f"({_JUNK_CAMERA_TYPE!r}) MUST win\n{out}"
        )
        # The final Humanoid:Move is now the hand-broken junk (C's post Move
        # never runs).
        assert float(_grab(out, "LASTMOVESRCYAW=")) == _JUNK_MOVE_YAW, (
            f"MUTATION: without C's post-bracket the hand-broken raw Move (junk "
            f"_srcYaw {_JUNK_MOVE_YAW}) MUST be the last :Move writer\n{out}"
        )
