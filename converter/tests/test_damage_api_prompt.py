"""Phase 2 (relation #8) slice 2.1: the GENERIC prompt teaches that ``FindFirstChildOfType`` is
not a Roblox method — use ``FindFirstChildOfClass`` (so a converted hit-handler damages the
victim's Humanoid). The legacy byte-frozen ``_AI_SYSTEM_PROMPT`` is left untouched.
"""

from __future__ import annotations

from converter.code_transpiler import _AI_SYSTEM_PROMPT, _GENERIC_RUNTIME_PROMPT


def test_generic_prompt_forbids_findfirstchildoftype():
    assert "FindFirstChildOfType` does NOT exist" in _GENERIC_RUNTIME_PROMPT
    assert 'FindFirstChildOfClass("Humanoid")' in _GENERIC_RUNTIME_PROMPT


def test_legacy_frozen_prompt_untouched():
    # Phase 2 must not edit the byte-frozen legacy prompt (generic mode never uses it).
    assert "FindFirstChildOfType` does NOT exist" not in _AI_SYSTEM_PROMPT
