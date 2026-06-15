"""Phase 1 (relation #8) slice 1.4: the contract verifier flags a raw linear
``part:ApplyImpulse(...)`` (rule ``im``) and reprompts toward ``self.host.applyImpulse``,
but the rule is NON-load-bearing — its surviving warning is tagged
``contract-verifier-impulse`` (fails OPEN, never promotes to project fail-closed, and is
excluded from the compliance-spike contract stats).
"""

from __future__ import annotations

from converter.runtime_contract import verify_module
from converter.code_transpiler import (
    _format_contract_survivor_warning, _FAIL_OPEN_RULES,
    _verify_and_reprompt, _refresh_contract_warnings,
)
from converter.contract_pipeline import _is_contract_warning, _is_post_reprompt_warning

_RAW_IMPULSE_MODULE = "function C:Start() self.rb:ApplyImpulse(Vector3.new(1,0,0)) end\nreturn C\n"


def _im_rules(src: str):
    return [v for v in verify_module(src).violations if v.rule == "im"]


def test_im_fails_open_through_verify_and_reprompt():
    # Through the REAL reprompt seam: a surviving raw impulse is tagged contract-verifier-impulse,
    # fails OPEN (never matches the fail-closed promotion predicate), and emits NO -pre warning.
    def reprompt(_msg):
        return _RAW_IMPULSE_MODULE  # reprompt did NOT route it through the host

    _out, warnings = _verify_and_reprompt(_RAW_IMPULSE_MODULE, "csharp", "generic", reprompt)
    imp = [w for w in warnings if "contract-verifier-impulse" in w]
    assert imp, f"im survivor must be tagged contract-verifier-impulse: {warnings}"
    assert not any(_is_post_reprompt_warning(w) for w in imp), f"im must fail open: {imp}"
    assert not any(_is_contract_warning(w) for w in imp), f"im must stay out of contract stats: {imp}"
    assert not any(w.startswith("contract-verifier-pre") and "rule im" in w for w in warnings), (
        f"im must emit no -pre warning (fail-open rule): {warnings}"
    )


def test_im_fails_open_on_cache_replay():
    # Through the REAL cache-replay seam: a cached raw impulse re-emits under the fail-open tag.
    refreshed = _refresh_contract_warnings(_RAW_IMPULSE_MODULE, [])
    assert any("contract-verifier-impulse" in w for w in refreshed), f"cache replay lost -impulse: {refreshed}"
    assert not any(_is_post_reprompt_warning(w) and "rule im" in w for w in refreshed), (
        f"cache-replayed im matched fail-closed: {refreshed}"
    )


def test_raw_apply_impulse_is_flagged():
    src = "function C:Start() local rb = self.gameObject; rb:ApplyImpulse(Vector3.new(1,0,0)) end\nreturn C\n"
    assert _im_rules(src), "raw rb:ApplyImpulse must be flagged (rule im)"


def test_dotted_self_gameobject_receiver_is_flagged():
    # codex design-MAJOR (false-negative): a dotted-receiver colon call must still be caught.
    src = "function C:Start() self.gameObject:ApplyImpulse(Vector3.new(1,0,0)) end\nreturn C\n"
    assert _im_rules(src), "self.gameObject:ApplyImpulse must be flagged"


def test_whitespace_tolerant_forms_are_flagged():
    for call in ("rb:ApplyImpulse(v)", "rb: ApplyImpulse(v)", "rb : ApplyImpulse (v)"):
        src = f"function C:Start() local rb = self.rb; {call} end\nreturn C\n"
        assert _im_rules(src), f"must flag whitespace form: {call!r}"


def test_method_definition_is_not_flagged():
    # A definition named ApplyImpulse is not a raw call — must not be flagged (incl. spaced form).
    for defn in ("function C:ApplyImpulse(v) self.x = v end",
                 "function C : ApplyImpulse(v) self.x = v end",
                 # long receiver chain (> the old 40-char lookback) — propagated phase-2 guard fix
                 "function ExtremelyLongNamespaceNameForTesting.ComponentClass:ApplyImpulse(v) self.x = v end"):
        src = f"{defn}\nreturn C\n"
        assert not _im_rules(src), f"definition must not be flagged: {defn!r}"


def test_host_call_is_not_flagged():
    src = "function C:Start() self.host.applyImpulse(self.rb, Vector3.new(1,0,0)) end\nreturn C\n"
    assert not _im_rules(src), "the host call must NOT be flagged"


def test_at_position_and_angular_not_flagged():
    src = (
        "function C:Start()\n"
        "  self.rb:ApplyImpulseAtPosition(Vector3.new(1,0,0), Vector3.zero)\n"
        "  self.rb:ApplyAngularImpulse(Vector3.new(0,1,0))\n"
        "end\nreturn C\n"
    )
    assert not _im_rules(src), "ApplyImpulseAtPosition / ApplyAngularImpulse are out of scope"


def test_im_warning_is_fail_open_and_excluded_from_stats():
    src = "function C:Start() self.rb:ApplyImpulse(Vector3.new(1,0,0)) end\nreturn C\n"
    v = _im_rules(src)[0]
    warning = _format_contract_survivor_warning(v)
    assert warning.startswith("contract-verifier-impulse"), warning
    # Fails OPEN: never matches the fail-closed promotion predicate.
    assert not _is_post_reprompt_warning(warning), "im must not promote to fail-closed"
    # Excluded from the contract-warning stats (like player rejects).
    assert not _is_contract_warning(warning), "im must not perturb contract stats"
    assert "im" in _FAIL_OPEN_RULES
