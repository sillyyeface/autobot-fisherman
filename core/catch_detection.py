# catch confirmation: reads the "item x1" notification that pops up after collecting a catch
# the notification region is read with the built-in windows ocr on a worker thread, so the main loop never waits

import asyncio
import re
import threading
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue

import cv2

import events
import settings

try:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter
except ImportError:
    OcrEngine = None

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "catches"

# ocr works best on dark text on a white page with large glyphs
OCR_REFERENCE_UPSCALE = 3.0  # upscale for a 1080p screen, divided by the screen scale
OCR_PAGE_PADDING = 20
CATCH_NAME_MIN_LENGTH = 3

# the item name followed by a count such as x1; ocr may read the x as a cyrillic or multiplication sign
CATCH_WITH_COUNT_PATTERN = re.compile(r"(?P<name>[A-Za-z][A-Za-z' -]*?)\s*[xх×]\s?(?P<count>\d+)", re.IGNORECASE)
# the small count line is sometimes not read, then the name alone is accepted
CATCH_NAME_ONLY_PATTERN = re.compile(r"[A-Za-z][A-Za-z' -]*[A-Za-z]")
# a count misread as letters, such as xl or xi, at the end of the name
MISREAD_COUNT_SUFFIX = re.compile(r"\s+[xх×][lIi|]?$")

# watcher phases, shown in the debug label
WATCH_IDLE = "IDLE"
WATCH_ACTIVE = "WATCHING"


def create_ocr_engine():
    if OcrEngine is None:
        return None
    engine = OcrEngine.try_create_from_language(Language(settings.CATCH_OCR_LANGUAGE))
    return engine or OcrEngine.try_create_from_user_profile_languages()


def prepare_ocr_page(frame_bgr, screen_scale):
    # keep only the white notification text, as black text on a white page, upscaled for ocr
    upscale = min(4.0, max(1.0, OCR_REFERENCE_UPSCALE / screen_scale))
    upscaled_bgr = cv2.resize(frame_bgr, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    hsv_frame = cv2.cvtColor(upscaled_bgr, cv2.COLOR_BGR2HSV)
    text_mask = cv2.inRange(hsv_frame, settings.CATCH_TEXT_HSV_LO, settings.CATCH_TEXT_HSV_HI)
    page_gray = cv2.copyMakeBorder(
        255 - text_mask, OCR_PAGE_PADDING, OCR_PAGE_PADDING, OCR_PAGE_PADDING, OCR_PAGE_PADDING,
        cv2.BORDER_CONSTANT, value=255,
    )
    return cv2.cvtColor(page_gray, cv2.COLOR_GRAY2BGRA)


async def recognize_lines(ocr_engine, page_bgra):
    buffer_writer = DataWriter()
    buffer_writer.write_bytes(page_bgra.tobytes())
    bitmap = SoftwareBitmap.create_copy_from_buffer(
        buffer_writer.detach_buffer(), BitmapPixelFormat.BGRA8, page_bgra.shape[1], page_bgra.shape[0]
    )
    ocr_result = await ocr_engine.recognize_async(bitmap)
    return [line.text.strip() for line in ocr_result.lines if line.text.strip()]


def parse_catch_text(ocr_lines):
    # returns (name, count) when the lines look like a catch notification, otherwise None
    joined_text = " ".join(ocr_lines)
    count_match = CATCH_WITH_COUNT_PATTERN.search(joined_text)
    if count_match is not None:
        catch_name, catch_count = count_match.group("name"), int(count_match.group("count"))
    else:
        name_matches = CATCH_NAME_ONLY_PATTERN.findall(joined_text)
        if len(name_matches) != 1:
            return None
        catch_name, catch_count = MISREAD_COUNT_SUFFIX.sub("", name_matches[0]), 1
    catch_name = " ".join(catch_name.split()).strip(" '-")
    if len(catch_name) < CATCH_NAME_MIN_LENGTH:
        return None
    return catch_name, catch_count


def save_snapshot(frame_bgr, catch_name):
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9]+", "_", catch_name).strip("_") or "unknown"
    snapshot_path = SNAPSHOT_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_{safe_name}.png"
    cv2.imwrite(str(snapshot_path), frame_bgr)
    return snapshot_path


