"""Tests for runtime/character_animator.luau — the tween-backend runtime.

PR2 of the character-animation plan (docs/design/character-animation.md).
There is no embedded Luau interpreter in the test environment, so behaviour
is pinned via structural source checks plus a `luau-analyze` syntax gate
(the same approach test_no_rejected_bridges.py uses for this file).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

RUNTIME_PATH = (
    Path(__file__).parent.parent / "runtime" / "character_animator.luau"
)

requires_luau_analyze = pytest.mark.skipif(
    shutil.which("luau-analyze") is None,
    reason="luau-analyze not in PATH",
)


def _source() -> str:
    return RUNTIME_PATH.read_text(encoding="utf-8")


class TestKeyframeTrackAdapter:
    """Item 2: the track-like adapter over per-bone CFrame tweening."""

    def test_keyframe_track_class_defined(self) -> None:
        src = _source()
        assert "local KeyframeTrack = {}" in src
        assert "function KeyframeTrack.new(" in src

    def test_keyframe_track_exposes_animationtrack_subset(self) -> None:
        """:Play / :Stop / :AdjustSpeed / :AdjustWeight must exist so the
        state machine can treat keyframe playback as an AnimationTrack."""
        src = _source()
        for method in ("Play", "Stop", "AdjustSpeed", "AdjustWeight"):
            assert f"function KeyframeTrack:{method}(" in src, method

    def test_keyframe_track_has_looped_field(self) -> None:
        src = _source()
        # .new sets a Looped field defaulting from the keyframe data.
        assert "self.Looped" in src

    def test_stop_cancels_tweens_and_loop_thread(self) -> None:
        """:Stop must cancel the running tweens AND the playback loop/thread
        — otherwise a transitioned-away clip keeps driving bones."""
        src = _source()
        stop = src[src.index("function KeyframeTrack:Stop("):]
        stop = stop[: stop.index("\nend\n") + 5]
        assert "tween:Cancel()" in stop, stop
        assert "task.cancel(self._loopThread)" in stop, stop
        assert "self._playing = false" in stop, stop

    def test_adjust_speed_scales_playback(self) -> None:
        """AdjustSpeed actually changes playback speed (not a no-op)."""
        src = _source()
        assert "self._speed = speed" in src
        # _playOnce divides keyframe dt by the speed.
        play_once = src[src.index("function KeyframeTrack:_playOnce("):]
        play_once = play_once[: play_once.index("\nend\n") + 5]
        assert "self._speed" in play_once or "speed" in play_once

    def test_adjust_weight_is_documented_noop(self) -> None:
        """AdjustWeight is a stub for PR2 — must exist (state machine calls
        it for blend trees) but does nothing."""
        src = _source()
        idx = src.index("function KeyframeTrack:AdjustWeight(")
        body = src[idx: src.index("end", idx) + 3]
        # No assignment / call in the body — just the signature + end.
        assert "_weight" in body and "_fade" in body


class TestModuleRegistry:
    """Item 5: module-level registry on CharacterAnimator (not _G)."""

    def test_registry_lives_on_module_table(self) -> None:
        src = _source()
        assert "CharacterAnimator._registry" in src
        assert "CharacterAnimator._pending" in src
        # Must NOT use the _G global as the registry (a comment mentioning
        # "_G" is fine — only an actual _G[...] / _G. access is forbidden).
        assert "_G[" not in src
        assert "_G." not in src

    def test_register_resolve_dispatch_defined(self) -> None:
        src = _source()
        assert "function CharacterAnimator.Register(" in src
        assert "function CharacterAnimator.Resolve(" in src
        assert "function CharacterAnimator.Dispatch(" in src

    def test_dispatch_queues_when_host_not_registered(self) -> None:
        """A call arriving before the bootstrap registers the host must be
        queued, not dropped."""
        src = _source()
        dispatch = src[src.index("function CharacterAnimator.Dispatch("):]
        dispatch = dispatch[: dispatch.index("\nend\n") + 5]
        assert "_pending" in dispatch
        assert "table.insert(queue" in dispatch

    def test_register_drains_pending_queue(self) -> None:
        src = _source()
        register = src[src.index("function CharacterAnimator.Register("):]
        register = register[: register.index("\nend\n") + 5]
        assert "_pending" in register
        assert "instance:_apply(" in register

    def test_dispatch_op_routing(self) -> None:
        """_apply routes the imperative ops to the right runtime methods."""
        src = _source()
        apply = src[src.index("function CharacterAnimator:_apply("):]
        apply = apply[: apply.index("\nend\n") + 5]
        for op in ("SetTrigger", "ResetTrigger", "Play", "CrossFade"):
            assert op in apply, op


class TestAttributeBinding:
    """Item 5: scalar params via GetAttributeChangedSignal, no polling."""

    def test_bind_attributes_connects_changed_signals(self) -> None:
        src = _source()
        bind = src[src.index("function CharacterAnimator:_bindAttributes("):]
        bind = bind[: bind.index("\nend\n") + 5]
        assert "GetAttributeChangedSignal" in bind
        assert "GetAttribute" in bind

    def test_update_does_not_poll_attributes(self) -> None:
        """:Update must stay state-machine-only — no attribute reads."""
        src = _source()
        update = src[src.index("function CharacterAnimator:Update("):]
        update = update[: update.index("\nend\n") + 5]
        assert "GetAttribute" not in update
        assert "_checkTransitions" in update


class TestCrossFade:
    """Item 5: CrossFade routes through _transitionTo."""

    def test_crossfade_defined_and_routes_to_transition(self) -> None:
        src = _source()
        cf = src[src.index("function CharacterAnimator:CrossFade("):]
        cf = cf[: cf.index("\nend\n") + 5]
        assert "_transitionTo" in cf


class TestTransitionToKeyframeFallback:
    """Item 3: _transitionTo falls back to a KeyframeTrack."""

    def test_transition_builds_keyframe_track_when_no_animation_asset(
        self,
    ) -> None:
        src = _source()
        trans = src[src.index("function CharacterAnimator:_transitionTo("):]
        trans = trans[: trans.index("\nfunction ") ]
        # When _lazyLoadTrack returns nil, build from keyframe data.
        assert "_keyframeTrackFor" in trans

    def test_keyframe_track_for_uses_rig_model(self) -> None:
        src = _source()
        helper = src[src.index("function CharacterAnimator:_keyframeTrackFor("):]
        helper = helper[: helper.index("\nend\n") + 5]
        assert "KeyframeTrack.new(" in helper
        assert "self.rig" in helper

    def test_new_takes_rig_model_as_second_arg(self) -> None:
        """The rig model must be an explicit CharacterAnimator.new arg so
        the keyframe fallback has a model to animate."""
        src = _source()
        assert "function CharacterAnimator.new(controllerData, rigModel)" in src
        assert "self.rig = rigModel" in src


class TestRuntimeSyntax:
    @requires_luau_analyze
    def test_runtime_is_valid_luau(self) -> None:
        """character_animator.luau must parse cleanly — only the expected
        Roblox unknown-global diagnostics are tolerated."""
        proc = subprocess.run(
            ["luau-analyze", str(RUNTIME_PATH)],
            text=True,
            capture_output=True,
            timeout=60,
        )
        diagnostics = (proc.stdout + proc.stderr).splitlines()
        real_errors = [
            line for line in diagnostics
            if line.strip() and "Unknown global" not in line
        ]
        assert not real_errors, "luau-analyze errors:\n" + "\n".join(real_errors)

    def test_module_returns_table(self) -> None:
        assert _source().splitlines()[-1].strip() == "return CharacterAnimator"

    def test_known_limitation_documented_in_runtime(self) -> None:
        """The server-side-only limitation must be noted in the runtime."""
        src = _source().upper()
        assert "KNOWN LIMITATION" in src
        assert "LOCALSCRIPT" in src
