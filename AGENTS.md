## Project overview
- This repository is a Windows-only desktop automation script for a fishing minigame.
- The code using OpenCV, screen capture, keyboard hooks, and direct mouse input.

## Coding conventions
- Keep changes small and targeted. This project is a focused bot script, not a broad framework.
- Preserve Windows-specific behavior; `pydirectinput`, `keyboard`, and `mss` are core dependencies and should remain compatible with the current runtime assumptions.
- Prefer local, surgical edits around the existing detection pipeline and input loop. Avoid large refactors unless required for an explicit fix.

## Working style
- Favor explicit helper functions and readable variable names over clever shortcuts.
- Keep the main capture loop responsive and non-blocking. Avoid introducing long sleeps or heavy processing in the live frame path.
- Leave brief explanations in English for all important functions and variables (using lowercase and no unnecessary characters).