# fishing settings, cv detection, bar tracking, and the state machine for the slayers2 minigame
# this module only decides which actions should happen; main.py captures frames and executes them

import cv2
import numpy as np

import settings

BAR_FORGET_TIMEOUT = 1.0   # seconds without a zone before the remembered bar position is dropped
VELOCITY_SMOOTHING = 0.5   # weight of the newest velocity sample
VELOCITY_MAX_GAP = 0.2     # seconds between samples after which velocities restart from zero

# bot states
STATE_WAITING = "WAITING"        # wait for the fishing bar to appear
STATE_MINIGAME = "MINIGAME"      # active fishing minigame
STATE_COLLECTING = "COLLECTING"  # collect the catch and recast

# pixel constants below are tuned on a 1080p screen and scaled by the real screen height
REFERENCE_SCREEN_HEIGHT = 1080

ZONE_MIN_AREA = 40
MARKER_MIN_AREA = 80
ZONE_CLOSE_KERNEL_SIZE = 5
# the marker opening kernel is not scaled so thin markers survive on every screen
MARKER_OPEN_KERNEL = np.ones((3, 3), np.uint8)
# zone width fraction added on each side of the zone to get the bar column
BAR_COLUMN_PADDING = 0.15


class DetectionScale:
    # detection thresholds for the current screen, derived from the 1080p reference values

    def __init__(self, screen_scale):
        area_scale = screen_scale ** 2
        self.zone_min_area = ZONE_MIN_AREA * area_scale
        self.marker_min_area = MARKER_MIN_AREA * area_scale
        close_kernel_size = max(1, round(ZONE_CLOSE_KERNEL_SIZE * screen_scale))
        self.zone_close_kernel = np.ones((close_kernel_size, close_kernel_size), np.uint8)


def _largest_contour_box(mask, minimum_area):
    # find the largest contour above the minimum area and return its bounding box
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest_contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest_contour) < minimum_area:
        return None
    return cv2.boundingRect(largest_contour)


def find_zone(hsv_frame, detection_scale):
    # detect the yellow-green catch zone
    mask = cv2.inRange(hsv_frame, settings.ZONE_HSV_LO, settings.ZONE_HSV_HI)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, detection_scale.zone_close_kernel)
    return _largest_contour_box(mask, detection_scale.zone_min_area)


def find_marker(hsv_frame, detection_scale):
    # detect the white tracking marker
    mask = cv2.inRange(hsv_frame, settings.MARKER_HSV_LO, settings.MARKER_HSV_HI)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, MARKER_OPEN_KERNEL)
    return _largest_contour_box(mask, detection_scale.marker_min_area)


def bar_column_from_zone(zone, frame_width):
    # the bar is the vertical strip that contains the zone, padded to fit a wider marker
    zone_x, _, zone_width, _ = zone
    padding = max(2, round(zone_width * BAR_COLUMN_PADDING))
    return (max(0, zone_x - padding), min(frame_width, zone_x + zone_width + padding))


def analyze_frame(frame_bgr, detection_scale, bar_column_hint=None):
    # find the zone in the whole search region, then the marker only inside the bar column
    hsv_frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    zone = find_zone(hsv_frame, detection_scale)
    if zone is not None:
        bar_column = bar_column_from_zone(zone, hsv_frame.shape[1])
    else:
        bar_column = bar_column_hint

    marker = None
    if bar_column is not None:
        column_left, column_right = bar_column
        column_marker = find_marker(hsv_frame[:, column_left:column_right], detection_scale)
        if column_marker is not None:
            marker_x, marker_y, marker_width, marker_height = column_marker
            marker = (marker_x + column_left, marker_y, marker_width, marker_height)

    if zone is None and marker is None:
        return None

    detection_result = {"zone": zone, "marker": marker, "bar_column": bar_column}
    if marker is not None:
        detection_result["marker_center_y"] = marker[1] + marker[3] / 2
    if zone is not None:
        detection_result["zone_top"] = zone[1]
        detection_result["zone_bottom"] = zone[1] + zone[3]
    return detection_result


class BarTracker:
    # remember the bar position between frames so the marker is still found when the zone flickers

    def __init__(self):
        self.reset()

    def reset(self):
        self.bar_column = None
        self.bar_top = None
        self.bar_bottom = None
        self.last_zone_time = 0.0

    def update(self, detection_result, current_time):
        zone = detection_result["zone"] if detection_result is not None else None
        if zone is not None:
            self.last_zone_time = current_time
            self.bar_column = detection_result["bar_column"]
        elif self.bar_column is not None and current_time - self.last_zone_time >= BAR_FORGET_TIMEOUT:
            self.reset()
            return

        if self.bar_column is None:
            return
        # the bar height is the span the zone and marker have covered during this minigame
        for box in (zone, detection_result["marker"] if detection_result is not None else None):
            if box is None:
                continue
            box_top, box_bottom = box[1], box[1] + box[3]
            self.bar_top = box_top if self.bar_top is None else min(self.bar_top, box_top)
            self.bar_bottom = box_bottom if self.bar_bottom is None else max(self.bar_bottom, box_bottom)

    def bar_box(self):
        if self.bar_column is None or self.bar_top is None:
            return None
        column_left, column_right = self.bar_column
        return (column_left, self.bar_top, column_right - column_left, self.bar_bottom - self.bar_top)


