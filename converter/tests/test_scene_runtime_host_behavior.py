"""PR4: behavioral tests for converter/runtime/scene_runtime.luau.

These tests drive the host runtime through the standalone ``luau``
interpreter with a mock service surface. Each test embeds a small Lua
harness, points it at the production ``scene_runtime.luau``, and
asserts on stdout markers. Skips cleanly when ``luau`` is absent so
CI environments without it don't fail.

Covered (per the design doc PR4 test matrix):
  * 2-MonoBehaviour synthetic scene wired end-to-end through the host.
  * Reference-cycle fixture (mutual peer refs do not loop forever).
  * Lifecycle order: ``new`` -> inject -> ``Awake`` -> ``OnEnable`` ->
    ``Start`` (next tick) -> ``Update``.
  * ``FixedUpdate`` fires on a fixed-step accumulator, not per-tick.
  * ``addComponent`` registers + runs the lifecycle.
  * ``findObjectOfType`` returns inactive objects.
  * ``host.invoke`` cancels on owning component's ``OnDestroy``.
  * ``host.destroy(parent)`` walks DFS deepest-first; idempotent.
  * ``GetComponent`` fallback: peer module hit + Roblox built-in hit.
  * ``host.connect`` lifecycle scoping: dispatch only while
    ``active && enabled``; flipping ``enabled`` re-arms; ``OnDestroy``
    disconnects.
  * Cross-domain refs inject ``nil`` + log + the edge is countable.
  * ``instantiatePrefab`` lifecycle.
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


def _luau_available() -> bool:
    return shutil.which("luau") is not None


pytestmark = pytest.mark.skipif(
    not _luau_available() or not HOST_RUNTIME_PATH.exists(),
    reason="needs standalone luau interpreter + host runtime file",
)


# ---------------------------------------------------------------------------
# Shared harness preamble: loads the host runtime and exposes the mock
# Roblox surface. Each test appends scenario code.
# ---------------------------------------------------------------------------

def _harness_preamble() -> str:
    # luau standalone has no loadfile -- read the host runtime
    # source in Python and embed it into the harness as a long string.
    host_source = HOST_RUNTIME_PATH.read_text(encoding="utf-8")
    delim = "==="
    while f"]{delim}]" in host_source or f"[{delim}[" in host_source:
        delim += "="
    embedded = f"[{delim}[\n{host_source}\n]{delim}]"
    return textwrap.dedent(f"""\
        -- Harness preamble: mocks the slice of the Roblox API the host
        -- runtime touches so tests can run under standalone luau.
        -- Subsequent scenario code sees SceneRuntime, servicesFor,
        -- advanceTime, runDeferred, mockSignal, logs as
        -- top-level locals in the same chunk.

        local HOST_RUNTIME_SOURCE = {embedded}
        local SceneRuntime
        do
            local chunk, err = loadstring(HOST_RUNTIME_SOURCE, "scene_runtime")
            assert(chunk, "load host runtime failed: " .. tostring(err))
            SceneRuntime = chunk()
        end
""") + _HARNESS_BODY


_HARNESS_BODY = """local _deferred = {}
local _delays = {}
local _cancelled = {}
local _nextHandle = 0
local function newHandle()
    _nextHandle = _nextHandle + 1
    return {handle = _nextHandle}
end
local task = {}
function task.spawn(fn, ...)
    local h = newHandle()
    local ok, err = pcall(fn, ...)
    if not ok then warn("[mocktask spawn] " .. tostring(err)) end
    return h
end
function task.defer(fn, ...)
    local h = newHandle()
    table.insert(_deferred, {handle = h, fn = fn, args = {...}})
    return h
end
local _clock = 0
function task.delay(secs, fn, ...)
    local h = newHandle()
    table.insert(_delays, {
        handle = h, fn = fn, args = {...},
        fireAt = _clock + secs,
    })
    return h
