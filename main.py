import time
import ctypes
import threading
import argparse
from queue import Queue

import cv2
import numpy as np
import pydirectinput
import keyboard
import mss
import tkinter as tk

pydirectinput.PAUSE = 0.001
pydirectinput.FAILSAFE = False

# time to hold t to collect the catch
HOLD_T_DURATION = 8.0
# resolution the base thresholds below were tuned for, used to auto-scale on other monitors
BASE_HEIGHT = 1080

CANNY_LO, CANNY_HI = 40, 120

# base (1080p) detection thresholds, scaled at runtime per monitor height
MIN_FRAG_LEN, MAX_SIDE_THICK = 40, 6
RAIL_GAP_X = 10
MIN_WIDTH, MAX_WIDTH = 20, 250
MIN_HEIGHT, MAX_HEIGHT = 100, 1400
MIN_ASPECT, MAX_ASPECT = 3.0, 14.0
MIN_Y_OVERLAP, MIN_TICKS = 0.7, 3
ZONE_MIN_AREA, MARKER_MIN_AREA, ROI_PAD = 40, 100, 6

# calibrated colors: zone (yellow-green), marker (light/white)
ZONE_HSV_LO, ZONE_HSV_HI = (20, 50, 40), (75, 255, 255)
MARKER_HSV_LO, MARKER_HSV_HI = (0, 0, 160), (180, 45, 255)

# region of the screen to scan, as fractions of monitor size, centered at
# (center_x, center_y). scanning only this box instead of the full screen is
# the single biggest speed win. calibrated for a track anchored near the
# right edge of the screen — tune to where it shows on your screen if
# different, tighter box = higher fps. run with --debug to see the scan box.
SEARCH_WIDTH_FRAC = 0.16
SEARCH_HEIGHT_FRAC = 0.55
SEARCH_CENTER_X_FRAC = 0.75
SEARCH_CENTER_Y_FRAC = 0.48

bot_enabled = False
input_queue = Queue()


def build_search_region(monitor):
    w = int(monitor["width"] * SEARCH_WIDTH_FRAC)
    h = int(monitor["height"] * SEARCH_HEIGHT_FRAC)
    cx = int(monitor["width"] * SEARCH_CENTER_X_FRAC)
    cy = int(monitor["height"] * SEARCH_CENTER_Y_FRAC)
    left = monitor["left"] + max(0, cx - w // 2)
    top = monitor["top"] + max(0, cy - h // 2)
    return {"left": left, "top": top, "width": w, "height": h}


def scale_int(value, scale):
    return max(1, round(value * scale))


def build_params(scale):
    # linear sizes scale by monitor_height/1080, areas scale by that squared
    return {
        "min_frag_len": scale_int(MIN_FRAG_LEN, scale),
        "max_side_thick": scale_int(MAX_SIDE_THICK, scale),
        "rail_gap_x": scale_int(RAIL_GAP_X, scale),
        "min_width": scale_int(MIN_WIDTH, scale),
        "max_width": scale_int(MAX_WIDTH, scale),
        "min_height": scale_int(MIN_HEIGHT, scale),
        "max_height": scale_int(MAX_HEIGHT, scale),
        "zone_min_area": scale_int(ZONE_MIN_AREA, scale * scale),
        "marker_min_area": scale_int(MARKER_MIN_AREA, scale * scale),
        "roi_pad": scale_int(ROI_PAD, scale),
        "close_k": scale_int(9, scale),
        "open_k": scale_int(41, scale),
        "tick_k": scale_int(7, scale),
    }


# --- input thread, keeps blocking delays off the vision loop ---
def input_worker():
    while True:
        command = input_queue.get()
        if command == "click":
            pydirectinput.click()
        elif command == "down":
            pydirectinput.mouseDown()
        elif command == "up":
            pydirectinput.mouseUp()
        elif command == "collect_sequence":
            time.sleep(2.0)
            pydirectinput.keyDown("t")
            time.sleep(HOLD_T_DURATION)
            pydirectinput.keyUp("t")
            time.sleep(1.0)
            pydirectinput.click()
        input_queue.task_done()


# --- vision ---
def get_edges(bgr):
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.Canny(gray, CANNY_LO, CANNY_HI)


def merge_rails(frags, x_tol):
    # the rail edge often breaks into several short pieces (tick marks and
    # antialiasing interrupt it) at the same x — group pieces within x_tol of
    # each other and take the outer y span, turning them back into one rail
    frags = sorted(frags, key=lambda f: f[0])
    rails = []
    for x, y, w, h in frags:
        merged = False
        for r in rails:
            if abs(r[0] - x) <= x_tol:
                r[1] = min(r[1], y)
                r[2] = max(r[2], y + h)
                merged = True
                break
        if not merged:
            rails.append([x, y, y + h])
    return [(x, y0, 1, y1 - y0) for x, y0, y1 in rails]


def find_vertical_segments(edges, p):
    close_k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, p["close_k"]))
    open_k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, p["open_k"]))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, close_k)
    vert = cv2.morphologyEx(closed, cv2.MORPH_OPEN, open_k)
    n, _, stats, _ = cv2.connectedComponentsWithStats(vert, 8)
    frags = []
    for i in range(1, n):
        x, y, w, h, _ = stats[i]
        if h >= p["min_frag_len"] and w <= p["max_side_thick"]:
            frags.append((int(x), int(y), int(w), int(h)))
    return merge_rails(frags, p["rail_gap_x"])


