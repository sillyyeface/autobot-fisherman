# main entry point for input execution, capture, processing, debug visualization,
# and top-level settings. fishing.py contains detection, state logic, and webhook reporting.
# pywin32 provides click-through overlay support through win32gui and win32con.

import argparse
import time
import threading
from queue import Queue

import cv2
import numpy as np
import pydirectinput
import keyboard
import mss
import tkinter as tk
import win32gui
import win32con

import fishing

# input stability settings
pydirectinput.PAUSE = 0.001
pydirectinput.FAILSAFE = False

# main bot settings
AUTOCLICKER_ENABLED = False  # the bot starts paused

# status hud settings, always enabled independently of debug mode
STATUS_OVERLAY_POS = (20, 20)     # status window position on screen (x, y)
STATUS_OVERLAY_PADDING = (10, 8)  # text padding (x, y)

STATE_LABELS = {
    fishing.STATE_WAITING: "WAITING FOR BITE",
    fishing.STATE_MINIGAME: "CATCHING FISH",
    fishing.STATE_COLLECTING: "COLLECTING CATCH",
}

TRANSPARENT_KEY = "#000000"  # transparent color key for click-through windows

input_action_queue = Queue()
fishing_session = fishing.FishingSession()


def safe_click():
    # click reliably with a short press duration
    pydirectinput.mouseDown()
    time.sleep(0.08)
    pydirectinput.mouseUp()


# asynchronous input worker thread
def input_command_worker():
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
            fishing_session.reset()  # return to the waiting state

        input_action_queue.task_done()


threading.Thread(target=input_command_worker, daemon=True).start()


# shared click-through window infrastructure for the overlay and status hud
def _make_click_through_window(root_window, position_x, position_y, width, height, transparent=True):
    # create a borderless, topmost window that does not intercept mouse input
    window = tk.Toplevel(root_window)
    window.overrideredirect(True)          # remove the border and title bar
    window.attributes("-topmost", True)    # keep the window above other windows
    if transparent:
        window.configure(bg=TRANSPARENT_KEY)
        window.attributes("-transparentcolor", TRANSPARENT_KEY)  # make the key color transparent
    else:
        window.configure(bg="black")
    window.geometry(f"{width}x{height}+{position_x}+{position_y}")
    window.update_idletasks()

    # add transparent and layered styles so mouse input passes through the window
    window_handle = win32gui.GetParent(window.winfo_id())
    extended_style = win32gui.GetWindowLong(window_handle, win32con.GWL_EXSTYLE)
    win32gui.SetWindowLong(
        window_handle,
        win32con.GWL_EXSTYLE,
        extended_style | win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT,
    )
    return window


class StatusHUD:
    # always-visible click-through bot status indicator

    def __init__(self, root_window):
        position_x, position_y = STATUS_OVERLAY_POS
        self.position_x = position_x
        self.position_y = position_y
        self.background = _make_click_through_window(
            root_window, position_x, position_y, 1, 1, transparent=False
        )
        self.background.attributes("-alpha", 0.5)
        self.window = _make_click_through_window(root_window, position_x, position_y, 1, 1)
        self.label = tk.Label(
            self.window,
            text="",
            fg="#FFFFFF",
            bg=TRANSPARENT_KEY,
            font=("Consolas", 11, "bold"),
            justify="left",
            anchor="w",
            padx=STATUS_OVERLAY_PADDING[0],
            pady=STATUS_OVERLAY_PADDING[1],
            borderwidth=0,
            highlightthickness=0,
        )
        self.label.pack(fill="both", expand=True)

    def update(self, text):
        self.label.config(text=text)
        self.label.update_idletasks()
        width = self.label.winfo_reqwidth()
        height = self.label.winfo_reqheight()
        geometry = f"{width}x{height}+{self.position_x}+{self.position_y}"
        self.background.geometry(geometry)
        self.window.geometry(geometry)


class DebugRectOverlay:
    # draw zone and marker boxes over the capture region in overlay debug mode

    def __init__(self, root_window):
        capture_region = fishing.CAPTURE_ROI
        self.window = _make_click_through_window(
            root_window,
            capture_region["left"],
            capture_region["top"],
            capture_region["width"],
            capture_region["height"],
        )
        self.canvas = tk.Canvas(
            self.window,
            width=capture_region["width"],
            height=capture_region["height"],
            bg=TRANSPARENT_KEY,
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)

    def update(self, detection_result):
        self.canvas.delete("all")
        capture_region = fishing.CAPTURE_ROI
        self.canvas.create_rectangle(
            1,
            1,
            capture_region["width"] - 2,
            capture_region["height"] - 2,
            outline="#00FFFF",
            width=2,
            dash=(6, 4),
        )
        if detection_result is None:
            return
        if detection_result.get("zone") is not None:
            zone_x, zone_y, zone_width, zone_height = detection_result["zone"]
            self.canvas.create_rectangle(
                zone_x,
                zone_y,
                zone_x + zone_width,
                zone_y + zone_height,
                outline="#00FF00",
                width=2,
            )
        if detection_result.get("marker") is not None:
            marker_x, marker_y, marker_width, marker_height = detection_result["marker"]
            self.canvas.create_rectangle(
                marker_x,
                marker_y,
                marker_x + marker_width,
                marker_y + marker_height,
                outline="#FFFF00",
                width=2,
            )


