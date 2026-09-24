"""fishing algorithm: settings, cv detection and state machine for the roblox fishing minigame.
this module does not touch mouse/keyboard directly - it only decides what should happen;
main.py is responsible for actually executing input actions and for capture/overlay/debug."""

import cv2
import numpy as np

# --- НАСТРОЙКИ ИГРЫ / game settings ---
HOLD_T_DURATION = 8.0          # сколько секунд удерживать Т для сбора улова
DISAPPEAR_TIMEOUT = 0.35       # задержка проверки конца мини-игры (в секундах)

# --- СОСТОЯНИЯ БОТА / bot states ---
STATE_WAITING = "WAITING"        # ожидание появления шкалы (клев)
STATE_MINIGAME = "MINIGAME"      # процесс ловли
STATE_COLLECTING = "COLLECTING"  # сбор улова и перезаброс

# --- ТОЧНАЯ ОБЛАСТЬ ЗАХВАТА ШКАЛЫ (для 1920x1080) / capture roi ---
CAPTURE_ROI = {
    "top": 280,      # отступ сверху
    "left": 1380,    # отступ слева (правая часть экрана)
    "width": 180,    # ширина захвата
    "height": 480    # высота захвата
}

# цветовые диапазоны HSV (желто-зеленая зона и белый маркер) / hsv color ranges
ZONE_HSV_LO, ZONE_HSV_HI = (15, 40, 40), (85, 255, 255)
MARKER_HSV_LO, MARKER_HSV_HI = (0, 0, 150), (180, 60, 255)

ZONE_MIN_AREA = 40
MARKER_MIN_AREA = 80


# --- ДЕТЕКЦИЯ ПО ЦВЕТУ (HSV) / color detection ---
def _largest_contour_box(mask, min_area):
    """finds the bounding box of the largest contour above min_area, or none"""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    return cv2.boundingRect(c) if cv2.contourArea(c) >= min_area else None


def find_zone(hsv_img):
    """detects the yellow-green catch zone"""
    mask = cv2.inRange(hsv_img, ZONE_HSV_LO, ZONE_HSV_HI)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    return _largest_contour_box(mask, ZONE_MIN_AREA)


def find_marker(hsv_img):
    """detects the white tracking marker"""
    mask = cv2.inRange(hsv_img, MARKER_HSV_LO, MARKER_HSV_HI)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return _largest_contour_box(mask, MARKER_MIN_AREA)


def analyze_static(bgr):
    """runs cv detection on a single frame, returns zone/marker geometry or none"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    zone = find_zone(hsv)
    marker = find_marker(hsv)

    if zone is None and marker is None:
        return None

    res = {"zone": zone, "marker": marker}
    if marker is not None:
        res["marker_center_y"] = marker[1] + marker[3] / 2
    if zone is not None:
        res["zone_top"] = zone[1]
        res["zone_bottom"] = zone[1] + zone[3]
    return res


# --- ВЕБХУК / webhook stub ---
def send_catch_webhook_data(catch_info=None):
    """placeholder for reporting a completed catch.
    real implementation will move into webhook.py once that module is added -
    for now this is intentionally a no-op so the call site in main.py already exists."""
    # todo: forward catch_info to webhook.py when it's introduced
    pass


# --- АЛГОРИТМ / state machine ---
class FishingSession:
    """holds the fishing state machine and decides what input actions are needed
    on each frame. it never calls pydirectinput/keyboard itself - main.py executes
    the action strings this class returns through its input queue."""

    def __init__(self):
        self.current_state = STATE_WAITING
        self.is_pressing = False
        self.last_seen_time = 0.0

    def reset(self):
        """resets the session back to waiting for a new bite"""
        self.current_state = STATE_WAITING
        self.is_pressing = False
        self.last_seen_time = 0.0

    def update(self, res, now):
        """
        advances the state machine by one frame.
        res: output of analyze_static() for the current frame (or none)
        now: time.perf_counter() timestamp for this frame
        returns a list of action strings for the input queue, e.g.
        ["down"], ["up"], ["collect_sequence"], or [] if nothing to do.
        """
        actions = []
        has_scale = (res is not None) and ("zone_bottom" in res or "marker_center_y" in res)

        if self.current_state == STATE_WAITING:
            # в режиме ожидания мы только ищем шкалу. ложные пропажи игнорируются!
            if has_scale:
                print("[БОТ] Шкала обнаружена! Начинаем ловлю...")
                self.current_state = STATE_MINIGAME
                self.last_seen_time = now

        elif self.current_state == STATE_MINIGAME:
            # в режиме мини-игры держим маркер в зоне и следим за пропажей шкалы
            if has_scale:
                self.last_seen_time = now

                if "marker_center_y" in res and "zone_bottom" in res:
                    m_y = res["marker_center_y"]
                    z_bot = res["zone_bottom"]

                    if m_y > z_bot:
                        if not self.is_pressing:
                            actions.append("down")
                            self.is_pressing = True
                    else:
                        if self.is_pressing:
                            actions.append("up")
                            self.is_pressing = False
            else:
                # шкала пропала во время игры
                if self.is_pressing:
                    actions.append("up")
                    self.is_pressing = False

                # если шкала отсутствует дольше DISAPPEAR_TIMEOUT, переходим к сбору
                if (now - self.last_seen_time) >= DISAPPEAR_TIMEOUT:
                    print(f"[БОТ] Шкала пропала более чем на {DISAPPEAR_TIMEOUT}s. Мини-игра завершена.")
                    self.current_state = STATE_COLLECTING
                    actions.append("collect_sequence")

        elif self.current_state == STATE_COLLECTING:
            # пока идет сбор улова, ничего с кнопками мыши не делаем
            if self.is_pressing:
                actions.append("up")
                self.is_pressing = False

        return actions