def count_ticks(edges, box, p):
    x, y, w, h = box
    roi = edges[y:y + h, x:x + w]
    if roi.size == 0:
        return 0
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (p["tick_k"], 1))
    horiz = cv2.morphologyEx(roi, cv2.MORPH_OPEN, kernel)
    n, _, stats, _ = cv2.connectedComponentsWithStats(horiz, 8)
    ticks = 0
    for i in range(1, n):
        _, _, ww, hh, _ = stats[i]
        if ww >= 6 and hh <= 4 and ww < w * 0.8:
            ticks += 1
    return ticks


def find_track(bgr, p):
    # locates the fishing minigame track: two parallel vertical bars with tick marks
    edges = get_edges(bgr)
    segs = find_vertical_segments(edges, p)
    best = None
    for i in range(len(segs)):
        xa, ya, wa, ha = segs[i]
        for j in range(i + 1, len(segs)):
            xb, yb, wb, hb = segs[j]
            width = (xb + wb) - xa
            if not (p["min_width"] <= width <= p["max_width"]):
                continue
            top, bottom = max(ya, yb), min(ya + ha, yb + hb)
            overlap = bottom - top
            if overlap <= 0 or overlap / max(ha, hb) < MIN_Y_OVERLAP:
                continue
            y0, y1 = min(ya, yb), max(ya + ha, yb + hb)
            height = y1 - y0
            if not (p["min_height"] <= height <= p["max_height"]):
                continue
            if not (MIN_ASPECT <= height / width <= MAX_ASPECT):
                continue
            box = (xa, y0, width, height)
            ticks = count_ticks(edges, box, p)
            if ticks < MIN_TICKS:
                continue
            score = ticks * 10 + (overlap / max(ha, hb)) * 20 + height * 0.05
            if best is None or score > best["score"]:
                best = {"box": box, "score": score}
    return best["box"] if best else None


def largest_box(mask, min_area):
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    return cv2.boundingRect(c) if cv2.contourArea(c) >= min_area else None


def find_zone(roi_bgr, p):
    mask = cv2.inRange(cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV), ZONE_HSV_LO, ZONE_HSV_HI)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    return largest_box(mask, p["zone_min_area"])


def find_marker(roi_bgr, p):
    mask = cv2.inRange(cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV), MARKER_HSV_LO, MARKER_HSV_HI)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return largest_box(mask, p["marker_min_area"])


def analyze(bgr, p):
    track = find_track(bgr, p)
    if track is None:
        return None
    tx, ty, tw, th = track
    pad = p["roi_pad"]
    x0, y0 = max(0, tx - pad), max(0, ty - pad)
    x1, y1 = min(bgr.shape[1], tx + tw + pad), min(bgr.shape[0], ty + th + pad)
    roi = bgr[y0:y1, x0:x1]

    def to_global(box):
        if box is None:
            return None
        x, y, w, h = box
        return (x + x0, y + y0, w, h)

    zone = to_global(find_zone(roi, p))
    marker = to_global(find_marker(roi, p))
    res = {"track": track, "zone": zone, "marker": marker}
    if marker:
        res["marker_y"] = marker[1] + marker[3] / 2
    if zone:
        res["zone_top"] = zone[1]
        res["zone_bottom"] = zone[1] + zone[3]
    return res


# --- bot logic ---
def toggle_bot():
    global bot_enabled
    bot_enabled = not bot_enabled
    print(f"[bot] {'enabled' if bot_enabled else 'paused'}")
    if bot_enabled:
        input_queue.put("click")


def update_reel(res, is_pressing):
    # holds the mouse while the marker is below the green zone, releases on contact or above
    if res is None or "marker_y" not in res or "zone_top" not in res:
        if is_pressing:
            input_queue.put("up")
            is_pressing = False
        return is_pressing

    m_y, z_bot = res["marker_y"], res["zone_bottom"]
    if m_y > z_bot:
        if not is_pressing:
            input_queue.put("down")
            is_pressing = True
    else:
        if is_pressing:
            input_queue.put("up")
            is_pressing = False
    return is_pressing


