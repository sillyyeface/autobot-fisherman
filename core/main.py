# entry point: command line, hotkeys, and the live capture loop that ties all modules together
# settings.py - tunables, vision.py - detection, fishing.py - state machine, input_control.py - input,
# region.py - search region, overlays.py - status hud and debug views

import argparse
import ctypes
import threading
import time

import cv2
import keyboard
import mss
import numpy as np
import tkinter as tk

import fishing
import input_control
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


def build_status_text(elapsed_seconds, status_label, fps=None):
    # run time since the last start on top, the current phase below, fps only in debug modes
    status_lines = [format_uptime(elapsed_seconds), status_label]
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


def guarded_hotkey(requested_event, selection_active):
    # hotkeys only set flags for the main loop and are ignored while a region is being selected
    def on_hotkey():
        if not selection_active.is_set():
            requested_event.set()
    return on_hotkey


def reset_bot_run(fishing_session, bar_tracker, current_time):
    # cancel queued and running input, release everything, and return to the first phase
    input_control.cancel_and_release()
    fishing_session.reset(current_time)
    bar_tracker.reset()


def run_live(debug_mode):
    # debug_mode can be None, overlay, or window
    stop_requested = threading.Event()
    toggle_requested = threading.Event()
    selection_requested = threading.Event()
    selection_active = threading.Event()
    keyboard.add_hotkey(settings.HOTKEY_TOGGLE, guarded_hotkey(toggle_requested, selection_active))
    keyboard.add_hotkey(settings.HOTKEY_SELECT_REGION, guarded_hotkey(selection_requested, selection_active))
    keyboard.add_hotkey(settings.HOTKEY_EXIT, guarded_hotkey(stop_requested, selection_active))

    root_window = tk.Tk()
    root_window.withdraw()  # hidden container for the overlay windows

    with mss.MSS() as screen_capture:
        monitor = screen_capture.monitors[1]
        screen_scale = monitor["height"] / vision.REFERENCE_SCREEN_HEIGHT
        detection_scale = vision.DetectionScale(screen_scale)
        search_region = region.fractions_to_region(region.load_search_fractions(), monitor)

        status_hud = overlays.StatusHUD(root_window, screen_scale, monitor)
        debug_overlay = (
            overlays.DebugOverlay(root_window, search_region, screen_scale) if debug_mode == "overlay" else None
        )
        debug_window = overlays.DebugWindow() if debug_mode == "window" else None

        fishing_session = fishing.FishingSession()
        bar_tracker = vision.BarTracker()
        bot_enabled = False

        run_start_time = time.perf_counter()
        previous_frame_time = run_start_time
        fps = 0.0

        print(f"Screen: {monitor['width']}x{monitor['height']}, search region: {search_region}")
        print(
            f"Bot ready. {settings.HOTKEY_TOGGLE.upper()} - start/pause, "
            f"{settings.HOTKEY_SELECT_REGION.upper()} - select search region, {settings.HOTKEY_EXIT.upper()} - exit."
        )

        try:
            while not stop_requested.is_set():
                frame_start_time = time.perf_counter()

                if selection_requested.is_set():
                    selection_requested.clear()
                    if bot_enabled:
                        bot_enabled = False
                        reset_bot_run(fishing_session, bar_tracker, frame_start_time)
                        print("[BOT] Stopped for region selection.")
                    status_hud.update(build_status_text(0.0, "SELECTING REGION"))
                    selection_active.set()
                    try:
                        selected_region = region.select_search_region(
                            root_window, screen_capture, monitor, screen_scale
                        )
                    finally:
                        selection_active.clear()
                    if selected_region is not None:
                        search_region = selected_region
                        region.save_search_fractions(region.region_to_fractions(search_region, monitor))
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
                    print(f"\n[BOT] Status: {'STARTED' if bot_enabled else 'STOPPED'}")
                    # both directions start from scratch: stop all input, reset the phase and the run timer
                    reset_bot_run(fishing_session, bar_tracker, frame_start_time)
                    run_start_time = frame_start_time
                    if bot_enabled:
                        input_control.queue_action("click")

                if input_control.consume_collect_done():
                    if bot_enabled and fishing_session.current_state == fishing.STATE_COLLECTING:
                        fishing_session.reset(frame_start_time)

                frame_bgr = cv2.cvtColor(np.asarray(screen_capture.grab(search_region)), cv2.COLOR_BGRA2BGR)
                detection_result = vision.analyze_frame(frame_bgr, detection_scale, bar_tracker.bar_column)
                bar_tracker.update(detection_result, frame_start_time)

                if bot_enabled:
                    for action in fishing_session.update(detection_result, frame_start_time):
                        input_control.queue_action(action)

                fps = 0.9 * fps + 0.1 / max(frame_start_time - previous_frame_time, 1e-6)
                previous_frame_time = frame_start_time

                if bot_enabled:
                    run_time = frame_start_time - run_start_time
                    status_label = STATE_LABELS.get(fishing_session.current_state, "IDLE")
                else:
                    run_time, status_label = 0.0, "STOPPED"
                status_hud.update(build_status_text(run_time, status_label, fps if debug_mode is not None else None))

                if debug_overlay is not None:
                    debug_overlay.update(detection_result, bar_tracker.bar_box())
                elif debug_window is not None:
                    debug_frame = overlays.draw_debug_frame(
                        frame_bgr, detection_result, bar_tracker.bar_box(), screen_scale
                    )
                    debug_window.show(debug_frame, search_region, monitor)

                root_window.update()

                # sleep the rest of the frame budget instead of spinning at max fps
                frame_budget = 1.0 / target_fps_for(bot_enabled, fishing_session.current_state)
                remaining_time = frame_budget - (time.perf_counter() - frame_start_time)
                if remaining_time > 0:
                    time.sleep(remaining_time)
        finally:
            input_control.release_all_inputs()
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
    input_control.start_input_worker()
    try:
        run_live(resolve_debug_mode(command_line_args))
    except Exception as error:
        print(f"\nRuntime error: {error}")
        input()
