"""movement_facet_lowering.py -- generic-allowlist player movement lowering.

A deterministic, structure-gated lowering on the generic scene-runtime
allowlist (called from ``contract_pipeline.transpile_with_contract``, AFTER
identifying the player but BEFORE ``lower_camera_facet`` erases the camera
fingerprint). It retargets the converted player controller's WASD movement
from the vestigial scene rig Part (``self.gameObject:PivotTo(...)``) onto the
Roblox character's ``Humanoid:Move(...)``, so Roblox physics owns
gravity/collision/floor (required by the ``FloorMaterial`` spawn oracle and to
kill the unbounded sink of the collision-less rig Part).

This is a *lowering pass*, NOT a coherence pack: it is deterministic, gated on
a structural fingerprint (never ``s.name`` / per-game identity), biased to
PRECISION over coverage (the retarget is destructive, so it abstains when the
positive evidence is weak), and the canonical movement body is fixed here, not
in the AI prompt. See docs/design/camera-input-fidelity-plan.md and the
generic player-binding design.

Player identity (``find_player_controllers``) requires ALL THREE structural
signals on the script's own lexer-blanked source:
  1. a Unity ``CharacterController`` reference -- the decisive avatar signal;
  2. a camera facet (``camera_facet_lowering._find_look_method`` matches);
  3. a WASD method (a colon-method reading >=3 distinct WASD key codes).
If ZERO or MORE THAN ONE script satisfies all three, NOTHING is lowered
(fail-closed): a split- or multi-controller game abstains rather than guess.

Idempotent: the lowered body calls ``getYawBasis():VectorToWorldSpace`` +
``Humanoid:Move``, so a re-run detects the marker and skips.
"""

from __future__ import annotations

import re

from converter import camera_facet_lowering
from converter.camera_facet_lowering import _HasLuauSource, _METHOD_RE
from converter.runtime_contract import (
    _extract_function_body,
    _strip_strings_and_comments,
)

# CharacterController reference: ``GetComponent("CharacterController")``. The
# class name lives inside a string literal, so the lexer-blanker nulls its
# bytes (quotes included) -- we can't match the full call on the stripped
# source alone. So we locate the *code-level* ``GetComponent(`` on the STRIPPED
# source (this excludes a call sitting inside a comment/string, acceptance 7),
# anchoring the match END just after the ``(`` (NO ``\s*`` past it, so the
# offset is stable regardless of the blanked arg), then confirm the RAW
# argument starting at that offset is the ``CharacterController`` literal.
_GET_COMPONENT_RE = re.compile(r"GetComponent\(")
_CC_ARG_RE = re.compile(r"""\s*['"]CharacterController['"]\s*\)""")

# A WASD key read: ``IsKeyDown(Enum.KeyCode.W|A|S|D)``. Counted body-wide on
# the lexer-blanked source; >=3 *distinct* letters identifies the move method.
_WASD_RE = re.compile(
    r"IsKeyDown\(\s*Enum\.KeyCode\.(?P<key>[WASD])\b\s*\)",
)


def _wasd_method_body(stripped: str):
    """Return ``(body_start, body_len, param)`` for the colon-method whose
    body reads >=3 distinct WASD key codes, or ``None``. Scans the
    comment/string-stripped source so reads inside literals never count;
    offsets map 1:1 to the real source (the strip is length-preserving)."""
    for m in _METHOD_RE.finditer(stripped):
        body, body_start = _extract_function_body(stripped, m.end())
        if body is None:
            continue
        keys = {mm.group("key") for mm in _WASD_RE.finditer(body)}
        if len(keys) < 3:
            continue
        # First identifier in the (already-passed) arg list, if any.
        close = stripped.find(")", m.end())
        param = None
        if close != -1:
            pm = re.match(r"\s*([A-Za-z_]\w*)", stripped[m.end():close])
            if pm:
                param = pm.group(1)
        return body_start, len(body), param
    return None


def _has_character_controller_ref(src: str, stripped: str) -> bool:
    """True iff a *code-level* ``GetComponent("CharacterController")`` appears.
    ``GetComponent(`` is located on the lexer-blanked source (so a call inside
    a comment/string never counts); the literal arg is confirmed on the RAW
    source at the same 1:1 offset (the strip nulls string contents)."""
    for m in _GET_COMPONENT_RE.finditer(stripped):
        if _CC_ARG_RE.match(src, m.end()):
            return True
    return False


