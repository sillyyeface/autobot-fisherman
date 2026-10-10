# user-tunable settings in one place; the search region itself is picked with the region hotkey
# and stored per user in config.json

# hotkeys (keyboard library names); they are global and also fire while typing in the roblox chat
HOTKEY_TOGGLE = "k"
HOTKEY_SELECT_REGION = "l"
HOTKEY_EXIT = "esc"

# fishing loop timings in seconds
HOLD_T_DURATION = 8.0      # hold t while collecting the catch
DISAPPEAR_TIMEOUT = 0.35   # bar must be missing this long before the minigame counts as finished
WAITING_TIMEOUT = 30.0     # recast the rod after this long without a bite

# marker control: hold while the predicted marker is below the predicted target point in the zone
PREDICTION_TIME = 0.2      # look-ahead, about twice the capture-to-game latency; raise if the marker overshoots
ZONE_TARGET_OFFSET = 0.0   # target point as a fraction of zone height from its center, positive is lower

# vision frame rate per bot phase; only the minigame needs fast reactions
TARGET_FPS_MINIGAME = 60
TARGET_FPS_WAITING = 30
TARGET_FPS_IDLE = 30  # paused or collecting

# status hud in 1080p pixels, scaled by the screen height
STATUS_HUD_POSITION = (16, 72)  # below the roblox top bar buttons
STATUS_HUD_PADDING = (10, 6)
STATUS_HUD_FONT_SIZE = 15
STATUS_HUD_BACKGROUND_ALPHA = 0.55

# search region used until one is selected, as screen fractions (the original 1920x1080 capture box)
DEFAULT_SEARCH_REGION = {
    "left": 1380 / 1920,
    "top": 280 / 1080,
    "width": 180 / 1920,
    "height": 480 / 1080,
}

# detection colors (advanced), opencv hsv: h 0-180, s and v 0-255
# measured: zone h 38-43 s 84-114 v 73-87, marker s 6-18 v 188-222, bar and water s 110+ or v below 125
# the marker range is loose so the marker is still found when the translucent zone tints it
ZONE_HSV_LO, ZONE_HSV_HI = (15, 40, 40), (85, 255, 255)
MARKER_HSV_LO, MARKER_HSV_HI = (0, 0, 140), (180, 90, 255)

# integrations (see core/integrations)
DISCORD_WEBHOOK_ENABLED = False
DISCORD_WEBHOOK_URL = ""
