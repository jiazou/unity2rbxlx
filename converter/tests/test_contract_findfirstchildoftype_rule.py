"""Phase 2 (relation #8) slice 2.2: the contract verifier flags the nonexistent
``:FindFirstChildOfType(`` (rule ``fc``) and reprompts toward ``FindFirstChildOfClass``. Unlike the
impulse rule, an invalid API CRASHES, so ``fc`` is LOAD-BEARING: a surviving violation gets the
default ``contract-verifier `` tag and promotes to a project fail-closed.
"""

from __future__ import annotations

from converter.runtime_contract import verify_module
from converter.code_transpiler import _format_contract_survivor_warning, _FAIL_OPEN_RULES
from converter.contract_pipeline import _is_contract_warning, _is_post_reprompt_warning


def _fc(src: str):
    return [v for v in verify_module(src).violations if v.rule == "fc"]


def test_findfirstchildoftype_is_flagged():
    src = 'function C:OnHit(char) local h = char:FindFirstChildOfType("Humanoid"); h:TakeDamage(1) end\nreturn C\n'
    assert _fc(src), "char:FindFirstChildOfType must be flagged (rule fc)"


def test_whitespace_forms_flagged():
    for call in ('c:FindFirstChildOfType("Humanoid")', 'c: FindFirstChildOfType("Humanoid")',
                 'c : FindFirstChildOfType ("Humanoid")'):
        src = f"function C:OnHit(c) local h = {call} end\nreturn C\n"
        assert _fc(src), f"must flag whitespace form: {call!r}"


def test_valid_findfirstchildofclass_not_flagged():
    src = 'function C:OnHit(char) local h = char:FindFirstChildOfClass("Humanoid") end\nreturn C\n'
    assert not _fc(src), "the VALID FindFirstChildOfClass must NOT be flagged"


def test_method_definition_not_flagged():
    src = "function C:FindFirstChildOfType(t) return nil end\nreturn C\n"
    assert not _fc(src), "a method definition must not be flagged"


def test_fc_is_fail_closed():
    src = 'function C:OnHit(c) local h = c:FindFirstChildOfType("Humanoid") end\nreturn C\n'
    v = _fc(src)[0]
    warning = _format_contract_survivor_warning(v)
    # fc is NOT fail-open: it uses the default 'contract-verifier ' tag and promotes to fail-closed.
    assert "fc" not in _FAIL_OPEN_RULES
    assert warning.startswith("contract-verifier (rule fc"), warning
    assert _is_post_reprompt_warning(warning), "fc must promote to project fail-closed"
    assert _is_contract_warning(warning), "fc is a load-bearing contract warning"
