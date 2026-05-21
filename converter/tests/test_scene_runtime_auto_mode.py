"""PR5: ``--scene-runtime=auto`` mode + fail-closed signal aggregation.

Covers two layers:

1. ``contract_pipeline.detect_fail_closed_signals`` -- pure function that
   walks the already-transpiled artifacts + planner ``scene_runtime`` to
   surface the seven ``FailClosed`` kinds the spec defines
   (``verifier``, ``require_missing``, ``require_collision``,
   ``runtime_bearing_collision``, ``stub_strategy``, ``both_side_api``,
   ``intra_class_conflict``, ``reachability_conflict``).

2. ``Pipeline._check_auto_fail_closed`` -- the subphase that consumes
   the function above and decides whether ``auto`` routes to ``generic``
   or falls back to ``legacy``. The PR5 conservative semantics: route
   to legacy on any signal; route to generic when the signal list is
   empty. The deferred "byte-identical legacy re-route" + "per-module
   coexistence" are documented in ``scene-runtime-pr5-followups.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.conversion_context import ConversionContext  # noqa: E402
from core.roblox_types import RbxPlace  # noqa: E402
from converter.code_transpiler import (  # noqa: E402
    TranspilationResult,
    TranspiledScript,
)
from converter.contract_pipeline import (  # noqa: E402
    FailClosed,
    detect_fail_closed_signals,
)
from converter.pipeline import Pipeline  # noqa: E402
from unity.script_analyzer import ScriptInfo  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_script_info(
    path: Path, class_name: str = "Foo",
) -> ScriptInfo:
    return ScriptInfo(
        path=path,
        class_name=class_name,
        base_class="MonoBehaviour",
    )


def _make_transpiled(
    source_path: Path,
    *,
    strategy: str = "ai",
    warnings: list[str] | None = None,
    luau_source: str = "",
) -> TranspiledScript:
    return TranspiledScript(
        source_path=str(source_path),
        output_filename=f"{source_path.stem}.luau",
        csharp_source="",
        luau_source=luau_source,
        strategy=strategy,
        confidence=1.0,
        warnings=warnings or [],
    )


def _empty_transpilation(scripts: list[TranspiledScript]) -> TranspilationResult:
    return TranspilationResult(
        scripts=scripts,
        total_transpiled=len(scripts),
    )


# ---------------------------------------------------------------------------
# detect_fail_closed_signals — per-kind cases
# ---------------------------------------------------------------------------

class TestDetectFailClosedSignals:

    def test_empty_artifact_emits_no_signals(self, tmp_path):
        artifact = {"modules": {}, "scenes": {}, "prefabs": {}}
        transpilation = _empty_transpilation([])
        signals = detect_fail_closed_signals(
            transpilation, artifact, [], tmp_path,
        )
        assert signals == []

    def test_verifier_post_reprompt_warning_surfaces(self, tmp_path):
        cs_path = tmp_path / "Foo.cs"
        cs_path.write_text("")
        artifact = {
            "modules": {
                "guid-Foo": {
                    "stem": "Foo", "class_name": "Foo",
                    "runtime_bearing": True,
                    "module_path": "ReplicatedStorage.Foo",
                },
            },
        }
        script = _make_transpiled(
            cs_path,
            warnings=["contract-verifier (rule a, line 5): bad shape"],
        )
        transpilation = _empty_transpilation([script])
        signals = detect_fail_closed_signals(
            transpilation, artifact, [_make_script_info(cs_path)], tmp_path,
        )
        assert [s.kind for s in signals] == ["verifier"]
        assert "Foo.cs" in signals[0].detail

    def test_pre_reprompt_warning_only_does_not_fail_closed(self, tmp_path):
        """``contract-verifier-pre`` is the FIRST AI output's violation
        that the reprompt later fixed. Only POST-reprompt warnings
        (the bare ``contract-verifier`` prefix) feed fail-closed."""
        cs_path = tmp_path / "Foo.cs"
        cs_path.write_text("")
        artifact = {
            "modules": {
                "guid-Foo": {
                    "stem": "Foo", "class_name": "Foo",
                    "runtime_bearing": True,
                },
            },
        }
        script = _make_transpiled(
            cs_path,
            warnings=["contract-verifier-pre (rule a, line 5): bad shape"],
        )
        transpilation = _empty_transpilation([script])
        signals = detect_fail_closed_signals(
            transpilation, artifact, [_make_script_info(cs_path)], tmp_path,
        )
        assert signals == []

    def test_stub_strategy_runtime_bearing_fails_closed(self, tmp_path):
        cs_path = tmp_path / "Foo.cs"
        cs_path.write_text("")
        artifact = {
            "modules": {
                "guid-Foo": {
                    "stem": "Foo", "class_name": "Foo",
                    "runtime_bearing": True,
                },
            },
        }
        script = _make_transpiled(cs_path, strategy="stub")
        transpilation = _empty_transpilation([script])
        signals = detect_fail_closed_signals(
            transpilation, artifact, [_make_script_info(cs_path)], tmp_path,
        )
        kinds = [s.kind for s in signals]
        assert "stub_strategy" in kinds

    def test_stub_strategy_non_runtime_bearing_does_not_fail(self, tmp_path):
        """Stub strategy on a NON-runtime-bearing module is fine -- the
        contract only constrains runtime-bearing MBs."""
        cs_path = tmp_path / "Helper.cs"
        cs_path.write_text("")
        artifact = {
            "modules": {
                "guid-Helper": {
                    "stem": "Helper", "class_name": "Helper",
                    "runtime_bearing": False,
                },
            },
        }
        script = _make_transpiled(cs_path, strategy="stub")
        transpilation = _empty_transpilation([script])
        signals = detect_fail_closed_signals(
            transpilation, artifact, [_make_script_info(cs_path)], tmp_path,
        )
        assert signals == []

    def test_runtime_bearing_stem_collision_surfaces(self, tmp_path):
        sub1 = tmp_path / "A" / "Foo.cs"
        sub2 = tmp_path / "B" / "Foo.cs"
        sub1.parent.mkdir()
        sub2.parent.mkdir()
        sub1.write_text("")
        sub2.write_text("")
        artifact = {
            "modules": {
                "guid-Foo": {
                    "stem": "Foo", "class_name": "Foo",
                    "runtime_bearing": True,
                },
            },
        }
        script_infos = [_make_script_info(sub1), _make_script_info(sub2)]
        transpilation = _empty_transpilation([])
        signals = detect_fail_closed_signals(
            transpilation, artifact, script_infos, tmp_path,
        )
        kinds = [s.kind for s in signals]
        assert "runtime_bearing_collision" in kinds

    def test_require_missing_stem_surfaces(self, tmp_path):
        cs_path = tmp_path / "Foo.cs"
        cs_path.write_text("")
        artifact = {
            "modules": {
                "guid-Foo": {
                    "stem": "Foo", "class_name": "Foo",
                    "runtime_bearing": True,
                    "module_path": "ReplicatedStorage.Foo",
                },
            },
        }
        # Foo's transpiled output requires a non-existent stem.
        script = _make_transpiled(
            cs_path,
            luau_source='local Bar = require("@scene_runtime/Bar")\n',
        )
        transpilation = _empty_transpilation([script])
        signals = detect_fail_closed_signals(
            transpilation, artifact, [_make_script_info(cs_path)], tmp_path,
        )
        kinds = [s.kind for s in signals]
        assert "require_missing" in kinds

    def test_require_collision_stem_surfaces(self, tmp_path):
        a = tmp_path / "Foo.cs"
        b = tmp_path / "Foo.cs.dup"
        a.write_text("")
        b.write_text("")
        artifact = {
            "modules": {
                "guid-Foo-1": {
                    "stem": "Foo", "class_name": "FooA",
                    "runtime_bearing": True,
                    "module_path": "ReplicatedStorage.A.Foo",
                },
                "guid-Foo-2": {
                    "stem": "Foo", "class_name": "FooB",
                    "runtime_bearing": True,
                    "module_path": "ReplicatedStorage.B.Foo",
                },
            },
        }
        consumer_path = tmp_path / "Consumer.cs"
        consumer_path.write_text("")
        script = _make_transpiled(
            consumer_path,
            luau_source='local Foo = require("@scene_runtime/Foo")\n',
        )
        transpilation = _empty_transpilation([script])
        signals = detect_fail_closed_signals(
            transpilation, artifact, [
                _make_script_info(consumer_path),
                _make_script_info(a, class_name="FooA"),
            ], tmp_path,
        )
        kinds = [s.kind for s in signals]
        assert "require_collision" in kinds

    def test_classifier_both_side_api_surfaces(self, tmp_path):
        artifact = {
            "modules": {
                "guid-Foo": {
                    "stem": "Foo", "class_name": "Foo",
                    "runtime_bearing": True,
                    "domain": "legacy",
                    "domain_signals": {"fail_closed_reason": "both_side_api"},
                },
            },
        }
        transpilation = _empty_transpilation([])
        signals = detect_fail_closed_signals(
            transpilation, artifact, [], tmp_path,
        )
        kinds = [s.kind for s in signals]
        assert "both_side_api" in kinds

    def test_classifier_intra_class_conflict_surfaces(self, tmp_path):
        artifact = {
            "modules": {
                "guid-Foo": {
                    "stem": "Foo", "class_name": "Foo",
                    "runtime_bearing": True,
                    "domain": "legacy",
                    "domain_signals": {
                        "fail_closed_reason": "intra_class_conflict",
                    },
                },
            },
        }
        transpilation = _empty_transpilation([])
        signals = detect_fail_closed_signals(
            transpilation, artifact, [], tmp_path,
        )
        kinds = [s.kind for s in signals]
        assert "intra_class_conflict" in kinds

    def test_classifier_reachability_conflict_surfaces(self, tmp_path):
        artifact = {
            "modules": {
                "guid-Foo": {
                    "stem": "Foo", "class_name": "Foo",
                    "runtime_bearing": True,
                    "domain": "legacy",
                    "domain_signals": {
                        "fail_closed_reason": "reachability_conflict",
                    },
                },
            },
        }
        transpilation = _empty_transpilation([])
        signals = detect_fail_closed_signals(
            transpilation, artifact, [], tmp_path,
        )
        kinds = [s.kind for s in signals]
        assert "reachability_conflict" in kinds

    def test_unrecognized_classifier_reason_surfaces_as_legacy(self, tmp_path):
        """R2-P1.3 (codex round 2): forward-compat must NOT fail-open
        on unknown reason strings. A runtime-bearing module sent to
        ``domain == "legacy"`` is a fail-closed signal regardless of
        which reason string the classifier stamped -- a PR6+
        classifier adding a new conflict vocabulary must not silently
        re-open auto to generic just because the auto router doesn't
        recognise the string.
        """
        artifact = {
            "modules": {
                "guid-Foo": {
                    "stem": "Foo", "runtime_bearing": True,
                    "domain": "legacy",
                    "domain_signals": {
                        "fail_closed_reason": "future_unseen_reason",
                    },
                },
            },
        }
        transpilation = _empty_transpilation([])
        signals = detect_fail_closed_signals(
            transpilation, artifact, [], tmp_path,
        )
        # An unrecognized reason surfaces under the bucketing kind
        # ``classifier_legacy`` so the auto router still treats the
        # legacy verdict as a fail-closed signal.
        kinds = [s.kind for s in signals]
        assert "classifier_legacy" in kinds
        # Detail string carries the original reason for the operator.
        legacy_signal = next(
            s for s in signals if s.kind == "classifier_legacy"
        )
        assert "future_unseen_reason" in legacy_signal.detail

    def test_legacy_domain_without_runtime_bearing_does_not_surface(
        self, tmp_path,
    ):
        """Helpers (non-runtime-bearing) sitting in ``domain == "legacy"``
        are the classifier's default verdict, NOT a fail-closed
        signal. Only runtime-bearing MBs that the classifier kicked
        back are PR5 fail-closed triggers.
        """
        artifact = {
            "modules": {
                "guid-Helper": {
                    "stem": "Helper", "runtime_bearing": False,
                    "domain": "legacy",
                    "domain_signals": {
                        "fail_closed_reason": "future_unseen_reason",
                    },
                },
            },
        }
        signals = detect_fail_closed_signals(
            _empty_transpilation([]), artifact, [], tmp_path,
        )
        # Non-runtime-bearing legacy row is not a fail-closed signal.
        assert signals == []


# ---------------------------------------------------------------------------
# Pipeline._check_auto_fail_closed -- subphase routing
# ---------------------------------------------------------------------------

def _make_pipeline_with_artifact(
    tmp_path: Path,
    mode: str,
    scene_runtime: dict | None,
    transpilation: TranspilationResult | None,
) -> Pipeline:
    """Build a Pipeline.__new__ wrapper with the minimum state
    ``_check_auto_fail_closed`` reads."""
    p = Pipeline.__new__(Pipeline)
    p.ctx = ConversionContext(unity_project_path=str(tmp_path / "project"))
    p.ctx.scene_runtime_mode = mode
    if scene_runtime is not None:
        p.ctx.scene_runtime = scene_runtime
    p.output_dir = tmp_path
    p.unity_project_path = tmp_path / "project"
    p.unity_project_path.mkdir(parents=True, exist_ok=True)

    state = MagicMock()
    state.rbx_place = RbxPlace()
    state.rbx_place.scripts = []
    state.transpilation_result = transpilation
    state.parsed_scene = None
    state.dependency_map = {}
    p.state = state
    return p


class TestCheckAutoFailClosedRouting:

    def test_legacy_mode_is_noop(self, tmp_path):
        p = _make_pipeline_with_artifact(
            tmp_path, "legacy", {"modules": {}}, _empty_transpilation([]),
        )
        p._check_auto_fail_closed()
        assert p.ctx.scene_runtime_mode == "legacy"

    def test_generic_mode_is_noop(self, tmp_path):
        p = _make_pipeline_with_artifact(
            tmp_path, "generic", {"modules": {}}, _empty_transpilation([]),
        )
        p._check_auto_fail_closed()
        # Generic stays generic; the subphase never flips an
        # explicit-generic invocation back to anything else.
        assert p.ctx.scene_runtime_mode == "generic"

    def test_auto_no_artifact_routes_to_generic(self, tmp_path):
        p = _make_pipeline_with_artifact(
            tmp_path, "auto", None, _empty_transpilation([]),
        )
        p._check_auto_fail_closed()
        assert p.ctx.scene_runtime_mode == "generic"

    def test_auto_no_transpile_no_prior_verdict_routes_to_legacy(self, tmp_path):
        """R2-P1.2 (codex round 2): when transpile was skipped (assemble/
        upload rehydrate paths) AND the persisted artifact carries
        no prior ``auto_fail_closed`` verdict, the subphase cannot
        prove generic safety -- safe choice is legacy."""
        p = _make_pipeline_with_artifact(
            tmp_path, "auto", {"modules": {}}, None,
        )
        p._check_auto_fail_closed()
        assert p.ctx.scene_runtime_mode == "legacy"
        triggers = p.ctx.scene_runtime["auto_fail_closed"]
        assert any(t["kind"] == "no_transpile_result" for t in triggers)

    def test_auto_no_transpile_prior_clean_verdict_reproduces_generic(
        self, tmp_path,
    ):
        """R2-P1.2: when transpile was skipped but a prior generic
        run on the same output dir was clean (``auto_fail_closed == []``
        on the persisted artifact), reproduce the prior verdict and
        route to generic. This is the assemble/upload happy path."""
        p = _make_pipeline_with_artifact(
            tmp_path, "auto",
            {"modules": {}, "auto_fail_closed": []},
            None,
        )
        p._check_auto_fail_closed()
        assert p.ctx.scene_runtime_mode == "generic"

    def test_auto_no_transpile_prior_dirty_verdict_reproduces_legacy(
        self, tmp_path,
    ):
        """R2-P1.2: when a prior auto run flipped to legacy, the
        rehydrate path must NOT re-open to generic just because the
        transpile artifact is absent on this invocation. Honor the
        prior verdict."""
        prior_triggers = [{"kind": "verifier", "detail": "prior fail"}]
        p = _make_pipeline_with_artifact(
            tmp_path, "auto",
            {"modules": {}, "auto_fail_closed": prior_triggers},
            None,
        )
        p._check_auto_fail_closed()
        assert p.ctx.scene_runtime_mode == "legacy"

    def test_auto_clean_routes_to_generic(self, tmp_path):
        """The PR5 -> PR7 canary gate: auto + no fail-closed signals
        routes to generic. ``ctx.scene_runtime["auto_fail_closed"]`` is
        the empty list (distinguished from "never ran" = key absent)."""
        artifact = {"modules": {}, "scenes": {}, "prefabs": {}}
        p = _make_pipeline_with_artifact(
            tmp_path, "auto", artifact, _empty_transpilation([]),
        )
        p._check_auto_fail_closed()
        assert p.ctx.scene_runtime_mode == "generic"
        assert p.ctx.scene_runtime["auto_fail_closed"] == []

    def test_auto_verifier_signal_routes_to_legacy(self, tmp_path):
        cs_path = tmp_path / "project" / "Assets" / "Foo.cs"
        cs_path.parent.mkdir(parents=True, exist_ok=True)
        cs_path.write_text("// MonoBehaviour Foo")
        artifact = {
            "modules": {
                "guid-Foo": {
                    "stem": "Foo", "class_name": "Foo",
                    "runtime_bearing": True,
                },
            },
        }
        script = _make_transpiled(
            cs_path,
            warnings=["contract-verifier (rule a, line 1): bad"],
        )
        p = _make_pipeline_with_artifact(
            tmp_path, "auto", artifact, _empty_transpilation([script]),
        )
        p._check_auto_fail_closed()
        assert p.ctx.scene_runtime_mode == "legacy"
        triggers = p.ctx.scene_runtime["auto_fail_closed"]
        assert len(triggers) >= 1
        assert any(t["kind"] == "verifier" for t in triggers)

    def test_auto_classifier_signal_routes_to_legacy(self, tmp_path):
        artifact = {
            "modules": {
                "guid-Foo": {
                    "stem": "Foo", "class_name": "Foo",
                    "runtime_bearing": True,
                    "domain": "legacy",
                    "domain_signals": {
                        "fail_closed_reason": "both_side_api",
                    },
                },
            },
        }
        p = _make_pipeline_with_artifact(
            tmp_path, "auto", artifact, _empty_transpilation([]),
        )
        p._check_auto_fail_closed()
        assert p.ctx.scene_runtime_mode == "legacy"
        triggers = p.ctx.scene_runtime["auto_fail_closed"]
        assert any(t["kind"] == "both_side_api" for t in triggers)

    def test_auto_stub_strategy_routes_to_legacy(self, tmp_path):
        cs_path = tmp_path / "project" / "Assets" / "Foo.cs"
        cs_path.parent.mkdir(parents=True, exist_ok=True)
        cs_path.write_text("")
        artifact = {
            "modules": {
                "guid-Foo": {
                    "stem": "Foo", "class_name": "Foo",
                    "runtime_bearing": True,
                },
            },
        }
        script = _make_transpiled(cs_path, strategy="stub")
        p = _make_pipeline_with_artifact(
            tmp_path, "auto", artifact, _empty_transpilation([script]),
        )
        p._check_auto_fail_closed()
        assert p.ctx.scene_runtime_mode == "legacy"
        triggers = p.ctx.scene_runtime["auto_fail_closed"]
        assert any(t["kind"] == "stub_strategy" for t in triggers)

    def test_auto_multi_signal_logs_all_kinds(self, tmp_path):
        """When several signals fire simultaneously, every trigger is
        listed in the stashed report so the operator can rank the
        diagnostic work."""
        cs_path = tmp_path / "project" / "Assets" / "Foo.cs"
        cs_path.parent.mkdir(parents=True, exist_ok=True)
        cs_path.write_text("")
        artifact = {
            "modules": {
                "guid-Foo": {
                    "stem": "Foo", "runtime_bearing": True,
                    "domain": "legacy",
                    "domain_signals": {
                        "fail_closed_reason": "intra_class_conflict",
                    },
                },
            },
        }
        script = _make_transpiled(
            cs_path,
            warnings=["contract-verifier (rule a, line 1): bad"],
        )
        p = _make_pipeline_with_artifact(
            tmp_path, "auto", artifact, _empty_transpilation([script]),
        )
        p._check_auto_fail_closed()
        assert p.ctx.scene_runtime_mode == "legacy"
        triggers = p.ctx.scene_runtime["auto_fail_closed"]
        kinds = {t["kind"] for t in triggers}
        # Both signals appear.
        assert "verifier" in kinds
        assert "intra_class_conflict" in kinds

    def test_auto_subphase_idempotent_under_no_signals(self, tmp_path):
        """Re-running ``_check_auto_fail_closed`` is safe -- the second
        call sees ``mode == "generic"`` (from the first call's flip)
        and no-ops. This matches the ``--phase write_output`` resume
        contract."""
        artifact = {"modules": {}, "scenes": {}, "prefabs": {}}
        p = _make_pipeline_with_artifact(
            tmp_path, "auto", artifact, _empty_transpilation([]),
        )
        p._check_auto_fail_closed()
        assert p.ctx.scene_runtime_mode == "generic"
        # Second call is a no-op (mode is no longer "auto").
        p._check_auto_fail_closed()
        assert p.ctx.scene_runtime_mode == "generic"


# ---------------------------------------------------------------------------
# R1-P1 (codex round 1): auto-fallback must restore pre-classifier
# ``parent_path`` mutations so the legacy .rbxlx doesn't ship with
# generic storage routing.
# ---------------------------------------------------------------------------

class TestAutoFallbackRestoresParentPaths:
    """The reachability sub-pass of the domain classifier hoists
    client-reached helpers from ``ServerStorage`` to
    ``ReplicatedStorage`` (a generic-only routing decision). PR5
    snapshots ``parent_path`` BEFORE the classifier runs (under auto)
    and ``_check_auto_fail_closed`` restores the snapshot on
    fail-closed fallback to legacy.
    """

    def test_restore_reverts_parent_path_mutation(self, tmp_path):
        from core.roblox_types import RbxScript
        artifact = {
            "modules": {
                "guid-Foo": {
                    "stem": "Foo", "runtime_bearing": True,
                    "domain": "legacy",
                    "domain_signals": {
                        "fail_closed_reason": "both_side_api",
                    },
                },
            },
        }
        p = _make_pipeline_with_artifact(
            tmp_path, "auto", artifact, _empty_transpilation([]),
        )
        # Seed a script the classifier would have mutated.
        script = RbxScript(
            name="Helper",
            source="",
            script_type="ModuleScript",
            parent_path="ServerStorage",  # original (legacy) placement
        )
        p.state.rbx_place.scripts = [script]
        # Simulate the snapshot the classifier subphase would have
        # taken, then a mutation to a generic placement. The snapshot
        # is keyed by ``id(script)`` (R2-P2.1) so duplicate-name
        # scripts each get their own restore.
        p._auto_parent_path_snapshot = {id(script): "ServerStorage"}
        script.parent_path = "ReplicatedStorage"

        p._check_auto_fail_closed()

        # Fell back to legacy AND the mutation was rolled back.
        assert p.ctx.scene_runtime_mode == "legacy"
        assert script.parent_path == "ServerStorage", (
            "Auto-fallback must restore the pre-classifier parent_path "
            "so the legacy .rbxlx doesn't ship generic storage routing"
        )

    def test_no_snapshot_is_noop(self, tmp_path):
        from core.roblox_types import RbxScript
        artifact = {
            "modules": {
                "guid-Foo": {
                    "stem": "Foo", "runtime_bearing": True,
                    "domain": "legacy",
                    "domain_signals": {
                        "fail_closed_reason": "both_side_api",
                    },
                },
            },
        }
        p = _make_pipeline_with_artifact(
            tmp_path, "auto", artifact, _empty_transpilation([]),
        )
        script = RbxScript(
            name="Helper", source="", script_type="ModuleScript",
            parent_path="ReplicatedStorage",
        )
        p.state.rbx_place.scripts = [script]
        # No snapshot taken (the classifier was bypassed via
        # ``__skip_domain_classifier__`` or tests calling the subphase
        # in isolation). Restore must be a safe no-op.
        p._check_auto_fail_closed()

        assert p.ctx.scene_runtime_mode == "legacy"
        # parent_path unchanged because no snapshot existed.
        assert script.parent_path == "ReplicatedStorage"

    def test_duplicate_name_scripts_restored_independently(self, tmp_path):
        """R2-P2.1 (codex round 2): the snapshot must key by object
        identity, not script name. Two same-name scripts in different
        containers each need their own legacy ``parent_path`` back on
        fallback -- the pre-fix snapshot keyed by ``name`` and one
        entry would silently win, routing one of the scripts to the
        wrong container.
        """
        from core.roblox_types import RbxScript
        artifact = {
            "modules": {
                "guid-Foo": {
                    "stem": "Foo", "runtime_bearing": True,
                    "domain": "legacy",
                    "domain_signals": {
                        "fail_closed_reason": "both_side_api",
                    },
                },
            },
        }
        p = _make_pipeline_with_artifact(
            tmp_path, "auto", artifact, _empty_transpilation([]),
        )
        helper_a = RbxScript(
            name="Helper", source="// from A",
            script_type="ModuleScript",
            parent_path="ServerStorage",
        )
        helper_b = RbxScript(
            name="Helper", source="// from B",
            script_type="ModuleScript",
            parent_path="ReplicatedStorage.A",
        )
        p.state.rbx_place.scripts = [helper_a, helper_b]
        # Snapshot pre-classifier state with id() keys so both rows
        # survive.
        p._auto_parent_path_snapshot = {
            id(helper_a): "ServerStorage",
            id(helper_b): "ReplicatedStorage.A",
        }
        # Classifier mutates both to a common container.
        helper_a.parent_path = "ReplicatedStorage"
        helper_b.parent_path = "ReplicatedStorage"

        p._check_auto_fail_closed()

        assert p.ctx.scene_runtime_mode == "legacy"
        # Each script gets its own legacy container back.
        assert helper_a.parent_path == "ServerStorage"
        assert helper_b.parent_path == "ReplicatedStorage.A"

    def test_clean_auto_path_does_not_restore(self, tmp_path):
        """When auto routes to generic (no fail-closed signals), the
        snapshot must NOT be applied -- generic mode keeps the
        classifier mutations. Operator opted in to generic via auto.
        """
        from core.roblox_types import RbxScript
        artifact = {"modules": {}, "scenes": {}, "prefabs": {}}
        p = _make_pipeline_with_artifact(
            tmp_path, "auto", artifact, _empty_transpilation([]),
        )
        script = RbxScript(
            name="Helper", source="", script_type="ModuleScript",
            parent_path="ReplicatedStorage",  # post-classifier
        )
        p.state.rbx_place.scripts = [script]
        p._auto_parent_path_snapshot = {id(script): "ServerStorage"}

        p._check_auto_fail_closed()

        assert p.ctx.scene_runtime_mode == "generic"
        # Clean path keeps the classifier's mutation.
        assert script.parent_path == "ReplicatedStorage"


# ---------------------------------------------------------------------------
# R1-P2 (codex round 1): analyze_all_scripts failure under auto must
# fall back to legacy, not fail-open to generic.
# ---------------------------------------------------------------------------

class TestAnalyzerFailureUnderAutoFallsBackToLegacy:
    """``_check_auto_fail_closed`` re-runs ``analyze_all_scripts``
    against the project root to feed ``detect_fail_closed_signals``.
    On exception the prior implementation routed to ``generic``,
    which is fail-open at the exact safety gate -- if we can't read
    the project we cannot prove it's safe for generic mode. PR5's
    R1-P2 absorption switches that branch to ``legacy``.
    """

    def test_analyzer_exception_routes_to_legacy(self, tmp_path):
        from core.roblox_types import RbxScript
        artifact = {"modules": {}, "scenes": {}, "prefabs": {}}
        # Force ``analyze_all_scripts`` to throw by pointing
        # ``unity_project_path`` at a non-existent path.
        p = _make_pipeline_with_artifact(
            tmp_path, "auto", artifact, _empty_transpilation([]),
        )
        # Build a real path then delete it so the analyzer can't read
        # the Assets dir. ``analyze_all_scripts`` does a defensive
        # ``Path.is_dir()`` check on ``Assets/`` and returns [] when
        # missing -- to actually force an exception we monkeypatch.
        import converter.pipeline as pipeline_mod

        original_analyze = pipeline_mod.__dict__.get("analyze_all_scripts")
        # Inject a stub on the import location used by the subphase.
        from unity import script_analyzer as analyzer_mod

        def _boom(_path):
            raise OSError("simulated analyzer failure for test")

        original = analyzer_mod.analyze_all_scripts
        try:
            analyzer_mod.analyze_all_scripts = _boom  # type: ignore[assignment]
            p._check_auto_fail_closed()
        finally:
            analyzer_mod.analyze_all_scripts = original  # type: ignore[assignment]

        # Fell back to legacy + stashed the diagnostic.
        assert p.ctx.scene_runtime_mode == "legacy", (
            "Analyzer exception must route to legacy (cannot prove "
            "generic safety without a fresh script list)"
        )
        triggers = p.ctx.scene_runtime["auto_fail_closed"]
        assert len(triggers) == 1
        assert triggers[0]["kind"] == "script_analyzer_failure"
        assert "OSError" in triggers[0]["detail"]

    def test_classify_storage_followed_by_fallback_restores_path(
        self, tmp_path,
    ):
        """End-to-end (subphase-level): ``_classify_storage`` snapshots
        parent_path under auto BEFORE running the classifier; the
        classifier's reachability sub-pass hoists a client-reached
        helper to ReplicatedStorage; ``_check_auto_fail_closed`` sees
        a fail-closed signal (intra_class_conflict, seeded) and
        restores the snapshot. The legacy fallback .rbxlx ships with
        the original ServerStorage placement.
        """
        from core.roblox_types import RbxScript
        # Project root with an Assets dir + one .cs so analyze_all_scripts
        # returns at least one row.
        cs_path = tmp_path / "project" / "Assets" / "Foo.cs"
        cs_path.parent.mkdir(parents=True, exist_ok=True)
        cs_path.write_text("public class Foo : MonoBehaviour {}")

        artifact = {
            "modules": {
                "guid-Foo": {
                    "stem": "Foo", "class_name": "Foo",
                    "runtime_bearing": True,
                    "domain": "legacy",
                    "domain_signals": {
                        "fail_closed_reason": "intra_class_conflict",
                    },
                },
            },
            "scenes": {},
            "prefabs": {},
        }
        p = _make_pipeline_with_artifact(
            tmp_path, "auto", artifact, _empty_transpilation([]),
        )
        helper = RbxScript(
            name="Helper", source="", script_type="ModuleScript",
            parent_path="ServerStorage",
        )
        p.state.rbx_place.scripts = [helper]

        # Simulate the classifier path: take the snapshot the
        # production code takes, then mutate.
        p._auto_parent_path_snapshot = {
            id(s): s.parent_path for s in p.state.rbx_place.scripts
        }
        helper.parent_path = "ReplicatedStorage"  # classifier hoist

        # Now run the fail-closed check (the artifact already encodes
        # an intra_class_conflict signal).
        p._check_auto_fail_closed()

        assert p.ctx.scene_runtime_mode == "legacy"
        # Snapshot restoration kicked in.
        assert helper.parent_path == "ServerStorage", (
            "Fallback must roll back the classifier's storage routing"
        )
        # Trigger surface carries the signal that drove the fallback.
        triggers = p.ctx.scene_runtime["auto_fail_closed"]
        assert any(t["kind"] == "intra_class_conflict" for t in triggers)

    def test_analyzer_exception_restores_parent_paths(self, tmp_path):
        from core.roblox_types import RbxScript
        from unity import script_analyzer as analyzer_mod

        artifact = {"modules": {}, "scenes": {}, "prefabs": {}}
        p = _make_pipeline_with_artifact(
            tmp_path, "auto", artifact, _empty_transpilation([]),
        )
        script = RbxScript(
            name="Helper", source="", script_type="ModuleScript",
            parent_path="ReplicatedStorage",
        )
        p.state.rbx_place.scripts = [script]
        p._auto_parent_path_snapshot = {id(script): "ServerStorage"}

        def _boom(_path):
            raise OSError("forced for test")

        original = analyzer_mod.analyze_all_scripts
        try:
            analyzer_mod.analyze_all_scripts = _boom  # type: ignore[assignment]
            p._check_auto_fail_closed()
        finally:
            analyzer_mod.analyze_all_scripts = original  # type: ignore[assignment]

        assert p.ctx.scene_runtime_mode == "legacy"
        # Restoration must fire on the analyzer-failure path too.
        assert script.parent_path == "ServerStorage"