end
function task.wait(secs) return secs or 0 end
function task.cancel(h)
    if type(h) ~= "table" or h.handle == nil then return end
    _cancelled[h.handle] = true
    for i = #_delays, 1, -1 do
        if _delays[i].handle == h.handle then table.remove(_delays, i) end
    end
    for i = #_deferred, 1, -1 do
        if _deferred[i].handle == h.handle then table.remove(_deferred, i) end
    end
end

local function advanceTime(dt)
    _clock = _clock + dt
    local fired = {}
    for i = #_delays, 1, -1 do
        if _delays[i].fireAt <= _clock then
            table.insert(fired, _delays[i])
            table.remove(_delays, i)
        end
    end
    for _, entry in ipairs(fired) do
        if not _cancelled[entry.handle] then
            pcall(entry.fn, table.unpack(entry.args))
        end
    end
end
local function runDeferred()
    local snap = _deferred
    _deferred = {}
    for _, entry in ipairs(snap) do
        if not _cancelled[entry.handle] then
            pcall(entry.fn, table.unpack(entry.args))
        end
    end
end

local function mockSignal()
    local sig = { _conns = {}, _connId = 0 }
    function sig:Connect(fn)
        sig._connId = sig._connId + 1
        local id = sig._connId
        sig._conns[id] = fn
        local conn = {}
        function conn:Disconnect()
            sig._conns[id] = nil
        end
        return conn
    end
    function sig:fire(...)
        for _, fn in pairs(sig._conns) do
            fn(...)
        end
    end
    return sig
end

local logs = {}
local function logWarn(...)
    local parts = {...}
    for i, p in ipairs(parts) do parts[i] = tostring(p) end
    table.insert(logs, table.concat(parts, " "))
end

local function servicesFor(plan, modules, instances)
    return {
        task = task,
        warn = logWarn,
        resolveModule = function(scriptId, modulePath)
            return modules[scriptId]
        end,
        workspaceFind = function(sceneRuntimeId)
            return instances[sceneRuntimeId]
        end,
        findFirstChildWhichIsA = function(inst, class)
            if not inst or not inst._builtins then return nil end
            return inst._builtins[class]
        end,
        heartbeat = mockSignal(),
        fixedStep = 0.02,
        now = function() return _clock end,
        getInstanceId = function(inst)
            return inst and inst._sceneRuntimeId
        end,
        clonePrefabTemplate = function(prefabId, parent, cframe)
            return nil
        end,
        resolveCloneChild = function(clone, gameObjectId)
            return (clone and clone._children
                    and clone._children[gameObjectId]) or clone
        end,
        collectDescendantIds = function(inst)
            local out = {}
            local function walk(node)
                if node._children then
                    for _, child in pairs(node._children) do
                        walk(child)
                    end
                end
                table.insert(out, node._sceneRuntimeId)
            end
            walk(inst)
            return out
        end,
        destroyInstance = function(inst) end,
    }
end

