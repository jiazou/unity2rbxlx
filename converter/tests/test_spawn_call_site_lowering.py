"""Tests for the gap #5 L1 spawn call-site lowering.

Drives the REAL transpiled TrackManager.luau shapes (segment / obstacle /
premium / cloud rewrites + the deferred consumable), idempotence, fail-closed
abstention, and the in-place script orchestration helper.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from converter.spawn_call_site_lowering import (
    SpawnRewriteResult,
    lower_spawn_call_sites,
    lower_spawn_call_sites_in_scripts,
)


# --- Real-shape fixtures (verbatim spans from the #210 diag TrackManager.luau) ---

SEGMENT_SRC = """\
function TrackManager:SpawnNewSegment()
    local zone = self.currentTheme.zones[self.currentZone + 1]
    local prefabCount = #zone.prefabList
    local segmentUse = math.random(0, prefabCount - 1)

    -- AssetReference.InstantiateAsync(offscreen pos): no Roblox addressable equivalent.
    -- UNCONVERTED: instantiate the segment prefab from its zone asset reference.
    local newSegment = nil
    if newSegment == nil then
        warn(string.format("Unable to load segment %d.", segmentUse))
        return
    end

    newSegment.manager = self
end
"""

OBSTACLE_SRC = """\
function TrackManager:SpawnFromAssetReference(reference, segment, posIndex)
    local _ = reference
    -- Addressables.LoadAssetAsync<GameObject>(reference): no Roblox equivalent.
    -- UNCONVERTED: load the obstacle prefab from its asset reference.
    local obj = nil
    if obj ~= nil then
        local obstacle = obj:GetComponent("Obstacle")
        if obstacle ~= nil then
            obstacle:Spawn(segment, segment.obstaclePositions[posIndex + 1])
        end
    end
end
"""

PREMIUM_SRC = """\
function TrackManager:SpawnCoinAndPowerup(segment)
    local toUse = nil
    if true then
        -- Addressables.InstantiateAsync(premiumCollectible name): UNCONVERTED.
        toUse = nil
        if toUse == nil then
            warn(string.format("Unable to load collectable %s.",
                tostring(self.currentTheme.premiumCollectible.name)))
            return
        end
        toUse.Parent = segment.gameObject
    end
end
"""

CONSUMABLE_SRC = """\
function TrackManager:SpawnCoinAndPowerup(segment)
    local toUse = nil
    if true then
        -- Addressables.InstantiateAsync(consumable name): UNCONVERTED (no addressable-by-name).
        toUse = nil
        if toUse == nil then
            warn(string.format("Unable to load consumable %s.",
                tostring(self.consumableDatabase.consumbales[picked].gameObject.Name)))
            return
        end
        toUse.Parent = segment.gameObject
    end
end
"""

CLOUD_SRC = """\
function TrackManager:Update(dt)
    if cloud ~= nil then
        -- Instantiate(cloud) under parallaxRoot. Theme cloud assets carry no prefab_id
        -- (asset-reference); degrade to a Clone of the source instance.
        local obj = cloud:Clone()
        obj.Parent = self.parallaxRoot

        local cmd = self.currentTheme.cloudMinimumDistance
    end
end
"""

# The character site — already lowered to a scene-find, NO sentinel. Must abstain.
CHARACTER_SRC = """\
function TrackManager:Begin()
    -- Addressables.InstantiateAsync(PlayerData character name) loads a prefab by string
    -- key; Roblox has no addressable-by-name equivalent. Degrade to finding the existing
    -- Character component in the scene.
    local player = self.host.findObjectOfType("Character")
    if player == nil then
        warn(string.format("Unable to load character %s.", "x"))
        return
    end
end
"""


def test_segment_rewrite() -> None:
    new, res = lower_spawn_call_sites(SEGMENT_SRC)
    assert res.rewritten == 1
    assert res.deferred == 0
    assert (
        "local newSegment = self.host.instantiatePrefab("
        "zone.prefabList[segmentUse + 1], self.gameObject, nil)" in new
    )
    assert "local newSegment = nil" not in new  # sentinel removed
    # The fail-soft guard is preserved (host.instantiatePrefab may return nil).
    assert "Unable to load segment" in new


def test_obstacle_inverted_rewrite_keeps_body() -> None:
    new, res = lower_spawn_call_sites(OBSTACLE_SRC)
    assert res.rewritten == 1
    assert (
        "local obj = self.host.instantiatePrefab(reference, segment.gameObject, nil)"
        in new
    )
    assert "local obj = nil" not in new
    # The inverted guard + body are preserved (they consume obj).
    assert "if obj ~= nil then" in new
    assert 'obj:GetComponent("Obstacle")' in new


def test_premium_rewrite_recovers_prefab_id_expr() -> None:
    new, res = lower_spawn_call_sites(PREMIUM_SRC)
    assert res.rewritten == 1
    assert (
        "toUse = self.host.instantiatePrefab("
        "self.currentTheme.premiumCollectible, segment.gameObject, nil)" in new
    )
    # bare ``toUse = nil`` sentinel removed (the leading ``local toUse = nil`` decl
    # at the method top is NOT the sentinel and is left intact).
    assert "toUse = nil\n        if toUse == nil then" not in new
    assert "Unable to load collectable" in new


def test_cloud_clone_on_string_rewritten() -> None:
    new, res = lower_spawn_call_sites(CLOUD_SRC)
    assert res.rewritten == 1
    assert (
        "local obj = self.host.instantiatePrefab(cloud, self.parallaxRoot, nil)"
        in new
    )
    assert ":Clone()" not in new  # the broken clone-on-a-string is gone
    assert "obj.Parent = self.parallaxRoot" in new


def test_consumable_is_deferred_not_rewritten() -> None:
    new, res = lower_spawn_call_sites(CONSUMABLE_SRC)
    assert res.rewritten == 0
    assert res.deferred == 1
    # Fail-closed: the site is UNCHANGED (its loud warn-abort path is preserved).
    assert new == CONSUMABLE_SRC
    assert "instantiatePrefab" not in new


def test_character_site_excluded_no_sentinel() -> None:
    # Origin comment present but already lowered to findObjectOfType (no sentinel).
    new, res = lower_spawn_call_sites(CHARACTER_SRC)
    assert res.rewritten == 0
    assert res.deferred == 0
    assert new == CHARACTER_SRC


def test_idempotent_twice_call() -> None:
    for src in (SEGMENT_SRC, OBSTACLE_SRC, PREMIUM_SRC, CLOUD_SRC):
        once, _ = lower_spawn_call_sites(src)
        twice, res2 = lower_spawn_call_sites(once)
        assert once == twice, "second pass must be byte-stable"
        assert res2.rewritten == 0, "second pass finds no sentinel"


def test_segment_fail_closed_without_zone_local() -> None:
    # Origin comment + sentinel present, but the zone/index locals are absent —
    # fail-closed (no rewrite), never a guessed expr.
    broken = """\
