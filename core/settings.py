# user-tunable settings in one place; the minigame and notification regions are picked on every launch
# and stored per user in config.json

# hotkey (keyboard library name) that starts and stops the bot; it is global and also fires in the roblox chat
HOTKEY_TOGGLE = "k"

# fishing loop timings in seconds
HOLD_T_DURATION = 8.0      # hold t while collecting the catch
DISAPPEAR_TIMEOUT = 0.35   # bar must be missing this long before the minigame counts as finished
WAITING_TIMEOUT = 60.0     # recast the rod after this long without a bite
MINIGAME_TIMEOUT = 30.0    # a minigame never lasts longer; after this the bot collects and recasts

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

# minigame search region used until one is selected, as screen fractions (the original 1920x1080 capture box)
DEFAULT_SEARCH_REGION = {
    "left": 1380 / 1920,
    "top": 280 / 1080,
    "width": 180 / 1920,
    "height": 480 / 1080,
}
# notification region used until one is selected: the "item x1" popup right of the character
DEFAULT_NOTIFICATION_REGION = {
    "left": 0.505,
    "top": 0.5,
    "width": 0.11,
    "height": 0.065,
}

# catch confirmation: the "item x1" notification is read with windows ocr after each minigame
CATCH_WATCH_TIMEOUT = 20.0    # seconds after the minigame to wait for the notification, then the catch counts as missed
CATCH_READ_INTERVAL = 0.25    # seconds between ocr reads of the notification region
CATCH_CONFIRM_READS = 2       # the same name must be read this many times in a row
CATCH_OCR_LANGUAGE = "en-US"  # falls back to the windows profile languages when not installed
CATCH_SAVE_SNAPSHOTS = True   # save each confirmed notification to the catches folder

# detection colors (advanced), opencv hsv: h 0-180, s and v 0-255
# measured: zone h 38-43 s 84-114 v 73-87, marker s 6-18 v 188-222, bar and water s 110+ or v below 125
# the marker range is loose so the marker is still found when the translucent zone tints it
ZONE_HSV_LO, ZONE_HSV_HI = (15, 40, 40), (85, 255, 255)
MARKER_HSV_LO, MARKER_HSV_HI = (0, 0, 140), (180, 90, 255)
# white notification text; everything else is blanked before ocr
CATCH_TEXT_HSV_LO, CATCH_TEXT_HSV_HI = (0, 0, 170), (180, 70, 255)

# integrations (see core/integrations)
# discord: the webhook url is stored in the git-ignored config.json as discord_webhook_url
# (channel settings > integrations > webhooks > copy webhook url); message formats are in discord_messages.json
DISCORD_WEBHOOK_ENABLED = True  # send notifications to discord when a webhook url is configured
DISCORD_SEND_SNAPSHOT = True  # attach the notification snapshot where a message uses {snapshot}
