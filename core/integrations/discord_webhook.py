# discord webhook notifications (stub): prints the payload until sending to DISCORD_WEBHOOK_URL is implemented

import events


def on_catch_collected(catch_count):
    print(f"[WEBHOOK] Catch collected, total this run: {catch_count}")


def register():
    events.subscribe(events.EVENT_CATCH_COLLECTED, on_catch_collected)