"""

def _run_scenario(scenario_body: str) -> tuple[int, str, str]:
    """Stitch the preamble + scenario, execute, return (rc, stdout, stderr)."""
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


# ---------------------------------------------------------------------------
# 2-MonoBehaviour synthetic scene wired end-to-end
# ---------------------------------------------------------------------------

class TestTwoMonoBehaviourScene:

    def test_lifecycle_order_new_inject_awake_enable_start_update(self):
        scenario = textwrap.dedent("""\
            local order = {}
            local Foo = {}
            Foo.__index = Foo
            function Foo.new(config)
                table.insert(order, "Foo.new")
                local self = setmetatable({}, Foo)
                self._config = config
                return self
            end
            function Foo:Awake()
                table.insert(order, "Foo.Awake")
                assert(self.host ~= nil, "host must be bound before Awake")
                assert(self.gameObject ~= nil, "go must be bound")
            end
            function Foo:OnEnable() table.insert(order, "Foo.OnEnable") end
            function Foo:Start() table.insert(order, "Foo.Start") end
            function Foo:Update(dt) table.insert(order, "Foo.Update") end

            local Bar = {}
            Bar.__index = Bar
            function Bar.new(config)
                table.insert(order, "Bar.new")
                return setmetatable({}, Bar)
            end
            function Bar:Awake() table.insert(order, "Bar.Awake") end
            function Bar:OnEnable() table.insert(order, "Bar.OnEnable") end
            function Bar:Start() table.insert(order, "Bar.Start") end

            local plan = {
                modules = {
                    foo = {stem = "Foo", runtime_bearing = true, module_path = "x"},
                    bar = {stem = "Bar", runtime_bearing = true, module_path = "y"},
                },
                scenes = {
                    A = {
                        instances = {
                            {instance_id = "A:1", script_id = "foo",
                             game_object_id = "go1", active = true,
                             enabled = true, config = {}},
                            {instance_id = "A:2", script_id = "bar",
                             game_object_id = "go2", active = true,
                             enabled = true, config = {}},
                        },
                        references = {},
                        lifecycle_order = {"A:1", "A:2"},
                    },
                },
                prefabs = {},
                domain_overrides = {},
            }
            local modules = {foo = Foo, bar = Bar}
            local instances = {
                go1 = {Name = "Go1", _sceneRuntimeId = "go1", _children = {}},
                go2 = {Name = "Go2", _sceneRuntimeId = "go2", _children = {}},
            }
            local services = servicesFor(plan, modules, instances)
            local engine = SceneRuntime.new(services, plan)
            engine:start(nil)  -- run both domains
            runDeferred()  -- flush Start
            services.heartbeat:fire(0.016)  -- one heartbeat tick

            for _, x in ipairs(order) do print(x) end
            print("DONE")
        """)
        rc, out, err = _run_scenario(scenario)
        assert rc == 0, f"luau failed: {err}"
        # Expected order: every new() first, then per-instance Awake,
        # then OnEnable, then Start (after defer flush), then Update.
        lines = out.strip().splitlines()
        assert "DONE" in lines
        # ``new`` events come before any ``Awake``.
        first_awake = next((i for i, l in enumerate(lines)
                            if l.endswith(".Awake")), -1)
        last_new = max((i for i, l in enumerate(lines)
                        if l.endswith(".new")), default=-1)
        assert last_new < first_awake, (
            f"all new() must precede any Awake; got {lines}"
        )
        # ``OnEnable`` after ``Awake``, ``Start`` after ``OnEnable``.
        assert lines.index("Foo.Awake") < lines.index("Foo.OnEnable")
        assert lines.index("Foo.OnEnable") < lines.index("Foo.Start")
        assert lines.index("Foo.Start") < lines.index("Foo.Update")


# ---------------------------------------------------------------------------
# Reference-cycle fixture
# ---------------------------------------------------------------------------

class TestReferenceCycle:

    def test_mutual_peer_refs_resolve_without_looping(self):
        scenario = textwrap.dedent("""\
            local Foo = {} ; Foo.__index = Foo
            function Foo.new(_) return setmetatable({}, Foo) end
            function Foo:Awake()
                assert(self.peer ~= nil, "peer ref must be wired before Awake")
                -- Cycle: peer's peer is self.
                assert(self.peer.peer == self, "cycle must close")
            end
            local plan = {
                modules = {
                    foo = {stem = "Foo", runtime_bearing = true, module_path = "x"},
                },
                scenes = {
                    A = {
                        instances = {
                            {instance_id = "A:1", script_id = "foo",
                             game_object_id = "g1", active = true,
                             enabled = true, config = {}},
                            {instance_id = "A:2", script_id = "foo",
                             game_object_id = "g2", active = true,
                             enabled = true, config = {}},
                        },
                        references = {
                            {["from"] = "A:1", field = "peer", index = nil,
                             target_kind = "component", target_ref = "A:2",
                             target_is_ui = false},
                            {["from"] = "A:2", field = "peer", index = nil,
                             target_kind = "component", target_ref = "A:1",
                             target_is_ui = false},
                        },
                        lifecycle_order = {"A:1", "A:2"},
                    },
                },
                prefabs = {},
                domain_overrides = {},
            }
            local services = servicesFor(plan, {foo = Foo}, {
                g1 = {Name = "G1", _sceneRuntimeId = "g1", _children = {}},
                g2 = {Name = "G2", _sceneRuntimeId = "g2", _children = {}},
            })
            local engine = SceneRuntime.new(services, plan)
            engine:start(nil)
            print("OK")
        """)
        rc, out, err = _run_scenario(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        assert "OK" in out


# ---------------------------------------------------------------------------
# FixedUpdate fixed-step
# ---------------------------------------------------------------------------

class TestFixedUpdate:

    def test_fixed_update_fires_on_step_not_per_tick(self):
        scenario = textwrap.dedent("""\
            local fixedCount = 0
            local updateCount = 0
            local Foo = {} ; Foo.__index = Foo
            function Foo.new(_) return setmetatable({}, Foo) end
            function Foo:Update(dt) updateCount = updateCount + 1 end
            function Foo:FixedUpdate(dt) fixedCount = fixedCount + 1 end
            local plan = {
                modules = {foo = {stem = "Foo", runtime_bearing = true,
                                  module_path = "x"}},
                scenes = {
                    A = {
                        instances = {{instance_id = "A:1", script_id = "foo",
                                      game_object_id = "g", active = true,
                                      enabled = true, config = {}}},
                        references = {},
                        lifecycle_order = {"A:1"},
                    },
                },
                prefabs = {}, domain_overrides = {},
            }
            local services = servicesFor(plan, {foo = Foo}, {
                g = {Name = "G", _sceneRuntimeId = "g", _children = {}},
            })
            -- fixedStep is 0.02 (default in services).
            local engine = SceneRuntime.new(services, plan)
            engine:start(nil)
            runDeferred()
            -- Fire 5 heartbeats of 0.016s each = 0.08s elapsed.
            for i = 1, 5 do services.heartbeat:fire(0.016) end
            -- 0.08 / 0.02 = 4 fixed steps; Update fires every tick = 5.
            print("U=" .. updateCount, "F=" .. fixedCount)
        """)
        rc, out, err = _run_scenario(scenario)
        assert rc == 0, err
        assert "U=5" in out
        assert "F=4" in out


# ---------------------------------------------------------------------------
# addComponent
# ---------------------------------------------------------------------------

class TestAddComponent:

    def test_add_component_registers_and_runs_lifecycle(self):
        scenario = textwrap.dedent("""\
            local awakeCount = 0
            local startCount = 0
            local Foo = {} ; Foo.__index = Foo
            function Foo.new(c) return setmetatable({_c = c}, Foo) end
            function Foo:Awake() awakeCount = awakeCount + 1 end
            function Foo:Start() startCount = startCount + 1 end
            local plan = {
                modules = {foo = {stem = "Foo", runtime_bearing = true,
                                  module_path = "x"}},
                scenes = {}, prefabs = {}, domain_overrides = {},
            }
            local services = servicesFor(plan, {foo = Foo}, {})
            local engine = SceneRuntime.new(services, plan)
            local go = {Name = "G", _sceneRuntimeId = "g", _children = {}}
            local comp = engine:addComponent(go, "foo", {speed = 5})
            assert(comp ~= nil, "addComponent must return the instance")
            assert(comp._c.speed == 5, "config must reach new()")
            runDeferred()  -- flush Start
            -- findObjectOfType should now see the new component.
            assert(engine:findObjectOfType("Foo") == comp,
                "addComponent must register in global lookup")
            print("A=" .. awakeCount, "S=" .. startCount)
        """)
        rc, out, err = _run_scenario(scenario)
        assert rc == 0, err
        assert "A=1" in out
        assert "S=1" in out


# ---------------------------------------------------------------------------
# findObjectOfType: sees inactive
# ---------------------------------------------------------------------------

class TestFindObjectOfType:

    def test_finds_inactive_objects(self):
        scenario = textwrap.dedent("""\
            local Foo = {} ; Foo.__index = Foo
            function Foo.new(_) return setmetatable({}, Foo) end
            local plan = {
                modules = {foo = {stem = "Foo", runtime_bearing = true,
                                  module_path = "x"}},
                scenes = {
                    A = {
                        instances = {{instance_id = "A:1", script_id = "foo",
                                      game_object_id = "g", active = false,
                                      enabled = true, config = {}}},
                        references = {},
                        lifecycle_order = {"A:1"},
                    },
                },
                prefabs = {}, domain_overrides = {},
            }
            local services = servicesFor(plan, {foo = Foo}, {
                g = {Name = "G", _sceneRuntimeId = "g", _children = {}},
            })
            local engine = SceneRuntime.new(services, plan)
            engine:start(nil)
            local found = engine:findObjectOfType("Foo")
            assert(found ~= nil, "findObjectOfType must see inactive objects")
            print("OK")
        """)
        rc, out, err = _run_scenario(scenario)
        assert rc == 0, err
        assert "OK" in out


# ---------------------------------------------------------------------------
# host.invoke cancels on OnDestroy
# ---------------------------------------------------------------------------

class TestHostInvokeCancellation:

    def test_invoke_cancels_on_destroy(self):
        scenario = textwrap.dedent("""\
            local fired = 0
            local Foo = {} ; Foo.__index = Foo
            function Foo.new(_) return setmetatable({}, Foo) end
            function Foo:DoLater() fired = fired + 1 end
            local plan = {
                modules = {foo = {stem = "Foo", runtime_bearing = true,
                                  module_path = "x"}},
                scenes = {
                    A = {
                        instances = {{instance_id = "A:1", script_id = "foo",
                                      game_object_id = "g", active = true,
                                      enabled = true, config = {}}},
                        references = {},
                        lifecycle_order = {"A:1"},
                    },
                },
                prefabs = {}, domain_overrides = {},
            }
            local services = servicesFor(plan, {foo = Foo}, {
                g = {Name = "G", _sceneRuntimeId = "g", _children = {}},
            })
            local engine = SceneRuntime.new(services, plan)
            engine:start(nil)
            runDeferred()
            local comp = engine:findObjectOfType("Foo")
            engine:invoke(comp, "DoLater", 1.0)
            -- Destroy before the delay fires.
            engine:destroy(comp)
            advanceTime(2.0)
            print("fired=" .. fired)
        """)
        rc, out, err = _run_scenario(scenario)
        assert rc == 0, err
        assert "fired=0" in out, (
            "invoke must cancel on OnDestroy; got " + out
        )


# ---------------------------------------------------------------------------
# host.destroy DFS deepest-first; idempotent
# ---------------------------------------------------------------------------

class TestRecursiveDestroy:

    def test_destroy_runs_disable_then_destroy_deepest_first_and_is_idempotent(self):
        scenario = textwrap.dedent("""\
            local order = {}
            local Foo = {} ; Foo.__index = Foo
            function Foo.new(_) return setmetatable({}, Foo) end
            function Foo:OnEnable() end
            function Foo:OnDisable() table.insert(order, self._tag .. ":disable") end
            function Foo:OnDestroy() table.insert(order, self._tag .. ":destroy") end

            local plan = {
                modules = {foo = {stem = "Foo", runtime_bearing = true,
                                  module_path = "x"}},
                scenes = {
                    A = {
                        instances = {
                            {instance_id = "A:1", script_id = "foo",
                             game_object_id = "parent", active = true,
                             enabled = true, config = {}},
                            {instance_id = "A:2", script_id = "foo",
                             game_object_id = "child", active = true,
                             enabled = true, config = {}},
                        },
                        references = {},
                        lifecycle_order = {"A:1", "A:2"},
                    },
                },
                prefabs = {}, domain_overrides = {},
            }
            local childGo = {Name = "Child", _sceneRuntimeId = "child", _children = {}}
            local parentGo = {Name = "Parent", _sceneRuntimeId = "parent",
                              _children = {child = childGo}}
            local services = servicesFor(plan, {foo = Foo}, {
                parent = parentGo, child = childGo,
            })
            local engine = SceneRuntime.new(services, plan)
            engine:start(nil)
            -- Tag the components so OnDisable/OnDestroy print which one ran.
            for comp, m in pairs(engine._meta) do
                comp._tag = m.gameObjectName or m.gameObjectId
            end
            engine:destroy(parentGo)
            for _, x in ipairs(order) do print(x) end
            -- Second destroy: idempotent.
            local lenBefore = #order
            engine:destroy(parentGo)
            print("len_after=" .. #order)
            print("len_before=" .. lenBefore)
        """)
        rc, out, err = _run_scenario(scenario)
        assert rc == 0, err
        lines = out.strip().splitlines()
        # ``Child`` events come before ``Parent`` events (deepest-first).
        try:
            child_destroy = lines.index("Child:destroy")
            parent_destroy = lines.index("Parent:destroy")
            assert child_destroy < parent_destroy
            child_disable = lines.index("Child:disable")
            assert child_disable < child_destroy   # disable before destroy
        except ValueError as exc:
            pytest.fail(f"missing expected destroy event in {lines}: {exc}")
        # Idempotent: second destroy did not add more events.
        len_before = int([l for l in lines if l.startswith("len_before=")][0]
                         .split("=")[1])
        len_after = int([l for l in lines if l.startswith("len_after=")][0]
                        .split("=")[1])
        assert len_after == len_before


# ---------------------------------------------------------------------------
# GetComponent peer + Roblox fallback
# ---------------------------------------------------------------------------

class TestGetComponent:

    def test_peer_lookup_returns_module_instance(self):
        scenario = textwrap.dedent("""\
            local Foo = {} ; Foo.__index = Foo
            function Foo.new(_) return setmetatable({}, Foo) end
            local Bar = {} ; Bar.__index = Bar
            function Bar.new(_) return setmetatable({}, Bar) end
            function Bar:Awake()
                local peer = self:GetComponent("Foo")
                assert(peer ~= nil, "peer GetComponent must hit")
                self._peerTag = peer
            end
            local plan = {
                modules = {
                    foo = {stem = "Foo", runtime_bearing = true, module_path = "x"},
                    bar = {stem = "Bar", runtime_bearing = true, module_path = "y"},
                },
                scenes = {
                    A = {
                        instances = {
                            {instance_id = "A:1", script_id = "foo",
                             game_object_id = "g", active = true,
                             enabled = true, config = {}},
                            {instance_id = "A:2", script_id = "bar",
                             game_object_id = "g", active = true,
                             enabled = true, config = {}},
                        },
                        references = {},
                        lifecycle_order = {"A:1", "A:2"},
                    },
                },
                prefabs = {}, domain_overrides = {},
            }
            local services = servicesFor(plan, {foo = Foo, bar = Bar}, {
                g = {Name = "G", _sceneRuntimeId = "g", _children = {}},
            })
            local engine = SceneRuntime.new(services, plan)
            engine:start(nil)
            print("OK")
        """)
        rc, out, err = _run_scenario(scenario)
        assert rc == 0, err
        assert "OK" in out

    def test_builtin_fallback_for_rigidbody(self):
        scenario = textwrap.dedent("""\
            local mockRigidbody = {Name = "FakeRigidbody"}
            local Foo = {} ; Foo.__index = Foo
            function Foo.new(_) return setmetatable({}, Foo) end
            function Foo:Awake()
                local rb = self:GetComponent("Rigidbody")
                assert(rb == mockRigidbody,
                    "GetComponent fallback must hit findFirstChildWhichIsA")
            end
            local plan = {
                modules = {foo = {stem = "Foo", runtime_bearing = true,
                                  module_path = "x"}},
                scenes = {
                    A = {
                        instances = {{instance_id = "A:1", script_id = "foo",
                                      game_object_id = "g", active = true,
                                      enabled = true, config = {}}},
                        references = {},
                        lifecycle_order = {"A:1"},
                    },
                },
                prefabs = {}, domain_overrides = {},
            }
            local go = {
                Name = "G",
                _sceneRuntimeId = "g",
                _children = {},
                _builtins = {Rigidbody = mockRigidbody},
            }
            local services = servicesFor(plan, {foo = Foo}, {g = go})
            local engine = SceneRuntime.new(services, plan)
            engine:start(nil)
            print("OK")
        """)
        rc, out, err = _run_scenario(scenario)
        assert rc == 0, err
        assert "OK" in out


# ---------------------------------------------------------------------------
# host.connect lifecycle scoping
# ---------------------------------------------------------------------------

class TestHostConnect:

    def test_dispatch_gated_on_active_and_enabled(self):
        scenario = textwrap.dedent("""\
            local sig = mockSignal()
            local hits = 0
            local Foo = {} ; Foo.__index = Foo
            function Foo.new(_) return setmetatable({}, Foo) end
            function Foo:Awake()
                self.host.connect(self, sig, function() hits = hits + 1 end)
            end
            local plan = {
                modules = {foo = {stem = "Foo", runtime_bearing = true,
                                  module_path = "x"}},
                scenes = {
                    A = {
                        instances = {{instance_id = "A:1", script_id = "foo",
                                      game_object_id = "g", active = true,
                                      enabled = true, config = {}}},
                        references = {},
                        lifecycle_order = {"A:1"},
                    },
                },
                prefabs = {}, domain_overrides = {},
            }
            local go = {Name = "G", _sceneRuntimeId = "g", _children = {}}
            local services = servicesFor(plan, {foo = Foo}, {g = go})
            local engine = SceneRuntime.new(services, plan)
            engine:start(nil)
            runDeferred()
            local comp = engine:findObjectOfType("Foo")

            sig:fire()
            assert(hits == 1, "subscribed callback should fire when enabled")
            engine:setEnabled(comp, false)
            sig:fire()
            assert(hits == 1, "flipping enabled=false must suspend dispatch")
            engine:setEnabled(comp, true)
            sig:fire()
            assert(hits == 2, "re-enabling must reconnect dispatch")

            engine:setActive(go, false)
            sig:fire()
            assert(hits == 2, "setActive(false) suspends dispatch")
            engine:setActive(go, true)
            sig:fire()
            assert(hits == 3, "setActive(true) re-arms")

            -- OnDestroy disconnects all subs.
            engine:destroy(comp)
            sig:fire()
            assert(hits == 3, "OnDestroy must disconnect subs")
            print("OK")
        """)
        rc, out, err = _run_scenario(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        assert "OK" in out


# ---------------------------------------------------------------------------
# Cross-domain reference policy
# ---------------------------------------------------------------------------

class TestCrossDomainPolicy:

    def test_cross_domain_ref_injects_nil_and_logs(self):
        scenario = textwrap.dedent("""\
            local Foo = {} ; Foo.__index = Foo
            function Foo.new(_) return setmetatable({}, Foo) end
            function Foo:Awake()
                assert(self.peer == nil,
                    "cross-domain ref must inject nil; got " .. tostring(self.peer))
            end
            local Bar = {} ; Bar.__index = Bar
            function Bar.new(_) return setmetatable({}, Bar) end
            local plan = {
                modules = {
                    foo = {stem = "Foo", runtime_bearing = true,
                           module_path = "x", domain = "client"},
                    bar = {stem = "Bar", runtime_bearing = true,
                           module_path = "y", domain = "server"},
                },
                scenes = {
                    A = {
                        instances = {
                            {instance_id = "A:1", script_id = "foo",
                             game_object_id = "g1", active = true,
                             enabled = true, config = {}},
                            {instance_id = "A:2", script_id = "bar",
                             game_object_id = "g2", active = true,
                             enabled = true, config = {}},
                        },
                        references = {
                            {["from"] = "A:1", field = "peer", index = nil,
                             target_kind = "component", target_ref = "A:2",
                             target_is_ui = false},
                        },
                        lifecycle_order = {"A:1", "A:2"},
                    },
                },
                prefabs = {}, domain_overrides = {},
            }
            local services = servicesFor(plan, {foo = Foo, bar = Bar}, {
                g1 = {Name = "G1", _sceneRuntimeId = "g1", _children = {}},
                g2 = {Name = "G2", _sceneRuntimeId = "g2", _children = {}},
            })
            local engine = SceneRuntime.new(services, plan)
            local edges = engine:start(nil)
            assert(#edges == 1, "cross-domain edge must surface in start() return")
            assert(edges[1].from_script == "foo")
            assert(edges[1].to_script == "bar")
            local logged = false
            for _, line in ipairs(logs) do
                if string.find(line, "cross%-domain") then logged = true end
            end
            assert(logged, "cross-domain ref must log a structured warning")
            print("OK")
        """)
        rc, out, err = _run_scenario(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        assert "OK" in out

    def test_same_domain_ref_resolves_live_instance(self):
        scenario = textwrap.dedent("""\
            local Foo = {} ; Foo.__index = Foo
            function Foo.new(_) return setmetatable({_marker = true}, Foo) end
            function Foo:Awake()
                assert(self.peer ~= nil, "same-domain ref must resolve")
                assert(self.peer._marker, "ref must point at peer module instance")
            end
            local plan = {
                modules = {
                    foo = {stem = "Foo", runtime_bearing = true,
                           module_path = "x", domain = "client"},
                },
                scenes = {
                    A = {
                        instances = {
                            {instance_id = "A:1", script_id = "foo",
                             game_object_id = "g1", active = true,
                             enabled = true, config = {}},
                            {instance_id = "A:2", script_id = "foo",
                             game_object_id = "g2", active = true,
                             enabled = true, config = {}},
                        },
                        references = {
                            {["from"] = "A:1", field = "peer", index = nil,
                             target_kind = "component", target_ref = "A:2",
                             target_is_ui = false},
                        },
                        lifecycle_order = {"A:1", "A:2"},
                    },
                },
                prefabs = {}, domain_overrides = {},
            }
            local services = servicesFor(plan, {foo = Foo}, {
                g1 = {Name = "G1", _sceneRuntimeId = "g1", _children = {}},
                g2 = {Name = "G2", _sceneRuntimeId = "g2", _children = {}},
            })
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            print("OK")
        """)
        rc, out, err = _run_scenario(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        assert "OK" in out


# ---------------------------------------------------------------------------
# instantiatePrefab lifecycle
# ---------------------------------------------------------------------------

class TestInstantiatePrefab:

    def test_instantiate_prefab_runs_lifecycle(self):
        scenario = textwrap.dedent("""\
            local awakeCount = 0
            local Foo = {} ; Foo.__index = Foo
            function Foo.new(_) return setmetatable({}, Foo) end
            function Foo:Awake() awakeCount = awakeCount + 1 end
            local plan = {
                modules = {foo = {stem = "Foo", runtime_bearing = true,
                                  module_path = "x"}},
                scenes = {},
                prefabs = {
                    ["pfb1"] = {
                        name = "MyPrefab",
                        instances = {{instance_id = "pfb1:1", script_id = "foo",
                                      game_object_id = "pfb1:1", active = true,
                                      enabled = true, config = {}}},
                        references = {},
                        lifecycle_order = {"pfb1:1"},
                    },
                },
                domain_overrides = {},
            }
            local cloneInstance = {
                Name = "Clone", _sceneRuntimeId = "clone",
                _children = {["pfb1:1"] = {Name = "ClonedChild",
                              _sceneRuntimeId = "pfb1:1", _children = {}}},
            }
            local services = servicesFor(plan, {foo = Foo}, {})
            services.clonePrefabTemplate = function(prefabId, parent, cframe)
                return cloneInstance
            end
            local engine = SceneRuntime.new(services, plan)
            local clone = engine:instantiatePrefab("pfb1", nil, nil, nil)
            runDeferred()
            assert(clone == cloneInstance, "instantiatePrefab returns the clone")
            assert(awakeCount == 1, "prefab component Awake must fire once")
            print("OK")
        """)
        rc, out, err = _run_scenario(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        assert "OK" in out
