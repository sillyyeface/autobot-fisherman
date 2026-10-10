## Introducing
Auto-fishing macro for the Roblox game Slayers2. It watches a selected part of the screen with computer vision, plays the fishing minigame with virtual mouse input, and repeats the whole loop: cast, wait for a bite, catch, collect, recast.

## Features
- Detection via `mss` + OpenCV, no injection and no memory reading.
- Works on any resolution and Windows display scaling: detection thresholds scale with the screen height.
- Selectable search region (`L`), saved per user in `config.json` as screen fractions.
- Predictive marker control: keeps the marker in the center of the zone using marker and zone speed.
- Automatic catch sequence and recast, plus a recast after 30 s without a bite.
- Always-visible status HUD in the top-left corner: uptime on top, the current phase below.
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
3. Press `K` again to pause, `ESC` to exit.

## Hotkeys
| Key | Action |
|---|---|
| `K` | Start / pause the bot |
| `L` | Select the search region (pauses the bot) |
| `ESC` | Exit |

In the region selection screen: drag with the left mouse button to select, release to save, `ESC` or right click to cancel. The hotkeys are global, so they also fire while typing in the Roblox chat.

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
4. `core/main.py` runs the input actions on a worker thread, so the capture loop never blocks.

## Tuning
Constants at the top of `core/fishing.py`:
- `PREDICTION_TIME` (0.2 s): raise to 0.25-0.3 if the marker overshoots the zone up and down, lower to 0.15 if it reacts late.
- `ZONE_TARGET_OFFSET` (0.0): move the target point inside the zone, positive is lower.
- `ZONE_HSV_*`, `MARKER_HSV_*`: color ranges of the zone and the marker.
- `WAITING_TIMEOUT`, `HOLD_T_DURATION`, `DISAPPEAR_TIMEOUT`: fishing loop timings.

Constants at the top of `core/main.py`: `TARGET_FPS_*` for the vision rate and `STATUS_HUD_*` for the HUD position and look.

## Project structure
```
core/main.py      capture, input, region selection, overlays, status HUD, live loop
core/fishing.py   detection, bar tracking, state machine, webhook stub
config.json       per-user search region (created by L, git-ignored)
```
