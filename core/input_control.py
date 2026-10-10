# input execution: a worker thread runs queued mouse and keyboard actions off the capture loop

import threading
import time
from queue import Queue

import pydirectinput

import fishing
import settings

# input stability settings
pydirectinput.PAUSE = 0.001
pydirectinput.FAILSAFE = False

input_action_queue = Queue()
collect_sequence_done = threading.Event()


def safe_click():
    # click reliably with a short press duration
    pydirectinput.mouseDown()
    time.sleep(0.08)
    pydirectinput.mouseUp()


def input_command_worker():
    # execute queued input actions off the capture thread
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
            time.sleep(settings.HOLD_T_DURATION)
            pydirectinput.keyUp('t')
            print("[INPUT] Catch collected.")

            fishing.send_catch_webhook_data()

            time.sleep(1.0)
            print("[INPUT] Casting the rod...")
            safe_click()
            time.sleep(1.5)  # wait for the casting animation

            print("[INPUT] Ready. Waiting for the next bite...")
            collect_sequence_done.set()  # the main loop resets the session


def release_mouse_if_pressing(fishing_session):
    if fishing_session.is_pressing:
        input_action_queue.put("up")
        fishing_session.is_pressing = False


def start_input_worker():
    threading.Thread(target=input_command_worker, daemon=True).start()


def release_all_inputs():
    # make sure nothing stays pressed in the game after exit
    pydirectinput.mouseUp()
    pydirectinput.keyUp('t')
