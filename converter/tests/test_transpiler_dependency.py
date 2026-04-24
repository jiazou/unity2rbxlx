"""Phase 4.3.1 — dependency-aware context in code_transpiler."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from converter.code_transpiler import (
    _extract_class_names,
    _extract_references,
    _build_dependency_graph,
    _topological_sort,
    _compute_dependency_levels,
    _build_scoped_context,
)


class TestExtractClassNames:
    def test_public_class(self):
        assert _extract_class_names("public class Foo {}") == {"Foo"}

    def test_multiple_decls(self):
        src = "public class A {} internal struct B {} public enum C {}"
        assert _extract_class_names(src) == {"A", "B", "C"}

    def test_interface(self):
        assert "IPickup" in _extract_class_names("public interface IPickup {}")

    def test_modifiers(self):
        src = "public abstract sealed partial class MyScript : MonoBehaviour {}"
        assert _extract_class_names(src) == {"MyScript"}


class TestExtractReferences:
    def test_finds_external_reference(self):
        player_src = "public class Player { public void hasKey() {} }"
        door_src = "public class Door { Player p; void Check() { p.hasKey(); } }"
        all_names = _extract_class_names(player_src) | _extract_class_names(door_src)
        refs = _extract_references(door_src, all_names)
        assert "Player" in refs
        assert "Door" not in refs  # Self-references filtered

    def test_word_boundaries(self):
        src = "class Foo { PlayerHelper h; }"
        # 'Player' should NOT match 'PlayerHelper' — word boundary.
        all_names = {"Player", "PlayerHelper"}
        refs = _extract_references(src, all_names)
        assert "PlayerHelper" in refs
        assert "Player" not in refs


class TestDependencyGraph:
    def test_player_door_edge(self):
        """The PR 3 bug pattern: Door depends on Player."""
        sources = {
            "Player": "public class Player { public bool hasKey() { return gotKey; } }",
            "Door": "public class Door { Player p; void Check() { p.hasKey(); } }",
        }
        graph, class_map = _build_dependency_graph(sources)
        assert graph["Door"] == {"Player"}
        assert graph["Player"] == set()
        assert class_map["Player"] == "Player"

    def test_self_references_filtered(self):
        sources = {
            "Foo": "public class Foo { Foo other; }",
        }
        graph, _ = _build_dependency_graph(sources)
        assert graph["Foo"] == set()


class TestTopologicalSort:
    def test_dependency_first(self):
        """Dependents come AFTER their dependencies."""
        graph = {
            "Door": {"Player"},
            "Player": set(),
        }
        order = _topological_sort(graph)
        assert order.index("Player") < order.index("Door")

    def test_deterministic_across_runs(self):
        """Alphabetical tie-break — same output every time."""
        graph = {
            "C": {"A", "B"},
            "B": {"A"},
            "A": set(),
            "D": set(),
        }
        first = _topological_sort(graph)
        for _ in range(5):
            assert _topological_sort(graph) == first

    def test_cycle_does_not_crash(self):
        """Cycles break gracefully; every node still appears once."""
        graph = {"A": {"B"}, "B": {"A"}}
        order = _topological_sort(graph)
        assert sorted(order) == ["A", "B"]


class TestDependencyLevels:
    def test_leaves_at_level_0(self):
        graph = {
            "Door": {"Player"},
            "Player": set(),
            "UI": set(),
        }
        levels = _compute_dependency_levels(graph)
        # Level 0 is leaf dependencies; Door is one level above.
        assert "Player" in levels[0]
        assert "UI" in levels[0]
        assert levels[1] == ["Door"]

    def test_diamond(self):
        graph = {
            "A": set(),
            "B": {"A"},
            "C": {"A"},
            "D": {"B", "C"},
        }
        levels = _compute_dependency_levels(graph)
        assert levels[0] == ["A"]
        assert set(levels[1]) == {"B", "C"}
        assert levels[2] == ["D"]


class TestScopedContext:
    def test_uses_transpiled_luau_when_available(self):
        """When a dep has been transpiled, its Luau goes into the context —
        NOT the raw C# source. This is what lets Door's AI prompt see
        Player's exported `hasKey` function.
        """
        sources = {
            "Player": "public class Player { public bool hasKey() { return gotKey; } }",
            "Door": "public class Door { void Open() {} }",
        }
        graph, _ = _build_dependency_graph(sources)
        graph["Door"] = {"Player"}  # Manual edge for test clarity
        transpiled = {
            "Player": "local Player = {}\nPlayer.hasKey = function() return gotKey end\nreturn Player",
        }
        ctx = _build_scoped_context("Door", graph, sources, transpiled)
        assert "Already-transpiled dependency: Player.luau" in ctx
        assert "Player.hasKey = function()" in ctx
        # Must NOT include the raw C# when Luau is available.
        assert "public class Player" not in ctx

    def test_falls_back_to_csharp_for_not_yet_transpiled(self):
        sources = {
            "Player": "public class Player { public void Foo() {} }",
            "Door": "public class Door {}",
        }
        graph = {"Door": {"Player"}, "Player": set()}
        ctx = _build_scoped_context("Door", graph, sources, transpiled_luau={})
        assert "Dependency (not yet transpiled): Player.cs" in ctx
        assert "public class Player" in ctx

    def test_empty_when_no_deps(self):
        ctx = _build_scoped_context("Solo", {"Solo": set()}, {"Solo": "class Solo {}"}, {})
        assert ctx == ""
