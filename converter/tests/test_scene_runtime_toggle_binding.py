"""Slice 1.2: runtime Toggle ``isOn`` -> checkmark ``.Visible`` binding.

Drives the production ``scene_runtime.luau`` through the shared standalone-luau
harness (``_run_scenario`` / ``servicesFor`` from
``test_scene_runtime_host_behavior``) and asserts the generic binding:

  * A1  pickup flip (``isOn`` true) -> ``graphic.Visible == true`` (RED on
        pre-binding code: no Toggle pass exists at the slice base).
  * A2  initial visibility via the live-read at the install scan, and via a
        late ``DescendantAdded`` -- both with ``initial_on`` fallback.
  * A7  edges: E3 non-HUD binds; E5 late no-component clone binds via the
        standing watch; E6 exactly-once per row; E8 shared ``graphic_sri`` AND
        shared ``toggle_sri`` both bind; E9 server services table -> NOTHING
        binds; E10 pre-bind live write reflected; E11 ResetOnSpawn re-clone
        re-binds; E12 non-matching ``DescendantAdded`` no rescan; and the watch
        stays connected across >=2 fires (vs one-shot ``awaitUiHost``).

The binding is client-only: it installs only when the
``installUiDescendantWatch`` service is present (the harness injects it exactly
as the real client entrypoint does; the server table omits it).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from tests.test_scene_runtime_host_behavior import (  # noqa: F401
    _luau_available,
    _run_scenario,
    pytestmark,
)


# A3 runs WITHOUT luau (a pure source grep over the slice DIFF), so it is its
# own module-level test, not gated by the luau ``pytestmark`` skip below.
def test_no_simplefps_node_name_literals_in_slice_diff():
    """A3 — the generic binding introduces NO SimpleFPS node-name literals.

    Grep the ADDED lines of this slice's diff (the binding region only --
    pre-existing unrelated uses of e.g. ``Background`` / ``SimpleFPS`` in the
    same files are not this slice's concern). The binding is keyed on
    ``_SceneRuntimeId`` values, never node names.
    """
    import subprocess

    repo_root = Path(__file__).parent.parent.parent  # repo root (above converter/)
    base = "b02b3e5"  # the slice 1.2 base (slice 1.1 tip)
    banned = [
        "Checkmark", "Battery", "Background", "ItemModule",
        "HudControl", "SimpleFPS",
    ]
    prod_paths = [
        "converter/converter/ui_translator.py",
        "converter/converter/scene_converter.py",
        "converter/converter/autogen.py",
        "converter/runtime/scene_runtime.luau",
    ]
    diff = subprocess.run(
        ["git", "diff", base, "--", *prod_paths],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout
    added = [
        ln[1:] for ln in diff.splitlines()
        if ln.startswith("+") and not ln.startswith("+++")
    ]
    added_text = "\n".join(added)
    for name in banned:
        assert name not in added_text, (
            f"slice diff introduces SimpleFPS node-name literal {name!r} -- "
            f"the binding must be generic (keyed on SRIs, not names)"
        )


# A reusable Lua preamble: a toggle/graphic instance factory + a
# PlayerGui-style DescendantAdded signal + an ``installUiDescendantWatch``
# injector. Appended into each scenario before the plan is built.
_TOGGLE_HARNESS = textwrap.dedent("""\
    -- A GuiObject-ish instance: settable .Visible, an _SceneRuntimeId, named
    -- attributes with per-attribute changed signals, and SetAttribute that
    -- fires the matching changed signal (mirrors Roblox semantics).
    local function mkInst(sri)
        local inst = {Visible = nil, _sri = sri, _attrs = {}, _attrSignals = {}}
        function inst:GetAttribute(name)
            if name == "_SceneRuntimeId" then return self._sri end
            return self._attrs[name]
        end
        function inst:_signalFor(name)
            local s = self._attrSignals[name]
            if not s then
                s = {_conns = {}, _n = 0}
                function s:Connect(fn)
                    s._n = s._n + 1
                    local id = s._n
                    s._conns[id] = fn
                    return {Disconnect = function() s._conns[id] = nil end}
                end
                function s:fire() for _, fn in pairs(s._conns) do fn() end end
                self._attrSignals[name] = s
            end
            return s
        end
        function inst:GetAttributeChangedSignal(name)
            return self:_signalFor(name)
        end
        function inst:SetAttribute(name, value)
            if name == "_SceneRuntimeId" then self._sri = value; return end
            self._attrs[name] = value
            local s = self._attrSignals[name]
            if s then s:fire() end
        end
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
    rc, out, err = _run_scenario(_TOGGLE_HARNESS + "\n" + scenario_body)
    return rc, out, err