def _is_player_controller(s: _HasLuauSource) -> bool:
    """True iff ``s`` satisfies ALL THREE player signals (D5). Camera facet is
    a BOOLEAN co-signal only -- its offsets are NOT carried into the edit."""
    src = s.luau_source or ""
    stripped = _strip_strings_and_comments(src)
    # 1. CharacterController ref (code-level GetComponent only).
    if not _has_character_controller_ref(src, stripped):
        return False
    # 2. Camera facet (boolean co-signal; offsets intentionally discarded).
    if camera_facet_lowering._find_look_method(stripped) is None:
        return False
    # 3. WASD move method.
    if _wasd_method_body(stripped) is None:
        return False
    return True


def find_player_controllers(scripts: list[_HasLuauSource]) -> list[_HasLuauSource]:
    """Return the UNIQUE script satisfying camera-facet + >=3-WASD-method +
    CharacterController-ref (on lexer-blanked source, never ``s.name``), or
    ``[]`` if zero or more than one match (fail-closed). Must run BEFORE
    ``lower_camera_facet`` (which erases the camera fingerprint signal 2 relies
    on)."""
    matches = [s for s in scripts if _is_player_controller(s)]
    if len(matches) != 1:
        return []
    return matches


def _move_body(param: str | None) -> str:
    """The canonical character-Humanoid move body (whole-method-body replace).
    Lazy-acquires ``_cam`` with ``followCharacter = true`` so the eye follows
    the character regardless of method order vs ``Rotate`` on frame 1; reads
    the service's yaw basis; drives the LocalPlayer.Character Humanoid."""
    arg = param or "dt"
    return (
        "\n"
        '\tlocal UIS = game:GetService("UserInputService")\n'
        "\tif not self._cam then\n"
        '\t\tself._cam = require(game:GetService("ReplicatedStorage")'
        ':WaitForChild("SceneCameraInput")).acquire()\n'
        "\t\tself._cam:configure({rig = self.gameObject, followCharacter = true})\n"
        "\tend\n"
        '\tlocal lp = game:GetService("Players").LocalPlayer\n'
        "\tlocal char = lp and lp.Character\n"
        '\tlocal hum = char and char:FindFirstChildOfClass("Humanoid")\n'
        "\tif not hum then return end\n"
        "\tlocal h = 0\n"
        "\tif UIS:IsKeyDown(Enum.KeyCode.D) then h += 1 end\n"
        "\tif UIS:IsKeyDown(Enum.KeyCode.A) then h -= 1 end\n"
        "\tlocal v = 0\n"
        "\tif UIS:IsKeyDown(Enum.KeyCode.W) then v += 1 end\n"
        "\tif UIS:IsKeyDown(Enum.KeyCode.S) then v -= 1 end\n"
        "\tlocal dir = self._cam:getYawBasis():VectorToWorldSpace("
        "Vector3.new(h, 0, -v))\n"
        "\tif dir.Magnitude > 0 then hum:Move(dir.Unit, false) "
        "else hum:Move(Vector3.zero, false) end\n"
        "\tif UIS:IsKeyDown(Enum.KeyCode.Space) then hum.Jump = true end\n"
    )


def lower_movement_facet(players: list[_HasLuauSource]) -> int:
    """Whole-body-replace each player's WASD method with the canonical
    character-Humanoid move body. Idempotent: if the body already calls
    ``getYawBasis():VectorToWorldSpace`` + ``:Move(``, skip. Returns the number
    of scripts modified."""
    changed = 0
    for s in players:
        src = s.luau_source or ""
        # Idempotency: already-lowered body carries both markers.
        if "getYawBasis():VectorToWorldSpace" in src and ":Move(" in src:
            continue
        stripped = _strip_strings_and_comments(src)
        found = _wasd_method_body(stripped)
        if found is None:
            continue
        body_start, body_len, param = found
        new_src = src[:body_start] + _move_body(param) + src[body_start + body_len:]
        if new_src != src:
            s.luau_source = new_src
            changed += 1
    return changed
