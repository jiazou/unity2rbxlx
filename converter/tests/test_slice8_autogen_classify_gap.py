"""Phase 2a slice 8 — autogen-classify gap acceptance gate.

Design doc (``scene-runtime-architecture-ir.md`` §"Slice 8") declares:

> **Acceptance gate (CRITICAL):** golden-output diff over bundled test
> projects shows **zero** ``parent_path`` drift on autogen / runtime-
> injection scripts. Autogen scripts (``GameServerManager``,
> ``CollisionGroupSetup``, ``NavAgent``, ``EventSystem``,
> ``CinemachineRuntime``, etc.) get appended AFTER classify today; the
> lift means they're either never classified or need a follow-on
> classify-pass before ``write_output``.

Slice 8 chose Option (b) — the late-append safety net
(``_classify_late_appended_scripts``) stamps the rbxlx_writer default
container on any script whose generator left ``parent_path = None``.

This file pins the golden routing table for every autogen / runtime /
scene-runtime script the converter knows how to inject. If any one of
these drifts, the test fails with a concrete diff — providing the
"zero drift" witness the design doc requires.

The table mirrors the rbxlx_writer fallback at
``roblox/rbxlx_writer.py:1620-1632`` (the slice-8 lift does NOT
change writer behavior — late-appended scripts are still routed by
the same default; this safety net just stamps that default
explicitly so it becomes auditable in the data model rather than
implicit in the serializer).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from converter.autogen import (  # noqa: E402
    generate_camera_rig_follower_script,
    generate_collision_fidelity_recook_script,
    generate_collision_group_script,
    generate_game_server_script,
    generate_scene_runtime_client_entrypoint,
    generate_scene_runtime_server_entrypoint,
)
from converter.pipeline import Pipeline  # noqa: E402
from core.roblox_types import RbxPlace, RbxScript  # noqa: E402


# ---------------------------------------------------------------------------
# Golden table: every well-known autogen / runtime / scene-runtime
# script the converter injects, paired with the parent_path the
# rbxlx_writer default routing would have chosen (and therefore the
# parent_path the slice-8 safety net stamps explicitly).
#
# Sources:
# - autogen.py (`generate_*` factories: lines 240, 309, 426, 486, 595,
#   939, 949)
# - pipeline.py `_inject_runtime_modules` (lines 5285+, 5299+) — runtime
#   library ModuleScripts + CinemachineRuntime LocalScript
# - pipeline.py `_subphase_inject_autogen_scripts` ClientBootstrap
#   (line 3098+) — LocalScript with no explicit parent_path
#
# Drift detection: today's behavior comes from the rbxlx_writer
# fallback table at `roblox/rbxlx_writer.py:1620-1632`. The lift +
# safety-net path must produce the same routing AND stamp the field
# explicitly. ANY drift here is a design-doc acceptance-gate failure.
# ---------------------------------------------------------------------------

GOLDEN_PARENT_PATHS: dict[str, tuple[str, str]] = {
    # name → (script_type, expected_parent_path)
    # Autogen Scripts — no explicit parent_path → SSS via writer default
    "GameServerManager":         ("Script",      "ServerScriptService"),
    "CollisionGroupSetup":       ("Script",      "ServerScriptService"),
    "CollisionFidelityRecook":   ("Script",      "ServerScriptService"),
    # Autogen LocalScripts — no explicit parent_path → SPS via writer default
    "CameraRigFollower":         ("LocalScript", "StarterPlayer.StarterPlayerScripts"),
    "ClientBootstrap":           ("LocalScript", "StarterPlayer.StarterPlayerScripts"),
    # Runtime library ModuleScripts — no explicit parent_path → RS via writer default
    "NavAgent":                  ("ModuleScript", "ReplicatedStorage"),
    "EventSystem":               ("ModuleScript", "ReplicatedStorage"),
    "CharacterBridge":           ("ModuleScript", "ReplicatedStorage"),
    "ObjectPool":                ("ModuleScript", "ReplicatedStorage"),
    "SubEmitterRuntime":         ("ModuleScript", "ReplicatedStorage"),
    # CinemachineRuntime LocalScript — no explicit parent_path → SPS
    "CinemachineRuntime":        ("LocalScript", "StarterPlayer.StarterPlayerScripts"),
    # Scene-runtime entrypoints — generators set parent_path EXPLICITLY
    # in autogen.py:599/943/953. The safety net must NOT touch these.
    "SceneRuntimePlan":          ("ModuleScript", "ReplicatedStorage"),
    "SceneRuntimeClient":        ("LocalScript", "StarterPlayer.StarterPlayerScripts"),
    "SceneRuntimeServer":        ("Script",      "ServerScriptService"),
}


def _make_pipeline(tmp_path: Path) -> Pipeline:
    unity_project = tmp_path / "unity"
    (unity_project / "Assets").mkdir(parents=True)
    output = tmp_path / "out"
    output.mkdir()
    return Pipeline(str(unity_project), str(output))


def _construct_autogen_scripts() -> list[RbxScript]:
    """Build a synthetic post-injection script set covering every
    well-known autogen / runtime / scene-runtime script.

    Uses the REAL generator factories where one exists; for runtime
    library modules (NavAgent, etc.) which are constructed inline in
    ``_inject_runtime_modules``, we mirror their construction here.
    """
    scripts: list[RbxScript] = []

    # autogen.py factories (Scripts with no parent_path)
    scripts.append(generate_game_server_script())
    scripts.append(generate_collision_group_script())
    scripts.append(generate_collision_fidelity_recook_script())
    # autogen.py factories (LocalScript with no parent_path)
    scripts.append(generate_camera_rig_follower_script())
    # ClientBootstrap (LocalScript constructed inline in pipeline)
    scripts.append(RbxScript(
        name="ClientBootstrap",
        source="-- bootstrap stub",
        script_type="LocalScript",
    ))
    # Runtime library ModuleScripts (constructed inline in
    # _inject_runtime_modules with NO explicit parent_path)
    for name in ("NavAgent", "EventSystem", "CharacterBridge",
                 "ObjectPool", "SubEmitterRuntime"):
        scripts.append(RbxScript(
            name=name,
            source="-- runtime module stub",
            script_type="ModuleScript",
        ))
    # CinemachineRuntime LocalScript (no parent_path)
    scripts.append(RbxScript(
        name="CinemachineRuntime",
        source="-- cinemachine stub",
        script_type="LocalScript",
    ))
    # Scene-runtime entrypoints (factories stamp parent_path explicitly)
    scripts.append(generate_scene_runtime_client_entrypoint())
    scripts.append(generate_scene_runtime_server_entrypoint())
    # SceneRuntimePlan is generated by generate_scene_runtime_plan_module
    # which requires a plan dict; mirror its construction here.
    scripts.append(RbxScript(
        name="SceneRuntimePlan",
        source="-- plan module stub",
        script_type="ModuleScript",
        parent_path="ReplicatedStorage",
    ))

    return scripts


class TestAutogenClassifyAcceptanceGate:
    """Slice 8 acceptance gate (design doc §Slice 8 — CRITICAL):
    ZERO ``parent_path`` drift on autogen / runtime-injection scripts
    after the lift. The Option (b) safety net stamps the
    rbxlx_writer-default container explicitly; this test pins what
    "default" means for every known autogen script."""

    def test_every_known_autogen_script_gets_golden_parent_path(
        self, tmp_path: Path,
    ) -> None:
        pipeline = _make_pipeline(tmp_path)
        pipeline.state.rbx_place = RbxPlace()
        pipeline.state.rbx_place.scripts = _construct_autogen_scripts()

        # Mimic the write_output ordering: after all the injection
        # subphases, the safety-net pass runs.
        pipeline._classify_late_appended_scripts()

        actual = {s.name: (s.script_type, s.parent_path)
                  for s in pipeline.state.rbx_place.scripts}

        drift: list[str] = []
        for name, (expected_type, expected_path) in (
            GOLDEN_PARENT_PATHS.items()
        ):
            if name not in actual:
                drift.append(
                    f"  {name}: NOT INJECTED (test fixture stale?)"
                )
                continue
            got_type, got_path = actual[name]
            if got_type != expected_type:
                drift.append(
                    f"  {name}: script_type drift "
                    f"(expected {expected_type!r}, got {got_type!r})"
                )
            if got_path != expected_path:
                drift.append(
                    f"  {name}: parent_path drift "
                    f"(expected {expected_path!r}, got {got_path!r})"
                )

        assert not drift, (
            "Slice 8 acceptance gate — parent_path drift on autogen "
            "/ runtime-injection scripts:\n" + "\n".join(drift)
        )

    def test_explicit_parent_paths_preserved_by_safety_net(
        self, tmp_path: Path,
    ) -> None:
        """The scene-runtime entrypoint factories set parent_path
        EXPLICITLY (autogen.py:599 / 943 / 953). The safety-net pass
        must NOT overwrite an explicitly-set parent_path — this is the
        "scripts with explicit parent_path pass through untouched"
        contract."""
        client = generate_scene_runtime_client_entrypoint()
        server = generate_scene_runtime_server_entrypoint()
        assert client.parent_path == "StarterPlayer.StarterPlayerScripts"
        assert server.parent_path == "ServerScriptService"

        pipeline = _make_pipeline(tmp_path)
        pipeline.state.rbx_place = RbxPlace()
        pipeline.state.rbx_place.scripts = [client, server]

        # Pre-state captured.
        pre_client = pipeline.state.rbx_place.scripts[0].parent_path
        pre_server = pipeline.state.rbx_place.scripts[1].parent_path

        pipeline._classify_late_appended_scripts()

        # Post-state must match pre-state — the safety net did not
        # touch already-explicit parent_paths.
        assert pipeline.state.rbx_place.scripts[0].parent_path == pre_client
        assert pipeline.state.rbx_place.scripts[1].parent_path == pre_server
