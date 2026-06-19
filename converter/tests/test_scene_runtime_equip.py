"""Phase 2 (camera-mount -> player-mount equip) acceptance criteria 6-9.

Drives the NEW server-side SceneRuntime equip methods through the standalone
``luau`` interpreter over a stub service surface + mock Roblox Instances. Skips
cleanly when ``luau`` is absent.

  6. resolveEquipPrefabId maps field->prefab_id from a seeded plan.equip_prefabs,
     nil on an unknown field (D13 / Edge c).
  7. equipWeaponOnCharacter welds + parents + replicates-by-parenting: a
     WeldConstraint(Part0=RightHand, Part1=weldPart) is created, the clone is
     parented under the Character named "_EquippedWeapon", and clone BaseParts are
     CanCollide=false / Massless=true.
  8. double-equip removes the prior weapon (Edge d): two calls leave exactly ONE
     _EquippedWeapon under the Character.
  9. R6 fallback + hard no-op (Edge b): RightHand when present, "Right Arm" when
     only that exists, nil when neither -> equipWeaponOnCharacter returns nil and
     creates no weld.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

HOST_RUNTIME_PATH = (
    Path(__file__).parent.parent / "runtime" / "scene_runtime.luau"
)

pytestmark = pytest.mark.skipif(
    shutil.which("luau") is None or not HOST_RUNTIME_PATH.exists(),
    reason="needs standalone luau interpreter + host runtime file",
)


def _harness_preamble() -> str:
    host_source = HOST_RUNTIME_PATH.read_text(encoding="utf-8")
    delim = "==="
    while f"]{delim}]" in host_source or f"[{delim}[" in host_source:
        delim += "="
    embedded = f"[{delim}[\n{host_source}\n]{delim}]"
    return textwrap.dedent(f"""\
        local HOST_RUNTIME_SOURCE = {embedded}
        local SceneRuntime
        do
            local chunk, err = loadstring(HOST_RUNTIME_SOURCE, "scene_runtime")
            assert(chunk, "load host runtime failed: " .. tostring(err))
            SceneRuntime = chunk()
        end
    """) + _HARNESS_BODY


# Mock Roblox Instance surface: enough for the equip methods. A part / model is a
# table with Name, a ClassName, parent/children bookkeeping, and the methods the
# runtime calls (FindFirstChild / IsA / GetDescendants / Destroy / CFrame).
# ``SceneRuntime`` + the helpers below are top-level LOCALS in this chunk so the
# appended scenario code sees them (standalone luau makes ``_G`` readonly, so we
# do NOT stash anything there). ``Instance`` is a real GLOBAL because the host
# runtime chunk (loaded via loadstring) reads it as a global.
_HARNESS_BODY = r"""
-- ``Instance.new`` only needs to mint a WeldConstraint for the weld core.
local _createdWelds = {}
Instance = {}
function Instance.new(className)
    local inst = {
        ClassName = className,
        _children = {},
        Parent = nil,
        Part0 = nil,
        Part1 = nil,
    }
    function inst:IsA(c) return c == self.ClassName end
    function inst:Destroy() self._destroyed = true; self.Parent = nil end
    if className == "WeldConstraint" then
        table.insert(_createdWelds, inst)
        -- Setting .Parent on a weld is a no-op for the harness.
    end
    return inst
end

-- A minimal mock instance factory. Children are tracked in an ORDERED list
-- (``_childList``) and ``FindFirstChild`` scans it by LIVE ``.Name`` (Roblox
-- resolves children by live name, and the equip path renames a clone AFTER
-- parenting), skipping destroyed entries.
local function mockInst(name, className)
    local inst = {
        Name = name,
        ClassName = className or "Part",
        _childList = {},
        _descendants = {},
        Parent = nil,
        CanCollide = true,
        Massless = false,
        -- Real prefab-field templates are emitted HIDDEN (transparency=1.0) and
        -- parts default Anchored=true. Default the mock to that pinned/hidden
        -- shape so the equip path must reset it (P1-A).
        Transparency = 1,
        Anchored = true,
        CFrame = "cf0",
        PrimaryPart = nil,
        _pivotedTo = nil,
    }
    function inst:IsA(c)
        if c == self.ClassName then return true end
        -- A "Part"/"MeshPart" mock answers true to BasePart.
        if c == "BasePart" then
            return self.ClassName == "Part" or self.ClassName == "MeshPart"
                or self._isBasePart == true
        end
        return false
    end
    function inst:FindFirstChild(n)
        for _, child in ipairs(self._childList) do
            if not child._destroyed and child.Name == n then
                return child
            end
        end
        return nil
    end
    function inst:GetDescendants()
        return self._descendants
    end
    function inst:FindFirstChildWhichIsA(c, recurse)
        for _, d in ipairs(self._descendants) do
            if d:IsA(c) then return d end
        end
        return nil
    end
    function inst:Destroy()
        self._destroyed = true
        self.Parent = nil
    end
    -- A Model relocates as a unit via PivotTo; record the target for assertions.
    function inst:PivotTo(cf)
        self._pivotedTo = cf
    end
    return inst
