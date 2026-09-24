## Project overview
- This repository is a Windows-only desktop automation script for the Roblox Slayers2 fishing minigame.
- `core/main.py` owns screen capture, input execution, overlays, the status HUD, and the live loop.
- `core/fishing.py` owns HSV detection, the fishing state machine, and the webhook stub.
- `requirements.txt` lists the runtime dependencies: OpenCV, NumPy, pydirectinput, keyboard, mss, and pywin32.
- The bot starts paused, uses `K` to toggle fishing, and uses `ESC` to exit.
- Debug modes are `overlay` for click-through boxes and `window` for a cv2 preview window.

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