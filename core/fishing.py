# fishing state machine for the slayers2 minigame
# it only decides which input actions should happen; main.py captures frames and executes them

import settings

VELOCITY_SMOOTHING = 0.5   # weight of the newest velocity sample
VELOCITY_MAX_GAP = 0.2     # seconds between samples after which velocities restart from zero

# bot states
STATE_WAITING = "WAITING"        # wait for the fishing bar to appear
STATE_MINIGAME = "MINIGAME"      # active fishing minigame
STATE_COLLECTING = "COLLECTING"  # collect the catch and recast


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
