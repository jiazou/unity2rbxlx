"""Unit-3 theme registration — host-level behavioral tests for
``SceneRuntime.seedAddressableDatabases`` in runtime/scene_runtime.luau.

Drives the REAL host runtime under standalone ``luau`` with a focused
service surface (``resolveModule`` keyed by module path, ``warn`` captured).
Each test mirrors the REAL ThemeDatabase drain (``_pendingThemeData`` +
``Register`` + a once-only guarded ``LoadDatabase``) and the consumer lookup
path, so the seed is exercised end-to-end, not against a self-satisfying stub.

Covers:
  * AC-1  registry non-nil via the CONSUMER's lookup-key path
          (GetThemeData(PlayerData.themes[usedTheme+1])) — NOT the seed's key.
  * AC-3  loaded() == true after seed + LoadDatabase (seed never sets m_Loaded).
  * AC-4  exactly the owned SOs registered; a key-less SO is abstained.
  * AC-5  ordering: seed-then-Load populates; Load-then-seed locks empty.
  * the runtime drain-bind fallback (drain-field table when appender absent).
  * generic no-op when plan.addressable_db_seeds is absent/empty.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

HOST_RUNTIME_PATH = Path(__file__).parent.parent / "runtime" / "scene_runtime.luau"


def _luau_available() -> bool:
    return shutil.which("luau") is not None


pytestmark = pytest.mark.skipif(
    not _luau_available() or not HOST_RUNTIME_PATH.exists(),
    reason="needs standalone luau interpreter + host runtime file",
)


def _run(scenario: str) -> str:
    host_source = HOST_RUNTIME_PATH.read_text(encoding="utf-8")
    delim = "==="
    while f"]{delim}]" in host_source or f"[{delim}[" in host_source:
        delim += "="
    embedded = f"[{delim}[\n{host_source}\n]{delim}]"
    preamble = textwrap.dedent(f"""\
        local HOST_RUNTIME_SOURCE = {embedded}
        local SceneRuntime
        do
            local chunk, err = loadstring(HOST_RUNTIME_SOURCE, "scene_runtime")
            assert(chunk, "load host runtime failed: " .. tostring(err))
            SceneRuntime = chunk()
        end

        local logs = {{}}
        local function logWarn(...)
            local parts = {{...}}
            for i, p in ipairs(parts) do parts[i] = tostring(p) end
            table.insert(logs, table.concat(parts, " "))
        end

        -- A ThemeDatabase mirroring the REAL transpiled drain (once-only
        -- themeDataList==nil guard, drains _pendingThemeData by op.themeName,
        -- sets m_Loaded). Register appends to the same _pendingThemeData list.
        local function makeThemeDatabase()
            local db = {{}}
            local themeDataList = nil
            local m_Loaded = false
            db._pendingThemeData = {{}}
            function db.Register(td) table.insert(db._pendingThemeData, td) end
            function db.dictionnary() return themeDataList end
            function db.loaded() return m_Loaded end
            function db.GetThemeData(t)
                if themeDataList == nil then return nil end
                return themeDataList[t]
            end
            function db.LoadDatabase()
                if themeDataList == nil then
                    themeDataList = {{}}
                    for _, op in ipairs(db._pendingThemeData) do
                        if op ~= nil and themeDataList[op.themeName] == nil then
                            themeDataList[op.themeName] = op
                        end
                    end
                    m_Loaded = true
                end
            end
            return db
        end

        -- services.resolveModule keyed by module path (the shim calls
        -- resolveModule(nil, modulePath)).
        local function servicesFor(modules)
            return {{
                warn = logWarn,
                resolveModule = function(_id, path) return modules[path] end,
            }}
        end

        -- Emit every captured warn on its own stable-prefixed line so a test
        -- can assert the fail-loud WARNING actually fired (not just "no crash").
        local function dumpLogs()
            for _, msg in ipairs(logs) do
                print("WARN_LINE=" .. msg)
            end
            print("WARN_COUNT=" .. tostring(#logs))
        end
    """)
    src = preamble + "\n" + scenario
    with tempfile.NamedTemporaryFile("w", suffix=".luau", delete=False) as fh:
        fh.write(src)
        path = fh.name
    try:
        proc = subprocess.run(
            [shutil.which("luau") or "luau", path],
            capture_output=True, text=True, timeout=30,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    assert proc.returncode == 0, f"luau failed:\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout


# --- shared scenario fragments ---------------------------------------------

_PLAN_AND_MODULES = """\
local db = makeThemeDatabase()
local modules = {
    ["ReplicatedStorage.ThemeDatabase"] = db,
    ["ReplicatedStorage.ThemeData_Day"] = { themeName = "Day", segments = {"d"} },
    ["ReplicatedStorage.ThemeData_Night"] = { themeName = "NightTime", segments = {"n"} },
}
local plan = {
    addressable_db_seeds = {
        {
            db_module_path = "ReplicatedStorage.ThemeDatabase",
            load_method_name = "LoadDatabase",
            drain_field = "_pendingThemeData",
            appender_name = "Register",
            key_field = "themeName",
            so_module_paths = {
                "ReplicatedStorage.ThemeData_Day",
                "ReplicatedStorage.ThemeData_Night",
            },
        },
    },
}
-- the CONSUMER lookup path: PlayerData.themes[usedTheme+1] == "Day"
local PlayerData = { themes = {"Day", "NightTime"}, usedTheme = 0 }
"""


def test_ac1_registry_non_nil_via_consumer_lookup_path():
    """AC-1: after seed + LoadDatabase, GetThemeData(PlayerData.themes[
    usedTheme+1]) is non-nil — indexed by the CONSUMER's value ("Day"), NOT
    the seed's derived key. Self-satisfaction-proof: the seed never wrote the
    key; LoadDatabase extracts it from op.themeName."""
    out = _run(_PLAN_AND_MODULES + """
SceneRuntime.seedAddressableDatabases(plan, servicesFor(modules))
db.LoadDatabase()
local key = PlayerData.themes[PlayerData.usedTheme + 1]   -- "Day"
local theme = db.GetThemeData(key)
print("AC1_DAY_NONNIL=" .. tostring(theme ~= nil))
print("AC1_NIGHT_NONNIL=" .. tostring(db.GetThemeData("NightTime") ~= nil))
""")
    assert "AC1_DAY_NONNIL=true" in out
    assert "AC1_NIGHT_NONNIL=true" in out


def test_ac3_loaded_coupling_seed_does_not_set_m_loaded():
    """AC-3: loaded() is false until LoadDatabase runs (the seed must NOT set
    m_Loaded — LoadDatabase owns that transition)."""
    out = _run(_PLAN_AND_MODULES + """
SceneRuntime.seedAddressableDatabases(plan, servicesFor(modules))
print("AC3_BEFORE_LOAD=" .. tostring(db.loaded()))   -- seed must not flip it
db.LoadDatabase()
print("AC3_AFTER_LOAD=" .. tostring(db.loaded()))
""")
    assert "AC3_BEFORE_LOAD=false" in out
    assert "AC3_AFTER_LOAD=true" in out


def test_ac4_exactly_owned_sos_registered_keyless_abstained():
    """AC-4: exactly the owned SOs are registered; a key-less SO (no
    themeName) is abstained — not added under any fallback key."""
    out = _run(_PLAN_AND_MODULES.replace(
        '["ReplicatedStorage.ThemeData_Night"] = { themeName = "NightTime", segments = {"n"} },',
        '["ReplicatedStorage.ThemeData_Night"] = { themeName = "NightTime", segments = {"n"} },\n'
        '    ["ReplicatedStorage.ThemeData_Broken"] = { segments = {"x"} },  -- NO themeName',
    ).replace(
        '"ReplicatedStorage.ThemeData_Night",\n            },',
        '"ReplicatedStorage.ThemeData_Night",\n                "ReplicatedStorage.ThemeData_Broken",\n            },',
    ) + """
SceneRuntime.seedAddressableDatabases(plan, servicesFor(modules))
db.LoadDatabase()
local dict = db.dictionnary()
local count = 0
for _ in pairs(dict) do count = count + 1 end
print("AC4_COUNT=" .. tostring(count))                       -- exactly 2
print("AC4_DAY=" .. tostring(dict["Day"] ~= nil))
print("AC4_NIGHT=" .. tostring(dict["NightTime"] ~= nil))
"""
    )
    assert "AC4_COUNT=2" in out
    assert "AC4_DAY=true" in out
    assert "AC4_NIGHT=true" in out


def test_ac5_order_seed_before_load_populates():
    """AC-5: seed-then-Load populates the store (the production order)."""
    out = _run(_PLAN_AND_MODULES + """
SceneRuntime.seedAddressableDatabases(plan, servicesFor(modules))
db.LoadDatabase()
print("AC5_GOOD=" .. tostring(db.GetThemeData("Day") ~= nil))
""")
    assert "AC5_GOOD=true" in out


def test_ac5_order_load_before_seed_locks_empty():
    """AC-5 hazard pin: Load-then-seed locks an empty store (the themeDataList
    ~= nil guard makes the late seed invisible) — proving the entrypoint slot
    ordering is load-bearing."""
    out = _run(_PLAN_AND_MODULES + """
db.LoadDatabase()   -- TOO EARLY: locks themeDataList = {}
SceneRuntime.seedAddressableDatabases(plan, servicesFor(modules))
db.LoadDatabase()   -- guard early-returns; seed never drains
print("AC5_BAD=" .. tostring(db.GetThemeData("Day") == nil))
""")
    assert "AC5_BAD=true" in out


def test_drain_field_fallback_when_no_appender():
    """When appender_name is nil, the shim seeds the drain-field table directly
    (still drain-bound: that field IS what LoadDatabase drains)."""
    scenario = _PLAN_AND_MODULES.replace(
        'appender_name = "Register",', "appender_name = nil,",
    ) + """
SceneRuntime.seedAddressableDatabases(plan, servicesFor(modules))
db.LoadDatabase()
print("FALLBACK=" .. tostring(db.GetThemeData("Day") ~= nil))
"""
    out = _run(scenario)
    assert "FALLBACK=true" in out


def test_generic_noop_when_no_seeds():
    """AC-6 (runtime half): an absent/empty plan key is a clean no-op."""
    out = _run("""
local services = servicesFor({})
SceneRuntime.seedAddressableDatabases({}, services)
SceneRuntime.seedAddressableDatabases({ addressable_db_seeds = {} }, services)
print("NOOP_OK=true")
""")
    assert "NOOP_OK=true" in out


def test_ac2_instantiate_prefab_accepts_phase1_resolved_id():
    """AC-2: a Phase-1-resolved ``"<guid>:<path>"`` prefabList string is a valid
    prefab id — passing it to host.instantiatePrefab returns a real instance.
    (The seed feeds the registry whose currentTheme.zones[].prefabList carries
    exactly these strings; here we prove the id is instantiable when invoked
    directly, decoupled from gameplay flow.)"""
    out = _run("""
local PREFAB_ID = "0d80a5ee0a199154784a904ed88da003:Assets/Bundles/Day/Segment.prefab"
local plan = {
    modules = {},
    scenes = {},
    prefabs = {
        [PREFAB_ID] = {
            name = "Segment",
            instances = {},
            references = {},
            lifecycle_order = {},
        },
    },
    domain_overrides = {},
}
local cloneInstance = { Name = "Segment", _sceneRuntimeId = "seg", _children = {} }
local services = {
    warn = logWarn,
    task = { spawn = function(fn) pcall(fn) end, defer = function() end,
             delay = function() end, wait = function() end },
    resolveModule = function() return nil end,
    workspaceFind = function() return nil end,
    findFirstChildWhichIsA = function() return nil end,
    heartbeat = { Connect = function() return { Disconnect = function() end } end },
    fixedStep = 0.02,
    now = function() return 0 end,
    getInstanceId = function(inst) return inst and inst._sceneRuntimeId end,
    clonePrefabTemplate = function(prefabId, parent, cframe)
        if prefabId == PREFAB_ID then return cloneInstance end
        return nil
    end,
}
local engine = SceneRuntime.new(services, plan)
local clone = engine:instantiatePrefab(PREFAB_ID, nil, nil, nil)
print("AC2_NONNIL=" .. tostring(clone ~= nil))
print("AC2_IS_CLONE=" .. tostring(clone == cloneInstance))
""")
    assert "AC2_NONNIL=true" in out
    assert "AC2_IS_CLONE=true" in out


def test_warns_when_proven_surface_absent_at_runtime():
    """Fail-loud (scene_runtime.luau:195): if the resolved DB module lacks BOTH
    the appender fn AND the drain-field table at runtime, the shim must WARN and
    seed nothing. Asserts the warning actually fires (a silent regression that
    drops the warn makes this RED) and that no SO was registered."""
    scenario = _PLAN_AND_MODULES + """
-- A DB missing BOTH the appender fn (Register) AND a drain-field TABLE: the
-- seed's drain_field ("_pendingThemeData") is present but is NOT a table, so
-- neither write-surface branch matches -> fail-loud. GetThemeData stays empty.
local broken = {
    _pendingThemeData = false,   -- present but not a table -> no drain-field write
    GetThemeData = function() return nil end,
}
modules["ReplicatedStorage.ThemeDatabase"] = broken
local svc = servicesFor(modules)
SceneRuntime.seedAddressableDatabases(plan, svc)
print("SEEDED_DAY=" .. tostring(broken.GetThemeData("Day") ~= nil))
print("DRAIN_UNTOUCHED=" .. tostring(broken._pendingThemeData == false))
dumpLogs()
"""
    out = _run(scenario)
    # The fail-loud warn fired with the documented message + offending path.
    assert any(
        line.startswith("WARN_LINE=[seed] proven write surface absent at runtime on")
        and "ReplicatedStorage.ThemeDatabase" in line
        for line in out.splitlines()
    ), out
    # ... and nothing was seeded (the drain field was left untouched).
    assert "SEEDED_DAY=false" in out
    assert "DRAIN_UNTOUCHED=true" in out


def test_warns_when_db_module_does_not_resolve_to_a_table():
    """Sibling fail-loud branch (scene_runtime.luau:181): when resolveModule
    returns a non-table (nil) for the DB module path, the shim must WARN and
    seed nothing — never index a non-table. A silent regression (warn removed)
    makes this RED."""
    scenario = _PLAN_AND_MODULES + """
-- the DB module fails to resolve (require miss) -> resolveModule returns nil
modules["ReplicatedStorage.ThemeDatabase"] = nil
local svc = servicesFor(modules)
SceneRuntime.seedAddressableDatabases(plan, svc)
print("NO_CRASH=true")
dumpLogs()
"""
    out = _run(scenario)
    assert "NO_CRASH=true" in out
    assert any(
        line.startswith("WARN_LINE=[seed] database module did not resolve to a table:")
        and "ReplicatedStorage.ThemeDatabase" in line
        for line in out.splitlines()
    ), out


def test_warns_when_db_module_resolves_to_non_table_scalar():
    """Same fail-loud branch (scene_runtime.luau:181) with a non-nil non-table
    (a scalar) — proves the guard keys on ``type(db) ~= "table"``, not just nil."""
    scenario = _PLAN_AND_MODULES + """
modules["ReplicatedStorage.ThemeDatabase"] = 42  -- non-table scalar
local svc = servicesFor(modules)
SceneRuntime.seedAddressableDatabases(plan, svc)
print("NO_CRASH=true")
dumpLogs()
"""
    out = _run(scenario)
    assert "NO_CRASH=true" in out
    assert any(
        line.startswith("WARN_LINE=[seed] database module did not resolve to a table:")
        for line in out.splitlines()
    ), out


def test_named_appender_falls_back_to_drain_table_when_appender_missing():
    """Compat fallback (scene_runtime.luau:191->193): the seed NAMES an appender
    (``Register``) but the runtime DB exposes only the drained table, not the
    appender fn. The shim must fall back to inserting into the drain-field table
    directly (still drain-bound) — and seed successfully, no warn."""
    scenario = _PLAN_AND_MODULES + """
-- runtime DB drains _pendingThemeData via LoadDatabase but exposes NO Register fn
local noAppender = makeThemeDatabase()
noAppender.Register = nil
modules["ReplicatedStorage.ThemeDatabase"] = noAppender
local svc = servicesFor(modules)
SceneRuntime.seedAddressableDatabases(plan, svc)
noAppender.LoadDatabase()
print("FALLBACK_DAY=" .. tostring(noAppender.GetThemeData("Day") ~= nil))
print("FALLBACK_NIGHT=" .. tostring(noAppender.GetThemeData("NightTime") ~= nil))
dumpLogs()
"""
    out = _run(scenario)
    assert "FALLBACK_DAY=true" in out
    assert "FALLBACK_NIGHT=true" in out
    # The drain-field fallback is a proven surface, so NO fail-loud warn fired.
    assert "WARN_COUNT=0" in out
