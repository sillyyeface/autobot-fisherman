# tiny publish/subscribe hub that keeps integrations (discord webhook, rich presence, ...) out of the bot loop
# handlers run on one background thread, so slow network calls never stall capture or input

import threading
from collections import defaultdict
from queue import Queue

EVENT_BOT_STARTED = "bot_started"          # the run started, no data
EVENT_BOT_STOPPED = "bot_stopped"          # the run stopped, run_time in seconds
EVENT_STATUS_CHANGED = "status_changed"    # status label shown in the hud
EVENT_CATCH_COLLECTED = "catch_collected"  # a catch was collected, catch_count in this run
# the catch notification was read: catch_name, catch_count (the x1 amount), catch_number in this run,
# snapshot_path of the saved notification image or None
EVENT_CATCH_CONFIRMED = "catch_confirmed"
EVENT_CATCH_MISSED = "catch_missed"        # no catch notification after a minigame, no data

_subscribers = defaultdict(list)
_event_queue = Queue()
_dispatcher_started = False


def subscribe(event_name, handler):
    _subscribers[event_name].append(handler)


def emit(event_name, **event_data):
    # never blocks the caller; does nothing until someone subscribed
    if _subscribers[event_name]:
        _start_dispatcher()
        _event_queue.put((event_name, event_data))


def _start_dispatcher():
    global _dispatcher_started
    if not _dispatcher_started:
        _dispatcher_started = True
        threading.Thread(target=_dispatch_events, daemon=True).start()


def _dispatch_events():
    while True:
        event_name, event_data = _event_queue.get()
        for handler in _subscribers[event_name]:
            try:
                handler(**event_data)
            except Exception as error:
                print(f"[EVENTS] {event_name} handler failed: {error}")
