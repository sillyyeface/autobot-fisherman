# fishing settings, cv detection, and state machine for the roblox minigame
# this module only decides which actions should happen; main.py executes them

import cv2
import numpy as np

# game settings
HOLD_T_DURATION = 8.0          # seconds to hold t while collecting the catch
DISAPPEAR_TIMEOUT = 0.35       # delay before confirming the minigame ended

# bot states
STATE_WAITING = "WAITING"        # wait for the fishing bar to appear
STATE_MINIGAME = "MINIGAME"      # active fishing minigame
STATE_COLLECTING = "COLLECTING"  # collect the catch and recast

# capture region for the fishing bar on a 1920x1080 display
CAPTURE_ROI = {
    "top": 280,      # top offset
    "left": 1380,    # left offset on the right side of the screen
    "width": 180,    # capture width
    "height": 480    # capture height
}

# hsv color ranges for the yellow-green zone and white marker
ZONE_HSV_LO, ZONE_HSV_HI = (15, 40, 40), (85, 255, 255)
MARKER_HSV_LO, MARKER_HSV_HI = (0, 0, 150), (180, 60, 255)

ZONE_MIN_AREA = 40
MARKER_MIN_AREA = 80


# hsv color detection
def _largest_contour_box(mask, minimum_area):
    # find the largest contour above the minimum area and return its bounding box
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest_contour = max(contours, key=cv2.contourArea)
    return (
        cv2.boundingRect(largest_contour)
        if cv2.contourArea(largest_contour) >= minimum_area
        else None
    )


def find_zone(hsv_frame):
    # detect the yellow-green catch zone
    mask = cv2.inRange(hsv_frame, ZONE_HSV_LO, ZONE_HSV_HI)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    return _largest_contour_box(mask, ZONE_MIN_AREA)


def find_marker(hsv_frame):
    # detect the white tracking marker
    mask = cv2.inRange(hsv_frame, MARKER_HSV_LO, MARKER_HSV_HI)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return _largest_contour_box(mask, MARKER_MIN_AREA)


def analyze_frame(frame_bgr):
    # detect zone and marker geometry in a single frame
    hsv_frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    zone = find_zone(hsv_frame)
    marker = find_marker(hsv_frame)

    if zone is None and marker is None:
        return None

    detection_result = {"zone": zone, "marker": marker}
    if marker is not None:
        detection_result["marker_center_y"] = marker[1] + marker[3] / 2
    if zone is not None:
        detection_result["zone_top"] = zone[1]
        detection_result["zone_bottom"] = zone[1] + zone[3]
    return detection_result


# webhook stub
def send_catch_webhook_data(catch_data=None):
    # keep this as a no-op until webhook.py is introduced
    pass


# state machine
class FishingSession:
    # decide which input actions are needed for each frame

    def __init__(self):
        self.current_state = STATE_WAITING
        self.is_pressing = False
        self.last_seen_time = 0.0

    def reset(self):
        # return to the waiting state for a new bite
        self.current_state = STATE_WAITING
        self.is_pressing = False
        self.last_seen_time = 0.0

    def update(self, detection_result, current_time):
        # advance the state machine by one frame and return input actions
        actions = []
        has_detection_geometry = (detection_result is not None) and (
            "zone_bottom" in detection_result or "marker_center_y" in detection_result
        )

        if self.current_state == STATE_WAITING:
            # only look for the fishing bar while waiting
            if has_detection_geometry:
                print("[BOT] Fishing bar detected. Starting the minigame...")
                self.current_state = STATE_MINIGAME
                self.last_seen_time = current_time

        elif self.current_state == STATE_MINIGAME:
            # keep the marker in the zone and watch for the bar to disappear
            if has_detection_geometry:
                self.last_seen_time = current_time

                if "marker_center_y" in detection_result and "zone_bottom" in detection_result:
                    marker_center_y = detection_result["marker_center_y"]
                    zone_bottom = detection_result["zone_bottom"]

                    if marker_center_y > zone_bottom:
                        if not self.is_pressing:
                            actions.append("down")
                            self.is_pressing = True
                    else:
                        if self.is_pressing:
                            actions.append("up")
                            self.is_pressing = False
            else:
                # the fishing bar disappeared during the minigame
                if self.is_pressing:
                    actions.append("up")
                    self.is_pressing = False

                # collect the catch after the bar is absent for long enough
                if (current_time - self.last_seen_time) >= DISAPPEAR_TIMEOUT:
                    print(f"[BOT] Fishing bar missing for more than {DISAPPEAR_TIMEOUT}s. Minigame complete.")
                    self.current_state = STATE_COLLECTING
                    actions.append("collect_sequence")

        elif self.current_state == STATE_COLLECTING:
            # do not issue mouse actions while collecting the catch
            if self.is_pressing:
                actions.append("up")
                self.is_pressing = False

        return actions