# click-through overlay windows: the status hud and both debug visualizations

import ctypes

import cv2
import tkinter as tk
import win32con
import win32gui

import settings

TRANSPARENT_KEY = "#000000"  # color key that becomes fully transparent in overlay windows

# debug colors stay outside the zone and marker hsv ranges, so a captured overlay is never detected
# they are also bright warm or violet tones that stand out on the blue water and the dark bar
DEBUG_COLOR_SEARCH = "#FF6000"  # orange
DEBUG_COLOR_BAR = "#C060FF"     # violet
DEBUG_COLOR_ZONE = "#FF1A1A"    # red
DEBUG_COLOR_MARKER = "#FF40C0"  # pink
DEBUG_LABEL_PLATE = "#101010"   # near-black label plate, pure black is the transparent key

# debug drawing sizes in 1080p pixels, scaled by the screen height
DEBUG_LINE_WIDTH = 3
DEBUG_LABEL_FONT_SIZE = 12
DEBUG_LABEL_GAP = 4
DEBUG_OVERLAY_MARGIN = (90, 24)  # extra room around the search region for labels (x, y)

DEBUG_WINDOW_TITLE = "fisherman vision"

WDA_EXCLUDEFROMCAPTURE = 0x11  # windows 10 2004+


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
        self.position_x = monitor["left"] + round(settings.STATUS_HUD_POSITION[0] * screen_scale)
        self.position_y = monitor["top"] + round(settings.STATUS_HUD_POSITION[1] * screen_scale)
        self.background = create_overlay_window(root_window, "black", alpha=settings.STATUS_HUD_BACKGROUND_ALPHA)
        self.window = create_overlay_window(root_window, TRANSPARENT_KEY)
        self.label = tk.Label(
            self.window,
            text="",
            fg="#FFFFFF",
            bg=TRANSPARENT_KEY,
            font=("Consolas", -round(settings.STATUS_HUD_FONT_SIZE * screen_scale), "bold"),
            justify="left",
            anchor="w",
            padx=round(settings.STATUS_HUD_PADDING[0] * screen_scale),
            pady=round(settings.STATUS_HUD_PADDING[1] * screen_scale),
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


def label_anchor_point(box_left, box_top, box_right, box_bottom, label_side, gap):
    # point and tk anchor for a label next to a box; zone labels go right, marker and bar labels go left
    if label_side == "above":
        return box_left, box_top - gap, "sw"
    if label_side == "right":
        return box_right + gap, box_top, "nw"
    if label_side == "left_top":
        return box_left - gap, box_top, "ne"
    return box_left - gap, (box_top + box_bottom) / 2, "e"


DEBUG_BOX_STYLES = {
    "search": (DEBUG_COLOR_SEARCH, "SEARCH", "above"),
    "bar": (DEBUG_COLOR_BAR, "BAR", "left_top"),
    "zone": (DEBUG_COLOR_ZONE, "ZONE", "right"),
    "marker": (DEBUG_COLOR_MARKER, "MARKER", "left"),
}


class DebugOverlay:
    # click-through labeled boxes over the screen: search region, bar, zone, and marker

    def __init__(self, root_window, search_region, screen_scale):
        self.margin_x = round(DEBUG_OVERLAY_MARGIN[0] * screen_scale)
        self.margin_y = round(DEBUG_OVERLAY_MARGIN[1] * screen_scale)
        self.label_gap = max(2, round(DEBUG_LABEL_GAP * screen_scale))
        line_width = max(2, round(DEBUG_LINE_WIDTH * screen_scale))
        label_font = ("Consolas", -round(DEBUG_LABEL_FONT_SIZE * screen_scale), "bold")

        self.window = create_overlay_window(root_window, TRANSPARENT_KEY)
        self.canvas = tk.Canvas(self.window, bg=TRANSPARENT_KEY, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        # each box is a rectangle, a dark label plate, and the label text
        self.canvas_items = {}
        for box_name, (color, label_text, _) in DEBUG_BOX_STYLES.items():
            self.canvas_items[box_name] = (
                self.canvas.create_rectangle(
                    0, 0, 0, 0, outline=color, width=line_width,
                    dash=(6, 4) if box_name == "search" else None, state="hidden",
                ),
                self.canvas.create_rectangle(0, 0, 0, 0, fill=DEBUG_LABEL_PLATE, outline="", state="hidden"),
                self.canvas.create_text(0, 0, text=label_text, fill=color, font=label_font, state="hidden"),
            )
        self.placed_boxes = {}
        self.move_to(search_region)

    def move_to(self, search_region):
        width, height = search_region["width"], search_region["height"]
        window_width, window_height = width + 2 * self.margin_x, height + 2 * self.margin_y
        window_left, window_top = search_region["left"] - self.margin_x, search_region["top"] - self.margin_y
        self.window.geometry(f"{window_width}x{window_height}+{window_left}+{window_top}")
        self.canvas.config(width=window_width, height=window_height)
        self._place_box("search", (0, 0, width, height))

    def _place_box(self, box_name, box):
        # reuse canvas items and skip tk calls when the box did not move since the last frame
        if box_name in self.placed_boxes and self.placed_boxes[box_name] == box:
            return
        self.placed_boxes[box_name] = box
        rect_item, plate_item, text_item = self.canvas_items[box_name]
        if box is None:
            for canvas_item in (rect_item, plate_item, text_item):
                self.canvas.itemconfigure(canvas_item, state="hidden")
            return
        box_x, box_y, box_width, box_height = box
        box_left, box_top = box_x + self.margin_x, box_y + self.margin_y
        box_right, box_bottom = box_left + box_width, box_top + box_height
        self.canvas.coords(rect_item, box_left, box_top, box_right, box_bottom)

        label_side = DEBUG_BOX_STYLES[box_name][2]
        label_x, label_y, label_anchor = label_anchor_point(
            box_left, box_top, box_right, box_bottom, label_side, self.label_gap
        )
        self.canvas.coords(text_item, label_x, label_y)
        for canvas_item in (rect_item, plate_item, text_item):
            self.canvas.itemconfigure(canvas_item, state="normal")
        # bbox is only available for visible items
        self.canvas.itemconfigure(text_item, anchor=label_anchor)
        text_left, text_top, text_right, text_bottom = self.canvas.bbox(text_item)
        self.canvas.coords(plate_item, text_left - 2, text_top - 1, text_right + 2, text_bottom + 1)

    def update(self, detection_result, bar_box):
        self._place_box("bar", bar_box)
        self._place_box("zone", detection_result["zone"] if detection_result else None)
        self._place_box("marker", detection_result["marker"] if detection_result else None)


def hex_to_bgr(hex_color):
    return tuple(int(hex_color[index:index + 2], 16) for index in (5, 3, 1))


# cv2 colors parsed once instead of every debug frame
DEBUG_BOX_BGR_COLORS = {box_name: hex_to_bgr(style[0]) for box_name, style in DEBUG_BOX_STYLES.items()}
DEBUG_LABEL_PLATE_BGR = hex_to_bgr(DEBUG_LABEL_PLATE)


def draw_debug_label(debug_frame, label_text, label_x, label_y, label_anchor, bgr_color, font_scale):
    # cv2 version of a tk anchored label on a dark plate
    (text_width, text_height), baseline = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)
    text_left = label_x - text_width if "e" in label_anchor else label_x
    if label_anchor.startswith("s"):
        text_top = label_y - text_height - baseline
    elif label_anchor.startswith("n"):
        text_top = label_y
    else:
        text_top = label_y - (text_height + baseline) / 2
    text_left, text_top = int(text_left), int(text_top)
    cv2.rectangle(
        debug_frame, (text_left - 2, text_top - 1),
        (text_left + text_width + 2, text_top + text_height + baseline + 1), DEBUG_LABEL_PLATE_BGR, -1,
    )
    cv2.putText(
        debug_frame, label_text, (text_left, text_top + text_height),
        cv2.FONT_HERSHEY_SIMPLEX, font_scale, bgr_color, 1, cv2.LINE_AA,
    )


def draw_debug_frame(frame_bgr, detection_result, bar_box, screen_scale):
    # draw the same labeled boxes as the overlay on the captured frame with a border for labels
    margin_x = round(DEBUG_OVERLAY_MARGIN[0] * screen_scale)
    margin_y = round(DEBUG_OVERLAY_MARGIN[1] * screen_scale)
    debug_frame = cv2.copyMakeBorder(
        frame_bgr, margin_y, margin_y, margin_x, margin_x, cv2.BORDER_CONSTANT, value=(24, 24, 24)
    )
    line_width = max(2, round(DEBUG_LINE_WIDTH * screen_scale))
    label_gap = max(2, round(DEBUG_LABEL_GAP * screen_scale))
    font_scale = 0.42 * screen_scale
    frame_height, frame_width = frame_bgr.shape[:2]

    debug_boxes = {"search": (0, 0, frame_width, frame_height), "bar": bar_box}
    if detection_result is not None:
        debug_boxes["zone"] = detection_result["zone"]
        debug_boxes["marker"] = detection_result["marker"]
    for box_name, box in debug_boxes.items():
        if box is None:
            continue
        _, label_text, label_side = DEBUG_BOX_STYLES[box_name]
        bgr_color = DEBUG_BOX_BGR_COLORS[box_name]
        box_x, box_y, box_width, box_height = box
        box_left, box_top = box_x + margin_x, box_y + margin_y
        box_right, box_bottom = box_left + box_width, box_top + box_height
        cv2.rectangle(debug_frame, (box_left, box_top), (box_right, box_bottom), bgr_color, line_width)
        label_x, label_y, label_anchor = label_anchor_point(
            box_left, box_top, box_right, box_bottom, label_side, label_gap
        )
        draw_debug_label(debug_frame, label_text, label_x, label_y, label_anchor, bgr_color, font_scale)
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
