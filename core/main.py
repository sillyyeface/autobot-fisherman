# entry point: screen capture, input execution, region selection, overlays, status hud, and the live loop
# fishing.py contains detection, bar tracking, state logic, and webhook reporting

import argparse
import base64
import ctypes
import json
import threading
import time
from pathlib import Path
from queue import Queue

import cv2
import keyboard
import mss
import numpy as np
import pydirectinput
import tkinter as tk
import win32con
import win32gui

import fishing

# input stability settings
pydirectinput.PAUSE = 0.001
pydirectinput.FAILSAFE = False

# per-user settings such as the selected search region, stored as screen fractions
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

# vision frame rate per bot phase; only the minigame needs fast reactions
TARGET_FPS_MINIGAME = 60
TARGET_FPS_WAITING = 30
TARGET_FPS_IDLE = 30  # paused or collecting

# status hud settings in 1080p pixels, scaled by the screen height
STATUS_HUD_POSITION = (16, 16)
STATUS_HUD_PADDING = (10, 6)
STATUS_HUD_FONT_SIZE = 15
STATUS_HUD_BACKGROUND_ALPHA = 0.55

STATE_LABELS = {
    fishing.STATE_WAITING: "WAITING FOR BITE",
    fishing.STATE_MINIGAME: "CATCHING FISH",
    fishing.STATE_COLLECTING: "COLLECTING CATCH",
}

TRANSPARENT_KEY = "#000000"  # color key that becomes fully transparent in overlay windows

# debug colors stay outside the zone and marker hsv ranges, so a captured overlay is never detected
DEBUG_COLOR_SEARCH = "#3070FF"  # blue
DEBUG_COLOR_BAR = "#FF00FF"     # magenta
DEBUG_COLOR_ZONE = "#FF2020"    # red
DEBUG_COLOR_MARKER = "#00FFFF"  # cyan

DEBUG_WINDOW_TITLE = "fisherman vision"

WDA_EXCLUDEFROMCAPTURE = 0x11  # windows 10 2004+

input_action_queue = Queue()
collect_sequence_done = threading.Event()


def enable_dpi_awareness():
    # use physical pixels everywhere so capture, overlays, and clicks agree on scaled displays
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()


def safe_click():
    # click reliably with a short press duration
    pydirectinput.mouseDown()
    time.sleep(0.08)
    pydirectinput.mouseUp()


def input_command_worker():
    # execute queued input actions off the capture thread
    while True:
        input_command = input_action_queue.get()
        if input_command == "click":
            safe_click()
        elif input_command == "down":
            pydirectinput.mouseDown()
        elif input_command == "up":
            pydirectinput.mouseUp()
        elif input_command == "collect_sequence":
            pydirectinput.mouseUp()

            print("[INPUT] Waiting for the catch animation...")
            time.sleep(3.0)

            print("[INPUT] Holding T to collect the catch...")
            pydirectinput.keyDown('t')
            time.sleep(fishing.HOLD_T_DURATION)
            pydirectinput.keyUp('t')
            print("[INPUT] Catch collected.")

            fishing.send_catch_webhook_data()

            time.sleep(1.0)
            print("[INPUT] Casting the rod...")
            safe_click()
            time.sleep(1.5)  # wait for the casting animation

            print("[INPUT] Ready. Waiting for the next bite...")
            collect_sequence_done.set()  # the main loop resets the session

        input_action_queue.task_done()


# search region config
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
        return dict(fishing.DEFAULT_SEARCH_REGION)


def save_search_fractions(fractions):
    CONFIG_PATH.write_text(json.dumps({"search_region": fractions}, indent=2), encoding="utf-8")


# click-through window infrastructure shared by the debug overlay and the status hud
def exclude_from_capture(window_handle):
    # hide our own windows from mss frames so drawings never reach the detector
    ctypes.windll.user32.SetWindowDisplayAffinity(window_handle, WDA_EXCLUDEFROMCAPTURE)


def make_click_through(window_handle):
    # mouse input passes through, the window never takes focus and stays out of alt-tab
    extended_style = win32gui.GetWindowLong(window_handle, win32con.GWL_EXSTYLE)
    win32gui.SetWindowLong(
        window_handle,
        win32con.GWL_EXSTYLE,
        extended_style
        | win32con.WS_EX_LAYERED
        | win32con.WS_EX_TRANSPARENT
        | win32con.WS_EX_NOACTIVATE
        | win32con.WS_EX_TOOLWINDOW,
    )
    exclude_from_capture(window_handle)


