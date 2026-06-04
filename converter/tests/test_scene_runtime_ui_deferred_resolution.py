"""Slice 1.1: deferred UI-host gameObject resolution (generic boot race).

A UI-owned instance binds its host GameObject to a ScreenGui that Roblox
clones StarterGui->PlayerGui at spawn. At client boot the synchronous build
loop can run BEFORE that clone lands, so the one-shot ``workspaceFind``
returns nil and a UI controller (e.g. HudControl) would be constructed with
``self.gameObject == nil`` and crash.

These tests drive the production ``scene_runtime.luau`` through the shared
standalone-luau harness and assert:

  * A UI-owned instance whose ``workspaceFind`` MISSES is NOT built with a
    nil gameObject during the synchronous pass; instead it is deferred and
    completed via ``awaitUiHost`` (event-driven clone wait), so its
    ``Awake`` runs with a non-nil ``self.gameObject``.
  * The ``instance_owner_is_ui`` gate is strict: a NON-UI instance whose
    ``workspaceFind`` misses stays on the one-shot path (built immediately
    with a nil gameObject, ``awaitUiHost`` never called) -- no boot-time
    deferral / timeout penalty for the common path.

Regression guard: against the PRE-FIX ``start`` (one-shot build for every
instance) the first test FAILS -- the UI component is built synchronously
with a nil gameObject and its ``Awake`` assert blows up.
"""

from __future__ import annotations

import textwrap

from tests.test_scene_runtime_host_behavior import (  # noqa: F401
    _run_scenario,
    pytestmark,
)


# The harness ``servicesFor`` does not provide ``awaitUiHost``; each
# scenario appends it to the returned services table (this is exactly how a
# production client entrypoint injects the host-surface resolver).


class TestDeferredUiHostResolution:

    def test_ui_owned_miss_defers_and_binds_via_await(self):
        scenario = textwrap.dedent("""\
            local events = {}

            -- UI controller: crashes if gameObject is nil at Awake (the
            -- real HudControl:46 shape).
            local Hud = {} ; Hud.__index = Hud
            function Hud.new(_) return setmetatable({}, Hud) end
            function Hud:Awake()
                assert(self.gameObject ~= nil,
                    "Hud.gameObject must be bound before Awake")
                table.insert(events, "Hud.Awake go=" .. tostring(self.gameObject.Name))
            end
            function Hud:OnEnable() table.insert(events, "Hud.OnEnable") end
            function Hud:Start() table.insert(events, "Hud.Start") end

            local plan = {
                modules = {
                    hud = {stem = "Hud", runtime_bearing = true, module_path = "x"},
                },
                scenes = {
                    A = {
                        instances = {
                            {instance_id = "A:1", script_id = "hud",
                             game_object_id = "hudId", active = true,
                             enabled = true, config = {},
                             instance_owner_is_ui = true},
                        },
                        references = {},
                        lifecycle_order = {"A:1"},
                    },
                },
                prefabs = {},
                domain_overrides = {},
            }

            -- The HUD clone instance -- present in PlayerGui, but the
            -- synchronous workspaceFind must NOT see it (simulate "clone
            -- hasn't landed in workspace yet at boot").
            local hudClone = {Name = "HUD", _sceneRuntimeId = "hudId", _children = {}}

            -- workspaceFind returns nil for the UI id at boot (the race).
            local services = servicesFor(plan, {hud = Hud}, {})
            local awaitCalls = {}
            services.awaitUiHost = function(id)
                table.insert(awaitCalls, id)
                -- The clone has landed by the time the deferred resolver runs.
                if id == "hudId" then return hudClone end
                return nil
            end

            local engine = SceneRuntime.new(services, plan)
            engine:start(nil)
            runDeferred()  -- flush deferred Starts (sync batches + late UI batch)

            print("AWAIT_CALLS=" .. tostring(#awaitCalls))
            for _, e in ipairs(events) do print(e) end
            print("DONE")
        """)
        rc, out, err = _run_scenario(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        # awaitUiHost was used to resolve the missed UI host.
        assert "AWAIT_CALLS=1" in lines, out
        # Awake ran with a non-nil gameObject bound to the landed clone.
        assert any(l.startswith("Hud.Awake go=HUD") for l in lines), out
        # Full late lifecycle batch ran (Awake -> OnEnable -> Start).
        assert "Hud.OnEnable" in lines, out
        assert "Hud.Start" in lines, out
        assert lines.index("Hud.Awake go=HUD") < lines.index("Hud.OnEnable")
        assert lines.index("Hud.OnEnable") < lines.index("Hud.Start")

    def test_non_ui_miss_stays_one_shot_no_await(self):
        scenario = textwrap.dedent("""\
            local events = {}

            -- Non-UI controller tolerant of a nil gameObject (the one-shot
            -- path builds it immediately even on a workspaceFind miss).
            local Logic = {} ; Logic.__index = Logic
            function Logic.new(_) return setmetatable({}, Logic) end
            function Logic:Awake()
                table.insert(events,
                    "Logic.Awake go=" .. tostring(self.gameObject))
            end

            local plan = {
                modules = {
                    logic = {stem = "Logic", runtime_bearing = true, module_path = "x"},
                },
                scenes = {
                    A = {
                        instances = {
                            -- No instance_owner_is_ui flag -> non-UI.
                            {instance_id = "A:1", script_id = "logic",
                             game_object_id = "missingId", active = true,
                             enabled = true, config = {}},
                        },
                        references = {},
                        lifecycle_order = {"A:1"},
                    },
                },
                prefabs = {},
                domain_overrides = {},
            }

            -- workspaceFind misses (empty instance table) -> one-shot nil.
            local services = servicesFor(plan, {logic = Logic}, {})
            local awaitCalls = {}
            services.awaitUiHost = function(id)
                table.insert(awaitCalls, id)
                return nil
            end

            local engine = SceneRuntime.new(services, plan)
            engine:start(nil)
            runDeferred()

            print("AWAIT_CALLS=" .. tostring(#awaitCalls))
            for _, e in ipairs(events) do print(e) end
            print("DONE")
        """)
        rc, out, err = _run_scenario(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        # The non-UI miss must NOT route through awaitUiHost (no deferral,
        # no timeout penalty) -- it stays one-shot.
        assert "AWAIT_CALLS=0" in lines, out
        # It was still built immediately (with a nil gameObject), proving the
        # one-shot path is unchanged for non-UI.
        assert "Logic.Awake go=nil" in lines, out
