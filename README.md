## Introducing
Auto-fishing macro for the Roblox game Slayers2. It watches a selected part of the screen with computer vision, plays the fishing minigame with virtual mouse input, and repeats the whole loop: cast, wait for a bite, catch, collect, recast.

## Features
- Detection via `mss` + OpenCV, no injection and no memory reading.
- Works on any resolution and Windows display scaling: detection thresholds scale with the screen height.
- Selectable search region (`L`), saved per user in `config.json` as screen fractions.
- Predictive marker control: keeps the marker in the center of the zone using marker and zone speed.
- Automatic catch sequence and recast, plus a recast after 30 s without a bite.
- Always-visible status HUD in the top-left corner: run time since start on top, the current phase below.
- All tunables in one file: `core/settings.py`.
- Event hub for integrations (Discord webhook stub included, Rich Presence and others can be added).
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
1. Open the fishing minigame, press `L`, and drag a box around the fishing bar. This is needed once per PC; the region is saved to `config.json`.
2. Press `K` to start fishing. The bot casts the rod right away.
3. Press `K` again to stop: all input is released at once and the timer and phase reset. The next `K` starts from scratch.
4. Press `ESC` to exit.

## Hotkeys
| Key | Action |
|---|---|
| `K` | Start / stop the bot (stop resets the timer and the phase) |
| `L` | Select the search region (stops the bot) |
| `ESC` | Exit |

In the region selection screen: drag with the left mouse button to select, release to save, `ESC` or right click to cancel. The hotkeys are global, so they also fire while typing in the Roblox chat. Rebind them in `core/settings.py`.

## Debug
```bash
python core/main.py debug
python core/main.py debug --overlay
python core/main.py debug --window
```
- `debug` and `debug --overlay` draw boxes directly over the screen. The overlay never takes focus and lets every click pass through.
- `debug --window` opens a separate window with the captured region. Keep the mouse cursor off this window while fishing, otherwise the bot clicks into it.
- Boxes: orange dashed `SEARCH` - search region, violet `BAR` - bar, red `ZONE` - zone, pink `MARKER` - marker.
- The status HUD shows an extra FPS line in debug modes.

## How it works
1. The search region is captured and converted to HSV.
2. The yellow-green zone is searched in the whole region. The bar is the vertical column around the zone; the white marker is searched only inside that column, so white text around the bar is ignored.
3. The state machine in `core/fishing.py` moves through `WAITING` -> `MINIGAME` -> `COLLECTING`. During the minigame it holds the mouse while the predicted marker position is below the predicted zone center.
4. `core/input_control.py` runs the input actions on a worker thread, so the capture loop never blocks. Stopping the bot cancels queued actions and interrupts the collect sequence.

## Settings
Everything you would normally tune is in `core/settings.py`:
- hotkeys: `HOTKEY_TOGGLE`, `HOTKEY_SELECT_REGION`, `HOTKEY_EXIT`
- fishing timings: `HOLD_T_DURATION`, `DISAPPEAR_TIMEOUT`, `WAITING_TIMEOUT`
- marker control: `PREDICTION_TIME` (0.2 s) - raise to 0.25-0.3 if the marker overshoots the zone up and down, lower to 0.15 if it reacts late; `ZONE_TARGET_OFFSET` moves the target point inside the zone, positive is lower
- vision rate: `TARGET_FPS_MINIGAME`, `TARGET_FPS_WAITING`, `TARGET_FPS_IDLE`
- status HUD: `STATUS_HUD_*`
- detection colors (advanced): `ZONE_HSV_*`, `MARKER_HSV_*`
- integrations: `DISCORD_WEBHOOK_ENABLED`, `DISCORD_WEBHOOK_URL`

The search region is not set here: press `L` in the bot, it is saved to `config.json`.

## Integrations
`core/events.py` publishes bot events; handlers run on a background thread, so a slow network call never stalls the bot.

| Event | Data |
|---|---|
| `bot_started` | - |
| `bot_stopped` | `run_time` (seconds) |
| `status_changed` | `status` (HUD label) |
| `catch_collected` | `catch_count` (this run) |

To add an integration, create `core/integrations/<name>.py` with a `register()` function that calls `events.subscribe(...)`, add an enable flag to `settings.py`, and register it in `core/integrations/__init__.py`. `discord_webhook.py` is a stub that only prints for now.

## Project structure
```
core/main.py            command line, hotkeys, live loop
core/settings.py        user-tunable settings
core/vision.py          hsv detection, bar tracking
core/fishing.py         fishing state machine
core/input_control.py   input worker, collect sequence, cancellation
core/region.py          search region storage and selection screen
core/overlays.py        status HUD, debug overlay and window
core/events.py          event hub for integrations
core/integrations/      optional integrations (discord webhook stub)
config.json             per-user search region (created by L, git-ignored)
```
