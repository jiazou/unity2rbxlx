"""Fix #15 (re-transpile collateral) -- cheap unit coverage for the
component-class routing fix and the Root-A fail-closed gating.

These run fast (no AI, no Studio) and are the guard Codex asked for BEFORE
paying the ~20-30 min generic e2e re-transpile: they catch predicate /
routing bugs that would otherwise only surface after a long real-AI run.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from converter.scene_runtime_planner import _resolves_to_component  # noqa: E402
from converter.contract_pipeline import (  # noqa: E402
    FailClosed,
    _component_class_paths,
    _runtime_bearing_paths,
)
from converter.pipeline import _contract_failure_errors  # noqa: E402
from converter.code_transpiler import _inert_component_stub  # noqa: E402
from converter.runtime_contract import verify_module  # noqa: E402


# ---------------------------------------------------------------------------
# Fix B: inheritance-aware component detection (Codex C2)
# ---------------------------------------------------------------------------

class TestResolvesToComponent:

    def test_direct_monobehaviour(self):
        assert _resolves_to_component("Player", "MonoBehaviour", {})

    def test_direct_networkbehaviour(self):
        # Mirror / legacy UNet networked components are still components.
        assert _resolves_to_component("Mover", "NetworkBehaviour", {})

    def test_transitive_through_project_base(self):
        # Turret : Weapon, Weapon : MonoBehaviour  =>  Turret is a component
        # even though its immediate base is a project-local class.
        base_by_class = {"Turret": "Weapon", "Weapon": "MonoBehaviour"}
        assert _resolves_to_component("Turret", "Weapon", base_by_class)

    def test_plain_class_is_not_component(self):
        assert not _resolves_to_component("DamageMath", "", {})

    def test_scriptableobject_is_not_component(self):
        assert not _resolves_to_component("WeaponConfig", "ScriptableObject", {})

    def test_unrelated_base_is_not_component(self):
        base_by_class = {"Foo": "Bar", "Bar": "Baz"}
        assert not _resolves_to_component("Foo", "Bar", base_by_class)

    def test_inheritance_cycle_terminates(self):
        # A pathological self/mutual cycle must not loop forever.
        base_by_class = {"A": "B", "B": "A"}
        assert not _resolves_to_component("A", "B", base_by_class)


# ---------------------------------------------------------------------------
# Fix B: component-class path selection vs placement (C1 superset invariant)
# ---------------------------------------------------------------------------

class _Info:
    """Minimal ScriptInfo stand-in: the path-join only reads ``.path``."""

    def __init__(self, path: Path) -> None:
        self.path = path


class TestComponentClassPathSelection:

    def _modules_and_infos(self):
        placed = Path("/proj/Assets/Player.cs")
        spawned = Path("/proj/Assets/Explosive.cs")
        plain = Path("/proj/Assets/DamageMath.cs")
        modules = {
            "guid-placed": {
                "stem": "Player", "runtime_bearing": True,
                "is_component_class": True,
            },
            "guid-spawned": {
                "stem": "Explosive", "runtime_bearing": False,
                "is_component_class": True,
            },
            "guid-plain": {
                "stem": "DamageMath", "runtime_bearing": False,
                "is_component_class": False,
            },
        }
        infos = [_Info(placed), _Info(spawned), _Info(plain)]
        return modules, infos, placed, spawned, plain

    def test_spawned_component_is_a_generic_path_but_not_runtime_bearing(self):
        modules, infos, placed, spawned, plain = self._modules_and_infos()
        comp_paths, _ = _component_class_paths(modules, infos, "/proj")
        bearing_paths, _ = _runtime_bearing_paths(modules, infos, "/proj")

        # The spawned-only component routes generic (gets the contract)...
        assert spawned in comp_paths
        # ...but is NOT runtime-bearing, so the host won't boot it at start.
        assert spawned not in bearing_paths
        # Placed component is in both.
        assert placed in comp_paths and placed in bearing_paths
        # Plain non-component is in neither.
        assert plain not in comp_paths and plain not in bearing_paths

    def test_runtime_bearing_implies_component_backcompat(self):
        # Old artifact: runtime_bearing set but is_component_class absent.
        # The OR invariant keeps placed MonoBehaviours routed generic.
        placed = Path("/proj/Assets/Player.cs")
        modules = {"g": {"stem": "Player", "runtime_bearing": True}}
        infos = [_Info(placed)]
        comp_paths, _ = _component_class_paths(modules, infos, "/proj")
        assert placed in comp_paths


# ---------------------------------------------------------------------------
# Fix A: fail-closed reasons become conversion errors (pure render helper)
# ---------------------------------------------------------------------------

class TestContractFailureErrors:

    def test_renders_each_row(self):
        rows = [
            FailClosed(kind="verifier", detail="Player.luau: 1 violation(s) survived reprompt"),
            FailClosed(kind="stub_strategy", detail="Explosive.cs: fell through to stub"),
        ]
        msgs = _contract_failure_errors(rows)
        assert len(msgs) == 2
        assert all(m.startswith("scene-runtime contract failed closed") for m in msgs)
        assert "verifier" in msgs[0] and "Player.luau" in msgs[0]

    def test_empty_is_empty(self):
        assert _contract_failure_errors([]) == []

    def test_does_not_mutate_input(self):
        rows = [FailClosed(kind="verifier", detail="x")]
        _contract_failure_errors(rows)
        assert rows == [FailClosed(kind="verifier", detail="x")]


# ---------------------------------------------------------------------------
# Visual-only component stub: contract-valid inert ModuleScript (e2e finding)
# ---------------------------------------------------------------------------

class TestInertComponentStub:

    def test_inert_stub_passes_the_verifier(self):
        # A water-shader MonoBehaviour is routed generic; its stub must be a
        # requirable inert class table, not a nil-returning print(...) — the
        # latter both fails rule (d) and throws when the host requires it.
        src = _inert_component_stub("WaterBase", "no Roblox equivalent")
        result = verify_module(src)
        assert result.ok, [(v.rule, v.line) for v in result.violations]

    def test_inert_stub_returns_a_class_table(self):
        src = _inert_component_stub("WaterBase", "no Roblox equivalent")
        assert src.rstrip().endswith("return WaterBase")

    def test_inert_stub_sanitizes_non_identifier_stems(self):
        src = _inert_component_stub("Weird-Name 2", "x")
        assert "local Weird_Name_2 = {}" in src
        assert verify_module(src).ok
