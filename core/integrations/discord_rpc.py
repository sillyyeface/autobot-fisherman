# discord rich presence: shows the run timer, the current status, and the confirmed catches of this run
# handlers only store the latest state; one worker thread pushes it, because discord accepts a presence update
# only every 15 s and the discord client may be closed or restarted at any time

import threading
import time

from pypresence import Presence

import events
import settings

MIN_UPDATE_INTERVAL = 15.0  # discord rate limit for presence updates
RECONNECT_INTERVAL = 15.0   # pause before connecting again when the discord client is not running
STOPPED_STATUS = "STOPPED"

presence_state = {"status": STOPPED_STATUS, "run_start_epoch": None, "confirmed_total": 0}
state_lock = threading.Lock()
state_changed = threading.Event()


def update_state(**state_changes):
    with state_lock:
        presence_state.update(state_changes)
    state_changed.set()


def build_presence_fields():
    with state_lock:
        status = presence_state["status"]
        run_start_epoch = presence_state["run_start_epoch"]
        confirmed_total = presence_state["confirmed_total"]
    presence_fields = {"details": status.capitalize(), "state": f"Caught: {confirmed_total}"}
    if run_start_epoch is not None:
        # discord shows the elapsed time next to the text
        presence_fields["start"] = int(run_start_epoch)
    return presence_fields


def run_presence_worker():
    presence_client = None
    last_update_time = 0.0
    while True:
        if presence_client is None:
            try:
                presence_client = Presence(settings.DISCORD_RPC_CLIENT_ID)
                presence_client.connect()
                state_changed.set()
            except Exception:
                presence_client = None
                time.sleep(RECONNECT_INTERVAL)
                continue
        state_changed.wait()
        wait_time = last_update_time + MIN_UPDATE_INTERVAL - time.monotonic()
        if wait_time > 0:
            # more changes during the pause are merged into one update
            time.sleep(wait_time)
        state_changed.clear()
        try:
            presence_client.update(**build_presence_fields())
            last_update_time = time.monotonic()
        except Exception:
            # the discord client closed: reconnect and send the current state again
            presence_client = None


def on_bot_started():
    update_state(run_start_epoch=time.time(), confirmed_total=0)


def on_bot_stopped(run_time):
    update_state(status=STOPPED_STATUS, run_start_epoch=None)


def on_status_changed(status):
    update_state(status=status)


def on_catch_confirmed(catch_name, catch_count, catch_number, snapshot_path):
    update_state(confirmed_total=catch_number)


def register():
    if not settings.DISCORD_RPC_CLIENT_ID:
        print("[RPC] DISCORD_RPC_CLIENT_ID is empty in settings.py, Discord presence is off.")
        return
    events.subscribe(events.EVENT_BOT_STARTED, on_bot_started)
    events.subscribe(events.EVENT_BOT_STOPPED, on_bot_stopped)
    events.subscribe(events.EVENT_STATUS_CHANGED, on_status_changed)
    events.subscribe(events.EVENT_CATCH_CONFIRMED, on_catch_confirmed)
    threading.Thread(target=run_presence_worker, daemon=True).start()
