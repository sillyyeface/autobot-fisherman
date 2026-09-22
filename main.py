import argparse
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

# Очередь для передачи команд в отдельный поток ввода
input_queue = Queue()

# --- ПАРАМЕТРЫ OPENCV ---
CANNY_LO, CANNY_HI = 40, 120
MIN_SIDE_LEN, MAX_SIDE_THICK = 80, 6
MIN_WIDTH, MAX_WIDTH = 20, 250
MIN_HEIGHT, MAX_HEIGHT = 100, 1400
MIN_ASPECT, MAX_ASPECT = 3.0, 14.0
MIN_Y_OVERLAP, MIN_TICKS = 0.7, 3
SHOW_SCALE = 0.6

# Калиброванные цвета (Желтый+Зеленый для зоны, Белый/Светлый для маркера)
ZONE_HSV_LO, ZONE_HSV_HI = (20, 50, 40), (75, 255, 255)
MARKER_HSV_LO, MARKER_HSV_HI = (0, 0, 160), (180, 45, 255)
ZONE_MIN_AREA, MARKER_MIN_AREA, ROI_PAD = 40, 100, 6

# --- АСИНХРОННЫЙ ПОТОК ВВОДА (ЗАЩИТА ОТ ЗАВИСАНИЙ) ---
def input_worker():
    """Фоновый поток. Делает задержки ввода, не мешая окну OpenCV крутить FPS."""
    while True:
        command = input_queue.get()
        if command == "click":
            pydirectinput.click()
        elif command == "down":
            pydirectinput.mouseDown()
        elif command == "up":
            pydirectinput.mouseUp()
        elif command == "collect_sequence":
            # Асинхронная цепочка: ждем анимацию -> собираем -> закидываем заново
            print("[ПОТОК ВВОДА] Ждем 2 секунды анимации поимки...")
            time.sleep(2.0)
            
            print("[ПОТОК ВВОДА] Зажимаем T для сбора рыбы...")
            pydirectinput.keyDown('t')
            time.sleep(HOLD_T_DURATION)
            pydirectinput.keyUp('t')
            print("[ПОТОК ВВОДА] Улов успешно собран.")
            
            time.sleep(1.0)
            pydirectinput.click()
            print("[ПОТОК ВВОДА] Удочка заброшена на новый круг.")
            
        input_queue.task_done()

# Сразу запускаем фоновый поток при старте скрипта
threading.Thread(target=input_worker, daemon=True).start()

# --- АЛГОРИТМЫ КОМПЬЮТЕРНОГО ЗРЕНИЯ ---
def _edges(bgr):
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.Canny(gray, CANNY_LO, CANNY_HI)

def _vertical_segments(edges):
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 9)))
    vert = cv2.morphologyEx(closed, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 41)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(vert, 8)
    segs = []
    for i in range(1, n):
        x, y, w, h, _ = stats[i]
        if h >= MIN_SIDE_LEN and w <= MAX_SIDE_THICK:
            segs.append((int(x), int(y), int(w), int(h)))
    segs.sort(key=lambda s: s)
    return segs, vert

def _count_ticks(edges, box):
    x, y, w, h = box
    roi = edges[y:y+h, x:x+w]
    if roi.size == 0: return 0
    horiz = cv2.morphologyEx(roi, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 1)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(horiz, 8)
    ticks = 0
    for i in range(1, n):
        _, _, ww, hh, _ = stats[i]
        if ww >= 6 and hh <= 4 and ww < w * 0.8: ticks += 1
    return ticks

def find_track(bgr, debug=False):
    edges = _edges(bgr)
    segs, vert_mask = _vertical_segments(edges)
    candidates = []
    for i in range(len(segs)):
        xa, ya, wa, ha = segs[i]
        for j in range(i + 1, len(segs)):
            xb, yb, wb, hb = segs[j]
            width = (xb + wb) - xa
            if not (MIN_WIDTH <= width <= MAX_WIDTH): continue
            top, bottom = max(ya, yb), min(ya + ha, yb + hb)
            overlap = bottom - top
            if overlap <= 0 or overlap / max(ha, hb) < MIN_Y_OVERLAP: continue
            y0, y1 = min(ya, yb), max(ya + ha, yb + hb)
            height = y1 - y0
            if not (MIN_HEIGHT <= height <= MAX_HEIGHT) or not (MIN_ASPECT <= height/width <= MAX_ASPECT): continue
            box = (xa, y0, width, height)
            ticks = _count_ticks(edges, box)
            if ticks >= MIN_TICKS:
                score = ticks * 10 + (overlap / max(ha, hb)) * 20 + height * 0.05
                candidates.append({"box": box, "ticks": ticks, "score": score})
    return max(candidates, key=lambda c: c["score"]) if candidates else None

def _largest_contour_box(mask, min_area):
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts: return None
    c = max(cnts, key=cv2.contourArea)
    return cv2.boundingRect(c) if cv2.contourArea(c) >= min_area else None

def find_zone(roi_bgr):
    mask = cv2.inRange(cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV), ZONE_HSV_LO, ZONE_HSV_HI)
    return _largest_contour_box(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5,5), np.uint8)), ZONE_MIN_AREA)

def find_marker(roi_bgr):
    mask = cv2.inRange(cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV), MARKER_HSV_LO, MARKER_HSV_HI)
    return _largest_contour_box(cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3,3), np.uint8)), MARKER_MIN_AREA)

