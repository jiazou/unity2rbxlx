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

import subprocess
import tempfile
import textwrap
from pathlib import Path

from tests.test_scene_runtime_host_behavior import (  # noqa: F401
    _luau_available,
    _run_scenario,
    pytestmark,
)


def _await_ui_host_source() -> str:
    """Extract the ``awaitUiHost`` Luau function body from the emitted
    client entrypoint source, so the connect-first / timeout logic is
    tested as actually shipped (not a synchronous stub)."""
    from converter import autogen

    src = autogen._SCENE_RUNTIME_CLIENT_SOURCE
    start = src.index("local function awaitUiHost(")
    # The function ends at the first line that is exactly ``end`` at column 0
    # after the start (top-level ``local function``).
    rest = src[start:]
    end_marker = "\nend\n"
    end = rest.index(end_marker) + len(end_marker)
    return rest[:end]


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


class TestBatchedDeferralAndBackPatch:
    """Fix-round-1 BLOCKING #1 (batched lifecycle) + #2 (inbound ref
    back-patch). A synchronous non-UI ``Controller`` holds a serialized ref
    to a deferred UI ``Hud``; the deferred batch must (a) back-patch that
    inbound ref and (b) run the whole deferred set as ONE batch so its
    Awake/OnEnable all precede its Start (intra-batch order)."""

    def test_inbound_ref_backpatched_and_intra_batch_order(self):
        scenario = textwrap.dedent("""\
            local events = {}

            -- Synchronous (non-UI) source. Its serialized ``hud`` ref targets
            -- the deferred UI component; pre-fix it stays nil forever.
            local Controller = {} ; Controller.__index = Controller
            function Controller.new(_) return setmetatable({hud = nil}, Controller) end
            function Controller:Awake() table.insert(events, "Controller.Awake") end
            function Controller:Start()
                table.insert(events,
                    "Controller.Start hud=" .. tostring(self.hud and self.hud._tag))
            end

            -- Two deferred UI components on DIFFERENT hosts. ``Hud`` is the
            -- inbound-ref target; ``Hud2`` is a second deferred peer to prove
            -- the batch runs all Awakes before any Start.
            local Hud = {} ; Hud.__index = Hud
            function Hud.new(_) return setmetatable({_tag = "HUD"}, Hud) end
            function Hud:Awake()
                assert(self.gameObject ~= nil, "Hud.go must be bound")
                table.insert(events, "Hud.Awake")
            end
            function Hud:Start() table.insert(events, "Hud.Start") end

            local Hud2 = {} ; Hud2.__index = Hud2
            function Hud2.new(_) return setmetatable({}, Hud2) end
            function Hud2:Awake()
                assert(self.gameObject ~= nil, "Hud2.go must be bound")
                table.insert(events, "Hud2.Awake")
            end
            function Hud2:Start() table.insert(events, "Hud2.Start") end

            local plan = {
                modules = {
                    ctl  = {stem = "Controller", runtime_bearing = true, module_path = "x"},
                    hud  = {stem = "Hud",  runtime_bearing = true, module_path = "x"},
                    hud2 = {stem = "Hud2", runtime_bearing = true, module_path = "x"},
                },
                scenes = {
                    A = {
                        instances = {
                            {instance_id = "A:c", script_id = "ctl",
                             game_object_id = "ctlId", active = true,
                             enabled = true, config = {}},
                            {instance_id = "A:h", script_id = "hud",
                             game_object_id = "hudId", active = true,
                             enabled = true, config = {},
                             instance_owner_is_ui = true},
                            {instance_id = "A:h2", script_id = "hud2",
                             game_object_id = "hud2Id", active = true,
                             enabled = true, config = {},
                             instance_owner_is_ui = true},
                        },
                        -- Controller present in the workspace; both Huds miss.
                        references = {
                            {["from"] = "A:c", field = "hud", index = nil,
                             target_kind = "component", target_ref = "A:h"},
                        },
                        -- lifecycle_order: Hud2 BEFORE Hud, so the batch must
                        -- Awake Hud2 first (proves intra-batch ordering, not
                        -- defer/resolve order which is Hud then Hud2).
                        lifecycle_order = {"A:c", "A:h2", "A:h"},
                    },
                },
                prefabs = {},
                domain_overrides = {},
            }

            -- Only the Controller host exists in workspace at boot.
            local ctlGo = {Name = "Ctl", _sceneRuntimeId = "ctlId", _children = {}}
            local services = servicesFor(plan, {ctl = Controller, hud = Hud, hud2 = Hud2}, {ctlId = ctlGo})

            -- awaitUiHost resolves both clones (they've landed by now).
            local hudClone  = {Name = "HUD",  _sceneRuntimeId = "hudId",  _children = {}}
            local hud2Clone = {Name = "HUD2", _sceneRuntimeId = "hud2Id", _children = {}}
            services.awaitUiHost = function(id)
                if id == "hudId" then return hudClone end
                if id == "hud2Id" then return hud2Clone end
                return nil
            end

            local engine = SceneRuntime.new(services, plan)
            engine:start(nil)
            runDeferred()  -- flush all Starts (sync batch + late UI batch)

            for _, e in ipairs(events) do print(e) end
            print("DONE")
        """)
        rc, out, err = _run_scenario(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        # BLOCKING #2: the inbound ref was back-patched -- Controller.Start
        # sees the built Hud (pre-fix: nil).
        assert "Controller.Start hud=HUD" in lines, out
        # BLOCKING #1: the deferred set ran as ONE batch -- both Awakes
        # precede both Starts.
        i_h_awake = lines.index("Hud.Awake")
        i_h2_awake = lines.index("Hud2.Awake")
        i_h_start = lines.index("Hud.Start")
        i_h2_start = lines.index("Hud2.Start")
        assert max(i_h_awake, i_h2_awake) < min(i_h_start, i_h2_start), out
        # BLOCKING #1: intra-batch lifecycle_order honored -- Hud2 (earlier in
        # lifecycle_order) Awakes before Hud.
        assert i_h2_awake < i_h_awake, out


class TestServerNoResolverOneShot:
    """Fix-round-1 MAJOR #3. When ``awaitUiHost`` is absent (server domain /
    any partition without the client host-surface helper), a UI-owned miss
    must NOT defer-then-never-build; it falls back to the pre-slice
    synchronous one-shot build (even with a nil gameObject)."""

    def test_no_resolver_builds_one_shot(self):
        scenario = textwrap.dedent("""\
            local events = {}

            local Hud = {} ; Hud.__index = Hud
            function Hud.new(_) return setmetatable({}, Hud) end
            function Hud:Awake()
                table.insert(events, "Hud.Awake go=" .. tostring(self.gameObject))
            end
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

            -- workspaceFind misses; NO awaitUiHost on services (server path).
            local services = servicesFor(plan, {hud = Hud}, {})
            -- Ensure no resolver is present.
            services.awaitUiHost = nil

            local engine = SceneRuntime.new(services, plan)
            engine:start(nil)
            runDeferred()

            for _, e in ipairs(events) do print(e) end
            print("DONE")
        """)
        rc, out, err = _run_scenario(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        # MAJOR #3: built one-shot with a nil gameObject (pre-slice
        # behaviour), NOT silently never-built.
        assert "Hud.Awake go=nil" in lines, out
        assert "Hud.Start" in lines, out


class TestAwaitUiHostResolverDirect:
    """Fix-round-1 MAJOR #5 + test-coverage #6. Drive the REAL emitted
    ``awaitUiHost`` body inside a true coroutine harness, exercising the
    connect-first scan, the DescendantAdded resume, and the timeout path."""

    def _run_await(self, body: str):
        await_src = _await_ui_host_source()
        script = textwrap.dedent("""\
            -- Minimal mock Roblox surface for awaitUiHost.
            local _delays = {}
            local _clock = 0
            local task = {}
            function task.spawn(fn, ...)
                if type(fn) == "thread" then
                    coroutine.resume(fn, ...)
                else
                    coroutine.resume(coroutine.create(fn), ...)
                end
            end
            function task.delay(secs, fn, ...)
                table.insert(_delays, {fireAt = _clock + secs, fn = fn, args = {...}})
            end
            local function advanceTime(dt)
                _clock = _clock + dt
                local fired = {}
                for i = #_delays, 1, -1 do
                    if _delays[i].fireAt <= _clock then
                        table.insert(fired, _delays[i]); table.remove(_delays, i)
                    end
                end
                for _, e in ipairs(fired) do e.fn(table.unpack(e.args)) end
            end

            -- Mock PlayerGui: a descendant list + a DescendantAdded signal.
            local function mkSignal()
                local s = {_c = {}}
                function s:Connect(fn)
                    local id = tostring(fn); s._c[id] = fn
                    return {Disconnect = function() s._c[id] = nil end}
                end
                function s:fire(x) for _, fn in pairs(s._c) do fn(x) end end
                return s
            end
            local function mkGui(id)
                return {GetAttribute = function(self, n)
                    if n == "_SceneRuntimeId" then return id end
                end}
            end
            local PlayerGui = {_descs = {}, DescendantAdded = mkSignal()}
            function PlayerGui:GetDescendants() return self._descs end
            local function workspaceFind(id) return nil end

        """) + await_src + "\n" + body
        with tempfile.NamedTemporaryFile(
            suffix=".luau", mode="w", delete=False,
        ) as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run(
                ["luau", path], capture_output=True, text=True, timeout=15,
            )
            return r.returncode, r.stdout, r.stderr
        finally:
            Path(path).unlink(missing_ok=True)

    def test_initial_scan_hit(self):
        # Clone already present -> resolves on the initial scan, no timeout.
        body = textwrap.dedent("""\
            PlayerGui._descs = {mkGui("other"), mkGui("hudId")}
            local result
            local co = coroutine.create(function()
                result = awaitUiHost("hudId")
            end)
            coroutine.resume(co)
            print("RESULT=" .. tostring(result and result:GetAttribute("_SceneRuntimeId")))
            print("DONE")
        """)
        rc, out, err = self._run_await(body)
        assert rc == 0, f"{err}\n{out}"
        assert "RESULT=hudId" in out, out

    def test_resolves_via_descendant_added_after_miss(self):
        # Initial scan misses; the clone arrives via DescendantAdded -> the
        # connect-first wiring catches it and resumes the waiter.
        body = textwrap.dedent("""\
            PlayerGui._descs = {mkGui("other")}
            local result
            local co = coroutine.create(function()
                result = awaitUiHost("hudId")
            end)
            coroutine.resume(co)  -- yields, waiting
            -- Now the clone lands.
            PlayerGui.DescendantAdded:fire(mkGui("hudId"))
            print("RESULT=" .. tostring(result and result:GetAttribute("_SceneRuntimeId")))
            print("DONE")
        """)
        rc, out, err = self._run_await(body)
        assert rc == 0, f"{err}\n{out}"
        assert "RESULT=hudId" in out, out

    def test_timeout_returns_nil(self):
        # Clone never lands -> the 10s timeout wakes the waiter with nil.
        body = textwrap.dedent("""\
            PlayerGui._descs = {mkGui("other")}
            local result = "UNSET"
            local co = coroutine.create(function()
                result = awaitUiHost("hudId")
            end)
            coroutine.resume(co)  -- yields, waiting
            advanceTime(11)       -- fire the timeout
            print("RESULT=" .. tostring(result))
            print("DONE")
        """)
        rc, out, err = self._run_await(body)
        assert rc == 0, f"{err}\n{out}"
        assert "RESULT=nil" in out, out
