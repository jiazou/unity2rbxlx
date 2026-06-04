"""Tests for the movement-facet lowering pass (generic allowlist).

Identifies the unique player controller (camera-facet + WASD method +
CharacterController ref) and whole-body-replaces its WASD movement onto the
character's Humanoid:Move; parameterizes the camera pass to emit
``followCharacter = true`` for that script. Structure-gated, deterministic,
idempotent, fail-closed on ambiguity.

Fixtures use the VERBATIM daa09e post-transpile shape (Awake + Move) and the
adversarial bypass cases, exercised through the REAL
find_player_controllers -> lower_camera_facet -> lower_movement_facet ordering
(not synthetic isolation).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from converter.camera_facet_lowering import lower_camera_facet  # noqa: E402
from converter.code_transpiler import (  # noqa: E402
    TranspilationResult,
    TranspiledScript,
)
from converter.movement_facet_lowering import (  # noqa: E402
    find_player_controllers,
    lower_movement_facet,
)


class _S:
    """Minimal TranspiledScript stand-in (carries ``luau_source``)."""

    def __init__(self, src: str, name: str = "Player") -> None:
        self.luau_source = src
        # ``name`` is present to PROVE detection never reads it (signals are
        # structural). A misleading name must not change identification.
        self.name = name


# --- Verbatim daa09e shapes ------------------------------------------------

# Awake (Player.luau:83-96) -- carries GetComponent("CharacterController").
_AWAKE = textwrap.dedent("""\
    function Player:Awake()
        Player.instance = self.gameObject
        self.source = self:GetComponent("AudioSource")
        self.control = self:GetComponent("CharacterController")
        self.cam = workspace.CurrentCamera
        self.weaponSlot = self.cam and self.cam:GetChildren()[1]
    end
""")

# Rotate (the camera-facet look method, PRE-lowering -- the flattened FPS
# fingerprint _find_look_method matches: yaw-only body turn + pitch-only cam).
_ROTATE = textwrap.dedent("""\
    function Player:Rotate(dt)
        local UIS = game:GetService("UserInputService")
        local delta = UIS:GetMouseDelta()
        local yaw = self.sensitivity * dt * delta.X
        self.gameObject:PivotTo(self.gameObject:GetPivot() * CFrame.Angles(0, -math.rad(yaw), 0))
        self.camRotationX = self.camRotationX - delta.Y * self.sensitivity * dt
        self.camRotationX = math.clamp(self.camRotationX, self.minAngle, self.maxAngle)
        if self.cam then
            local pos = self.cam.CFrame.Position
            self.cam.CFrame = CFrame.new(pos) * CFrame.Angles(math.rad(self.camRotationX), 0, 0)
        end
    end
""")

# Move (Player.luau:143-179) verbatim -- WASD nested in ``if grounded``,
# ``local disp`` then ``self.gameObject:PivotTo(self.gameObject:GetPivot() + disp)``.
_MOVE = textwrap.dedent("""\
    function Player:Move(dt)
        local UIS = game:GetService("UserInputService")

        local grounded = true
        if self.control and self.control:IsA("Humanoid") then
            grounded = self.control.FloorMaterial ~= Enum.Material.Air
        end

        if grounded then
            local h = 0
            if UIS:IsKeyDown(Enum.KeyCode.D) then h = h + 1 end
            if UIS:IsKeyDown(Enum.KeyCode.A) then h = h - 1 end
            local v = 0
            if UIS:IsKeyDown(Enum.KeyCode.W) then v = v + 1 end
            if UIS:IsKeyDown(Enum.KeyCode.S) then v = v - 1 end

            local md = self.gameObject:GetPivot():VectorToWorldSpace(Vector3.new(h, 0, v))
            self.moveDir = md

            if UIS:IsKeyDown(Enum.KeyCode.Space) then
                self.moveDir = Vector3.new(self.moveDir.X, self.jumpSpeed, self.moveDir.Z)
            end
        end

        self.moveDir = Vector3.new(self.moveDir.X, self.moveDir.Y - self.gravity * dt, self.moveDir.Z)
        local disp = self.moveDir * self.speed * dt * STUDS_PER_METER
        self.gameObject:PivotTo(self.gameObject:GetPivot() + disp)
    end
