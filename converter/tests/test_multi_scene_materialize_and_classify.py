"""Phase 2a slice 8 (round 2) — multi-scene path regression test.

Slice 8 R1 introduced the ``materialize_and_classify`` phase between
``convert_scene`` and ``write_output`` in the canonical ``PHASES`` list.
The single-scene driver runs phases via ``run_through`` which honors
``PHASES`` ordering, so the new phase fired automatically.

The multi-scene driver (``run_all_scenes``) drives ``_run_phase``
DIRECTLY in its per-scene loop. R1 left the loop body as
``convert_scene`` then ``write_output`` — silently skipping
``materialize_and_classify`` on every per-scene rbxlx, so every late-
appended autogen / runtime / scene-runtime script lost its
``parent_path`` stamp in multi-scene mode.

This test pins the per-scene call sequence. Without the fix
(``self._run_phase("materialize_and_classify")`` between the
``convert_scene`` and ``write_output`` calls in
``run_all_scenes``), this test FAILS — proving the gate is load-
bearing.

The test exercises the real ``run_all_scenes`` driver, stubbing only
the IO-heavy preconditions (GUID index build, scene discovery, scene
parsing) so it can run as part of the fast ``-m "not slow"`` CI lane.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.unity_types import ParsedScene  # noqa: E402
from converter.pipeline import Pipeline  # noqa: E402


def _make_pipeline_with_two_scenes(
    tmp_path: Path,
) -> tuple[Pipeline, list[Path]]:
    """Build a Pipeline + a pair of fake .unity scene files on disk so
    ``run_all_scenes``'s ``rglob("*.unity")`` discovery returns two
    scenes without needing a real Unity project.

    The .unity files are empty stubs; real parsing happens via the
    ``parse_scene`` monkey-patch installed by callers.
    """
    unity_project = tmp_path / "unity"
    assets = unity_project / "Assets"
    assets.mkdir(parents=True)
    scene_a = assets / "main.unity"
    scene_b = assets / "menu.unity"
    scene_a.write_text("", encoding="utf-8")
    scene_b.write_text("", encoding="utf-8")

    output = tmp_path / "out"
    output.mkdir()

    pipeline = Pipeline(str(unity_project), str(output))
    return pipeline, sorted([scene_a, scene_b])


def _install_record_only_stubs(
    pipeline: Pipeline,
    monkeypatch,
    parsed_scenes: list[Path],
) -> list[str]:
    """Replace ``_run_phase`` with a recorder and stub the IO-heavy
    pre-loop work so ``run_all_scenes`` reaches the per-scene loop.

    Returns a list that will be populated with phase names in the
    order ``run_all_scenes`` invokes them.
    """
    calls: list[str] = []

    def _record(self: Pipeline, phase: str) -> None:  # noqa: ARG001
        calls.append(phase)

    # Replace _run_phase on the instance — keeps signature-compatible
    # behavior and lets us record without touching ctx/state.
    monkeypatch.setattr(
        Pipeline, "_run_phase", _record, raising=True,
    )

    # Stub the GUID index build — the multi-scene driver assigns
    # ``state.guid_index`` from this call and logs ``total_resolved``.
    class _GuidIndexStub:
        total_resolved: int = 0

    def _fake_build(_unused) -> _GuidIndexStub:
        return _GuidIndexStub()

    monkeypatch.setattr(
        "unity.guid_resolver.build_guid_index", _fake_build,
    )

    # Stub the per-scene parser. ``run_all_scenes`` calls
    # ``parse_scene(scene_path)`` per discovered .unity file and stores
    # results in ``state.all_parsed_scenes``.
    def _fake_parse(scene_path: Path) -> ParsedScene:
        return ParsedScene(scene_path=scene_path)

    monkeypatch.setattr(
        "unity.scene_parser.parse_scene", _fake_parse,
    )

    return calls


class TestMultiScenePerScenePhaseOrder:
    """The per-scene loop in ``run_all_scenes`` must invoke
    ``convert_scene`` → ``materialize_and_classify`` → ``write_output``
    in that order, for every discovered scene."""

    def test_materialize_and_classify_runs_per_scene_between_convert_and_write(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        pipeline, scene_paths = _make_pipeline_with_two_scenes(tmp_path)
        calls = _install_record_only_stubs(
            pipeline, monkeypatch, scene_paths,
        )

        pipeline.run_all_scenes()

        # Per scene, we expect the triple
        # convert_scene → materialize_and_classify → write_output
        # to appear contiguously in the recorded sequence. With two
        # scenes that's two copies of the triple, in order, after the
        # shared phases.
        n_scenes = len(scene_paths)
        per_scene_triple = [
            "convert_scene",
            "materialize_and_classify",
            "write_output",
        ]

        # Find every "convert_scene" occurrence; each one must be
        # followed by "materialize_and_classify" then "write_output".
        convert_positions = [
            i for i, p in enumerate(calls) if p == "convert_scene"
        ]
        assert len(convert_positions) == n_scenes, (
            f"Expected {n_scenes} convert_scene invocations "
            f"(one per scene), got {len(convert_positions)}. "
            f"Full sequence: {calls}"
        )

        for pos in convert_positions:
            window = calls[pos : pos + 3]
            assert window == per_scene_triple, (
                "Per-scene phase ordering broken at index "
                f"{pos}: expected {per_scene_triple!r}, got "
                f"{window!r}. Full sequence: {calls}"
            )

    def test_materialize_and_classify_appears_for_every_scene(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """Sanity check that the count of ``materialize_and_classify``
        invocations equals the count of ``convert_scene`` invocations —
        catches the regression mode where the new phase is added
        once (before/after the loop) instead of inside it."""
        pipeline, scene_paths = _make_pipeline_with_two_scenes(tmp_path)
        calls = _install_record_only_stubs(
            pipeline, monkeypatch, scene_paths,
        )

        pipeline.run_all_scenes()

        n_convert = calls.count("convert_scene")
        n_classify = calls.count("materialize_and_classify")
        n_write = calls.count("write_output")
        assert n_convert == n_classify == n_write == len(scene_paths), (
            f"Per-scene phase counts unbalanced: "
            f"convert_scene={n_convert}, "
            f"materialize_and_classify={n_classify}, "
            f"write_output={n_write}, scenes={len(scene_paths)}. "
            f"Full sequence: {calls}"
        )
