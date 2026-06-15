"""Phase 1 (relation #8) slice 1.3: the GENERIC transpiler prompt lowers a linear
``Rigidbody.AddForce`` through the host primitive ``self.host.applyImpulse`` so the host
applies the faithful stud-scaled launch velocity. The legacy, byte-frozen
``_AI_SYSTEM_PROMPT`` is intentionally left untouched (generic mode never uses it).
"""

from __future__ import annotations

from converter.code_transpiler import _AI_SYSTEM_PROMPT, _GENERIC_RUNTIME_PROMPT


def test_generic_prompt_routes_addforce_through_host():
    assert "self.host.applyImpulse(part, f)" in _GENERIC_RUNTIME_PROMPT
    # The generic prompt must NOT instruct a raw AddForce -> part:ApplyImpulse lowering.
    assert "`Rigidbody.AddForce(f)` → `part:ApplyImpulse(f)`" not in _GENERIC_RUNTIME_PROMPT


def test_generic_prompt_forbids_raw_apply_impulse():
    # The instruction explicitly tells the AI never to emit a raw part:ApplyImpulse.
    assert "Never emit a raw `part:ApplyImpulse(...)`" in _GENERIC_RUNTIME_PROMPT


def test_legacy_frozen_prompt_untouched():
    # _AI_SYSTEM_PROMPT is legacy-only + byte-frozen; the raw mapping must remain (not edited).
    assert "`Rigidbody.AddForce` → `part:ApplyImpulse(force)`" in _AI_SYSTEM_PROMPT
    assert "self.host.applyImpulse" not in _AI_SYSTEM_PROMPT
