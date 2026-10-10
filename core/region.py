# screen regions (minigame search and catch notification): fraction conversion, config.json storage,
# and the selection screen shown on every launch

import base64
import json
from pathlib import Path

import cv2
import numpy as np
import tkinter as tk

import settings

# per-user settings such as the selected regions, stored as screen fractions
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

# config.json keys
SEARCH_REGION_KEY = "search_region"
NOTIFICATION_REGION_KEY = "notification_region"

SELECTION_COLOR = "#FFFFFF"
SAVED_REGION_COLOR = "#FF6000"


def region_to_fractions(screen_region, monitor):
    return {
        "left": (screen_region["left"] - monitor["left"]) / monitor["width"],
        "top": (screen_region["top"] - monitor["top"]) / monitor["height"],
        "width": screen_region["width"] / monitor["width"],
        "height": screen_region["height"] / monitor["height"],
    }


def fractions_to_region(fractions, monitor):
    # convert screen fractions to an absolute pixel region clamped to the monitor
    left = min(max(0, round(fractions["left"] * monitor["width"])), monitor["width"] - 1)
    top = min(max(0, round(fractions["top"] * monitor["height"])), monitor["height"] - 1)
    width = min(max(1, round(fractions["width"] * monitor["width"])), monitor["width"] - left)
    height = min(max(1, round(fractions["height"] * monitor["height"])), monitor["height"] - top)
    return {"left": monitor["left"] + left, "top": monitor["top"] + top, "width": width, "height": height}


def load_config():
    try:
        stored_config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return stored_config if isinstance(stored_config, dict) else {}
    except (OSError, ValueError):
        return {}


def load_region_fractions(region_key, default_fractions):
    try:
        stored_fractions = load_config()[region_key]
        return {key: float(stored_fractions[key]) for key in ("left", "top", "width", "height")}
    except (KeyError, ValueError, TypeError):
        return dict(default_fractions)


def save_region_fractions(region_key, fractions):
    # keep the other stored regions
    stored_config = load_config()
    stored_config[region_key] = fractions
    CONFIG_PATH.write_text(json.dumps(stored_config, indent=2), encoding="utf-8")


def capture_dimmed_screenshot(screen_capture, monitor):
    # frozen, darkened copy of the screen used as the selection background
    screenshot_bgr = cv2.cvtColor(np.asarray(screen_capture.grab(monitor)), cv2.COLOR_BGRA2BGR)
    dimmed_screenshot = cv2.convertScaleAbs(screenshot_bgr, alpha=0.65)
    _, png_bytes = cv2.imencode(".png", dimmed_screenshot, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    return tk.PhotoImage(data=base64.b64encode(png_bytes.tobytes()).decode("ascii"))


def select_region(root_window, screenshot_image, monitor, screen_scale, current_region, instruction_text):
    # let the user drag a new box over the frozen screen; enter, esc, or right click keep the current one
    monitor_width, monitor_height = monitor["width"], monitor["height"]
    window = tk.Toplevel(root_window)
    window.overrideredirect(True)
    window.attributes("-topmost", True)
    window.geometry(f"{monitor_width}x{monitor_height}+{monitor['left']}+{monitor['top']}")
    canvas = tk.Canvas(
        window, width=monitor_width, height=monitor_height, highlightthickness=0, cursor="crosshair"
    )
    canvas.pack()
    canvas.create_image(0, 0, anchor="nw", image=screenshot_image)
    canvas.create_text(
        monitor_width // 2,
        round(24 * screen_scale),
        anchor="n",
        text=f"{instruction_text}\nEnter, Esc, or right click keeps the orange box.",
        fill="#FFFFFF",
        justify="center",
        font=("Segoe UI", -round(20 * screen_scale), "bold"),
    )
    current_left = current_region["left"] - monitor["left"]
    current_top = current_region["top"] - monitor["top"]
    canvas.create_rectangle(
        current_left, current_top,
        current_left + current_region["width"], current_top + current_region["height"],
        outline=SAVED_REGION_COLOR, width=2, dash=(6, 4),
    )
    selection_rect = canvas.create_rectangle(0, 0, 0, 0, outline=SELECTION_COLOR, width=2, state="hidden")

    drag_start = {}
    selection_result = {}

    def on_press(event):
        drag_start["x"], drag_start["y"] = event.x, event.y
        canvas.coords(selection_rect, event.x, event.y, event.x, event.y)
        canvas.itemconfigure(selection_rect, state="normal")

    def on_drag(event):
        if drag_start:
            canvas.coords(selection_rect, drag_start["x"], drag_start["y"], event.x, event.y)

    def on_release(event):
        if not drag_start:
            return
        left, right = sorted((drag_start["x"], event.x))
        top, bottom = sorted((drag_start["y"], event.y))
        drag_start.clear()
        # ignore accidental clicks and tiny boxes
        if right - left < 10 or bottom - top < 10:
            canvas.itemconfigure(selection_rect, state="hidden")
            return
        selection_result["region"] = {
            "left": monitor["left"] + left,
            "top": monitor["top"] + top,
            "width": right - left,
            "height": bottom - top,
        }
        window.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    canvas.bind("<ButtonPress-3>", lambda event: window.destroy())
    window.bind("<Escape>", lambda event: window.destroy())
    window.bind("<Return>", lambda event: window.destroy())

    window.focus_force()
    window.grab_set()
    root_window.wait_window(window)
    return selection_result.get("region", current_region)


# startup selection steps: config key, default fractions, instruction
STARTUP_REGION_STEPS = (
    (SEARCH_REGION_KEY, settings.DEFAULT_SEARCH_REGION, "Step 1/2: drag a box around the fishing minigame bar."),
    (
        NOTIFICATION_REGION_KEY,
        settings.DEFAULT_NOTIFICATION_REGION,
        "Step 2/2: drag a box where the catch notification appears (item icon, name, and x1).",
    ),
)


def select_startup_regions(root_window, screen_capture, monitor, screen_scale):
    # one frozen screenshot for both steps; each step starts from the saved region and stores the result
    screenshot_image = capture_dimmed_screenshot(screen_capture, monitor)
    selected_regions = {}
    for region_key, default_fractions, instruction_text in STARTUP_REGION_STEPS:
        current_region = fractions_to_region(load_region_fractions(region_key, default_fractions), monitor)
        selected_region = select_region(
            root_window, screenshot_image, monitor, screen_scale, current_region, instruction_text
        )
        save_region_fractions(region_key, region_to_fractions(selected_region, monitor))
        selected_regions[region_key] = selected_region
    return selected_regions[SEARCH_REGION_KEY], selected_regions[NOTIFICATION_REGION_KEY]