# webhook stub
def send_catch_webhook_data(catch_data=None):
    print("[WEBHOOK] Catch data:", catch_data)


class FishingSession:
    # decide which input actions are needed for each frame

    def __init__(self):
        self.reset(0.0)

    def reset(self, current_time):
        # return to the waiting state for a new bite
        self.current_state = STATE_WAITING
        self.is_pressing = False
        self.last_seen_time = current_time
        self.wait_start_time = current_time
        self._reset_motion()

    def _reset_motion(self):
        self.previous_geometry_time = None
        self.previous_marker_center_y = 0.0
        self.previous_zone_center_y = 0.0
        self.marker_velocity = 0.0
        self.zone_velocity = 0.0

    def _update_motion(self, marker_center_y, zone_center_y, current_time):
        # smoothed vertical speeds in pixels per second for the marker and the zone
        if self.previous_geometry_time is not None:
            elapsed_time = current_time - self.previous_geometry_time
            if 0 < elapsed_time <= VELOCITY_MAX_GAP:
                marker_sample = (marker_center_y - self.previous_marker_center_y) / elapsed_time
                zone_sample = (zone_center_y - self.previous_zone_center_y) / elapsed_time
                self.marker_velocity += VELOCITY_SMOOTHING * (marker_sample - self.marker_velocity)
                self.zone_velocity += VELOCITY_SMOOTHING * (zone_sample - self.zone_velocity)
            elif elapsed_time > VELOCITY_MAX_GAP:
                self.marker_velocity = 0.0
                self.zone_velocity = 0.0
        self.previous_geometry_time = current_time
        self.previous_marker_center_y = marker_center_y
        self.previous_zone_center_y = zone_center_y

    def should_press(self, detection_result, current_time):
        # holding lifts the marker, so hold while it is predicted to be below the target point
        zone_top = detection_result["zone_top"]
        zone_bottom = detection_result["zone_bottom"]
        zone_center_y = (zone_top + zone_bottom) / 2
        marker_center_y = detection_result["marker_center_y"]
        self._update_motion(marker_center_y, zone_center_y, current_time)

        predicted_marker_y = marker_center_y + self.marker_velocity * settings.PREDICTION_TIME
        predicted_target_y = (
            zone_center_y
            + self.zone_velocity * settings.PREDICTION_TIME
            + settings.ZONE_TARGET_OFFSET * (zone_bottom - zone_top)
        )
        return predicted_marker_y > predicted_target_y

    def update(self, detection_result, current_time):
        # advance the state machine by one frame and return input actions
        actions = []
        has_detection_geometry = (detection_result is not None) and (
            "zone_bottom" in detection_result or "marker_center_y" in detection_result
        )

        if self.current_state == STATE_WAITING:
            if has_detection_geometry:
                print("[BOT] Fishing bar detected. Starting the minigame...")
                self.current_state = STATE_MINIGAME
                self.last_seen_time = current_time
            elif current_time - self.wait_start_time >= settings.WAITING_TIMEOUT:
                print(f"[BOT] No bite for {settings.WAITING_TIMEOUT:.0f}s. Recasting the rod...")
                actions.append("click")
                self.wait_start_time = current_time

        elif self.current_state == STATE_MINIGAME:
            # keep the marker in the zone and watch for the bar to disappear
            if has_detection_geometry:
                self.last_seen_time = current_time

                if "marker_center_y" in detection_result and "zone_bottom" in detection_result:
                    if self.should_press(detection_result, current_time):
                        if not self.is_pressing:
                            actions.append("down")
                            self.is_pressing = True
                    elif self.is_pressing:
                        actions.append("up")
                        self.is_pressing = False
                else:
                    # one of the two is missing, so speeds from before the gap would be stale
                    self._reset_motion()
            else:
                self._reset_motion()
                if self.is_pressing:
                    actions.append("up")
                    self.is_pressing = False

                if current_time - self.last_seen_time >= settings.DISAPPEAR_TIMEOUT:
                    print(f"[BOT] Fishing bar missing for more than {settings.DISAPPEAR_TIMEOUT}s. Minigame complete.")
                    self.current_state = STATE_COLLECTING
                    actions.append("collect_sequence")

        elif self.current_state == STATE_COLLECTING:
            # do not issue mouse actions while collecting the catch
            if self.is_pressing:
                actions.append("up")
                self.is_pressing = False

        return actions
