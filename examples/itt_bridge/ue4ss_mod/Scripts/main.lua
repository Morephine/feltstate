-- itt_bridge — UE4SS Lua mod: file-watched cvar command bridge (README rung 3).
--
-- Polls CMD_FILE every 100 ms; when {"follow": 0|1} changes, issues
-- `Mod.FollowOther <v>` as a console command on the player controller.
-- The engine-side contract: define that cvar in the game's script layer and
-- let an EXISTING capability read it — the game then moves its character
-- with its own native code; nothing here fakes an input.
--
-- UE4SS is the UE4SS-RE project's framework (not ours); this file is just
-- the small bridge riding on it.

local CMD_FILE = "C:/path/to/game_state/cmd.json"  -- EDIT ME (absolute path)
local POLL_MS = 100
local last_cmd = -1

-- minimal parse: we only ever read {"follow": 0/1}
local function parse_follow(s)
    local v = s:match('"follow"%s*:%s*(%d+)')
    if v then return tonumber(v) end
    return nil
end

local function read_cmd()
    local f = io.open(CMD_FILE, "r")
    if not f then return nil end
    local s = f:read("*a")
    f:close()
    return parse_follow(s)
end

local function set_follow(v)
    ExecuteInGameThread(function()
        local pc = UEHelpers:GetPlayerController()
        if pc and pc:IsValid() then
            local cmd = string.format("Mod.FollowOther %d", v)
            pc:ConsoleCommand(cmd, false)
            print("[itt_bridge] -> " .. cmd)
        end
    end)
end

LoopAsync(POLL_MS, function()
    local v = read_cmd()
    if v ~= nil and v ~= last_cmd then
        last_cmd = v
        set_follow(v)
    end
    return false  -- keep looping
end)

print("[itt_bridge] loaded, polling " .. CMD_FILE)