function TrackManager:SpawnNewSegment()
    -- AssetReference.InstantiateAsync(offscreen pos): no Roblox addressable equivalent.
    local newSegment = nil
    if newSegment == nil then
        warn(string.format("Unable to load segment %d.", segmentUse))
        return
    end
end
"""
    new, res = lower_spawn_call_sites(broken)
    assert res.rewritten == 0
    assert new == broken  # untouched


def test_abstain_when_no_origin_comment() -> None:
    # A segment-shaped sentinel WITHOUT the deterministic origin comment must NOT
    # be rewritten (the comment is the load-bearing identity gate).
    no_comment = """\
function TrackManager:SpawnNewSegment()
    local zone = self.currentTheme.zones[self.currentZone + 1]
    local segmentUse = math.random(0, prefabCount - 1)
    local newSegment = nil
    if newSegment == nil then
        warn(string.format("Unable to load segment %d.", segmentUse))
        return
    end
end
"""
    new, res = lower_spawn_call_sites(no_comment)
    assert res.rewritten == 0
    assert new == no_comment


def test_all_five_shapes_in_one_module() -> None:
    # The real TrackManager.luau carries all 5 sites; combine them and assert the
    # 4 active rewrites + 1 deferral fire together with no cross-shape interference.
    combined = SEGMENT_SRC + OBSTACLE_SRC + PREMIUM_SRC + CONSUMABLE_SRC + CLOUD_SRC
    new, res = lower_spawn_call_sites(combined)
    assert res.rewritten == 4
    assert res.deferred == 1
    assert new.count("instantiatePrefab") == 4
    # Idempotent on the combined module too.
    again, res2 = lower_spawn_call_sites(new)
    assert again == new
    assert res2.rewritten == 0


def test_real_track_manager_shapes() -> None:
    """Drive the REAL #210 diag TrackManager.luau (not a synthetic fixture)."""
    import os

    real = (
        "/Users/jiazou/.claude/harness-runs/trash-dash-phase2-20260618T102928/"
        "wt/diag/converter/output/trash-dash-phase2-diag/scripts/TrackManager.luau"
    )
    if not os.path.exists(real):
        pytest.skip("diag TrackManager.luau not present in this environment")
    src = open(real, encoding="utf-8").read()
    new, res = lower_spawn_call_sites(src)
    assert res.rewritten == 4, "segment/obstacle/premium/cloud must all rewrite"
    assert res.deferred == 1, "consumable must defer"
    assert ":Clone()" not in new  # cloud clone-on-string fixed
    # No spawn site still parks on a dead ``= nil`` sentinel followed by an abort.
    assert "local newSegment = nil" not in new
    # Idempotent on the real source.
    again, res2 = lower_spawn_call_sites(new)
    assert again == new
    assert res2.rewritten == 0


@dataclass
class _FakeScript:
    source: str


def test_in_place_orchestration_helper() -> None:
    s1 = _FakeScript(source=SEGMENT_SRC)
    s2 = _FakeScript(source=OBSTACLE_SRC)
    s3 = _FakeScript(source="-- a module with no spawn sites\nreturn {}\n")
    result = lower_spawn_call_sites_in_scripts([s1, s2, s3])
    assert isinstance(result, SpawnRewriteResult)
    assert result.rewritten == 2
    assert "instantiatePrefab" in s1.source
    assert "instantiatePrefab" in s2.source
    assert s3.source == "-- a module with no spawn sites\nreturn {}\n"  # untouched


def test_in_place_helper_skips_non_string_source() -> None:
    @dataclass
    class _Bad:
        source: object

    bad = _Bad(source=None)
    result = lower_spawn_call_sites_in_scripts([bad])  # type: ignore[list-item]
    assert result.rewritten == 0
    assert result.deferred == 0