end

local function addChild(parent, child)
    child.Parent = parent
    table.insert(parent._childList, child)
end

-- A stub services table just for the equip methods. ``clonePrefabTemplate``
-- returns whatever ``cloneFactory`` produces (parented under ``parent``).
local function equipServices(cloneFactory)
    return {
        warn = function() end,
        clonePrefabTemplate = function(prefabId, parent, cframe)
            local clone = cloneFactory(prefabId)
            if clone and parent then
                clone.Parent = parent
                -- Mirror Roblox auto-registering a child on ``.Parent =``.
                if parent._childList then
                    table.insert(parent._childList, clone)
                end
            end
            return clone
        end,
    }
end

-- Each test's scenario body is appended into THIS chunk, so the locals above
-- (SceneRuntime, mockInst, addChild, equipServices) and ``createdWelds`` below
-- are in scope.
local function createdWelds() return _createdWelds end
"""


def _run_scenario(scenario_body: str) -> tuple[int, str, str]:
    script = _harness_preamble() + "\n" + scenario_body + "\n"
    with tempfile.NamedTemporaryFile(
        suffix=".luau", mode="w", delete=False,
    ) as f:
        f.write(script)
        path = f.name
    try:
        result = subprocess.run(
            ["luau", path], capture_output=True, text=True, timeout=15,
        )
        return result.returncode, result.stdout, result.stderr
    finally:
        Path(path).unlink(missing_ok=True)


def _assert_ok(scenario: str) -> str:
    rc, out, err = _run_scenario(scenario)
    assert rc == 0, f"luau exited {rc}\nstdout={out}\nstderr={err}"
    assert "OK" in out, f"scenario did not print OK\nstdout={out}\nstderr={err}"
    return out


# ---------------------------------------------------------------------------
# Criterion 6 — resolveEquipPrefabId
# ---------------------------------------------------------------------------

class TestResolveEquipPrefabId:

    def test_maps_field_to_prefab_id_and_nil_on_unknown(self):
        _assert_ok(textwrap.dedent("""\
            local plan = {equip_prefabs = {riflePrefab = "prefab_rifle_abc"}}
            local engine = SceneRuntime.new({warn = function() end}, plan)
            assert(engine:resolveEquipPrefabId("riflePrefab") == "prefab_rifle_abc",
                "known field must resolve to its prefab_id")
            assert(engine:resolveEquipPrefabId("unknownField") == nil,
                "unknown field must resolve to nil")
            assert(engine:resolveEquipPrefabId("") == nil, "empty field -> nil")
            -- No equip_prefabs map at all -> nil (Edge c).
            local engine2 = SceneRuntime.new({warn = function() end}, {})
            assert(engine2:resolveEquipPrefabId("riflePrefab") == nil,
                "missing equip_prefabs map -> nil")
            print("OK")
        """))


# ---------------------------------------------------------------------------
# Criterion 7 — equipWeaponOnCharacter welds + parents + collide off
# ---------------------------------------------------------------------------

class TestEquipWeaponOnCharacter:

    def test_welds_parents_and_disables_collision(self):
        _assert_ok(textwrap.dedent("""\

            -- Character with an R15 RightHand BasePart.
            local character = mockInst("Character", "Model")
            local rightHand = mockInst("RightHand", "Part")
            rightHand.CFrame = "handCF"
            addChild(character, rightHand)

            -- The clone is a Model with one nested BasePart (the muzzle).
            local muzzle = mockInst("Muzzle", "Part")
            local function cloneFactory(prefabId)
                local m = mockInst("RiflePrefabClone", "Model")
                m.PrimaryPart = muzzle
                m._descendants = {muzzle}
                return m
            end

            local services = equipServices(cloneFactory)
            local plan = {equip_prefabs = {riflePrefab = "prefab_rifle"}}
            local engine = SceneRuntime.new(services, plan)

            local clone = engine:equipWeaponOnCharacter(character, "prefab_rifle")
            assert(clone ~= nil, "equip must return the clone")
            -- (ii) parented under the Character + named _EquippedWeapon.
            assert(clone.Name == "_EquippedWeapon", "clone renamed to _EquippedWeapon")
            assert(clone.Parent == character, "clone parented under the Character")
            assert(character:FindFirstChild("_EquippedWeapon") == clone,
                "Character has the _EquippedWeapon child")
            -- (i) a WeldConstraint Part0=RightHand, Part1=weldPart was created.
            local welds = createdWelds()
            assert(#welds == 1, "exactly one WeldConstraint created, got " .. #welds)
            assert(welds[1].Part0 == rightHand, "weld Part0 == RightHand")
            assert(welds[1].Part1 == muzzle, "weld Part1 == the weldable anchor")
            -- A Model relocates as a unit via PivotTo(hand.CFrame).
            assert(clone._pivotedTo == "handCF", "model pivoted to the hand CFrame")
            -- (iii) clone BaseParts CanCollide=false, Massless=true, and the
            -- live weapon is VISIBLE + un-anchored (P1-A).
            assert(muzzle.CanCollide == false, "muzzle CanCollide disabled")
            assert(muzzle.Massless == true, "muzzle Massless enabled")
            assert(muzzle.Transparency == 0, "muzzle un-hidden")
            assert(muzzle.Anchored == false, "muzzle un-anchored")
            print("OK")
        """))

    def test_multipart_hidden_anchored_model_is_unhidden_unanchored_and_rigid(self):
        # P1-A: a real prefab template is HIDDEN (Transparency=1) and ANCHORED.
        # A ge-2-part Model must come back fully visible, un-anchored, pivoted to
        # the hand, with every descendant welded to the anchor and the anchor
        # welded to the hand. This FAILS against the pre-fix code (which left
        # Transparency=1 / Anchored=true and created only the hand weld).
        _assert_ok(textwrap.dedent("""\
            local character = mockInst("Character", "Model")
            local rightHand = mockInst("RightHand", "Part")
            rightHand.CFrame = "handCF"
            addChild(character, rightHand)

            -- Two-part rifle: a Body anchor (PrimaryPart) + a Barrel descendant.
            local body, barrel
            local function cloneFactory(_)
                local m = mockInst("Rifle", "Model")
                body = mockInst("Body", "Part")
                barrel = mockInst("Barrel", "Part")
                m.PrimaryPart = body
                m._descendants = {body, barrel}
                return m
            end
            local engine = SceneRuntime.new(equipServices(cloneFactory),
                {equip_prefabs = {riflePrefab = "p"}})

            -- Sanity: the template starts hidden + anchored.
            local clone = engine:equipWeaponOnCharacter(character, "p")
            assert(clone ~= nil, "equip returns the clone")

            -- Every part visible + un-anchored + collision/mass off.
            for _, part in ipairs({body, barrel}) do
                assert(part.Transparency == 0, part.Name .. " un-hidden")
                assert(part.Anchored == false, part.Name .. " un-anchored")
                assert(part.CanCollide == false, part.Name .. " CanCollide off")
                assert(part.Massless == true, part.Name .. " Massless on")
            end

            -- The whole model relocates as a unit.
            assert(clone._pivotedTo == "handCF", "model pivoted to the hand")

            -- Welds: one barrel->body (rigid), one body->hand (mount). Order is
            -- descendant-welds-first then the hand weld.
            local welds = createdWelds()
            assert(#welds == 2, "two WeldConstraints, got " .. #welds)
            -- Find the inner (anchor<->descendant) and the hand weld.
            local innerWeld, handWeld
            for _, w in ipairs(welds) do
                if w.Part0 == rightHand then handWeld = w
                elseif w.Part0 == body and w.Part1 == barrel then innerWeld = w end
            end
            assert(innerWeld ~= nil, "barrel welded to the body anchor")
            assert(handWeld ~= nil and handWeld.Part1 == body,
                "body anchor welded to the hand")
            print("OK")
        """))

    def test_bare_basepart_clone_welds_directly(self):
        # A clone that is itself a BasePart (no Model) welds directly.
        _assert_ok(textwrap.dedent("""\
            local character = mockInst("Character", "Model")
            local rightHand = mockInst("RightHand", "Part")
            addChild(character, rightHand)
            local function cloneFactory(_)
                local p = mockInst("BareGun", "Part")
                p._descendants = {}
                return p
            end
            local engine = SceneRuntime.new(equipServices(cloneFactory),
                {equip_prefabs = {riflePrefab = "p"}})
            local clone = engine:equipWeaponOnCharacter(character, "p")
            assert(clone ~= nil and clone.Name == "_EquippedWeapon")
            local welds = createdWelds()
            assert(welds[#welds].Part1 == clone, "bare BasePart welds itself")
            assert(clone.CanCollide == false and clone.Massless == true)
            print("OK")
        """))


# ---------------------------------------------------------------------------
# Criterion 8 — double-equip removes the prior weapon
# ---------------------------------------------------------------------------

class TestDoubleEquip:

    def test_two_equips_leave_exactly_one_weapon(self):
        _assert_ok(textwrap.dedent("""\

            local character = mockInst("Character", "Model")
            local rightHand = mockInst("RightHand", "Part")
            addChild(character, rightHand)

            local seq = 0
            local function cloneFactory(_)
                seq = seq + 1
                local p = mockInst("Gun" .. seq, "Part")
                p._descendants = {}
                return p
            end
            local engine = SceneRuntime.new(equipServices(cloneFactory),
                {equip_prefabs = {riflePrefab = "p"}})

            local first = engine:equipWeaponOnCharacter(character, "p")
            local second = engine:equipWeaponOnCharacter(character, "p")
            assert(first ~= second, "second equip is a fresh clone")
            -- The first clone was destroyed before the second weld.
            assert(first._destroyed == true, "prior _EquippedWeapon destroyed")
            -- Exactly one _EquippedWeapon survives under the character.
            assert(character:FindFirstChild("_EquippedWeapon") == second,
                "the surviving weapon is the second clone")
            print("OK")
        """))


# ---------------------------------------------------------------------------
# Criterion 9 — R6 fallback + hard no-op
# ---------------------------------------------------------------------------

class TestRightHandFallback:

    def test_right_hand_primary(self):
        _assert_ok(textwrap.dedent("""\
            local engine = SceneRuntime.new({warn = function() end}, {})
            local char = mockInst("C", "Model")
            local rh = mockInst("RightHand", "Part")
            addChild(char, rh)
            assert(engine:_resolveRightHand(char) == rh, "R15 RightHand resolves")
            print("OK")
        """))

    def test_right_arm_fallback_when_no_right_hand(self):
        _assert_ok(textwrap.dedent("""\
            local engine = SceneRuntime.new({warn = function() end}, {})
            local char = mockInst("C", "Model")
            local arm = mockInst("Right Arm", "Part")
            addChild(char, arm)
            assert(engine:_resolveRightHand(char) == arm,
                "R6 falls back to Right Arm")
            print("OK")
        """))

    def test_no_hand_returns_nil_and_equip_no_ops(self):
        _assert_ok(textwrap.dedent("""\
            local engine = SceneRuntime.new(
                equipServices(function() return mockInst("g", "Part") end),
                {equip_prefabs = {riflePrefab = "p"}})
            local char = mockInst("C", "Model")  -- no RightHand, no Right Arm
            assert(engine:_resolveRightHand(char) == nil, "no hand -> nil")
            local before = #createdWelds()
            local result = engine:equipWeaponOnCharacter(char, "p")
            assert(result == nil, "no hand -> equip returns nil (hard no-op)")
            assert(#createdWelds() == before, "no weld created on a hard no-op")
            assert(char:FindFirstChild("_EquippedWeapon") == nil,
                "no weapon parented on a hard no-op")
            print("OK")
        """))
