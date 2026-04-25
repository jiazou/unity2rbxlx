"""Phase 4.4 — check_method_completeness diagnostic."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from converter.transpile_diagnostics import (
    check_method_completeness,
    _strip_comments_and_strings,
)


class TestStripCommentsAndStrings:
    def test_strips_line_comment(self):
        src = "int x = 1; // public void Hidden() {}"
        clean = _strip_comments_and_strings(src)
        assert "Hidden" not in clean
        assert "int x = 1" in clean

    def test_strips_block_comment(self):
        src = "/* public void Hidden() {} */ int x;"
        clean = _strip_comments_and_strings(src)
        assert "Hidden" not in clean

    def test_strips_string_literal(self):
        src = 'Debug.Log("public void NotReal() {}"); int x;'
        clean = _strip_comments_and_strings(src)
        assert "NotReal" not in clean


class TestCheckMethodCompleteness:
    def test_no_methods_returns_empty(self):
        """Empty C# / no method declarations → no warnings."""
        assert check_method_completeness("", "local x = 1") == []
        assert check_method_completeness("int x = 1;", "local x = 1") == []

    def test_lifecycle_hooks_exempt(self):
        """Awake/Start/Update/etc. lower into top-level code — exempt."""
        cs = """
        public class Foo {
            void Awake() {}
            void Start() {}
            void Update() {}
            void OnDestroy() {}
        }
        """
        luau = "-- no functions\nlocal x = 1\n"
        assert check_method_completeness(cs, luau) == []

    def test_missing_method_reported(self):
        cs = "public class Foo { public void Shoot() {} public void Reload() {} }"
        # Luau only has Shoot.
        luau = "function Foo:Shoot() end\n"
        warnings = check_method_completeness(cs, luau, source_name="Foo.cs")
        assert len(warnings) == 1
        assert "Reload" in warnings[0]
        assert "Foo.cs" in warnings[0]

    def test_function_forms_recognized(self):
        """Multiple Luau function declaration idioms all count."""
        cs = """
        public class P {
            public void One() {}
            public void Two() {}
            public void Three() {}
            public void Four() {}
        }
        """
        luau = """
        function P:One() end
        function P.Two() end
        local function Three() end
        function Four() end
        """
        assert check_method_completeness(cs, luau) == []

    def test_unconverted_comment_counts(self):
        """A method marked with -- UNCONVERTED is an intentional drop."""
        cs = "public class Foo { public void TakeScreenshot() {} }"
        luau = "-- UNCONVERTED: TakeScreenshot needs Application.CaptureScreenshot\n"
        assert check_method_completeness(cs, luau) == []

    def test_todo_comment_counts(self):
        cs = "public class Foo { public void Defer() {} }"
        luau = "-- TODO: implement Defer\n"
        assert check_method_completeness(cs, luau) == []

    def test_comment_in_csharp_not_a_method(self):
        """Method names mentioned only in C# comments shouldn't be expected."""
        cs = """
        public class Foo {
            // public void NotReal() — this is just a comment
            public void Real() {}
        }
        """
        luau = "function Foo:Real() end\n"
        assert check_method_completeness(cs, luau) == []

    def test_string_literal_in_csharp_not_a_method(self):
        cs = 'public class Foo { public void Log() { Debug.Log("public void Fake()"); } }'
        luau = "function Foo:Log() end\n"
        assert check_method_completeness(cs, luau) == []

    def test_multiple_missing_sorted_deterministically(self):
        cs = """
        public class Foo {
            public void Zed() {}
            public void Alpha() {}
            public void Mid() {}
        }
        """
        # Non-empty Luau with no matching function definitions — forces
        # all three names to register as missing.
        luau = "local x = 1\nprint(\"stub\")\n"
        warnings = check_method_completeness(cs, luau)
        # Warnings are sorted alphabetically for determinism.
        names_in_order = [w.split("'")[1] for w in warnings]
        assert names_in_order == ["Alpha", "Mid", "Zed"]

    def test_empty_luau_returns_empty(self):
        """No Luau output → nothing to compare against; skip."""
        cs = "public class Foo { public void Bar() {} }"
        assert check_method_completeness(cs, "") == []

    def test_source_name_embedded_in_warnings(self):
        cs = "public class Foo { public void Only() {} }"
        luau = "-- nothing"
        w = check_method_completeness(cs, luau, source_name="MyScript.cs")
        assert "[MyScript.cs]" in w[0]


