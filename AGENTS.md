## Project overview
- This repository is a Windows-only desktop automation script for the Roblox Slayers2 fishing minigame.
- `core/main.py` owns screen capture, input execution, region selection, overlays, the status HUD, and the live loop.
- `core/fishing.py` owns HSV detection, bar tracking, the fishing state machine, and the webhook stub.
- `requirements.txt` lists the runtime dependencies: OpenCV, NumPy, pydirectinput, keyboard, mss, and pywin32.
- The bot starts paused, uses `K` to toggle fishing, `L` to select the search region, and `ESC` to exit.
- Debug modes: `main.py debug [--overlay]` for click-through boxes and `main.py debug --window` for a cv2 preview window.
- The process is DPI aware; all coordinates are physical pixels. Pixel constants in `fishing.py` are 1080p reference values scaled by `DetectionScale`.
- The search region is stored in the git-ignored `config.json` as monitor fractions; only the primary monitor is supported.
- The marker is searched only inside the bar column derived from the zone; `BarTracker` keeps that column for `BAR_FORGET_TIMEOUT` when the zone flickers.
- `FishingSession.should_press` holds while the predicted marker is below the predicted zone center; `PREDICTION_TIME` is the main tuning knob and should stay validated against real gameplay.
- Overlay windows are click-through, non-activating, and excluded from screen capture; debug colors stay outside the detection HSV ranges (no yellow, green, or white).
- The status HUD sits below the Roblox top bar (`STATUS_HUD_POSITION`, 1080p pixels).
- Hotkey callbacks only set events; all state changes happen in the main loop thread.
- Vision runs at `TARGET_FPS_MINIGAME` during the minigame and `TARGET_FPS_WAITING` / `TARGET_FPS_IDLE` otherwise.

## Coding conventions
- Keep changes small and targeted. This project is a focused bot script, not a broad framework.
- Preserve Windows-specific behavior; `pydirectinput`, `keyboard`, and `mss` are core dependencies and should remain compatible with the current runtime assumptions.
- Prefer local, surgical edits around the existing detection pipeline and input loop. Avoid large refactors unless required for an explicit fix.
- Keep all user-facing output, command-line help, status labels, and error messages in English.
- Keep all code comments in English, lowercase, concise, and written with `#` comments only.
- Remove redundant comments and decorative separator characters; comment only when behavior is not self-explanatory.
- Use brief lowercase English comments for important functions, variables, and non-obvious logic.

### Naming
- Use descriptive `snake_case` names for functions, variables, parameters, and local data.
- Avoid unclear abbreviations such as `res`, `out`, `cnts`, `c`, `m_y`, or `z_bot`; prefer names that describe the value's role.
- Name image data by color space or purpose, such as `frame_bgr`, `hsv_frame`, and `debug_frame`.
- Name detection dictionaries and geometry values explicitly, such as `detection_result`, `marker_center_y`, and `zone_bottom`.
- Name queues, workers, and callbacks after their responsibility, such as `input_action_queue` and `input_command_worker`.
- Keep established state constants and action strings unchanged unless their external contract changes.

## Working style
- Favor explicit helper functions and readable variable names over clever shortcuts.
- Keep the main capture loop responsive and non-blocking. Avoid introducing long sleeps or heavy processing in the live frame path.
- Preserve the current state names and action strings unless a behavior change requires updating their callers.
- Do not change capture coordinates, timing constants, or input semantics without a focused reason and validation.