def create_overlay_window(root_window, background, alpha=None):
    # borderless topmost click-through window; without alpha the background color is see-through
    window = tk.Toplevel(root_window)
    window.overrideredirect(True)
    window.attributes("-topmost", True)
    window.configure(bg=background)
    if alpha is None:
        window.attributes("-transparentcolor", TRANSPARENT_KEY)
    else:
        window.attributes("-alpha", alpha)
    window.geometry("1x1+0+0")
    window.update_idletasks()
    make_click_through(win32gui.GetParent(window.winfo_id()))
    return window


class StatusHUD:
    # always-visible uptime and status text in the top-left corner on a semi-transparent black plate

    def __init__(self, root_window, screen_scale, monitor):
        self.position_x = monitor["left"] + round(STATUS_HUD_POSITION[0] * screen_scale)
        self.position_y = monitor["top"] + round(STATUS_HUD_POSITION[1] * screen_scale)
        self.background = create_overlay_window(root_window, "black", alpha=STATUS_HUD_BACKGROUND_ALPHA)
        self.window = create_overlay_window(root_window, TRANSPARENT_KEY)
        self.label = tk.Label(
            self.window,
            text="",
            fg="#FFFFFF",
            bg=TRANSPARENT_KEY,
            font=("Consolas", -round(STATUS_HUD_FONT_SIZE * screen_scale), "bold"),
            justify="left",
            anchor="w",
            padx=round(STATUS_HUD_PADDING[0] * screen_scale),
            pady=round(STATUS_HUD_PADDING[1] * screen_scale),
            borderwidth=0,
            highlightthickness=0,
        )
        self.label.pack(fill="both", expand=True)
        self.current_text = None

    def update(self, text):
        # only touch the windows when the text actually changes
        if text == self.current_text:
            return
        self.current_text = text
        self.label.config(text=text)
        self.label.update_idletasks()
        geometry = f"{self.label.winfo_reqwidth()}x{self.label.winfo_reqheight()}+{self.position_x}+{self.position_y}"
        self.background.geometry(geometry)
        self.window.geometry(geometry)


