# discord webhook notifications: each bot event listed in discord_messages.json is sent as that message
# the file holds plain discord webhook payloads (content, embeds, ...) with {placeholders} in any text
# runs on the event thread, so slow requests never stall the bot

import json
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

import events
import settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MESSAGES_PATH = PROJECT_ROOT / "discord_messages.json"
# the url is kept in the git-ignored config.json next to the regions, so it never ends up in a commit
CONFIG_PATH = PROJECT_ROOT / "config.json"
WEBHOOK_URL_KEY = "discord_webhook_url"
SNAPSHOT_ATTACHMENT_NAME = "catch.png"
REQUEST_TIMEOUT = 10.0
MAX_SEND_ATTEMPTS = 3
USER_AGENT = "autobot-fisherman (https://github.com/vercim/autobot-fisherman, 1.0)"

PLACEHOLDER_PATTERN = re.compile(r"\{(\w+)\}")

# run statistics shared by all messages, reset when the bot starts
run_state = {"start_time": None, "confirmed_total": 0, "missed_total": 0}
webhook_url = ""


def load_webhook_url():
    # add an empty key on first use so the user only has to paste the url
    try:
        stored_config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except OSError:
        stored_config = {}
    except ValueError as error:
        print(f"[WEBHOOK] {CONFIG_PATH.name} could not be read: {error}")
        return ""
    if WEBHOOK_URL_KEY not in stored_config:
        stored_config[WEBHOOK_URL_KEY] = ""
        CONFIG_PATH.write_text(json.dumps(stored_config, indent=2), encoding="utf-8")
    return str(stored_config[WEBHOOK_URL_KEY]).strip()


def format_duration(elapsed_seconds):
    hours, remainder = divmod(int(elapsed_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def load_message_templates():
    try:
        return json.loads(MESSAGES_PATH.read_text(encoding="utf-8"))
    except OSError:
        print(f"[WEBHOOK] {MESSAGES_PATH.name} not found, nothing will be sent.")
    except ValueError as error:
        print(f"[WEBHOOK] {MESSAGES_PATH.name} is not valid JSON: {error}")
    return {}


def fill_placeholders(template_value, placeholder_values):
    # replace known {placeholders} in every string; unknown ones and other braces stay untouched
    if isinstance(template_value, str):
        return PLACEHOLDER_PATTERN.sub(
            lambda match: str(placeholder_values.get(match.group(1), match.group(0))), template_value
        )
    if isinstance(template_value, list):
        return [fill_placeholders(item, placeholder_values) for item in template_value]
    if isinstance(template_value, dict):
        filled_dict = {}
        for key, value in template_value.items():
            filled_value = fill_placeholders(value, placeholder_values)
            # an image or thumbnail without a url is rejected by discord, so drop it
            if isinstance(filled_value, dict) and filled_value.get("url") == "":
                continue
            filled_dict[key] = filled_value
        return filled_dict
    return template_value


def build_multipart_body(payload, attachment_path):
    boundary = uuid.uuid4().hex
    body_parts = [
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="payload_json"\r\n'
        "Content-Type: application/json\r\n\r\n".encode("utf-8"),
        json.dumps(payload).encode("utf-8"),
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files[0]"; filename="{SNAPSHOT_ATTACHMENT_NAME}"\r\n'
        "Content-Type: image/png\r\n\r\n".encode("utf-8"),
        Path(attachment_path).read_bytes(),
        f"\r\n--{boundary}--\r\n".encode("utf-8"),
    ]
    return b"".join(body_parts), f"multipart/form-data; boundary={boundary}"


def send_webhook(payload, attachment_path=None):
    if attachment_path is not None:
        request_body, content_type = build_multipart_body(payload, attachment_path)
    else:
        request_body, content_type = json.dumps(payload).encode("utf-8"), "application/json"

    for _ in range(MAX_SEND_ATTEMPTS):
        webhook_request = urllib.request.Request(
            webhook_url,
            data=request_body,
            headers={"Content-Type": content_type, "User-Agent": USER_AGENT},
            method="POST",
        )
        try:
            with urllib.request.urlopen(webhook_request, timeout=REQUEST_TIMEOUT):
                return
        except urllib.error.HTTPError as error:
            if error.code != 429:
                print(f"[WEBHOOK] Discord rejected the message ({error.code}): {error.read().decode('utf-8', 'replace')}")
                return
            # rate limited: wait as long as discord asks, then retry
            try:
                retry_after = float(json.loads(error.read()).get("retry_after", 1.0))
            except ValueError:
                retry_after = 1.0
            time.sleep(min(retry_after, 30.0))
        except (urllib.error.URLError, OSError) as error:
            print(f"[WEBHOOK] Sending failed: {error}")
            return
    print("[WEBHOOK] Still rate limited, message dropped.")


def send_event_message(event_name, **event_values):
    message_template = load_message_templates().get(event_name)
    if not message_template:
        return

    run_time = time.perf_counter() - run_state["start_time"] if run_state["start_time"] is not None else 0.0
    placeholder_values = {
        "run_time": format_duration(event_values.pop("run_time", run_time)),
        "time": datetime.now().strftime("%H:%M:%S"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "confirmed_total": run_state["confirmed_total"],
        "missed_total": run_state["missed_total"],
        "snapshot": "",
    }
    placeholder_values.update(event_values)

    snapshot_path = event_values.get("snapshot_path")
    attach_snapshot = (
        settings.DISCORD_SEND_SNAPSHOT
        and snapshot_path is not None
        and "{snapshot}" in json.dumps(message_template)
        and Path(snapshot_path).is_file()
    )
    if attach_snapshot:
        placeholder_values["snapshot"] = f"attachment://{SNAPSHOT_ATTACHMENT_NAME}"

    payload = fill_placeholders(message_template, placeholder_values)
    send_webhook(payload, snapshot_path if attach_snapshot else None)


def on_bot_started():
    run_state.update(start_time=time.perf_counter(), confirmed_total=0, missed_total=0)
    send_event_message(events.EVENT_BOT_STARTED)


def on_bot_stopped(run_time):
    send_event_message(events.EVENT_BOT_STOPPED, run_time=run_time)
    run_state["start_time"] = None


def on_catch_confirmed(catch_name, catch_count, catch_number, snapshot_path):
    run_state["confirmed_total"] = catch_number
    send_event_message(
        events.EVENT_CATCH_CONFIRMED,
        catch_name=catch_name, catch_count=catch_count, catch_number=catch_number, snapshot_path=snapshot_path,
    )


def on_catch_missed():
    run_state["missed_total"] += 1
    send_event_message(events.EVENT_CATCH_MISSED)


def register():
    global webhook_url
    webhook_url = load_webhook_url()
    if not webhook_url:
        print(f"[WEBHOOK] {WEBHOOK_URL_KEY} is empty in {CONFIG_PATH.name}, Discord notifications are off.")
        return
    events.subscribe(events.EVENT_BOT_STARTED, on_bot_started)
    events.subscribe(events.EVENT_BOT_STOPPED, on_bot_stopped)
    events.subscribe(events.EVENT_CATCH_CONFIRMED, on_catch_confirmed)
    events.subscribe(events.EVENT_CATCH_MISSED, on_catch_missed)
