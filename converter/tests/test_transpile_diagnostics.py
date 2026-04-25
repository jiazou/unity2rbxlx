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