class DebugOverlay:
    # click-through boxes over the screen: search region, bar, zone, and marker

    def __init__(self, root_window, search_region):
        self.window = create_overlay_window(root_window, TRANSPARENT_KEY)
        self.canvas = tk.Canvas(self.window, bg=TRANSPARENT_KEY, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.search_rect = self.canvas.create_rectangle(0, 0, 0, 0, outline=DEBUG_COLOR_SEARCH, width=2, dash=(6, 4))
        self.bar_rect = self.canvas.create_rectangle(0, 0, 0, 0, outline=DEBUG_COLOR_BAR, width=2, state="hidden")
        self.zone_rect = self.canvas.create_rectangle(0, 0, 0, 0, outline=DEBUG_COLOR_ZONE, width=2, state="hidden")
        self.marker_rect = self.canvas.create_rectangle(0, 0, 0, 0, outline=DEBUG_COLOR_MARKER, width=2, state="hidden")
        self.move_to(search_region)

    def move_to(self, search_region):
        width, height = search_region["width"], search_region["height"]
        self.window.geometry(f"{width}x{height}+{search_region['left']}+{search_region['top']}")
        self.canvas.config(width=width, height=height)
        self.canvas.coords(self.search_rect, 1, 1, width - 1, height - 1)

    def _place_box(self, canvas_item, box):
        # reuse canvas items instead of recreating them every frame
        if box is None:
            self.canvas.itemconfigure(canvas_item, state="hidden")
            return
        box_x, box_y, box_width, box_height = box
        self.canvas.coords(canvas_item, box_x, box_y, box_x + box_width, box_y + box_height)
        self.canvas.itemconfigure(canvas_item, state="normal")

    def update(self, detection_result, bar_box):
        self._place_box(self.bar_rect, bar_box)
        self._place_box(self.zone_rect, detection_result["zone"] if detection_result else None)
        self._place_box(self.marker_rect, detection_result["marker"] if detection_result else None)


def hex_to_bgr(hex_color):
    return tuple(int(hex_color[index:index + 2], 16) for index in (5, 3, 1))


def draw_debug_frame(frame_bgr, detection_result, bar_box):
    # draw the same boxes as the overlay on a copy of the captured frame
    debug_frame = frame_bgr.copy()
    frame_height, frame_width = debug_frame.shape[:2]
    cv2.rectangle(debug_frame, (0, 0), (frame_width - 1, frame_height - 1), hex_to_bgr(DEBUG_COLOR_SEARCH), 1)
    debug_boxes = [(bar_box, DEBUG_COLOR_BAR)]
    if detection_result is not None:
        debug_boxes.append((detection_result["zone"], DEBUG_COLOR_ZONE))
        debug_boxes.append((detection_result["marker"], DEBUG_COLOR_MARKER))
    for box, hex_color in debug_boxes:
        if box is None:
            continue
        box_x, box_y, box_width, box_height = box
        cv2.rectangle(debug_frame, (box_x, box_y), (box_x + box_width, box_y + box_height), hex_to_bgr(hex_color), 2)
    return debug_frame


class DebugWindow:
    # separate cv2 window with the captured search region

    def __init__(self):
        self.is_positioned = False

    def show(self, debug_frame, search_region, monitor):
        cv2.imshow(DEBUG_WINDOW_TITLE, debug_frame)
        if not self.is_positioned:
            # place the window next to the search region so it never covers the bar
            window_x = search_region["left"] - debug_frame.shape[1] - 40
            if window_x < monitor["left"]:
                window_x = search_region["left"] + search_region["width"] + 40
            cv2.moveWindow(DEBUG_WINDOW_TITLE, window_x, search_region["top"])
            window_handle = win32gui.FindWindow(None, DEBUG_WINDOW_TITLE)
            if window_handle:
                exclude_from_capture(window_handle)
            self.is_positioned = True
        cv2.waitKey(1)

    def close(self):
        cv2.destroyAllWindows()


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


def format_uptime(elapsed_seconds):
    hours, remainder = divmod(int(elapsed_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def build_status_text(elapsed_seconds, status_label, fps=None):
    # uptime on top, the current phase below, fps only in debug modes
    status_lines = [format_uptime(elapsed_seconds), status_label]
    if fps is not None:
        status_lines.append(f"{fps:.0f} FPS")
    return "\n".join(status_lines)


def target_fps_for(bot_enabled, session_state):
    if not bot_enabled:
        return TARGET_FPS_IDLE
    if session_state == fishing.STATE_MINIGAME:
        return TARGET_FPS_MINIGAME
    if session_state == fishing.STATE_WAITING:
        return TARGET_FPS_WAITING
    return TARGET_FPS_IDLE


def guarded_hotkey(requested_event, selection_active):
    # hotkeys only set flags for the main loop and are ignored while a region is being selected
    def on_hotkey():
        if not selection_active.is_set():
            requested_event.set()
    return on_hotkey


def release_mouse_if_pressing(fishing_session):
    if fishing_session.is_pressing:
        input_action_queue.put("up")
        fishing_session.is_pressing = False


def run_live(debug_mode):
    # debug_mode can be None, overlay, or window
    stop_requested = threading.Event()
    toggle_requested = threading.Event()
    selection_requested = threading.Event()
    selection_active = threading.Event()
    keyboard.add_hotkey('k', guarded_hotkey(toggle_requested, selection_active))
    keyboard.add_hotkey('l', guarded_hotkey(selection_requested, selection_active))
    keyboard.add_hotkey('esc', guarded_hotkey(stop_requested, selection_active))

    root_window = tk.Tk()
    root_window.withdraw()  # hidden container for the overlay windows

    with mss.MSS() as screen_capture:
        monitor = screen_capture.monitors[1]
        screen_scale = monitor["height"] / fishing.REFERENCE_SCREEN_HEIGHT
        detection_scale = fishing.DetectionScale(screen_scale)
        search_region = fractions_to_region(load_search_fractions(), monitor)

        status_hud = StatusHUD(root_window, screen_scale, monitor)
        debug_overlay = DebugOverlay(root_window, search_region) if debug_mode == "overlay" else None
        debug_window = DebugWindow() if debug_mode == "window" else None

        fishing_session = fishing.FishingSession()
        bar_tracker = fishing.BarTracker()
        bot_enabled = False

        start_time = time.perf_counter()
        previous_frame_time = start_time
        fps = 0.0

        print(f"Screen: {monitor['width']}x{monitor['height']}, search region: {search_region}")
        print("Bot ready. K - start/pause, L - select search region, ESC - exit.")

        try:
            while not stop_requested.is_set():
                frame_start_time = time.perf_counter()

                if selection_requested.is_set():
                    selection_requested.clear()
                    if bot_enabled:
                        bot_enabled = False
                        release_mouse_if_pressing(fishing_session)
                        print("[BOT] Paused for region selection.")
                    status_hud.update(build_status_text(frame_start_time - start_time, "SELECTING REGION"))
                    selection_active.set()
                    try:
                        selected_region = select_search_region(root_window, screen_capture, monitor, screen_scale)
                    finally:
                        selection_active.clear()
                    if selected_region is not None:
                        search_region = selected_region
                        save_search_fractions(region_to_fractions(search_region, monitor))
                        bar_tracker.reset()
                        if debug_overlay is not None:
                            debug_overlay.move_to(search_region)
                        if debug_window is not None:
                            debug_window.close()
                            debug_window.is_positioned = False
                        print(f"[BOT] New search region saved: {search_region}")
                    continue

                if toggle_requested.is_set():
                    toggle_requested.clear()
                    bot_enabled = not bot_enabled
                    print(f"\n[BOT] Status: {'ENABLED' if bot_enabled else 'PAUSED'}")
                    if bot_enabled:
                        fishing_session.reset(frame_start_time)
                        bar_tracker.reset()
                        input_action_queue.put("click")
                    else:
                        release_mouse_if_pressing(fishing_session)

                if collect_sequence_done.is_set():
                    collect_sequence_done.clear()
                    if bot_enabled and fishing_session.current_state == fishing.STATE_COLLECTING:
                        fishing_session.reset(frame_start_time)

                frame_bgr = cv2.cvtColor(np.asarray(screen_capture.grab(search_region)), cv2.COLOR_BGRA2BGR)
                detection_result = fishing.analyze_frame(frame_bgr, detection_scale, bar_tracker.bar_column)
                bar_tracker.update(detection_result, frame_start_time)

                if bot_enabled:
                    for action in fishing_session.update(detection_result, frame_start_time):
                        input_action_queue.put(action)

                fps = 0.9 * fps + 0.1 / max(frame_start_time - previous_frame_time, 1e-6)
                previous_frame_time = frame_start_time

                status_label = STATE_LABELS.get(fishing_session.current_state, "IDLE") if bot_enabled else "PAUSED"
                status_hud.update(build_status_text(
                    frame_start_time - start_time, status_label, fps if debug_mode is not None else None
                ))

                if debug_overlay is not None:
                    debug_overlay.update(detection_result, bar_tracker.bar_box())
                elif debug_window is not None:
                    debug_frame = draw_debug_frame(frame_bgr, detection_result, bar_tracker.bar_box())
                    debug_window.show(debug_frame, search_region, monitor)

                root_window.update()

                # sleep the rest of the frame budget instead of spinning at max fps
                frame_budget = 1.0 / target_fps_for(bot_enabled, fishing_session.current_state)
                remaining_time = frame_budget - (time.perf_counter() - frame_start_time)
                if remaining_time > 0:
                    time.sleep(remaining_time)
        finally:
            pydirectinput.mouseUp()
            pydirectinput.keyUp('t')
            keyboard.unhook_all()
            if debug_window is not None:
                debug_window.close()
            root_window.destroy()


def parse_args():
    parser = argparse.ArgumentParser(description="Slayers2 auto-fishing bot")
    subparsers = parser.add_subparsers(dest="command")
    debug_parser = subparsers.add_parser(
        "debug", help="show the search region, bar, zone, and marker (overlay by default)"
    )
    debug_mode_group = debug_parser.add_mutually_exclusive_group()
    debug_mode_group.add_argument(
        "--overlay", action="store_true", help="draw click-through boxes directly over the screen (default)"
    )
    debug_mode_group.add_argument(
        "--window", action="store_true", help="open a separate window with the captured search region"
    )
    return parser.parse_args()


def resolve_debug_mode(command_line_args):
    if command_line_args.command != "debug":
        return None
    return "window" if command_line_args.window else "overlay"


if __name__ == "__main__":
    command_line_args = parse_args()
    enable_dpi_awareness()
    threading.Thread(target=input_command_worker, daemon=True).start()
    try:
        run_live(resolve_debug_mode(command_line_args))
    except Exception as error:
        print(f"\nRuntime error: {error}")
        input()
