# input execution: a worker thread runs queued mouse and keyboard actions off the capture loop

import threading
import time
from queue import Empty, Queue

import pydirectinput

import settings

# input stability settings
pydirectinput.PAUSE = 0.001
pydirectinput.FAILSAFE = False

CANCEL_CHECK_INTERVAL = 0.02  # seconds between cancel checks inside long waits

input_action_queue = Queue()
# every queued action carries the generation it was queued in; cancelling bumps the generation
# so stale actions are skipped and a running collect sequence stops at its next wait
action_generation = 0
completed_collect_generation = None  # generation of the last finished collect sequence


def queue_action(input_command):
    input_action_queue.put((action_generation, input_command))


def cancel_and_release():
    # drop every pending action, interrupt a running sequence, and release the mouse and t
    global action_generation
    action_generation += 1
    while True:
        try:
            input_action_queue.get_nowait()
        except Empty:
            break
    queue_action("release")


def consume_collect_done():
    # true once after a collect sequence of the current generation finished
    global completed_collect_generation
    if completed_collect_generation != action_generation:
        return False
    completed_collect_generation = None
    return True


def _is_cancelled(generation):
    return generation != action_generation


def _wait(seconds, generation):
    # sleep in short steps; returns false as soon as the actions were cancelled
    deadline = time.perf_counter() + seconds
    while not _is_cancelled(generation):
        remaining_time = deadline - time.perf_counter()
        if remaining_time <= 0:
            return True
        time.sleep(min(CANCEL_CHECK_INTERVAL, remaining_time))
    return False


def safe_click():
    # click reliably with a short press duration
    pydirectinput.mouseDown()
    time.sleep(0.08)
    pydirectinput.mouseUp()


def release_all_inputs():
    # make sure nothing stays pressed in the game
    pydirectinput.mouseUp()
    pydirectinput.keyUp('t')


def run_collect_sequence(generation):
    # collect the catch and recast; any wait returns early when the bot is paused or restarted
    global completed_collect_generation
    pydirectinput.mouseUp()

    print("[INPUT] Waiting for the catch animation...")
    if not _wait(3.0, generation):
        return

    print("[INPUT] Holding T to collect the catch...")
    pydirectinput.keyDown('t')
    collected = _wait(settings.HOLD_T_DURATION, generation)
    pydirectinput.keyUp('t')
    if not collected:
        return
    print("[INPUT] Catch collected.")

    if not _wait(1.0, generation):
        return
    print("[INPUT] Casting the rod...")
    safe_click()
    if not _wait(1.5, generation):  # wait for the casting animation
        return

    print("[INPUT] Ready. Waiting for the next bite...")
    completed_collect_generation = generation  # the main loop resets the session


def input_command_worker():
    # execute queued input actions off the capture thread
    while True:
        generation, input_command = input_action_queue.get()
        if input_command == "release":
            release_all_inputs()
        elif _is_cancelled(generation):
            continue
        elif input_command == "click":
            safe_click()
        elif input_command == "down":
            pydirectinput.mouseDown()
        elif input_command == "up":
            pydirectinput.mouseUp()
        elif input_command == "collect_sequence":
            run_collect_sequence(generation)


def start_input_worker():
    threading.Thread(target=input_command_worker, daemon=True).start()
