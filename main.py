import time
import cv2
import numpy as np
import pydirectinput
import keyboard
import mss
import threading
from queue import Queue

# --- НАСТРОЙКИ СТАБИЛЬНОСТИ ИНПУТА ---
pydirectinput.PAUSE = 0.001
pydirectinput.FAILSAFE = False

# --- НАСТРОЙКИ ИГРЫ ---
HOLD_T_DURATION = 8.0          # Сколько секунд удерживать Т для сбора улова
AUTOCLICKER_ENABLED = False     # По умолчанию бот на паузе
DISAPPEAR_TIMEOUT = 0.35       # Задержка проверки конца мини-игры (в секундах)

# --- СОСТОЯНИЯ БОТА ---
STATE_WAITING = "WAITING"        # Ожидание появления шкалы (клев)
STATE_MINIGAME = "MINIGAME"      # Процесс ловли
STATE_COLLECTING = "COLLECTING"  # Сбор улова и перезаброс

current_state = STATE_WAITING

# --- ТОЧНАЯ ОБЛАСТЬ ЗАХВАТА ШКАЛЫ (для 1920x1080) ---
CAPTURE_ROI = {
    "top": 280,      # Отступ сверху
    "left": 1380,    # Отступ слева (правая часть экрана)
    "width": 180,    # Ширина захвата
    "height": 480    # Высота захвата
}

# Цветовые диапазоны HSV (Желто-зеленая зона и Белый маркер)
ZONE_HSV_LO, ZONE_HSV_HI = (15, 40, 40), (85, 255, 255)
MARKER_HSV_LO, MARKER_HSV_HI = (0, 0, 150), (180, 60, 255)

ZONE_MIN_AREA = 40
MARKER_MIN_AREA = 80

input_queue = Queue()

def safe_click():
    """Надежный клик для Roblox с задержкой нажатия"""
    pydirectinput.mouseDown()
    time.sleep(0.08)
    pydirectinput.mouseUp()

# --- АСИНХРОННЫЙ ПОТОК ВВОДА ---
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
            
            print("[ПОТОК ВВОДА] Ждем 2 секунды анимации поимки...")
            time.sleep(3.0)
            
            print("[ПОТОК ВВОДА] Зажимаем T для сбора рыбы...")
            pydirectinput.keyDown('t')
            time.sleep(HOLD_T_DURATION)
            pydirectinput.keyUp('t')
            print("[ПОТОК ВВОДА] Улов собран.")
            
            time.sleep(1.0)
            print("[ПОТОК ВВОДА] Забрасываем удочку...")
            safe_click()
            time.sleep(1.5)  # Задержка на анимацию броска
            
            print("[ПОТОК ВВОДА] Готово! Переходим в режим ожидания поклевки...")
            current_state = STATE_WAITING  # Сбрасываем состояние на ожидание
            
        input_queue.task_done()

threading.Thread(target=input_worker, daemon=True).start()

# --- ДЕТЕКЦИЯ ПО ЦВЕТУ (HSV) ---
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

def draw_analysis(bgr, res, fps):
    out = bgr.copy()
    color = (0, 255, 0) if AUTOCLICKER_ENABLED else (0, 0, 255)
    text = f"STATUS: {'ACTIVE' if AUTOCLICKER_ENABLED else 'PAUSED'} [{current_state}]"
    cv2.putText(out, text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    cv2.putText(out, f"{fps:.0f} FPS", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
    
    if res is None: return out
    
    if res.get("zone") is not None:
        zx, zy, zw, zh = res["zone"]
        cv2.rectangle(out, (zx, zy), (zx + zw, zy + zh), (0, 255, 0), 2)
    if res.get("marker") is not None:
        mx, my, mw, mh = res["marker"]
        cv2.rectangle(out, (mx, my), (mx + mw, my + mh), (0, 255, 255), 2)
        
    return out

def toggle_bot():
    global AUTOCLICKER_ENABLED, current_state
    AUTOCLICKER_ENABLED = not AUTOCLICKER_ENABLED
    print(f"\n[БОТ] СТАТУС: {AUTOCLICKER_ENABLED}")
    if AUTOCLICKER_ENABLED:
        current_state = STATE_WAITING
        input_queue.put("click")

# --- ГЛАВНЫЙ ПОТОК ---
def run_live():
    global AUTOCLICKER_ENABLED, current_state
    keyboard.add_hotkey('k', toggle_bot)
    
    is_pressing = False
    last_seen_time = 0.0

    with mss.mss() as sct:
        print("Бот готов (система состояний включена). Нажмите K для старта.")
        prev = time.perf_counter()
        fps = 0.0
        
        while True:
            frame = np.array(sct.grab(CAPTURE_ROI))[:, :, :3]
            res = analyze_static(frame)
            
            now = time.perf_counter()
            fps = 0.9 * fps + 0.1 * (1.0 / max(now - prev, 1e-6))
            prev = now

            out = draw_analysis(frame, res, fps)
            
            if AUTOCLICKER_ENABLED:
                has_scale = (res is not None) and ("zone_bottom" in res or "marker_center_y" in res)
                
                # --- ЛОГИКА СОСТОЯНИЙ ---
                if current_state == STATE_WAITING:
                    # В режиме ожидания МЫ ТОЛЬКО ИЩЕМ ШКАЛУ. Ложные пропажи игнорируются!
                    if has_scale:
                        print("[БОТ] Шкала обнаружена! Начинаем ловлю...")
                        current_state = STATE_MINIGAME
                        last_seen_time = now
                        
                elif current_state == STATE_MINIGAME:
                    # В режиме мини-игры держим маркер в зоне и следим за пропажей шкалы
                    if has_scale:
                        last_seen_time = now  # Обновляем таймер
                        
                        if "marker_center_y" in res and "zone_bottom" in res:
                            m_y = res["marker_center_y"]
                            z_bot = res["zone_bottom"]
                            
                            if m_y > z_bot:
                                if not is_pressing:
                                    input_queue.put("down")
                                    is_pressing = True
                            else:
                                if is_pressing:
                                    input_queue.put("up")
                                    is_pressing = False
                    else:
                        # Шкала пропала во время игры
                        if is_pressing:
                            input_queue.put("up")
                            is_pressing = False
                        
                        # Если шкала отсутствует дольше DISAPPEAR_TIMEOUT, переходим к сбору
                        if (now - last_seen_time) >= DISAPPEAR_TIMEOUT:
                            print(f"[БОТ] Шкала пропала более чем на {DISAPPEAR_TIMEOUT}s. Мини-игра завершена.")
                            current_state = STATE_COLLECTING
                            input_queue.put("collect_sequence")
                            
                elif current_state == STATE_COLLECTING:
                    # Пока идет сбор улова, ничего с кнопками мыши не делаем
                    if is_pressing:
                        input_queue.put("up")
                        is_pressing = False

            else:
                if is_pressing:
                    input_queue.put("up")
                    is_pressing = False

            cv2.imshow("track detector", out)
            if cv2.waitKey(1) & 0xFF in (27, ord('q')):
                break
                
    cv2.destroyAllWindows()

if __name__ == "__main__":
    try:
        run_live()
    except Exception as e:
        print(f"\nОшибка при работе: {e}")
        input()