class TestCodexFix1NoModifierMethods:
    """P1: regex used to require an access modifier; default-private +
    generic methods slipped through. Loosened regex catches them.
    """

    def test_default_private_method_captured(self):
        cs = "public class Foo { void Helper() {} public void Used() {} }"
        # Luau has Used; Helper is missing.
        luau = "function Foo:Used() end\n"
        warnings = check_method_completeness(cs, luau)
        names = [w.split("'")[1] for w in warnings]
        assert "Helper" in names

    def test_ienumerator_return_type_captured(self):
        """``IEnumerator Run()`` is a default-private coroutine — caught."""
        cs = """
        public class C {
            IEnumerator Run() { yield return null; }
            public void Other() {}
        }
        """
        luau = "function C:Other() end\n"
        names = [w.split("'")[1] for w in check_method_completeness(cs, luau)]
        assert "Run" in names

    def test_generic_method_captured(self):
        cs = "public class C { public TOut Map<TIn, TOut>(TIn x) { return default; } }"
        luau = ""  # No Luau functions.
        # Need a non-empty luau (early-return check). Add a stub line.
        names = [w.split("'")[1]
                 for w in check_method_completeness(cs, luau or "local x = 1")]
        assert "Map" in names

    def test_void_var_keywords_not_method_names(self):
        """``void`` and ``var`` are return-type tokens, not methods."""
        cs = "public class C { void DoIt() { var x = 1; } }"
        luau = "function C:DoIt() end\n"
        # 'var' or 'void' MUST NOT appear as missing-method warnings.
        warnings = check_method_completeness(cs, luau)
        for w in warnings:
            method = w.split("'")[1]
            assert method not in {"void", "var"}

    def test_control_flow_keywords_skipped(self):
        cs = """
        public class C {
            public void Loop() {
                if (true) { foo(); }
                while (true) { bar(); }
                for (int i = 0; i < 10; i++) {}
            }
        }
        """
        luau = "function C:Loop() end\n"
        warnings = check_method_completeness(cs, luau)
        # No false positives from `if(`, `while(`, `for(`.
        names = {w.split("'")[1] for w in warnings}
        assert names.isdisjoint({"if", "while", "for"})


class TestCodexFix2AssignmentLuauForms:
    """P1: the diagnostic must accept ``Class.method = function() end``
    and ``_G.X.method = function()`` as valid method definitions —
    that's how Player.luau (and the dep-aware exports) emit getters.
    """

    def test_dotted_assignment_form(self):
        cs = "public class Player { public bool hasKey() { return false; } }"
        luau = "Player.hasKey = function() return gotKey end\n"
        assert check_method_completeness(cs, luau) == []

    def test_global_dotted_assignment_form(self):
        cs = "public class Player { public bool hasKey() { return false; } }"
        luau = "_G.Player.hasKey = function() return gotKey end\n"
        assert check_method_completeness(cs, luau) == []

    def test_bare_assignment_form(self):
        """Bare ``name = function()`` at module top counts."""
        cs = "public class Foo { public void Bar() {} }"
        luau = "Bar = function() end\n"
        assert check_method_completeness(cs, luau) == []

    def test_function_keyword_form_still_works(self):
        cs = "public class Foo { public void Bar() {} }"
        luau = "function Foo:Bar() end\n"
        assert check_method_completeness(cs, luau) == []


class TestCodexFix3CollisionHooksExempt:
    """P2: Unity collision/trigger/mouse hooks lower into Touched
    or ClickDetector connections — no named Luau function — so they
    must be in the exempt set.
    """

    def test_collision_hook_exempt(self):
        cs = """
        public class C {
            void OnCollisionEnter(Collision c) {}
            void OnCollisionExit(Collision c) {}
            void OnTriggerEnter(Collider c) {}
            public void Real() {}
        }
        """
        luau = "function C:Real() end\nworkspace.Touched:Connect(function() end)\n"
        # No collision-hook-related warnings.
        warnings = check_method_completeness(cs, luau)
        for w in warnings:
            method = w.split("'")[1]
            assert "Collision" not in method
            assert "Trigger" not in method

    def test_mouse_hook_exempt(self):
        cs = """
        public class C {
            void OnMouseDown() {}
            void OnMouseUpAsButton() {}
            public void Real() {}
        }
        """
        luau = "function C:Real() end\n"
        warnings = check_method_completeness(cs, luau)
        for w in warnings:
            method = w.split("'")[1]
            assert "Mouse" not in method

    def test_2d_collider_hooks_exempt(self):
        cs = """
        public class C {
            void OnCollisionEnter2D(Collision2D c) {}
            void OnTriggerExit2D(Collider2D c) {}
        }
        """
        # All exempt → no warnings even with empty Luau.
        assert check_method_completeness(cs, "local x = 1") == []
