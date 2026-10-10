## Introducing
Auto-fishing macro for the Roblox game Slayers2. It watches a selected part of the screen with computer vision, plays the fishing minigame with virtual mouse input, and repeats the whole loop: cast, wait for a bite, catch, collect, recast. After every catch it reads the in-game notification to confirm what was caught.

## Features
- Detection via `mss` + OpenCV, no injection and no memory reading.
- Works on any resolution and Windows display scaling: detection thresholds scale with the screen height.
- Minigame and notification regions are confirmed or reselected on every launch and saved in `config.json` as screen fractions.
- Catch confirmation: the `Coral x1` notification is read with the built-in Windows OCR, so the bot knows whether something was caught and what.
- Predictive marker control: keeps the marker in the center of the zone using marker and zone speed.
- Automatic catch sequence and recast, plus a recast after 60 s without a bite.
- Hang protection: a minigame that lasts over 30 s is treated as stuck, the bot collects and recasts.
- Always-visible status HUD in the top-left corner: run time since start, the current phase, and the last confirmed catch.
- All tunables in one file: `core/settings.py`.
- Discord rich presence (run timer, status, confirmed catches).
- Discord webhook notifications with your own message and embed format (`discord_messages.json`), including the catch snapshot.
- Event hub for integrations (Rich Presence and others can be added).
- Debug modes: labeled click-through overlay over the screen or a separate vision window.
- Capped vision rate: 60 FPS during the minigame, 30 FPS otherwise.

## Requirements
- Windows 10 2004 or newer (overlays are hidden from screen capture with `WDA_EXCLUDEFROMCAPTURE`).
- Python 3.10+ (tested on 3.13).
- Roblox on the primary monitor.

## Setup
```bash
pip install -r requirements.txt
python core/main.py
```
1. On launch the screen freezes for two selection steps. The saved region (or the default) is shown as an orange dashed box.
   - Step 1: drag a box around the fishing minigame bar.
   - Step 2: drag a box where the catch notification appears (item icon, name, and `x1`, right of the character).
   - Press `Enter`, `Esc`, or right click to keep the orange box. Both regions are saved to `config.json`.
2. Press `K` to start fishing. The bot casts the rod right away.
3. Press `K` again to stop: all input is released at once and the timer and phase reset. The next `K` starts from scratch.
4. Press `Ctrl+C` in the console or close it to exit.

## Hotkeys
| Key | Action |
|---|---|
| `K` | Start / stop the bot (stop resets the timer and the phase) |

`K` is the only hotkey. It is global, so it also fires while typing in the Roblox chat; rebind it with `HOTKEY_TOGGLE` in `core/settings.py`. The regions are only selected on launch, restart the bot to change them.

## Debug
```bash
python core/main.py debug
python core/main.py debug --overlay
python core/main.py debug --window
```
- `debug` and `debug --overlay` draw boxes directly over the screen. The overlay never takes focus and lets every click pass through.
- `debug --window` opens two separate windows with the captured minigame and notification regions. Keep the mouse cursor off these windows while fishing, otherwise the bot clicks into them.
- Boxes: orange dashed `SEARCH` - search region, violet `BAR` - bar, red `ZONE` - zone, pink `MARKER` - marker, cyan dashed `NOTIFY` - notification region. The `NOTIFY` label shows the OCR text while reading and the last confirmed catch afterwards.
- The status HUD shows an extra FPS line in debug modes.

## How it works
1. The search region is captured and converted to HSV.
2. The yellow-green zone is searched in the whole region. The bar is the vertical column around the zone; the white marker is searched only inside that column, so white text around the bar is ignored.
3. The state machine in `core/fishing.py` moves through `WAITING` -> `MINIGAME` -> `COLLECTING`. During the minigame it holds the mouse while the predicted marker position is below the predicted zone center.
4. `core/input_control.py` runs the input actions on a worker thread, so the capture loop never blocks. Stopping the bot cancels queued actions and interrupts the collect sequence.
5. When the minigame ends, `core/catch_detection.py` reads the notification region every 0.25 s with Windows OCR on a worker thread. Only white text is kept before OCR, so the water and the character behind it are ignored. The same name read twice in a row confirms the catch; no name within 20 s counts as a missed catch. Confirmed notifications are saved to `catches/`.

