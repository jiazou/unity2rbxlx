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

    def test_unrecognized_classifier_reason_ignored(self, tmp_path):
        """Future-proofing: an unknown ``fail_closed_reason`` string
        from the classifier doesn't surface as a fail-closed kind.
        Forward-compat keeps PR5 from breaking on a PR6+ classifier
        adding new conflict shapes the auto router doesn't yet know
        to route on."""
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
        # No signals raised for unrecognized reasons.
        assert all(
            s.kind not in {"future_unseen_reason"} for s in signals
        )


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

    def test_auto_no_transpile_routes_to_generic(self, tmp_path):
        p = _make_pipeline_with_artifact(
            tmp_path, "auto", {"modules": {}}, None,
        )
        p._check_auto_fail_closed()
        assert p.ctx.scene_runtime_mode == "generic"

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
