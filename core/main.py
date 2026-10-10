# entry point: command line, startup region selection, the hotkey, and the live capture loop
# settings.py - tunables, vision.py - detection, fishing.py - state machine, input_control.py - input,
# region.py - minigame and notification regions, catch_detection.py - catch notification ocr,
# overlays.py - status hud and debug views, events.py + integrations - notifications

import argparse
import ctypes
import threading
import time

import cv2
import keyboard
import mss
import numpy as np
import tkinter as tk

import catch_detection
import events
import fishing
import input_control
import integrations
import overlays
import region
import settings
import vision

STATE_LABELS = {
    fishing.STATE_WAITING: "WAITING FOR BITE",
    fishing.STATE_MINIGAME: "CATCHING FISH",
    fishing.STATE_COLLECTING: "COLLECTING CATCH",
}


def enable_dpi_awareness():
    # use physical pixels everywhere so capture, overlays, and clicks agree on scaled displays
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()


def format_uptime(elapsed_seconds):
    hours, remainder = divmod(int(elapsed_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def build_status_text(elapsed_seconds, status_label, last_catch=None, fps=None):
    # run time since the last start on top, the current phase below, then the last confirmed catch,
    # fps only in debug modes
    status_lines = [format_uptime(elapsed_seconds), status_label]
    if last_catch is not None:
        status_lines.append(f"LAST: {last_catch[0]} x{last_catch[1]}")
    if fps is not None:
        status_lines.append(f"{fps:.0f} FPS")
    return "\n".join(status_lines)


def target_fps_for(bot_enabled, session_state):
    if not bot_enabled:
        return settings.TARGET_FPS_IDLE
    if session_state == fishing.STATE_MINIGAME:
        return settings.TARGET_FPS_MINIGAME
    if session_state == fishing.STATE_WAITING:
        return settings.TARGET_FPS_WAITING
    return settings.TARGET_FPS_IDLE


def reset_bot_run(fishing_session, bar_tracker, catch_watcher, current_time):
    # cancel queued and running input, release everything, and return to the first phase
    input_control.cancel_and_release()
    fishing_session.reset(current_time)
    bar_tracker.reset()
    catch_watcher.cancel()


def watch_toggle_key(toggle_requested):
    # keyboard.add_hotkey only fires when no other key is down, so it missed k while the bot holds t;
    # a key hook fires regardless, and key repeat is ignored until the key is released
    toggle_key_state = {"is_down": False}

    def on_toggle_key(key_event):
        if key_event.event_type == keyboard.KEY_UP:
            toggle_key_state["is_down"] = False
        elif not toggle_key_state["is_down"]:
            toggle_key_state["is_down"] = True
            toggle_requested.set()

    keyboard.hook_key(settings.HOTKEY_TOGGLE, on_toggle_key)


def capture_bgr(screen_capture, screen_region):
    return cv2.cvtColor(np.asarray(screen_capture.grab(screen_region)), cv2.COLOR_BGRA2BGR)


def run_live(debug_mode):
    # debug_mode can be None, overlay, or window
    root_window = tk.Tk()
    root_window.withdraw()  # hidden container for the overlay windows

    with mss.MSS() as screen_capture:
        monitor = screen_capture.monitors[1]
        screen_scale = monitor["height"] / vision.REFERENCE_SCREEN_HEIGHT
        detection_scale = vision.DetectionScale(screen_scale)

        # both regions are confirmed or picked on every launch, before the hotkey is active
        search_region, notification_region = region.select_startup_regions(
            root_window, screen_capture, monitor, screen_scale
        )

        # the hotkey only sets a flag; all state changes happen in this thread
        toggle_requested = threading.Event()
        watch_toggle_key(toggle_requested)

        status_hud = overlays.StatusHUD(root_window, screen_scale, monitor)
        debug_overlay = notification_overlay = debug_window = notification_window = None
        if debug_mode == "overlay":
            debug_overlay = overlays.DebugOverlay(root_window, search_region, screen_scale)
            notification_overlay = overlays.DebugOverlay(
                root_window, notification_region, screen_scale, region_box_name="notification"
            )
        elif debug_mode == "window":
            debug_window = overlays.DebugWindow(overlays.DEBUG_WINDOW_TITLE)
            notification_window = overlays.DebugWindow(overlays.DEBUG_NOTIFICATION_WINDOW_TITLE)

        fishing_session = fishing.FishingSession()
        bar_tracker = vision.BarTracker()
        catch_watcher = catch_detection.CatchWatcher(screen_scale)
        bot_enabled = False

        run_start_time = time.perf_counter()
        previous_frame_time = run_start_time
        catch_count = 0
        confirmed_catch_count = 0
        last_status_label = None
        fps = 0.0

        def capture_notification():
            return capture_bgr(screen_capture, notification_region)

        print(f"Screen: {monitor['width']}x{monitor['height']}")
        print(f"Search region: {search_region}")
        print(f"Notification region: {notification_region}")
        if not catch_watcher.is_available:
            print("[CATCH] Windows OCR is not available, catches are not confirmed. Run: pip install -r requirements.txt")
        print(f"Bot ready. {settings.HOTKEY_TOGGLE.upper()} - start/stop, Ctrl+C in this console - exit.")

        try:
            while True:
                frame_start_time = time.perf_counter()

                if toggle_requested.is_set():
                    toggle_requested.clear()
                    bot_enabled = not bot_enabled
                    print(f"[BOT] Status: {'STARTED' if bot_enabled else 'STOPPED'}")
                    if bot_enabled:
                        events.emit(events.EVENT_BOT_STARTED)
                    else:
                        events.emit(events.EVENT_BOT_STOPPED, run_time=frame_start_time - run_start_time)
                    # both directions start from scratch: stop all input, reset the phase and the run timer
                    reset_bot_run(fishing_session, bar_tracker, catch_watcher, frame_start_time)
                    run_start_time = frame_start_time
                    catch_count = 0
                    confirmed_catch_count = 0
                    if bot_enabled:
                        input_control.queue_action("click")

                if input_control.consume_collect_done():
                    if bot_enabled and fishing_session.current_state == fishing.STATE_COLLECTING:
                        fishing_session.reset(frame_start_time)
                        catch_count += 1
                        events.emit(events.EVENT_CATCH_COLLECTED, catch_count=catch_count)

                frame_bgr = capture_bgr(screen_capture, search_region)
                detection_result = vision.analyze_frame(frame_bgr, detection_scale, bar_tracker.bar_column)
                bar_tracker.update(detection_result, frame_start_time)

                if bot_enabled:
                    previous_state = fishing_session.current_state
                    for action in fishing_session.update(detection_result, frame_start_time):
                        input_control.queue_action(action)
                    # the notification shows up while collecting, so start reading it when the minigame ends
                    if previous_state != fishing.STATE_COLLECTING and fishing_session.current_state == fishing.STATE_COLLECTING:
                        catch_watcher.start(frame_start_time)

                catch_result = catch_watcher.update(capture_notification, frame_start_time)
                if catch_result is not None and catch_result[0] == "confirmed":
                    _, catch_name, catch_amount, notification_bgr = catch_result
                    confirmed_catch_count += 1
                    print(f"[CATCH] Confirmed: {catch_name} x{catch_amount}")
                    catch_detection.report_catch(notification_bgr, catch_name, catch_amount, confirmed_catch_count)
                elif catch_result is not None:
                    print("[CATCH] No catch notification after the minigame.")
                    events.emit(events.EVENT_CATCH_MISSED)

                fps = 0.9 * fps + 0.1 / max(frame_start_time - previous_frame_time, 1e-6)
                previous_frame_time = frame_start_time

                if bot_enabled:
                    run_time = frame_start_time - run_start_time
                    status_label = STATE_LABELS.get(fishing_session.current_state, "IDLE")
                else:
                    run_time, status_label = 0.0, "STOPPED"
                if status_label != last_status_label:
                    last_status_label = status_label
                    events.emit(events.EVENT_STATUS_CHANGED, status=status_label)
                status_hud.update(build_status_text(
                    run_time,
                    status_label,
                    catch_watcher.last_catch if bot_enabled else None,
                    fps if debug_mode is not None else None,
                ))

                if debug_overlay is not None:
                    debug_overlay.update(detection_result, bar_tracker.bar_box())
                    notification_overlay.set_label("notification", catch_watcher.status_label())
                elif debug_window is not None:
                    debug_frame = overlays.draw_debug_frame(
                        frame_bgr,
                        overlays.search_debug_boxes(frame_bgr, detection_result, bar_tracker.bar_box()),
                        screen_scale,
                    )
                    debug_window.show(debug_frame, search_region, monitor)
                    notification_bgr = capture_notification()
                    notification_debug_frame = overlays.draw_debug_frame(
                        notification_bgr,
                        {"notification": (0, 0, notification_bgr.shape[1], notification_bgr.shape[0])},
                        screen_scale,
                        {"notification": catch_watcher.status_label()},
                    )
                    notification_window.show(notification_debug_frame, notification_region, monitor)

                root_window.update()

                # sleep the rest of the frame budget instead of spinning at max fps
                frame_budget = 1.0 / target_fps_for(bot_enabled, fishing_session.current_state)
                remaining_time = frame_budget - (time.perf_counter() - frame_start_time)
                if remaining_time > 0:
                    time.sleep(remaining_time)
        finally:
            input_control.release_all_inputs()
            keyboard.unhook_all()
            for debug_view in (debug_window, notification_window):
                if debug_view is not None:
                    debug_view.close()
            root_window.destroy()


def parse_args():
    parser = argparse.ArgumentParser(description="Slayers2 auto-fishing bot")
    subparsers = parser.add_subparsers(dest="command")
    debug_parser = subparsers.add_parser(
        "debug", help="show the search and notification regions, bar, zone, and marker (overlay by default)"
    )
    debug_mode_group = debug_parser.add_mutually_exclusive_group()
    debug_mode_group.add_argument(
        "--overlay", action="store_true", help="draw click-through boxes directly over the screen (default)"
    )
    debug_mode_group.add_argument(
        "--window", action="store_true", help="open separate windows with the captured search and notification regions"
    )
    return parser.parse_args()


def resolve_debug_mode(command_line_args):
    if command_line_args.command != "debug":
        return None
    return "window" if command_line_args.window else "overlay"


if __name__ == "__main__":
    command_line_args = parse_args()
    enable_dpi_awareness()
    input_control.start_input_worker()
    integrations.register_integrations()
    try:
        run_live(resolve_debug_mode(command_line_args))
    except KeyboardInterrupt:
        print("\nExiting.")
    except Exception as error:
        print(f"\nRuntime error: {error}")
        input()
