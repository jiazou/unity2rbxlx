-- fieldcam_player.luau — the field-aliased-camera cold shape (slice 2.6).
--
-- Captured from a FRESH cold AI transpile of a SimpleFPS-style controller: the
-- camera is aliased to a FIELD in Awake (``self.cam = workspace.CurrentCamera``)
-- and the look body writes through that alias (``self.cam.CFrame = …``) WITHOUT
-- the ``CurrentCamera`` / ``CameraType = Enum.CameraType.Scriptable`` literal in
-- the look body. Paradigm A's camera-facet look-locator keys on that literal
-- being IN the scanned look body (strict pitch-rebuild OR the broad
-- mouse-delta+camera-owner+Angles trio), so on this shape it ABSTAINS — no
-- ``self._cam:step(`` is spliced and the RAW ``self.cam.CFrame`` write SURVIVES.
--
-- This is the §3 raw-CurrentCamera-survives case driven by a REAL captured cold
-- shape (vs the synthetic raw-writer of AC4b). It is intentionally RAW
-- (pre-facet-lowering): the camera is field-aliased so the look-locator abstains,
-- and the WASD body still drives the rig (so a future move-lower can run if it
-- chooses). ``self.cam`` aliases ``workspace.CurrentCamera`` — the SAME object —
-- so C's ``_playerWriteCamera`` (writing ``workspace.CurrentCamera.CFrame``) is
-- the last writer of the very cell this shape's ``self.cam.CFrame =`` stomps.

local Player = {}
Player.__index = Player

function Player:Awake()
    Player.instance = self.gameObject
    self.source = self:GetComponent("AudioSource")
    self.control = self:GetComponent("CharacterController")
    -- The camera is aliased to a FIELD here (the CurrentCamera literal lives in
    -- Awake, OUTSIDE the look body the camera-facet locator scans).
    self.cam = workspace.CurrentCamera
    self.camRotationX = 0
end

function Player:Rotate(dt)
    local mouseDelta = self.uis:GetMouseDelta()
    local yaw = self.sensitivity * dt * mouseDelta.X
    self.gameObject:PivotTo(self.gameObject:GetPivot() * CFrame.Angles(0, -math.rad(yaw), 0))
    self.camRotationX = self.camRotationX - mouseDelta.Y * self.sensitivity * dt
    self.camRotationX = math.clamp(self.camRotationX, self.minAngle, self.maxAngle)
    -- Writes through the field alias: no ``CurrentCamera`` literal and no
    -- ``CameraType = Enum.CameraType.Scriptable`` here, so A's broad camera-owner
    -- gate fails and the look-locator abstains (the raw write survives).
    if self.cam then
        -- A bare pitch-only orientation through the field alias: NOT the strict
        -- ``CFrame.new(pos) * CFrame.Angles(pitch, 0, 0)`` rebuild and with no
        -- ``CurrentCamera`` / ``CameraType = Scriptable`` literal here, so both
        -- the strict and broad camera locators miss it — A abstains.
        self.cam.CFrame = CFrame.Angles(math.rad(self.camRotationX), 0, 0)
    end
end

function Player:Move(dt)
    local humanoid = self.control
    if not humanoid then
        return
    end
    local h = self:_axis(Enum.KeyCode.D, Enum.KeyCode.A)
    local v = self:_axis(Enum.KeyCode.W, Enum.KeyCode.S)
    local localDir = Vector3.new(h, 0, -v)
    self.moveDirection = self.gameObject:GetPivot():VectorToWorldSpace(localDir)
    if self:_keyDown(Enum.KeyCode.Space) then
        humanoid.Jump = true
    end
    if self.moveDirection.Magnitude > 0 then
        humanoid:Move(self.moveDirection.Unit, false)
    else
        humanoid:Move(Vector3.zero, false)
    end
end

function Player:_axis(posCode, negCode)
    local a = 0
    if self.uis:IsKeyDown(posCode) then a = a + 1 end
    if self.uis:IsKeyDown(negCode) then a = a - 1 end
    return a
end

function Player:_keyDown(code)
    return self.uis:IsKeyDown(code)
end

return Player
