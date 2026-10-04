import time
import cv2
import numpy as np
import pydirectinput
import keyboard
import mss
import threading
from queue import Queue
import tkinter as tk
import ctypes

# --- НАСТРОЙКИ СТАБИЛЬНОСТИ ИНПУТА ---
pydirectinput.PAUSE = 0.001
pydirectinput.FAILSAFE = False

# --- НАСТРОЙКИ ИГРЫ ---
HOLD_T_DURATION = 8.0          
AUTOCLICKER_ENABLED = False     
DISAPPEAR_TIMEOUT = 0.35       
WAITING_TIMEOUT = 30.0         

STATE_WAITING = "WAITING"        
STATE_MINIGAME = "MINIGAME"      
STATE_COLLECTING = "COLLECTING"  

current_state = STATE_WAITING

# Цветовые диапазоны HSV (Желто-зеленая зона и Белый маркер)
ZONE_HSV_LO, ZONE_HSV_HI = (15, 40, 40), (85, 255, 255)
MARKER_HSV_LO, MARKER_HSV_HI = (0, 0, 150), (180, 60, 255)

ZONE_MIN_AREA = 40
MARKER_MIN_AREA = 80

input_queue = Queue()

def safe_click():
    pydirectinput.mouseDown()
    time.sleep(0.08)
    pydirectinput.mouseUp()

def input_worker():
    global current_state
    while True:
        command = input_queue.get()
        if command == "click":
            safe_click()
        elif command == "down":
            pydirectinput.mouseDown()
        elif command == "up":
            pydirectinput.mouseUp()
        elif command == "collect_sequence":
            pydirectinput.mouseUp()
            time.sleep(3.0)
            pydirectinput.keyDown('t')
            time.sleep(HOLD_T_DURATION)
            pydirectinput.keyUp('t')
            time.sleep(1.0)
            safe_click()
            time.sleep(1.5)
            current_state = STATE_WAITING
        input_queue.task_done()

threading.Thread(target=input_worker, daemon=True).start()

# --- ДЕТЕКЦИЯ ПО ЦВЕТУ ---
def _largest_contour_box(mask, min_area):
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts: return None
    c = max(cnts, key=cv2.contourArea)
    return cv2.boundingRect(c) if cv2.contourArea(c) >= min_area else None

def find_zone(hsv_img):
    mask = cv2.inRange(hsv_img, ZONE_HSV_LO, ZONE_HSV_HI)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5,5), np.uint8))
    return _largest_contour_box(mask, ZONE_MIN_AREA)

def find_marker(hsv_img):
    mask = cv2.inRange(hsv_img, MARKER_HSV_LO, MARKER_HSV_HI)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3,3), np.uint8))
    return _largest_contour_box(mask, MARKER_MIN_AREA)

def analyze_static(bgr):
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

def toggle_bot():
    global AUTOCLICKER_ENABLED, current_state
    AUTOCLICKER_ENABLED = not AUTOCLICKER_ENABLED
    print(f"\n[БОТ] СТАТУС: {AUTOCLICKER_ENABLED}")
    if AUTOCLICKER_ENABLED:
        current_state = STATE_WAITING
        input_queue.put("click")

# --- СОЗДАНИЕ ПРОЗРАЧНОГО ОВЕРЛЕЯ ---
class OverlayWindow:
    def __init__(self, roi):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-transparentcolor", "black")
        self.root.config(bg="black")
        self.root.geometry(f"{roi['width']}x{roi['height']}+{roi['left']}+{roi['top']}")

        # Делаем окно сквозным для кликов мыши (WS_EX_TRANSPARENT | WS_EX_LAYERED)
        hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x20 | 0x80000)

        self.canvas = tk.Canvas(self.root, width=roi['width'], height=roi['height'], bg="black", highlightthickness=0)
        self.canvas.pack()

    def update_overlay(self, res):
        self.canvas.delete("all")
        if res is not None:
            if res.get("zone") is not None:
                zx, zy, zw, zh = res["zone"]
                self.canvas.create_rectangle(zx, zy, zx + zw, zy + zh, outline="#00FF00", width=2)
            if res.get("marker") is not None:
                mx, my, mw, mh = res["marker"]
                self.canvas.create_rectangle(mx, my, mx + mw, my + mh, outline="#00FFFF", width=2)
        self.root.update_idletasks()
        self.root.update()

# --- ГЛАВНЫЙ ПОТОК ---
def run_live():
    global AUTOCLICKER_ENABLED, current_state
    keyboard.add_hotkey('k', toggle_bot)
    
    is_pressing = False
    last_seen_time = 0.0
    wait_start_time = time.perf_counter()
    prev_state = None

    with mss.mss() as sct:
        # Авторасчет области под разрешение экрана
        sw, sh = sct.monitors[1]["width"], sct.monitors[1]["height"]
        capture_roi = {
            "top": int(sh * (280 / 1080)),
            "left": int(sw * (1380 / 1920)),
            "width": int(sw * (180 / 1920)),
            "height": int(sh * (480 / 1080))
        }

        # Инициализация оверлея
        overlay = OverlayWindow(capture_roi)
        print(f"Разрешение экрана: {sw}x{sh}. Бот запущен в режиме оверлея. Нажмите K для старта.")

        while True:
            frame = np.array(sct.grab(capture_roi))[:, :, :3]
            res = analyze_static(frame)
            now = time.perf_counter()

            # Обновление прозрачного оверлея поверх экрана
            overlay.update_overlay(res)

            if current_state != prev_state:
                if current_state == STATE_WAITING:
                    wait_start_time = now
                prev_state = current_state

            if AUTOCLICKER_ENABLED:
                has_scale = (res is not None) and ("zone_bottom" in res or "marker_center_y" in res)
                
                if current_state == STATE_WAITING:
                    if has_scale:
                        current_state = STATE_MINIGAME
                        last_seen_time = now
                    else:
                        if (now - wait_start_time) >= WAITING_TIMEOUT:
                            input_queue.put("click")
                            wait_start_time = now
                        
                elif current_state == STATE_MINIGAME:
                    if has_scale:
                        last_seen_time = now
                        if "marker_center_y" in res and "zone_bottom" in res:
                            if res["marker_center_y"] > res["zone_bottom"]:
                                if not is_pressing:
                                    input_queue.put("down")
                                    is_pressing = True
                            else:
                                if is_pressing:
                                    input_queue.put("up")
                                    is_pressing = False
                    else:
                        if is_pressing:
                            input_queue.put("up")
                            is_pressing = False
                        if (now - last_seen_time) >= DISAPPEAR_TIMEOUT:
                            current_state = STATE_COLLECTING
                            input_queue.put("collect_sequence")
                            
                elif current_state == STATE_COLLECTING:
                    if is_pressing:
                        input_queue.put("up")
                        is_pressing = False
            else:
                if is_pressing:
                    input_queue.put("up")
                    is_pressing = False

if __name__ == "__main__":
    try:
        run_live()
    except Exception as e:
        print(f"\nОшибка при работе: {e}")
        input()