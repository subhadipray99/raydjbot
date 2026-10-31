-- ==============================================================================
-- Highrise Studio: In-Room Jukebox & Music Player (RoomRadio.lua)
-- Attach this script to an empty GameObject in Highrise Studio alongside PlayMusic
-- ==============================================================================

--!SerializedField
local MusicObject : GameObject = nil

-- The direct audio stream endpoint hosted on your Render bot service
local BOT_AUDIO_STREAM_URL = "https://raydjbot.onrender.com/audio/current.mp3"

function start()
    -- Get the PlayMusic component attached to the MusicObject (or this GameObject)
    local targetObject = MusicObject or self.gameObject
    local musicPlayer = targetObject:GetComponent(PlayMusic)

    if not musicPlayer then
        print("[RoomRadio] ERROR: PlayMusic component not found on target GameObject!")
        return
    end

    print("[RoomRadio] In-room music listener initialized.")

    -- Function to play the current active stream URL
    local function playCurrentStream()
        -- Append timestamp parameter to prevent caching previous songs
        local streamUrl = BOT_AUDIO_STREAM_URL .. "?t=" .. tostring(os.time())
        print("[RoomRadio] Streaming in-room audio from: " .. streamUrl)
        musicPlayer.PlayFromURL(streamUrl)
    end

    -- Listen for room chat triggers from users and the bot
    Chat.TextMessageReceivedHandler:Connect(function(channel, player, message)
        local msg = string.lower(message)

        -- When a user requests a song with /play or /p or !play
        if string.find(msg, "/play") or string.find(msg, "/p ") or string.find(msg, "!play") then
            -- Small delay to let the Render bot fetch & extract the YouTube audio stream
            Timer.After(2.5, function()
                playCurrentStream()
            end)

        -- When the bot announces a new song starting (auto-advance in queue)
        elseif string.find(message, "Now Playing:") then
            Timer.After(0.5, function()
                playCurrentStream()
            end)

        -- When user skips track
        elseif string.find(msg, "/skip") or string.find(msg, "/s ") or string.find(msg, "!skip") then
            Timer.After(1.5, function()
                playCurrentStream()
            end)

        -- Manual trigger to sync in-room audio
        elseif string.find(msg, "!radio") or string.find(msg, "/radio") or string.find(msg, "!sync") then
            playCurrentStream()

        -- Stop command
        elseif string.find(msg, "!stop") or string.find(msg, "/stop") then
            print("[RoomRadio] Stopping in-room music.")
            musicPlayer.StopMusic(true)
        end
    end)
end
