## Introducing
Auto-fishing macro for the roblox game Slayers2. It uses computer vision directly over the screen to track fishing progress and virtual mouse clicks to move the marker.

## Features
- Detection via `mss` + OpenCV, no injection and no memory reading.
- Works on any resolution and Windows display scaling: thresholds scale with the screen height.
- Selectable search region (`L`), saved per user in `config.json` as screen fractions.
- Smart hold: presses while the marker is below the zone, releases the moment it touches the zone.
- Automatic catch sequence, recast, and recast after 30 s without a bite.
- Always-visible status HUD in the top-left corner: uptime and the current phase.
- Debug modes: click-through overlay over the screen or a separate vision window.
- Capped vision rate: 30 FPS during the minigame, 10 FPS otherwise.

## Setup
- Download repo
- `pip install -r requirements.txt`
- `python core/main.py`
- Open the fishing minigame, press `L` and drag a box around the fishing bar (once per PC)
- Press `K` to enable/disable the bot, `ESC` to exit

## Debug
- `python core/main.py debug` or `python core/main.py debug --overlay` draws boxes over the screen without blocking input
- `python core/main.py debug --window` opens a separate window with the captured region
- Labeled boxes: orange dashed `SEARCH` - search region, violet `BAR` - bar, red `ZONE` - zone, pink `MARKER` - marker