## Settings
Everything you would normally tune is in `core/settings.py`:
- hotkey: `HOTKEY_TOGGLE`
- fishing timings: `HOLD_T_DURATION`, `DISAPPEAR_TIMEOUT`, `WAITING_TIMEOUT` (60 s), `MINIGAME_TIMEOUT` (30 s)
- marker control: `PREDICTION_TIME` (0.2 s) - raise to 0.25-0.3 if the marker overshoots the zone up and down, lower to 0.15 if it reacts late; `ZONE_TARGET_OFFSET` moves the target point inside the zone, positive is lower
- vision rate: `TARGET_FPS_MINIGAME`, `TARGET_FPS_WAITING`, `TARGET_FPS_IDLE`
- status HUD: `STATUS_HUD_*`
- catch confirmation: `CATCH_WATCH_TIMEOUT`, `CATCH_READ_INTERVAL`, `CATCH_CONFIRM_READS`, `CATCH_OCR_LANGUAGE`, `CATCH_SAVE_SNAPSHOTS`
- detection colors (advanced): `ZONE_HSV_*`, `MARKER_HSV_*`, `CATCH_TEXT_HSV_*` (notification text)
- integrations: `DISCORD_WEBHOOK_ENABLED`, `DISCORD_SEND_SNAPSHOT` (the webhook url itself is in `config.json`), `DISCORD_RPC_ENABLED`, `DISCORD_RPC_CLIENT_ID`

The regions are not set here: they are selected on launch and saved to `config.json`. `DEFAULT_SEARCH_REGION` and `DEFAULT_NOTIFICATION_REGION` are only the starting boxes.

## Integrations
`core/events.py` publishes bot events; handlers run on a background thread, so a slow network call never stalls the bot.

| Event | Data |
|---|---|
| `bot_started` | - |
| `bot_stopped` | `run_time` (seconds) |
| `status_changed` | `status` (HUD label) |
| `catch_collected` | `catch_count` (this run) |
| `catch_confirmed` | `catch_name`, `catch_count` (the `x1` amount), `catch_number` (this run), `snapshot_path` (or `None`) |
| `catch_missed` | - |

To add an integration, create `core/integrations/<name>.py` with a `register()` function that calls `events.subscribe(...)`, add an enable flag to `settings.py`, and register it in `core/integrations/__init__.py`. 
### Discord rich presence
Shows the run timer, the current status, and the confirmed catches of this run on your Discord profile.
1. Create an application at discord.com/developers/applications; its name is shown as the game name.
2. Copy the Application ID into `DISCORD_RPC_CLIENT_ID` in `core/settings.py` and keep `DISCORD_RPC_ENABLED = True`.
3. Run with the Discord desktop client open (if it is closed, the bot keeps retrying). Discord accepts one update per 15 s, so status changes are merged. Stopping the bot removes the timer.

### Discord webhook
1. In Discord: channel settings > Integrations > Webhooks > New Webhook > Copy Webhook URL.
2. In `core/settings.py` set `DISCORD_WEBHOOK_ENABLED = True`. Paste the URL into `discord_webhook_url` in `config.json` in the project root (the key is added empty on the first run with the webhook enabled). `config.json` is git-ignored, because anyone with the URL can post to the channel:
   ```json
   "discord_webhook_url": "https://discord.com/api/webhooks/..."
   ```
3. Edit `discord_messages.json` in the project root. Each key is an event (`catch_confirmed`, `catch_missed`, `bot_started`, `bot_stopped`) and its value is a regular Discord webhook payload (`content`, `username`, `avatar_url`, `embeds`). Remove a key to stop sending that event. The file is read on every message, so edits apply without a restart.

Placeholders work in any text value:

| Placeholder | Value |
|---|---|
| `{catch_name}`, `{catch_count}`, `{catch_number}` | catch name, the `x1` amount, catch number this run (`catch_confirmed` only) |
| `{snapshot}` | the notification snapshot as an attachment, use as an image or thumbnail url (`catch_confirmed` only) |
| `{confirmed_total}`, `{missed_total}` | confirmed and missed catches this run |
| `{run_time}` | run time `HH:MM:SS` |
| `{time}` | local time `HH:MM:SS` |
| `{timestamp}` | ISO time for the embed `timestamp` field |

An easy way to design the embed is a visual editor such as discohook.org: build the message there, copy its JSON, and put it under the event key.

## Project structure
```
core/main.py            command line, startup selection, hotkey, live loop
core/settings.py        user-tunable settings
core/vision.py          hsv detection, bar tracking
core/fishing.py         fishing state machine
core/input_control.py   input worker, collect sequence, cancellation
core/region.py          region storage and the startup selection screen
core/catch_detection.py catch notification ocr and confirmation
core/overlays.py        status HUD, debug overlay and window
core/events.py          event hub for integrations
core/integrations/      optional integrations (discord webhook, discord rich presence)
config.json             per-user regions and discord webhook url (git-ignored)
catches/                snapshots of confirmed catch notifications (git-ignored)
discord_messages.json   discord message formats per event
```