""")


def _player_src() -> str:
    return (
        "local Player = {}\nPlayer.__index = Player\n\n"
        + _AWAKE + "\n" + _ROTATE + "\n" + _MOVE + "\nreturn Player\n"
    )


class TestPositive:
    def test_find_camera_movement_ordering(self) -> None:
        """(1/10) Real find -> camera -> movement ordering on the daa09e shape."""
        s = _S(_player_src())
        scripts = [s]

        # 1. Identify the player (pre-lowering, camera fingerprint intact).
        players = find_player_controllers(scripts)
        assert players == [s]

        # 2. Camera lowering with follow=players emits followCharacter=true.
        assert lower_camera_facet(scripts, follow_character_paths=players) == 1
        assert (
            "self._cam:configure({rig = self.gameObject, followCharacter = true})"
            in s.luau_source
        )
        assert ":step(dt)" in s.luau_source

        # 3. Movement lowering replaces Move with the Humanoid:Move body.
        assert lower_movement_facet(players) == 1
        src = s.luau_source
        assert 'char:FindFirstChildOfClass("Humanoid")' in src
        assert "self._cam:getYawBasis():VectorToWorldSpace(Vector3.new(h, 0, -v))" in src
        assert "hum:Move(dir.Unit, false)" in src
        assert "hum:Move(Vector3.zero, false)" in src
        # The vestigial rig PivotTo displacement is gone from Move.
        assert "self.gameObject:PivotTo(self.gameObject:GetPivot() + disp)" not in src
        # Method header + param preserved.
        assert "function Player:Move(dt)" in src

    def test_lowered_move_has_lazy_cam_acquire_with_follow(self) -> None:
        """(8) Lowered Move carries the lazy _cam acquire w/ followCharacter=true."""
        s = _S(_player_src())
        players = find_player_controllers([s])
        lower_movement_facet(players)
        src = s.luau_source
        assert "if not self._cam then" in src
        assert (
            'require(game:GetService("ReplicatedStorage")'
            ':WaitForChild("SceneCameraInput")).acquire()' in src
        )
        assert (
            "self._cam:configure({rig = self.gameObject, followCharacter = true})"
            in src
        )

    def test_alias_displacement_still_lowered(self) -> None:
        """(6) A player Move using a local ``pivot`` var IS lowered (detection
        is by WASD reads, not the PivotTo literal)."""
        alias_move = textwrap.dedent("""\
            function Player:Move(dt)
                local UIS = game:GetService("UserInputService")
                local h = 0
                if UIS:IsKeyDown(Enum.KeyCode.D) then h = h + 1 end
                if UIS:IsKeyDown(Enum.KeyCode.A) then h = h - 1 end
                local v = 0
                if UIS:IsKeyDown(Enum.KeyCode.W) then v = v + 1 end
                if UIS:IsKeyDown(Enum.KeyCode.S) then v = v - 1 end
                local pivot = self.gameObject:GetPivot()
                local disp = Vector3.new(h, 0, v)
                self.gameObject:PivotTo(pivot + disp)
            end
        """)
        src = (
            "local Player = {}\nPlayer.__index = Player\n\n"
            + _AWAKE + "\n" + _ROTATE + "\n" + alias_move + "\nreturn Player\n"
        )
        s = _S(src)
        players = find_player_controllers([s])
        assert players == [s]
        assert lower_movement_facet(players) == 1
        assert "hum:Move(dir.Unit, false)" in s.luau_source
        assert "self.gameObject:PivotTo(pivot + disp)" not in s.luau_source


class TestIdempotency:
    def test_twice_call_is_noop(self) -> None:
        """(2) A second pass over the lowered source modifies 0 scripts."""
        s = _S(_player_src())
        players = find_player_controllers([s])
        assert lower_movement_facet(players) == 1
        once = s.luau_source
        # Re-identify on the lowered source + re-lower -> 0.
        assert lower_movement_facet(players) == 0
        assert s.luau_source == once
        assert once.count("hum:Move(dir.Unit, false)") == 1

    def test_idempotency_is_method_scoped_not_file_global(self) -> None:
        """(Codex P2) An unrelated ``:Move(`` call ELSEWHERE in the file must
        NOT suppress a needed first lowering. The pre-fix file-global guard
        (`getYawBasis():VectorToWorldSpace` + any `:Move(` anywhere ->
        early-return) would false-skip; the method-scoped guard lowers
        correctly because the WASD method's OWN body has neither marker yet."""
        # An UNLOWERED player whose Awake has a stray ``:Move(`` AND a stray
        # ``getYawBasis():VectorToWorldSpace`` (e.g. from some other helper) --
        # together they would trip a file-global idempotency scan.
        decoy_awake = textwrap.dedent("""\
            function Player:Awake()
                Player.instance = self.gameObject
                self.control = self:GetComponent("CharacterController")
                self.cam = workspace.CurrentCamera
                local basis = self.foo:getYawBasis():VectorToWorldSpace(Vector3.zero)
                self.other:Move(basis)
            end
        """)
        src = (
            "local Player = {}\nPlayer.__index = Player\n\n"
            + decoy_awake + "\n" + _ROTATE + "\n" + _MOVE + "\nreturn Player\n"
        )
        s = _S(src)
        players = find_player_controllers([s])
        assert players == [s]
        # Method-scoped: the WASD Move body has no markers yet -> lowered.
        assert lower_movement_facet(players) == 1
        assert "hum:Move(dir.Unit, false)" in s.luau_source
        # The decoy lines in Awake are untouched (only Move's body changed).
        assert "self.other:Move(basis)" in s.luau_source


class TestNegative:
    def test_vehicle_not_identified(self) -> None:
        """(3) A Jeep:Move (WASD + self PivotTo, NO camera facet, NO
        CharacterController) is not identified or lowered."""
        jeep = textwrap.dedent("""\
            local Jeep = {}
            Jeep.__index = Jeep

            function Jeep:Move(dt)
                local UIS = game:GetService("UserInputService")
                local h = 0
                if UIS:IsKeyDown(Enum.KeyCode.D) then h = h + 1 end
                if UIS:IsKeyDown(Enum.KeyCode.A) then h = h - 1 end
                local v = 0
                if UIS:IsKeyDown(Enum.KeyCode.W) then v = v + 1 end
                if UIS:IsKeyDown(Enum.KeyCode.S) then v = v - 1 end
                local disp = Vector3.new(h, 0, v)
                self.gameObject:PivotTo(self.gameObject:GetPivot() + disp)
            end

            return Jeep
        """)
        s = _S(jeep, name="Jeep")
        before = s.luau_source
        # Not identified -> the movement pass (driven by find's output) is a no-op.
        assert find_player_controllers([s]) == []
        assert lower_movement_facet(find_player_controllers([s])) == 0
        assert s.luau_source == before

    def test_drone_not_a_player(self) -> None:
        """(4) Camera facet + WASD but NO CharacterController -> not a player;
        movement untouched; followCharacter NOT emitted."""
        drone_move = textwrap.dedent("""\
            function Drone:Move(dt)
                local UIS = game:GetService("UserInputService")
                local h = 0
                if UIS:IsKeyDown(Enum.KeyCode.D) then h = h + 1 end
                if UIS:IsKeyDown(Enum.KeyCode.A) then h = h - 1 end
                local v = 0
                if UIS:IsKeyDown(Enum.KeyCode.W) then v = v + 1 end
                if UIS:IsKeyDown(Enum.KeyCode.S) then v = v - 1 end
                self.gameObject:PivotTo(self.gameObject:GetPivot() + Vector3.new(h, 0, v))
            end
        """)
        # Camera facet present (Rotate), WASD present, but NO CharacterController.
        rotate = _ROTATE.replace("Player:Rotate", "Drone:Rotate")
        src = (
            "local Drone = {}\nDrone.__index = Drone\n\n"
            + rotate + "\n" + drone_move + "\nreturn Drone\n"
        )
        s = _S(src, name="Drone")
        scripts = [s]
        players = find_player_controllers(scripts)
        assert players == []
        # Camera still lowers (drone is a camera rig) but followCharacter stays false.
        assert lower_camera_facet(scripts, follow_character_paths=players) == 1
        assert "followCharacter = true" not in s.luau_source
        assert "self._cam:configure({rig = self.gameObject})" in s.luau_source
        # Movement untouched.
        assert lower_movement_facet(players) == 0
        assert "hum:Move" not in s.luau_source

    def test_two_candidates_fail_closed(self) -> None:
        """(5) TWO all-three scripts -> [] (fail-closed)."""
        a = _S(_player_src(), name="PlayerA")
        b = _S(_player_src(), name="PlayerB")
        scripts = [a, b]
        assert find_player_controllers(scripts) == []
        # Nothing lowered for movement.
        assert lower_movement_facet(find_player_controllers(scripts)) == 0
        assert "hum:Move" not in a.luau_source
        assert "hum:Move" not in b.luau_source

    def test_split_controller_fail_closed(self) -> None:
        """(5) Camera facet in script A, WASD in script B -> [] (no all-three
        single script)."""
        # Script A: camera facet + CharacterController, NO WASD method.
        cam_only = (
            "local CamCtl = {}\nCamCtl.__index = CamCtl\n\n"
            + _AWAKE.replace("Player:Awake", "CamCtl:Awake")
            + "\n" + _ROTATE.replace("Player:Rotate", "CamCtl:Rotate")
            + "\nreturn CamCtl\n"
        )
        # Script B: WASD + CharacterController, NO camera facet.
        wasd_only = textwrap.dedent("""\
            local Mover = {}
            Mover.__index = Mover

            function Mover:Awake()
                self.control = self:GetComponent("CharacterController")
            end

            function Mover:Move(dt)
                local UIS = game:GetService("UserInputService")
                local h = 0
                if UIS:IsKeyDown(Enum.KeyCode.D) then h = h + 1 end
                if UIS:IsKeyDown(Enum.KeyCode.A) then h = h - 1 end
                local v = 0
                if UIS:IsKeyDown(Enum.KeyCode.W) then v = v + 1 end
                if UIS:IsKeyDown(Enum.KeyCode.S) then v = v - 1 end
                self.gameObject:PivotTo(self.gameObject:GetPivot() + Vector3.new(h, 0, v))
            end

            return Mover
        """)
        a = _S(cam_only, name="CamCtl")
        b = _S(wasd_only, name="Mover")
        scripts = [a, b]
        assert find_player_controllers(scripts) == []
        assert lower_movement_facet(find_player_controllers(scripts)) == 0

    def test_character_controller_in_comment_not_counted(self) -> None:
        """(7) A CharacterController ref ONLY inside a comment does NOT count,
        even with a real camera facet + real WASD method present (the
        CharacterController gate is the sole signal that fails)."""
        awake_commented_cc = textwrap.dedent("""\
            function Player:Awake()
                -- self:GetComponent("CharacterController") would go here someday
                self.cam = workspace.CurrentCamera
            end
        """)
        src = (
            "local Player = {}\nPlayer.__index = Player\n\n"
            + awake_commented_cc + "\n" + _ROTATE + "\n" + _MOVE + "\nreturn Player\n"
        )
        s = _S(src, name="Player")
        before = s.luau_source
        # Real camera facet + real WASD, but CharacterController only commented.
        assert find_player_controllers([s]) == []
        assert lower_movement_facet(find_player_controllers([s])) == 0
        assert s.luau_source == before

    def test_wasd_in_string_not_counted(self) -> None:
        """(7) WASD reads ONLY inside a string do NOT count toward the move
        method (real CharacterController + camera facet present)."""
        fake_move = textwrap.dedent("""\
            function Player:Move(dt)
                local doc = "IsKeyDown(Enum.KeyCode.W) Enum.KeyCode.A Enum.KeyCode.S Enum.KeyCode.D"
                return doc
            end
        """)
        src = (
            "local Player = {}\nPlayer.__index = Player\n\n"
            + _AWAKE + "\n" + _ROTATE + "\n" + fake_move + "\nreturn Player\n"
        )
        s = _S(src, name="Player")
        before = s.luau_source
        # CharacterController (Awake) + camera facet (Rotate) real, but the only
        # WASD reads are inside a string literal -> no WASD method -> not a player.
        assert find_player_controllers([s]) == []
        assert lower_movement_facet(find_player_controllers([s])) == 0
        assert s.luau_source == before

    def test_try_get_component_does_not_satisfy_cc_signal(self) -> None:
        """(Codex P2) ``self:TryGetComponent("CharacterController")`` must NOT
        satisfy the CharacterController signal -- the anchored regex requires a
        non-identifier char before ``GetComponent(`` so the longer identifier
        ``TryGetComponent`` (ending in ``GetComponent``) does not match. With a
        real camera facet + real WASD present, the CC gate is the sole failing
        signal, so the script is not a player and nothing is lowered."""
        awake_try = textwrap.dedent("""\
            function Player:Awake()
                Player.instance = self.gameObject
                self.control = self:TryGetComponent("CharacterController")
                self.cam = workspace.CurrentCamera
            end
        """)
        src = (
            "local Player = {}\nPlayer.__index = Player\n\n"
            + awake_try + "\n" + _ROTATE + "\n" + _MOVE + "\nreturn Player\n"
        )
        s = _S(src, name="Player")
        before = s.luau_source
        assert find_player_controllers([s]) == []
        assert lower_movement_facet(find_player_controllers([s])) == 0
        assert s.luau_source == before

    def test_two_wasd_methods_fail_closed(self) -> None:
        """(Codex P2) A script with TWO colon-methods each reading >=3 distinct
        WASD keys is ambiguous -> fail closed: not identified as a player and
        not lowered (consistent with abstain-on-ambiguity)."""
        second_wasd = textwrap.dedent("""\
            function Player:MoveAlt(dt)
                local UIS = game:GetService("UserInputService")
                local h = 0
                if UIS:IsKeyDown(Enum.KeyCode.D) then h = h + 1 end
                if UIS:IsKeyDown(Enum.KeyCode.A) then h = h - 1 end
                local v = 0
                if UIS:IsKeyDown(Enum.KeyCode.W) then v = v + 1 end
                if UIS:IsKeyDown(Enum.KeyCode.S) then v = v - 1 end
                self.gameObject:PivotTo(self.gameObject:GetPivot() + Vector3.new(h, 0, v))
            end
        """)
        src = (
            "local Player = {}\nPlayer.__index = Player\n\n"
            + _AWAKE + "\n" + _ROTATE + "\n" + _MOVE + "\n" + second_wasd
            + "\nreturn Player\n"
        )
        s = _S(src, name="Player")
        before = s.luau_source
        # Two WASD methods -> ambiguous -> not a player.
        assert find_player_controllers([s]) == []
        assert lower_movement_facet(find_player_controllers([s])) == 0
        # Even if a caller force-passed the script, lowering refuses (fail-closed).
        assert lower_movement_facet([s]) == 0
        assert s.luau_source == before


# --- Pipeline-invocation integration (acceptance #10) -----------------------


class _PInfo:
    """Minimal ``ScriptInfo`` stand-in for ``transpile_with_contract`` --
    it reads only ``path``, ``class_name`` (via the planner join) and
    ``referenced_types`` (unused here)."""

    def __init__(self, path: Path, class_name: str) -> None:
        self.path = path
        self.class_name = class_name
        self.referenced_types: list[str] = []


class TestPipelineInvocation:
    """Drives the REAL ``contract_pipeline.transpile_with_contract`` (not the
    three lowering functions called by hand) so that a future edit deleting
    the ``find_player_controllers -> lower_camera_facet(follow=...) ->
    lower_movement_facet`` wiring would FAIL this test. ``transpile_scripts``
    is stubbed to return the daa09e-shaped player so the test never hits the
    API, but everything downstream (identify, camera lower, movement lower) is
    the production path inside ``transpile_with_contract``."""

    def test_generic_pipeline_lowers_player_movement_and_follow(self) -> None:
        from converter import contract_pipeline

        player_path = Path("/proj/Assets/Player.cs")
        infos = [_PInfo(player_path, "Player")]
        scene_runtime = {
            "modules": {
                "guid-player": {
                    "stem": "Player",
                    "class_name": "Player",
                    "runtime_bearing": True,
                    "is_component_class": True,
                    "character_attached": False,
                    "is_loader": False,
                },
            },
            "scenes": {},
            "prefabs": {},
            "domain_overrides": {},
        }

        player_script = TranspiledScript(
            source_path=str(player_path),
            output_filename="Player.luau",
            csharp_source="",
            luau_source=_player_src(),
            strategy="ai",
            confidence=1.0,
            script_type="ModuleScript",
        )
        stub_result = TranspilationResult()
        stub_result.total_transpiled = 1
        stub_result.scripts.append(player_script)

        with patch(
            "converter.contract_pipeline.transpile_scripts",
            return_value=stub_result,
        ) as mock_transpile:
            result = contract_pipeline.transpile_with_contract(
                "/proj",
                infos,
                scene_runtime=scene_runtime,
                use_ai=False,
            )

        assert mock_transpile.called, (
            "transpile_with_contract must call transpile_scripts (stubbed)."
        )

        lowered_src = result.transpilation.scripts[0].luau_source
        # Movement was retargeted onto the Roblox character's Humanoid:Move --
        # the vestigial rig PivotTo displacement is gone.
        assert "hum:Move(dir.Unit, false)" in lowered_src
        assert (
            "self._cam:getYawBasis():VectorToWorldSpace(Vector3.new(h, 0, -v))"
            in lowered_src
        )
        assert (
            "self.gameObject:PivotTo(self.gameObject:GetPivot() + disp)"
            not in lowered_src
        )
        # The player's camera ``configure`` carries followCharacter = true
        # (proves find_player_controllers -> lower_camera_facet(follow=...) ran
        # together in the pipeline, not just the movement pass).
        assert (
            "self._cam:configure({rig = self.gameObject, followCharacter = true})"
            in lowered_src
        )
        # Camera look facet was routed to the service (step), not left as the
        # AI's per-game pitch math.
        assert ":step(dt)" in lowered_src
