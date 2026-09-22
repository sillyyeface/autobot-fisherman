## Introducing
Auto-fishing macro for the roblox game Slayers2. It uses computer vision directly over the screen to track fishing progress and virtual mouse clicks to move the marker.

## Features
- Detection via `mss` + OpenCV, no injection and no memory reading.
- Smart hold: presses while the marker is below the zone, releases the moment it touches the zone.
- Automatic catch sequence and recast the rod.
- Hotkey to start / pause the bot at any time.
- Debug mode with drawn boxes (track / zone / marker) and an FPS counter.

## Setup
- Download repo
- Install requirements
- Run python script
- Press `K` to enable/disable bot