# debug window mode using cv2.imshow with the captured frame
def draw_debug_frame(frame_bgr, detection_result):
    # draw zone and marker boxes on a copy of the captured frame
    debug_frame = frame_bgr.copy()
    if detection_result is None:
        return debug_frame

    if detection_result.get("zone") is not None:
        zone_x, zone_y, zone_width, zone_height = detection_result["zone"]
        cv2.rectangle(
            debug_frame,
            (zone_x, zone_y),
            (zone_x + zone_width, zone_y + zone_height),
            (0, 255, 0),
            2,
        )
    if detection_result.get("marker") is not None:
        marker_x, marker_y, marker_width, marker_height = detection_result["marker"]
        cv2.rectangle(
            debug_frame,
            (marker_x, marker_y),
            (marker_x + marker_width, marker_y + marker_height),
            (0, 255, 255),
            2,
        )

    return debug_frame


def toggle_bot_enabled():
    # toggle the bot with the configured hotkey
    global AUTOCLICKER_ENABLED
    AUTOCLICKER_ENABLED = not AUTOCLICKER_ENABLED
    print(f"\n[BOT] Status: {AUTOCLICKER_ENABLED}")
    if AUTOCLICKER_ENABLED:
        fishing_session.reset()
        input_action_queue.put("click")


# main capture and processing loop
def run_live(debug_mode):
    # debug_mode can be None, overlay, or window.
    stop_event = threading.Event()
    keyboard.add_hotkey('k', toggle_bot_enabled)
    keyboard.add_hotkey('esc', stop_event.set)

    root_window = tk.Tk()
    root_window.withdraw()  # keep the root window hidden; it only acts as a container

    status_hud = StatusHUD(root_window)
    debug_overlay = DebugRectOverlay(root_window) if debug_mode == "overlay" else None

    with mss.mss() as screen_capture:
        print("Bot ready (state machine enabled). Press K to start or ESC to exit.")
        previous_time = time.perf_counter()
        fps = 0.0
        window_positioned = False

        while not stop_event.is_set():
            captured_frame = np.array(screen_capture.grab(fishing.CAPTURE_ROI))[:, :, :3]
            detection_result = fishing.analyze_frame(captured_frame)

            current_time = time.perf_counter()
            fps = 0.9 * fps + 0.1 * (1.0 / max(current_time - previous_time, 1e-6))
            previous_time = current_time

            # the status hud is always enabled independently of debug_mode
            bot_status = "FISHING BOT ENABLED" if AUTOCLICKER_ENABLED else "FISHING BOT DISABLED"
            current_action = STATE_LABELS.get(fishing_session.current_state, "IDLE")
            status_text = f"{bot_status}\n{current_action}"
            if debug_mode is not None:
                status_text += f"\n{fps:.0f} FPS"
            status_hud.update(status_text)

            if AUTOCLICKER_ENABLED:
                actions = fishing_session.update(detection_result, current_time)
                for action in actions:
                    input_action_queue.put(action)
            else:
                if fishing_session.is_pressing:
                    input_action_queue.put("up")
                    fishing_session.is_pressing = False

            if debug_mode == "window":
                debug_frame = draw_debug_frame(captured_frame, detection_result)
                cv2.imshow("track detector", debug_frame)
                if not window_positioned:
                    window_x = max(0, root_window.winfo_screenwidth() - debug_frame.shape[1] - 40)
                    cv2.moveWindow("track detector", window_x, 40)
                    window_positioned = True
                if cv2.waitKey(1) & 0xFF in (27, ord('q')):
                    stop_event.set()
            elif debug_mode == "overlay":
                debug_overlay.update(detection_result)

            root_window.update()

    if debug_mode == "window":
        cv2.destroyAllWindows()
    root_window.destroy()


def parse_args():
    parser = argparse.ArgumentParser(description="roblox fishing bot")
    parser.add_argument(
        "--debug",
        nargs="?",
        const="overlay",
        default=None,
        choices=["overlay", "window"],
        help=(
            "debug visualization mode. without the flag, only StatusHUD is shown. "
            "'--debug' or '--debug overlay' shows click-through boxes over the screen. "
            "'--debug window' opens a separate cv2 window with the captured frame."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    command_line_args = parse_args()
    try:
        run_live(command_line_args.debug)
    except Exception as error:
        print(f"\nRuntime error: {error}")
        input()