class OcrWorker:
    # one background thread with its own event loop; frames go in, (frame, lines) come out

    def __init__(self):
        self.request_queue = Queue()
        self.result_queue = Queue()
        self.is_ready = threading.Event()
        self.is_available = False
        threading.Thread(target=self._run, daemon=True).start()
        self.is_ready.wait(5.0)

    def _run(self):
        event_loop = asyncio.new_event_loop()
        ocr_engine = create_ocr_engine()
        self.is_available = ocr_engine is not None
        self.is_ready.set()
        while True:
            request_id, frame_bgr, screen_scale = self.request_queue.get()
            try:
                page_bgra = prepare_ocr_page(frame_bgr, screen_scale)
                ocr_lines = event_loop.run_until_complete(recognize_lines(ocr_engine, page_bgra))
            except Exception as error:
                print(f"[CATCH] OCR failed: {error}")
                ocr_lines = []
            self.result_queue.put((request_id, frame_bgr, ocr_lines))

    def submit(self, request_id, frame_bgr, screen_scale):
        self.request_queue.put((request_id, frame_bgr, screen_scale))

    def poll(self):
        try:
            return self.result_queue.get_nowait()
        except Empty:
            return None


class CatchWatcher:
    # after a minigame, read the notification region a few times per second until a catch is confirmed
    # a catch counts once the same name was read CATCH_CONFIRM_READS times in a row

    def __init__(self, screen_scale):
        self.screen_scale = screen_scale
        self.ocr_worker = OcrWorker()
        self.request_id = 0
        self.last_catch = None  # (name, count) of the last confirmed catch
        self.last_lines = []
        self._stop()

    @property
    def is_available(self):
        return self.ocr_worker.is_available

    @property
    def is_active(self):
        return self.phase == WATCH_ACTIVE

    def _stop(self):
        self.phase = WATCH_IDLE
        self.request_id += 1  # results of older requests are ignored
        self.request_pending = False
        self.candidate = None
        self.candidate_reads = 0

    def start(self, current_time):
        if not self.is_available:
            return
        self._stop()
        self.phase = WATCH_ACTIVE
        self.deadline = current_time + settings.CATCH_WATCH_TIMEOUT
        self.next_read_time = current_time

    def cancel(self):
        self._stop()

    def update(self, capture_notification, current_time):
        # returns ("confirmed", name, count, frame), ("missed",), or None; capture_notification returns a bgr frame
        if not self.is_active:
            return None

        ocr_result = self.ocr_worker.poll()
        while ocr_result is not None:
            request_id, frame_bgr, ocr_lines = ocr_result
            if request_id == self.request_id:
                self.request_pending = False
                self.last_lines = ocr_lines
                confirmation = self._handle_read(frame_bgr, ocr_lines)
                if confirmation is not None:
                    return confirmation
            ocr_result = self.ocr_worker.poll()

        if current_time >= self.deadline:
            self._stop()
            return ("missed",)

        if not self.request_pending and current_time >= self.next_read_time:
            self.ocr_worker.submit(self.request_id, capture_notification(), self.screen_scale)
            self.request_pending = True
            self.next_read_time = current_time + settings.CATCH_READ_INTERVAL
        return None

    def _handle_read(self, frame_bgr, ocr_lines):
        parsed_catch = parse_catch_text(ocr_lines)
        if parsed_catch is None:
            self.candidate, self.candidate_reads = None, 0
            return None
        if parsed_catch[0].lower() == (self.candidate[0].lower() if self.candidate else None):
            self.candidate_reads += 1
        else:
            self.candidate, self.candidate_reads = parsed_catch, 1
        if self.candidate_reads < settings.CATCH_CONFIRM_READS:
            return None
        catch_name, catch_count = self.candidate
        self.last_catch = self.candidate
        self._stop()
        return ("confirmed", catch_name, catch_count, frame_bgr)

    def status_label(self):
        # short text for the debug overlay label
        if self.is_active:
            return f"NOTIFY: READING {' '.join(self.last_lines)[:24]}".rstrip()
        if self.last_catch is not None:
            return f"NOTIFY: {self.last_catch[0]} x{self.last_catch[1]}"
        return "NOTIFY"


def report_catch(frame_bgr, catch_name, catch_count, catch_number):
    # save the snapshot and publish the event off the main loop, so disk writes never stall it
    def save_and_emit():
        snapshot_path = None
        if settings.CATCH_SAVE_SNAPSHOTS:
            try:
                snapshot_path = str(save_snapshot(frame_bgr, catch_name))
            except OSError as error:
                print(f"[CATCH] Snapshot not saved: {error}")
        events.emit(
            events.EVENT_CATCH_CONFIRMED, catch_name=catch_name, catch_count=catch_count,
            catch_number=catch_number, snapshot_path=snapshot_path,
        )
    threading.Thread(target=save_and_emit, daemon=True).start()
