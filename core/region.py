# search region: screen fraction conversion, config.json storage, and the selection screen

import base64
import json
from pathlib import Path

import cv2
import numpy as np
import tkinter as tk

import settings

# per-user settings such as the selected search region, stored as screen fractions
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


def region_to_fractions(search_region, monitor):
    return {
        "left": (search_region["left"] - monitor["left"]) / monitor["width"],
        "top": (search_region["top"] - monitor["top"]) / monitor["height"],
        "width": search_region["width"] / monitor["width"],
        "height": search_region["height"] / monitor["height"],
    }


def fractions_to_region(fractions, monitor):
    # convert screen fractions to an absolute pixel region clamped to the monitor
    left = min(max(0, round(fractions["left"] * monitor["width"])), monitor["width"] - 1)
    top = min(max(0, round(fractions["top"] * monitor["height"])), monitor["height"] - 1)
    width = min(max(1, round(fractions["width"] * monitor["width"])), monitor["width"] - left)
    height = min(max(1, round(fractions["height"] * monitor["height"])), monitor["height"] - top)
    return {"left": monitor["left"] + left, "top": monitor["top"] + top, "width": width, "height": height}


def load_search_fractions():
    try:
        stored_fractions = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["search_region"]
        return {key: float(stored_fractions[key]) for key in ("left", "top", "width", "height")}
    except (OSError, ValueError, KeyError, TypeError):
        return dict(settings.DEFAULT_SEARCH_REGION)


def save_search_fractions(fractions):
    CONFIG_PATH.write_text(json.dumps({"search_region": fractions}, indent=2), encoding="utf-8")


def select_search_region(root_window, screen_capture, monitor, screen_scale):
    # freeze the screen and let the user drag a box around the fishing bar
    screenshot_bgr = cv2.cvtColor(np.asarray(screen_capture.grab(monitor)), cv2.COLOR_BGRA2BGR)
    dimmed_screenshot = cv2.convertScaleAbs(screenshot_bgr, alpha=0.65)
    _, png_bytes = cv2.imencode(".png", dimmed_screenshot, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    screenshot_image = tk.PhotoImage(data=base64.b64encode(png_bytes.tobytes()).decode("ascii"))

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
        round(40 * screen_scale),
        text="Drag a box around the fishing bar. Esc or right click cancels.",
        fill="#FFFFFF",
        font=("Segoe UI", -round(20 * screen_scale), "bold"),
    )
    selection_rect = canvas.create_rectangle(0, 0, 0, 0, outline="#FFFFFF", width=2, state="hidden")

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

    window.focus_force()
    window.grab_set()
    root_window.wait_window(window)
    return selection_result.get("region")