def analyze(bgr, debug=False):
    track = find_track(bgr, debug=debug)
    if track is None: return None
    tx, ty, tw, th = track["box"]
    x0, y0 = max(0, tx - ROI_PAD), max(0, ty - ROI_PAD)
    x1, y1 = min(bgr.shape[1], tx + tw + ROI_PAD), min(bgr.shape[0], ty + th + ROI_PAD)
    roi = bgr[y0:y1, x0:x1]
    
    zone_local = find_zone(roi)
    marker_local = find_marker(roi)
    
    def to_global(box):
        if box is None: return None
        x, y, w, h = box
        return (x + x0, y + y0, w, h)
        
    zone = to_global(zone_local)
    marker = to_global(marker_local)
    res = {"track": track["box"], "zone": zone, "marker": marker}
    
    if marker is not None:
        mx, my, mw, mh = marker
        res["marker_center_y"] = my + mh / 2
    if zone is not None:
        zx, zy, zw, zh = zone
        res["zone_top"] = zy
        res["zone_bottom"] = zy + zh
    return res

def draw_analysis(bgr, res):
    out = bgr.copy()
    color = (0, 255, 0) if AUTOCLICKER_ENABLED else (0, 0, 255)
    text = "BOT: ACTIVE (K to Pause)" if AUTOCLICKER_ENABLED else "BOT: PAUSED (K to Start)"
    cv2.putText(out, text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    if res is None: return out
    tx, ty, tw, th = res["track"]
    cv2.rectangle(out, (tx, ty), (tx + tw, ty + th), (0, 0, 255), 2)
    if res.get("zone") is not None:
        zx, zy, zw, zh = res["zone"]
        cv2.rectangle(out, (zx, zy), (zx + zw, zy + zh), (0, 255, 0), 2)
    if res.get("marker") is not None:
        mx, my, mw, mh = res["marker"]
        cv2.rectangle(out, (mx, my), (mx + mw, my + mh), (0, 255, 255), 2)
    return out

def toggle_bot():
    global AUTOCLICKER_ENABLED
    AUTOCLICKER_ENABLED = not AUTOCLICKER_ENABLED
    print(f"\n[БОТ] СТАТУС: {AUTOCLICKER_ENABLED}")
    if AUTOCLICKER_ENABLED:
        input_queue.put("click")

# --- ГЛАВНЫЙ ПОТОК ЗРЕНИЯ ---
def run_live(debug):
    global AUTOCLICKER_ENABLED
    keyboard.add_hotkey('k', toggle_bot)
    is_pressing, track_was_present = False, False
    
    with mss.MSS() as sct:
        monitor = sct.monitors[1]  # Фокусируемся на первом основном мониторе
        print("Запущено успешно. Нажмите K для старта бота.")
        prev = time.perf_counter()
        fps = 0.0
        
        while True:
            cur_time = time.perf_counter()
            frame = np.array(sct.grab(monitor))[:, :, :3]
            res = analyze(frame, debug)
            out = draw_analysis(frame, res)
            
            if AUTOCLICKER_ENABLED:
                if res is not None:
                    # Шкала мини-игры видна на экране — идет ловля
                    track_was_present = True
                    
                    if "marker_center_y" in res and "zone_top" in res and "zone_bottom" in res:
                        m_y = res["marker_center_y"]
                        z_top = res["zone_top"]
                        z_bot = res["zone_bottom"]
                        
                        # ЛОГИКА УМНОГО ЗАЖИМА ЛКМ ДО КАСАНИЯ ГРАНИЦЫ
                        if m_y > z_bot:
                            # Маркер НАХОДИТСЯ НИЖЕ зеленой зоны -> уверенно держим зажим для подъема
                            if not is_pressing:
                                input_queue.put("down")
                                is_pressing = True
                                print("[БОТ] Маркер ниже зоны — зажали ЛКМ")
                                time.sleep(0.3)
                                
                        elif z_top <= m_y <= z_bot:
                            # МАРКЕР КОСНУЛСЯ ИЛИ ВОШЕЛ В ЗОНУ -> мгновенно отжимаем
                            if is_pressing:
                                input_queue.put("up")
                                is_pressing = False
                                print("[БОТ] Касание зеленой зоны — отпустили ЛКМ")
                                
                        elif m_y < z_top:
                            # Предохранитель: если по инерции вылетел выше зоны -> отжимаем
                            if is_pressing:
                                input_queue.put("up")
                                is_pressing = False
                                print("[БОТ] Маркер выше зоны — отпустили ЛКМ")
                    else:
                        # Если маркер на кадр потерялся из виду
                        if is_pressing:
                            input_queue.put("up")
                            is_pressing = False
                else:
                    # Шкалы на экране НЕТ (мини-игра закончилась)
                    if is_pressing:
                        input_queue.put("up")
                        is_pressing = False
                    
                    # Если шкала ТОЛЬКО ЧТО пропала
                    if track_was_present:
                        print("[БОТ] Шкала пропала. Отправляем сбор улова в фоновый поток...")
                        # Задача уходит в фон, главный поток НЕ зависает и продолжает держать 60 FPS
                        input_queue.put("collect_sequence")
                        track_was_present = False
            else:
                if is_pressing:
                    input_queue.put("up")
                    is_pressing = False
                    
            now = time.perf_counter()
            fps = 0.9 * fps + 0.1 * (1.0 / max(now - prev, 1e-6))
            prev = now
            
            cv2.putText(out, f"{fps:.0f} fps", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            cv2.imshow("track detector", cv2.resize(out, None, fx=SHOW_SCALE, fy=SHOW_SCALE))
            
            if cv2.waitKey(1) & 0xFF in (27, ord('q')):
                break
                
    cv2.destroyAllWindows()

if __name__ == "__main__":
    try:
        run_live(False)
    except Exception as e:
        print(f"\nОшибка при работе: {e}")
        input()