class TestToggleBindingBasic:

    def test_pickup_flip_shows_graphic(self):
        """A1 (RED pre-fix): ``isOn`` flips true -> graphic.Visible == true."""
        scenario = textwrap.dedent("""\
            local toggle = mkInst("tog")
            local graphic = mkInst("gfx")
            local watch = mkWatchSurface()

            local plan = {
                modules = {}, scenes = {}, prefabs = {}, domain_overrides = {},
                ui_toggle_bindings = {
                    {toggle_sri = "tog", graphic_sri = "gfx",
                     initial_on = false, attr_name = "isOn"},
                },
            }
            local services = servicesFor(plan, {}, {tog = toggle, gfx = graphic})
            services.installUiDescendantWatch = function(handler)
                return watch:Connect(handler)
            end

            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()

            print("INIT_VISIBLE=" .. tostring(graphic.Visible))
            -- The HUD writer flips isOn true on a pickup event.
            toggle:SetAttribute("isOn", true)
            print("AFTER_FLIP=" .. tostring(graphic.Visible))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        # initial_on=false, no live isOn -> hidden at bind.
        assert "INIT_VISIBLE=false" in lines, out
        # Flip -> shown.
        assert "AFTER_FLIP=true" in lines, out

    def test_initial_on_true_visible_at_install_scan(self):
        """A2 — present clone with initial_on=true is visible at the install
        scan (before any DescendantAdded), no flip needed."""
        scenario = textwrap.dedent("""\
            local toggle = mkInst("tog")
            local graphic = mkInst("gfx")
            local watch = mkWatchSurface()
            local plan = {
                modules = {}, scenes = {}, prefabs = {}, domain_overrides = {},
                ui_toggle_bindings = {
                    {toggle_sri = "tog", graphic_sri = "gfx",
                     initial_on = true, attr_name = "isOn"},
                },
            }
            local services = servicesFor(plan, {}, {tog = toggle, gfx = graphic})
            services.installUiDescendantWatch = function(h) return watch:Connect(h) end
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            print("VISIBLE=" .. tostring(graphic.Visible))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        assert "VISIBLE=true" in out, out

    def test_prebind_live_write_reflected(self):
        """E10 — an ``isOn`` write that landed BEFORE the bind is reflected at
        bind even though initial_on=false."""
        scenario = textwrap.dedent("""\
            local toggle = mkInst("tog")
            local graphic = mkInst("gfx")
            local watch = mkWatchSurface()
            -- Pre-bind: a write already set isOn true.
            toggle:SetAttribute("isOn", true)
            local plan = {
                modules = {}, scenes = {}, prefabs = {}, domain_overrides = {},
                ui_toggle_bindings = {
                    {toggle_sri = "tog", graphic_sri = "gfx",
                     initial_on = false, attr_name = "isOn"},
                },
            }
            local services = servicesFor(plan, {}, {tog = toggle, gfx = graphic})
            services.installUiDescendantWatch = function(h) return watch:Connect(h) end
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            print("VISIBLE=" .. tostring(graphic.Visible))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        # Live read wins over the static initial_on=false.
        assert "VISIBLE=true" in out, out


class TestToggleBindingLateClone:

    def test_late_clone_binds_via_standing_watch(self):
        """E5/A2 — a toggle+graphic absent at install scan binds when they land
        via DescendantAdded (no deferred runtime component needed). Reads live
        state at the late bind."""
        scenario = textwrap.dedent("""\
            local toggle = mkInst("tog")
            local graphic = mkInst("gfx")
            local watch = mkWatchSurface()
            local plan = {
                modules = {}, scenes = {}, prefabs = {}, domain_overrides = {},
                ui_toggle_bindings = {
                    {toggle_sri = "tog", graphic_sri = "gfx",
                     initial_on = true, attr_name = "isOn"},
                },
            }
            -- workspaceFind MISSES both at install (not in the instances map).
            local services = servicesFor(plan, {}, {})
            services.installUiDescendantWatch = function(h) return watch:Connect(h) end
            -- But once they LAND, workspaceFind resolves the sibling.
            local landed = {}
            services.workspaceFind = function(id) return landed[id] end

            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            print("BEFORE=" .. tostring(graphic.Visible))

            -- Graphic lands FIRST (toggle sibling still missing): its
            -- DescendantAdded looks up the row but workspaceFind("tog")
            -- misses -> E7 skip, no bind yet.
            landed["gfx"] = graphic
            watch:fire(graphic)
            print("MID=" .. tostring(graphic.Visible))
            -- Toggle lands: its DescendantAdded resolves the now-present
            -- graphic sibling -> bind.
            landed["tog"] = toggle
            watch:fire(toggle)
            print("AFTER=" .. tostring(graphic.Visible))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        # Nothing bound before the clones land.
        assert "BEFORE=nil" in lines, out
        # Graphic-first fire can't bind yet (toggle missing) -> still nil.
        assert "MID=nil" in lines, out
        # Toggle landing completes the bind; initial_on=true -> visible.
        assert "AFTER=true" in lines, out

    def test_watch_stays_connected_across_two_fires(self):
        """A7 — the watch is the STANDING installUiDescendantWatch (not the
        one-shot awaitUiHost): it stays connected across >=2 fires."""
        scenario = textwrap.dedent("""\
            local watch = mkWatchSurface()
            local plan = {
                modules = {}, scenes = {}, prefabs = {}, domain_overrides = {},
                ui_toggle_bindings = {
                    {toggle_sri = "tog", graphic_sri = "gfx",
                     initial_on = false, attr_name = "isOn"},
                },
            }
            local services = servicesFor(plan, {}, {})
            services.installUiDescendantWatch = function(h) return watch:Connect(h) end
            services.workspaceFind = function(id) return nil end
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            -- Fire twice with non-matching descendants; the watch must remain.
            watch:fire(mkInst("noise1"))
            local afterOne = watch:isConnected()
            watch:fire(mkInst("noise2"))
            local afterTwo = watch:isConnected()
            print("CONNECTS=" .. tostring(watch.connectCount))
            print("AFTER_ONE=" .. tostring(afterOne))
            print("AFTER_TWO=" .. tostring(afterTwo))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        # Connected exactly once, and still connected after two fires.
        assert "CONNECTS=1" in lines, out
        assert "AFTER_ONE=true" in lines, out
        assert "AFTER_TWO=true" in lines, out

    def test_nonmatching_descendant_short_circuits(self):
        """E12 — a DescendantAdded whose SRI isn't in the index does NOT
        rescan / bind anything (O(1) miss)."""
        scenario = textwrap.dedent("""\
            local toggle = mkInst("tog")
            local graphic = mkInst("gfx")
            local watch = mkWatchSurface()
            local plan = {
                modules = {}, scenes = {}, prefabs = {}, domain_overrides = {},
                ui_toggle_bindings = {
                    {toggle_sri = "tog", graphic_sri = "gfx",
                     initial_on = false, attr_name = "isOn"},
                },
            }
            -- Both miss at install; count workspaceFind calls.
            local finds = 0
            local landed = {}
            local services = servicesFor(plan, {}, {})
            services.installUiDescendantWatch = function(h) return watch:Connect(h) end
            services.workspaceFind = function(id)
                finds = finds + 1
                return landed[id]
            end
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            local findsAfterInstall = finds
            -- Fire a non-matching descendant -> must NOT call workspaceFind.
            watch:fire(mkInst("totally-unrelated"))
            print("NEW_FINDS=" .. tostring(finds - findsAfterInstall))
            print("VISIBLE=" .. tostring(graphic.Visible))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        # No workspaceFind issued for the non-matching fire (index miss).
        assert "NEW_FINDS=0" in lines, out
        assert "VISIBLE=nil" in lines, out


class TestToggleBindingEdges:

    def test_exactly_once_per_row(self):
        """E6 — the install scan + a redundant DescendantAdded for an
        already-bound row connect the change signal exactly once."""
        scenario = textwrap.dedent("""\
            local toggle = mkInst("tog")
            local graphic = mkInst("gfx")
            local watch = mkWatchSurface()
            local plan = {
                modules = {}, scenes = {}, prefabs = {}, domain_overrides = {},
                ui_toggle_bindings = {
                    {toggle_sri = "tog", graphic_sri = "gfx",
                     initial_on = false, attr_name = "isOn"},
                },
            }
            local services = servicesFor(plan, {}, {tog = toggle, gfx = graphic})
            services.installUiDescendantWatch = function(h) return watch:Connect(h) end
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            -- Re-fire for the same (already-bound) toggle: must NOT re-connect.
            watch:fire(toggle)
            watch:fire(graphic)
            -- Count the isOn change-signal connections on the toggle.
            local conns = 0
            for _ in pairs(toggle._attrSignals["isOn"]._conns) do conns = conns + 1 end
            print("CONNS=" .. tostring(conns))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        # Exactly one change-signal connection despite the redundant fires.
        assert "CONNS=1" in lines, out

    def test_shared_graphic_sri_both_rows_bind(self):
        """E8 — two rows sharing a graphic_sri BOTH bind (sri->LIST index)."""
        scenario = textwrap.dedent("""\
            local togA = mkInst("togA")
            local togB = mkInst("togB")
            local graphic = mkInst("gfx")   -- shared graphic
            local watch = mkWatchSurface()
            local plan = {
                modules = {}, scenes = {}, prefabs = {}, domain_overrides = {},
                ui_toggle_bindings = {
                    {toggle_sri = "togA", graphic_sri = "gfx",
                     initial_on = false, attr_name = "isOn"},
                    {toggle_sri = "togB", graphic_sri = "gfx",
                     initial_on = false, attr_name = "isOn"},
                },
            }
            local services = servicesFor(plan, {},
                {togA = togA, togB = togB, gfx = graphic})
            services.installUiDescendantWatch = function(h) return watch:Connect(h) end
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            -- togB flips -> the shared graphic shows (row B connected).
            togB:SetAttribute("isOn", true)
            print("AFTER_B=" .. tostring(graphic.Visible))
            -- togA flips false -> last writer wins (row A connected too).
            togA:SetAttribute("isOn", false)
            print("AFTER_A=" .. tostring(graphic.Visible))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        # Row B's connection drove the shared graphic visible.
        assert "AFTER_B=true" in lines, out
        # Row A's connection ALSO bound (a per-instance marker would have
        # dropped one row -> this would stay true).
        assert "AFTER_A=false" in lines, out

    def test_shared_toggle_sri_both_graphics_bind(self):
        """E8 (codex r4 #1) — two rows sharing a TOGGLE instance each bind
        their OWN graphic (per-row _boundRows, not per-instance)."""
        scenario = textwrap.dedent("""\
            local toggle = mkInst("tog")        -- shared toggle
            local gfx1 = mkInst("gfx1")
            local gfx2 = mkInst("gfx2")
            local watch = mkWatchSurface()
            local plan = {
                modules = {}, scenes = {}, prefabs = {}, domain_overrides = {},
                ui_toggle_bindings = {
                    {toggle_sri = "tog", graphic_sri = "gfx1",
                     initial_on = false, attr_name = "isOn"},
                    {toggle_sri = "tog", graphic_sri = "gfx2",
                     initial_on = false, attr_name = "isOn"},
                },
            }
            local services = servicesFor(plan, {},
                {tog = toggle, gfx1 = gfx1, gfx2 = gfx2})
            services.installUiDescendantWatch = function(h) return watch:Connect(h) end
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            toggle:SetAttribute("isOn", true)
            print("GFX1=" .. tostring(gfx1.Visible))
            print("GFX2=" .. tostring(gfx2.Visible))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        # BOTH graphics show -> both rows bound their own graphic. A
        # per-instance marker would bind only the first (one stays nil).
        assert "GFX1=true" in lines, out
        assert "GFX2=true" in lines, out

    def test_server_services_table_binds_nothing(self):
        """E9 — no installUiDescendantWatch service (server table) -> NOTHING
        binds: no .Visible mutation, no change-signal connect."""
        scenario = textwrap.dedent("""\
            local toggle = mkInst("tog")
            local graphic = mkInst("gfx")
            local plan = {
                modules = {}, scenes = {}, prefabs = {}, domain_overrides = {},
                ui_toggle_bindings = {
                    {toggle_sri = "tog", graphic_sri = "gfx",
                     initial_on = true, attr_name = "isOn"},
                },
            }
            local services = servicesFor(plan, {}, {tog = toggle, gfx = graphic})
            -- Server: NO installUiDescendantWatch on the services table.
            services.installUiDescendantWatch = nil
            local engine = SceneRuntime.new(services, plan)
            engine:start("server")
            runDeferred()
            -- A flip must NOT change visibility (no connection installed).
            toggle:SetAttribute("isOn", true)
            print("VISIBLE=" .. tostring(graphic.Visible))
            print("HAS_SIGNAL=" .. tostring(toggle._attrSignals["isOn"] ~= nil))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        # initial_on=true would have shown the graphic IF the server bound --
        # it must NOT (nil = never touched).
        assert "VISIBLE=nil" in lines, out
        # No change-signal was ever connected on the toggle.
        assert "HAS_SIGNAL=false" in lines, out

    def test_resetonspawn_reclone_rebinds(self):
        """E11 — a respawn re-clone (NEW instance, SAME sri) re-binds via the
        per-row marker (instance value differs -> _boundRows[b] ~= new)."""
        scenario = textwrap.dedent("""\
            local toggle = mkInst("tog")
            local graphic = mkInst("gfx")
            local watch = mkWatchSurface()
            local plan = {
                modules = {}, scenes = {}, prefabs = {}, domain_overrides = {},
                ui_toggle_bindings = {
                    {toggle_sri = "tog", graphic_sri = "gfx",
                     initial_on = true, attr_name = "isOn"},
                },
            }
            local landed = {tog = toggle, gfx = graphic}
            local services = servicesFor(plan, {}, {})
            services.installUiDescendantWatch = function(h) return watch:Connect(h) end
            services.workspaceFind = function(id) return landed[id] end
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            print("FIRST=" .. tostring(graphic.Visible))

            -- Respawn: NEW instances with the SAME sris replace the old ones.
            local toggle2 = mkInst("tog")
            local graphic2 = mkInst("gfx")
            landed["tog"] = toggle2
            landed["gfx"] = graphic2
            watch:fire(toggle2)   -- DescendantAdded for the re-clone
            -- The new graphic was bound (initial_on=true) -> visible; and a
            -- flip on the NEW toggle drives the NEW graphic.
            print("RECLONE=" .. tostring(graphic2.Visible))
            toggle2:SetAttribute("isOn", false)
            print("AFTER_FLIP=" .. tostring(graphic2.Visible))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        lines = out.strip().splitlines()
        assert "DONE" in lines, out
        assert "FIRST=true" in lines, out
        # The re-clone re-bound (per-row marker saw a new instance).
        assert "RECLONE=true" in lines, out
        assert "AFTER_FLIP=false" in lines, out

    def test_non_hud_toggle_binds(self):
        """E3 — binding is not HUD-specific; any plan row binds wherever it
        lands. (Genericity is structural: rows are keyed on SRIs, not names.)"""
        scenario = textwrap.dedent("""\
            local toggle = mkInst("menuTog")
            local graphic = mkInst("menuCheck")
            local watch = mkWatchSurface()
            local plan = {
                modules = {}, scenes = {}, prefabs = {}, domain_overrides = {},
                ui_toggle_bindings = {
                    {toggle_sri = "menuTog", graphic_sri = "menuCheck",
                     initial_on = false, attr_name = "isOn"},
                },
            }
            local services = servicesFor(plan, {},
                {menuTog = toggle, menuCheck = graphic})
            services.installUiDescendantWatch = function(h) return watch:Connect(h) end
            local engine = SceneRuntime.new(services, plan)
            engine:start("client")
            runDeferred()
            toggle:SetAttribute("isOn", true)
            print("VISIBLE=" .. tostring(graphic.Visible))
            print("DONE")
        """)
        rc, out, err = _build(scenario)
        assert rc == 0, f"luau failed: {err}\n{out}"
        assert "VISIBLE=true" in out, out
