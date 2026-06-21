"""Runtime Button ``onClick`` -> target component-instance method binding.

Drives the production ``scene_runtime.luau`` through the shared standalone-luau
harness (``_run_scenario`` / ``servicesFor`` from
``test_scene_runtime_host_behavior``) and asserts the generic ClickBinding
round-trip consumed by ``_installClickWatch`` / ``_dispatchClick``:

  * a simulated ``Activated`` on a resolved button invokes the EXACT target
    component instance method with ``self`` (component-precise);
  * late TARGET resolution (dispatch at CLICK time, target registered first);
  * GO-level fallback ONLY when exactly one component on the GO defines the
    method; an ambiguous / unresolved precise miss dispatches NOTHING (loud);
  * client-only (server table -> no watch -> nothing binds);
  * instance-keyed idempotency (a respawn re-clone re-binds; a redundant fire
    does not double-connect).

The binding is client-only: it installs only when ``installUiDescendantWatch``
is present (the harness injects it exactly as the toggle test does).
"""

from __future__ import annotations

import textwrap

from tests.test_scene_runtime_host_behavior import (  # noqa: F401
    _luau_available,
    _run_scenario,
    pytestmark,
)


# A reusable Lua preamble: a Button instance factory (settable Activated mock
# signal + _SceneRuntimeId), and the client-only watch surface (mirrors the
# toggle harness). Appended into each scenario before the plan is built.
_CLICK_HARNESS = textwrap.dedent("""\
    -- A button-ish instance: an Activated signal + a stamped _SceneRuntimeId.
    local function mkButton(sri)
        local inst = {_sceneRuntimeId = sri, _children = {}}
        function inst:GetAttribute(name)
            if name == "_SceneRuntimeId" then return self._sceneRuntimeId end
            return self["_attr_" .. tostring(name)]
        end
        local sig = {_conns = {}, _n = 0}
        function sig:Connect(fn)
            sig._n = sig._n + 1
            local id = sig._n
            sig._conns[id] = fn
            return {Disconnect = function() sig._conns[id] = nil end}
        end
        function sig:fire(...)
            for _, fn in pairs(sig._conns) do fn(...) end
        end
        function sig:count()
            local n = 0
            for _ in pairs(sig._conns) do n = n + 1 end
            return n
        end
        inst.Activated = sig
        return inst
    end

    -- A standing DescendantAdded signal + the client-only watch service.
    local function mkWatchSurface()
        local sig = {_conns = {}, _n = 0, fireCount = 0, connectCount = 0}
        function sig:Connect(fn)
            sig._n = sig._n + 1
            sig.connectCount = sig.connectCount + 1
            local id = sig._n
            sig._conns[id] = fn
            return {Disconnect = function() sig._conns[id] = nil end}
        end
        function sig:fire(x)
            sig.fireCount = sig.fireCount + 1
            for _, fn in pairs(sig._conns) do fn(x) end
        end
        function sig:isConnected() return next(sig._conns) ~= nil end
        return sig
    end
""")


def _build(scenario_body: str):
    rc, out, err = _run_scenario(_CLICK_HARNESS + "\n" + scenario_body)
    return rc, out, err


# A scene plan with ONE target MonoBehaviour (``Loadout``) defining the onClick
# method, registered under the scene instance_id == the ClickBinding's
# ``target_component_id``. ``%s`` is filled with the extra scenario tail.
def _scene_with_target(
    *, method: str, target_id: str = "scene:tc",
    target_go: str = "scene:tgo", button_sri: str = "btn",
    extra_calls_lua: str = "",
):
    return textwrap.dedent("""\
        local invoked = {}
        local Loadout = {} ; Loadout.__index = Loadout
        function Loadout.new(_) return setmetatable({}, Loadout) end
        function Loadout:%(method)s()
            -- Record that the method ran with the right ``self``.
            invoked.self = self
            invoked.count = (invoked.count or 0) + 1
        end

        local button = mkButton("%(button_sri)s")
        local watch = mkWatchSurface()

        local plan = {
            modules = {
                ld = {stem = "Loadout", runtime_bearing = true,
                      module_path = "x", domain = "client"},
            },
            scenes = {
                A = {
                    instances = {
                        {instance_id = "%(target_id)s", script_id = "ld",
                         game_object_id = "%(target_go)s", active = true,
                         enabled = true, config = {}},
                    },
                    references = {},
                    lifecycle_order = {"%(target_id)s"},
                },
            },
            prefabs = {}, domain_overrides = {},
            ui_click_bindings = {
                {button_sri = "%(button_sri)s", target_sri = "%(target_go)s",
                 target_component_id = "%(target_id)s", method = "%(method)s",
                 call_index = 0},%(extra)s
            },
        }
        -- The target GO + button both resolvable via workspaceFind.
        local instances = {["%(target_go)s"] = {Name = "Loadout",
            _sceneRuntimeId = "%(target_go)s", _children = {}},
            ["%(button_sri)s"] = button}
        local services = servicesFor(plan, {ld = Loadout}, instances)
        services.installUiDescendantWatch = function(h) return watch:Connect(h) end
    """) % {
        "method": method, "target_id": target_id, "target_go": target_go,
        "button_sri": button_sri, "extra": extra_calls_lua,
    }


