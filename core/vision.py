# screen vision: hsv detection of the zone and marker, and bar tracking between frames
# all pixel constants are tuned on a 1080p screen and scaled by DetectionScale

import cv2
import numpy as np

import settings

REFERENCE_SCREEN_HEIGHT = 1080
BAR_FORGET_TIMEOUT = 1.0   # seconds without a zone before the remembered bar position is dropped

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