# --- debug overlay: transparent, click-through, drawn directly over the game ---
class Overlay:
    def __init__(self, monitor, region_offset, region_size):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.geometry(f"{monitor['width']}x{monitor['height']}+{monitor['left']}+{monitor['top']}")
        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.root.update_idletasks()
        self._make_transparent_click_through()

        # persistent canvas items, reused every frame instead of recreated
        # (delete+recreate each frame is the main overlay cost)
        self.status_id = self.canvas.create_text(20, 20, text="", anchor="nw", font=("Consolas", 14, "bold"))
        self.fps_id = self.canvas.create_text(20, 45, text="", fill="yellow", anchor="nw", font=("Consolas", 12))
        self.track_id = self.canvas.create_rectangle(0, 0, 0, 0, outline="#ff0000", width=2, state="hidden")
        self.zone_id = self.canvas.create_rectangle(0, 0, 0, 0, outline="#00ff00", width=2, state="hidden")
        self.marker_id = self.canvas.create_rectangle(0, 0, 0, 0, outline="#ffff00", width=2, state="hidden")

        # scan region box, drawn once for calibration, never moves
        ox, oy = region_offset
        rw, rh = region_size
        self.canvas.create_rectangle(ox, oy, ox + rw, oy + rh, outline="#00ffff", dash=(4, 4), width=1)

    def _make_transparent_click_through(self):
        # windows only: makes the black background see-through, lets clicks
        # pass through the overlay into the game, and stops the overlay from
        # ever taking keyboard/mouse focus (WS_EX_NOACTIVATE) so the game and
        # the k hotkey both keep working normally.
        # colorkey transparency is set manually here (not via tk's
        # -transparentcolor) so the extended style change below does not reset it.
        # tk's winfo_id() returns the inner drawing window, not the actual
        # top-level frame the OS uses for hit-testing, so we resolve the real
        # parent window and apply the styles there.
        child_hwnd = self.root.winfo_id()
        hwnd = ctypes.windll.user32.GetParent(child_hwnd) or child_hwnd

        gwl_exstyle = -20
        ws_ex_layered, ws_ex_transparent, ws_ex_noactivate = 0x80000, 0x20, 0x08000000
        lwa_colorkey = 0x1
        swp_flags = 0x0001 | 0x0002 | 0x0004 | 0x0020  # nomove, nosize, nozorder, framechanged

        style = ctypes.windll.user32.GetWindowLongW(hwnd, gwl_exstyle)
        new_style = style | ws_ex_layered | ws_ex_transparent | ws_ex_noactivate
        ctypes.windll.user32.SetWindowLongW(hwnd, gwl_exstyle, new_style)
        ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0x000000, 0, lwa_colorkey)
        ctypes.windll.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, swp_flags)

    def draw(self, res, enabled, fps, region_offset):
        color = "#00ff00" if enabled else "#ff0000"
        status = "BOT: ACTIVE (K to pause)" if enabled else "BOT: PAUSED (K to start)"
        self.canvas.itemconfigure(self.status_id, text=status, fill=color)
        self.canvas.itemconfigure(self.fps_id, text=f"{fps:.0f} fps")

        res = res or {}
        self._move_rect(self.track_id, res.get("track"), region_offset)
        self._move_rect(self.zone_id, res.get("zone"), region_offset)
        self._move_rect(self.marker_id, res.get("marker"), region_offset)
        self.root.update()

    def _move_rect(self, item_id, box, region_offset):
        if box is None:
            self.canvas.itemconfigure(item_id, state="hidden")
            return
        ox, oy = region_offset
        x, y, w, h = box
        self.canvas.coords(item_id, x + ox, y + oy, x + w + ox, y + h + oy)
        self.canvas.itemconfigure(item_id, state="normal")

    def close(self):
        self.root.destroy()


# --- main loop ---
def run(debug):
    keyboard.add_hotkey("k", toggle_bot)
    threading.Thread(target=input_worker, daemon=True).start()

    is_pressing = False
    track_was_present = False

    with mss.MSS() as sct:
        monitor = sct.monitors[1]
        scale = monitor["height"] / BASE_HEIGHT
        params = build_params(scale)

        region = build_search_region(monitor)
        region_offset = (region["left"] - monitor["left"], region["top"] - monitor["top"])
        region_size = (region["width"], region["height"])

        overlay = Overlay(monitor, region_offset, region_size) if debug else None

        print(f"monitor {monitor['width']}x{monitor['height']}, scale {scale:.2f}")
        print(f"scan region {region['width']}x{region['height']} (adjust SEARCH_*_FRAC to move/resize it)")
        print("k = start/pause, ctrl+c in console = quit")

        prev = time.perf_counter()
        fps = 0.0
        try:
            while True:
                raw = sct.grab(region)
                frame = cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)
                res = analyze(frame, params)

                if bot_enabled:
                    if res is not None:
                        track_was_present = True
                        is_pressing = update_reel(res, is_pressing)
                    else:
                        if is_pressing:
                            input_queue.put("up")
                            is_pressing = False
                        if track_was_present:
                            input_queue.put("collect_sequence")
                            track_was_present = False
                elif is_pressing:
                    input_queue.put("up")
                    is_pressing = False

                now = time.perf_counter()
                fps = 0.9 * fps + 0.1 * (1.0 / max(now - prev, 1e-6))
                prev = now

                if overlay:
                    overlay.draw(res, bot_enabled, fps, region_offset)
        except KeyboardInterrupt:
            pass
        finally:
            if overlay:
                overlay.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="show detection overlay on screen")
    args = parser.parse_args()
    try:
        run(args.debug)
    except Exception as e:
        print(f"error: {e}")
        input()