class TestClickDispatch:

    def test_activated_invokes_precise_target_with_self(self):
        """A simulated Activated dispatches to the precise target component
        instance method with the component as ``self``."""
        scenario = _scene_with_target(method="StartGame") + textwrap.dedent("""\
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            -- Before click: not invoked.
            print("BEFORE=" .. tostring(invoked.count))
            button.Activated:fire()
            print("AFTER=" .. tostring(invoked.count))
            -- ``self`` is the registered component (has the method).
            print("SELF_OK=" .. tostring(invoked.self ~= nil
                and type(invoked.self.StartGame) == "function"))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        assert "BEFORE=nil" in lines, out
        assert "AFTER=1" in lines, out
        assert "SELF_OK=true" in lines, out

    def test_late_target_resolution_at_click_time(self):
        """The target component need not exist at BIND time. The button binds
        first; the click still dispatches because resolution is at click time.
        (Here the target registers at start() and the click fires after, which
        proves the dispatch reads the registry live, not a bind-time capture.)"""
        scenario = _scene_with_target(method="Go") + textwrap.dedent("""\
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            button.Activated:fire()
            print("COUNT=" .. tostring(invoked.count))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        assert "COUNT=1" in out, out

    def test_server_table_binds_nothing(self):
        """Client-only: no installUiDescendantWatch (server table) -> the
        button's Activated is never connected, so a fire dispatches nothing."""
        scenario = _scene_with_target(method="StartGame") + textwrap.dedent("""\
            -- Server: drop the watch service.
            services.installUiDescendantWatch = nil
            local engine = SceneRuntime.new(services, plan)
            engine:start("server")
            runDeferred()
            print("ACTIVATED_CONNS=" .. tostring(button.Activated:count()))
            button.Activated:fire()
            print("COUNT=" .. tostring(invoked.count))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        assert "ACTIVATED_CONNS=0" in lines, out
        assert "COUNT=nil" in lines, out

    def test_late_button_clone_binds_via_watch(self):
        """A button absent at install scan binds when it lands via
        DescendantAdded, then dispatches on click."""
        scenario = _scene_with_target(method="StartGame") + textwrap.dedent("""\
            -- workspaceFind MISSES the button at install (remove from map).
            local realButton = button
            local landed = {}
            services.workspaceFind = function(id)
                if id == "btn" then return landed["btn"] end
                return instances[id]
            end
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            print("CONNS_BEFORE=" .. tostring(realButton.Activated:count()))
            -- Button lands -> DescendantAdded binds it.
            landed["btn"] = realButton
            watch:fire(realButton)
            print("CONNS_AFTER=" .. tostring(realButton.Activated:count()))
            realButton.Activated:fire()
            print("COUNT=" .. tostring(invoked.count))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        assert "CONNS_BEFORE=0" in lines, out
        assert "CONNS_AFTER=1" in lines, out
        assert "COUNT=1" in lines, out

    def test_redundant_fire_does_not_double_connect(self):
        """The install scan + a redundant DescendantAdded for an already-bound
        button connect Activated exactly once (instance-keyed idempotency)."""
        scenario = _scene_with_target(method="StartGame") + textwrap.dedent("""\
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            -- Re-fire for the same (already-bound) button: must NOT re-connect.
            watch:fire(button)
            watch:fire(button)
            print("CONNS=" .. tostring(button.Activated:count()))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        assert "CONNS=1" in lines, out

    def test_resetonspawn_reclone_rebinds(self):
        """A respawn re-clone (NEW button instance, SAME sri) re-binds via the
        per-row instance marker, and the NEW button dispatches."""
        scenario = _scene_with_target(method="StartGame") + textwrap.dedent("""\
            local landed = {btn = button}
            for k, v in pairs(instances) do
                if k ~= "btn" then landed[k] = v end
            end
            services.workspaceFind = function(id) return landed[id] end
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            button.Activated:fire()
            print("FIRST=" .. tostring(invoked.count))
            -- Respawn: a NEW button with the SAME sri replaces the old one.
            local button2 = mkButton("btn")
            landed["btn"] = button2
            watch:fire(button2)
            button2.Activated:fire()
            print("RECLONE=" .. tostring(invoked.count))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        assert "FIRST=1" in lines, out
        # The re-clone re-bound and dispatched again -> count incremented.
        assert "RECLONE=2" in lines, out

    def test_precise_resolved_missing_method_dispatches_nothing(self):
        """Component-precise contract (Codex r4): when the EXACT named target
        component RESOLVES but does NOT define the method, dispatch NOTHING --
        never a sibling component on the same GO (which would be a wrong-
        component guess masking a broken/stale binding)."""
        scenario = textwrap.dedent("""\
            local invoked = {}
            -- The precise target (Foo) does NOT define Doit; a SIBLING (Bar)
            -- on the same GO does. A wrong fall-through would call Bar.
            local Foo = {} ; Foo.__index = Foo
            function Foo.new(_) return setmetatable({}, Foo) end
            function Foo:Other() end
            local Bar = {} ; Bar.__index = Bar
            function Bar.new(_) return setmetatable({}, Bar) end
            function Bar:Doit() invoked.bar = (invoked.bar or 0) + 1 end

            local button = mkButton("btn")
            local watch = mkWatchSurface()
            local plan = {
                modules = {
                    foo = {stem = "Foo", runtime_bearing = true, module_path = "x", domain = "client"},
                    bar = {stem = "Bar", runtime_bearing = true, module_path = "y", domain = "client"},
                },
                scenes = {
                    A = {
                        instances = {
                            {instance_id = "A:foo", script_id = "foo",
                             game_object_id = "go", active = true,
                             enabled = true, config = {}},
                            {instance_id = "A:bar", script_id = "bar",
                             game_object_id = "go", active = true,
                             enabled = true, config = {}},
                        },
                        references = {},
                        lifecycle_order = {"A:foo", "A:bar"},
                    },
                },
                prefabs = {}, domain_overrides = {},
                ui_click_bindings = {
                    -- The precise id RESOLVES to Foo, which lacks Doit.
                    {button_sri = "btn", target_sri = "go",
                     target_component_id = "A:foo", method = "Doit",
                     call_index = 0},
                },
            }
            local instances = {
                go = {Name = "GO", _sceneRuntimeId = "go", _children = {}},
                btn = button,
            }
            local services = servicesFor(plan, {foo = Foo, bar = Bar}, instances)
            services.installUiDescendantWatch = function(h) return watch:Connect(h) end
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            button.Activated:fire()
            print("BAR=" .. tostring(invoked.bar))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        # The sibling Bar.Doit must NOT have run (precise contract held).
        assert "BAR=nil" in lines, out

    def test_ambiguous_go_level_dispatches_nothing(self):
        """A precise registry MISS (broken target_component_id) where TWO
        components on the target GO define the method dispatches NOTHING (loud),
        never a wrong-component guess."""
        scenario = textwrap.dedent("""\
            local invoked = {}
            local Foo = {} ; Foo.__index = Foo
            function Foo.new(_) return setmetatable({}, Foo) end
            function Foo:Doit() invoked.foo = (invoked.foo or 0) + 1 end
            local Bar = {} ; Bar.__index = Bar
            function Bar.new(_) return setmetatable({}, Bar) end
            function Bar:Doit() invoked.bar = (invoked.bar or 0) + 1 end

            local button = mkButton("btn")
            local watch = mkWatchSurface()
            local plan = {
                modules = {
                    foo = {stem = "Foo", runtime_bearing = true, module_path = "x", domain = "client"},
                    bar = {stem = "Bar", runtime_bearing = true, module_path = "y", domain = "client"},
                },
                scenes = {
                    A = {
                        instances = {
                            {instance_id = "A:foo", script_id = "foo",
                             game_object_id = "go", active = true,
                             enabled = true, config = {}},
                            {instance_id = "A:bar", script_id = "bar",
                             game_object_id = "go", active = true,
                             enabled = true, config = {}},
                        },
                        references = {},
                        lifecycle_order = {"A:foo", "A:bar"},
                    },
                },
                prefabs = {}, domain_overrides = {},
                ui_click_bindings = {
                    -- target_component_id points at NOTHING in the registry
                    -- (broken precise id) -> falls to GO-level on "go", where
                    -- BOTH Foo and Bar define Doit -> ambiguous -> nothing.
                    {button_sri = "btn", target_sri = "go",
                     target_component_id = "A:does-not-exist", method = "Doit",
                     call_index = 0},
                },
            }
            local instances = {
                go = {Name = "GO", _sceneRuntimeId = "go", _children = {}},
                btn = button,
            }
            local services = servicesFor(plan, {foo = Foo, bar = Bar}, instances)
            services.installUiDescendantWatch = function(h) return watch:Connect(h) end
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            button.Activated:fire()
            print("FOO=" .. tostring(invoked.foo))
            print("BAR=" .. tostring(invoked.bar))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        # Ambiguous -> dispatched nothing (never a wrong-component guess).
        assert "FOO=nil" in lines, out
        assert "BAR=nil" in lines, out

    def test_unambiguous_go_level_fallback_dispatches(self):
        """A precise registry MISS where exactly ONE component on the target GO
        defines the method DOES dispatch (GO-level unambiguous fallback)."""
        scenario = textwrap.dedent("""\
            local invoked = {}
            local Foo = {} ; Foo.__index = Foo
            function Foo.new(_) return setmetatable({}, Foo) end
            function Foo:Doit() invoked.foo = (invoked.foo or 0) + 1 end
            local button = mkButton("btn")
            local watch = mkWatchSurface()
            local plan = {
                modules = {
                    foo = {stem = "Foo", runtime_bearing = true, module_path = "x", domain = "client"},
                },
                scenes = {
                    A = {
                        instances = {
                            {instance_id = "A:foo", script_id = "foo",
                             game_object_id = "go", active = true,
                             enabled = true, config = {}},
                        },
                        references = {},
                        lifecycle_order = {"A:foo"},
                    },
                },
                prefabs = {}, domain_overrides = {},
                ui_click_bindings = {
                    {button_sri = "btn", target_sri = "go",
                     target_component_id = "A:nope", method = "Doit",
                     call_index = 0},
                },
            }
            local instances = {
                go = {Name = "GO", _sceneRuntimeId = "go", _children = {}},
                btn = button,
            }
            local services = servicesFor(plan, {foo = Foo}, instances)
            services.installUiDescendantWatch = function(h) return watch:Connect(h) end
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            button.Activated:fire()
            print("FOO=" .. tostring(invoked.foo))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        assert "FOO=1" in lines, out

    def test_absent_plan_key_no_crash(self):
        """The ``ui_click_bindings`` plan key may be ABSENT or EMPTY. On the
        client the install must no-op cleanly (no crash, watch never connected)."""
        for key_decl in ("", "ui_click_bindings = {},"):
            scenario = textwrap.dedent("""\
                local watch = mkWatchSurface()
                local plan = {
                    modules = {}, scenes = {}, prefabs = {}, domain_overrides = {},
                    %s
                }
                local services = servicesFor(plan, {}, {})
                services.installUiDescendantWatch = function(h) return watch:Connect(h) end
                local engine = SceneRuntime.new(services, plan)
                engine:start("client")
                runDeferred()
                watch:fire(mkButton("whatever"))
                print("CONNECTS=" .. tostring(watch.connectCount))
                print("DONE")
            """) % key_decl
            rc, out, err = _build(scenario)
            assert rc == 0, f"luau failed (key_decl={key_decl!r}): {err}\n{out}"
            lines = out.strip().splitlines()
            assert "DONE" in lines, out
            # No rows -> the watch is never connected (install bails first).
            assert "CONNECTS=0" in lines, out
