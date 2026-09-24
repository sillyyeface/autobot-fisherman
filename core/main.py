"""main entry point: input execution, capture/processing loop, debug visualization and top-level settings.
the fishing algorithm, cv detection and webhook reporting live in fishing.py.

зависимости помимо requirements.txt: pywin32 (win32gui, win32con) - нужен для click-through
overlay-окон (обводка и статус-hud поверх монитора, не перехватывающие клики мыши)."""

import argparse
import time
import threading
from queue import Queue

import cv2
import numpy as np
import pydirectinput
import keyboard
import mss
import tkinter as tk
import win32gui
import win32con

import fishing

# --- НАСТРОЙКИ СТАБИЛЬНОСТИ ИНПУТА / input stability settings ---
pydirectinput.PAUSE = 0.001
pydirectinput.FAILSAFE = False

# --- ГЛАВНЫЕ НАСТРОЙКИ БОТА / main bot settings ---
AUTOCLICKER_ENABLED = False  # по умолчанию бот на паузе

# --- НАСТРОЙКИ HUD-СТАТУСА (всегда включен, не зависит от debug) ---
STATUS_OVERLAY_POS = (20, 20)     # позиция окна статуса на экране (x, y)
STATUS_OVERLAY_PADDING = (10, 8)  # внутренние отступы текста (x, y)

STATE_LABELS = {
    fishing.STATE_WAITING: "WAITING FOR BITE",
    fishing.STATE_MINIGAME: "CATCHING FISH",
    fishing.STATE_COLLECTING: "COLLECTING CATCH",
}

TRANSPARENT_KEY = "#000000"  # черный цвет-ключ для прозрачности click-through окон

input_queue = Queue()
fishing_session = fishing.FishingSession()


def safe_click():
    """надежный клик для Roblox с задержкой нажатия"""
    pydirectinput.mouseDown()
    time.sleep(0.08)
    pydirectinput.mouseUp()


# --- АСИНХРОННЫЙ ПОТОК ВВОДА / async input worker thread ---
def input_worker():
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
            time.sleep(fishing.HOLD_T_DURATION)
            pydirectinput.keyUp('t')
            print("[ПОТОК ВВОДА] Улов собран.")

            fishing.send_catch_webhook_data()

            time.sleep(1.0)
            print("[ПОТОК ВВОДА] Забрасываем удочку...")
            safe_click()
            time.sleep(1.5)  # задержка на анимацию броска

            print("[ПОТОК ВВОДА] Готово! Переходим в режим ожидания поклевки...")
            fishing_session.reset()  # сбрасываем состояние на ожидание

        input_queue.task_done()


threading.Thread(target=input_worker, daemon=True).start()


# --- CLICK-THROUGH ОКНА (общая инфраструктура для overlay и hud) ---
def _make_click_through_window(root, x, y, w, h, transparent=True):
    """создает окно без рамки, всегда поверх остальных, прозрачное и не перехватывающее клики.
    клики/нажатия проходят сквозь него насквозь в игру, окно только рисует поверх экрана."""
    win = tk.Toplevel(root)
    win.overrideredirect(True)          # без рамки/заголовка
    win.attributes("-topmost", True)    # всегда поверх
    if transparent:
        win.configure(bg=TRANSPARENT_KEY)
        win.attributes("-transparentcolor", TRANSPARENT_KEY)  # этот цвет становится полностью прозрачным
    else:
        win.configure(bg="black")
    win.geometry(f"{w}x{h}+{x}+{y}")
    win.update_idletasks()

    # добавляем WS_EX_TRANSPARENT поверх layered-стиля, который уже выставил tkinter
    # для -transparentcolor - это и делает окно click-through (мышь проходит сквозь него)
    hwnd = win32gui.GetParent(win.winfo_id())
    ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    win32gui.SetWindowLong(
        hwnd, win32con.GWL_EXSTYLE, ex_style | win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
    )
    return win


class StatusHUD:
    """всегда включенный click-through индикатор статуса бота. НЕ зависит от режима debug -
    это отдельная, постоянно видимая фича, а не часть отладочной визуализации."""

    def __init__(self, root):
        x, y = STATUS_OVERLAY_POS
        self.x = x
        self.y = y
        self.background = _make_click_through_window(root, x, y, 1, 1, transparent=False)
        self.background.attributes("-alpha", 0.5)
        self.window = _make_click_through_window(root, x, y, 1, 1)
        self.label = tk.Label(
            self.window,
            text="",
            fg="#FFFFFF",
            bg=TRANSPARENT_KEY,
            font=("Consolas", 11, "bold"),
            justify="left",
            anchor="w",
            padx=STATUS_OVERLAY_PADDING[0],
            pady=STATUS_OVERLAY_PADDING[1],
            borderwidth=0,
            highlightthickness=0,
        )
        self.label.pack(fill="both", expand=True)

    def update(self, text):
        self.label.config(text=text)
        self.label.update_idletasks()
        width = self.label.winfo_reqwidth()
        height = self.label.winfo_reqheight()
        geometry = f"{width}x{height}+{self.x}+{self.y}"
        self.background.geometry(geometry)
        self.window.geometry(geometry)


class DebugRectOverlay:
    """debug-режим overlay: рисует рамки зоны/маркера click-through поверх реальной области
    экрана (там же, где CAPTURE_ROI). Только рамки, без текста - текст живет в StatusHUD."""

    def __init__(self, root):
        roi = fishing.CAPTURE_ROI
        self.window = _make_click_through_window(root, roi["left"], roi["top"], roi["width"], roi["height"])
        self.canvas = tk.Canvas(
            self.window,
            width=roi["width"],
            height=roi["height"],
            bg=TRANSPARENT_KEY,
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)

    def update(self, res):
        self.canvas.delete("all")
        roi = fishing.CAPTURE_ROI
        self.canvas.create_rectangle(
            1,
            1,
            roi["width"] - 2,
            roi["height"] - 2,
            outline="#00FFFF",
            width=2,
            dash=(6, 4),
        )
        if res is None:
            return
        if res.get("zone") is not None:
            zx, zy, zw, zh = res["zone"]
            self.canvas.create_rectangle(zx, zy, zx + zw, zy + zh, outline="#00FF00", width=2)
        if res.get("marker") is not None:
            mx, my, mw, mh = res["marker"]
            self.canvas.create_rectangle(mx, my, mx + mw, my + mh, outline="#FFFF00", width=2)


# --- DEBUG: РЕЖИМ ОТДЕЛЬНОГО ОКНА (существующий режим, cv2.imshow с захваченным кадром) ---
def draw_analysis(bgr, res):
    """рисует рамки зоны/маркера на копии захваченного кадра для debug-режима window.
    статус-текст сюда не добавляется - он всегда отдельно показывается через StatusHUD."""
    out = bgr.copy()
    if res is None:
        return out

    if res.get("zone") is not None:
        zx, zy, zw, zh = res["zone"]
        cv2.rectangle(out, (zx, zy), (zx + zw, zy + zh), (0, 255, 0), 2)
    if res.get("marker") is not None:
        mx, my, mw, mh = res["marker"]
        cv2.rectangle(out, (mx, my), (mx + mw, my + mh), (0, 255, 255), 2)

    return out


def toggle_bot():
    """включает/выключает бота по горячей клавише"""
    global AUTOCLICKER_ENABLED
    AUTOCLICKER_ENABLED = not AUTOCLICKER_ENABLED
    print(f"\n[БОТ] СТАТУС: {AUTOCLICKER_ENABLED}")
    if AUTOCLICKER_ENABLED:
        fishing_session.reset()
        input_queue.put("click")


# --- ГЛАВНЫЙ ПОТОК / main capture + processing loop ---
def run_live(debug_mode):
    """
    debug_mode: None (без визуализации, только всегда включенный StatusHUD),
                "overlay" (click-through рамки прямо поверх монитора, дефолт для --debug),
                "window" (старый отдельный cv2-window с захваченным кадром).
    """
    stop_event = threading.Event()
    keyboard.add_hotkey('k', toggle_bot)
    keyboard.add_hotkey('esc', stop_event.set)

    root = tk.Tk()
    root.withdraw()  # корневое tk-окно не показываем, оно нужно только как контейнер

    status_hud = StatusHUD(root)
    debug_overlay = DebugRectOverlay(root) if debug_mode == "overlay" else None

    with mss.mss() as sct:
        print("Бот готов (система состояний включена). Нажмите K для старта, ESC для выхода.")
        prev = time.perf_counter()
        fps = 0.0
        window_positioned = False

        while not stop_event.is_set():
            frame = np.array(sct.grab(fishing.CAPTURE_ROI))[:, :, :3]
            res = fishing.analyze_static(frame)

            now = time.perf_counter()
            fps = 0.9 * fps + 0.1 * (1.0 / max(now - prev, 1e-6))
            prev = now

            # --- статус-hud: всегда включенная фича, не зависит от debug_mode ---
            bot_status = "FISHING BOT ENABLED" if AUTOCLICKER_ENABLED else "FISHING BOT DISABLED"
            current_action = STATE_LABELS.get(fishing_session.current_state, "IDLE")
            status_text = f"{bot_status}\n{current_action}"
            if debug_mode is not None:
                status_text += f"\n{fps:.0f} FPS"
            status_hud.update(status_text)

            if AUTOCLICKER_ENABLED:
                actions = fishing_session.update(res, now)
                for action in actions:
                    input_queue.put(action)
            else:
                if fishing_session.is_pressing:
                    input_queue.put("up")
                    fishing_session.is_pressing = False

            if debug_mode == "window":
                out = draw_analysis(frame, res)
                cv2.imshow("track detector", out)
                if not window_positioned:
                    window_x = max(0, root.winfo_screenwidth() - out.shape[1] - 40)
                    cv2.moveWindow("track detector", window_x, 40)
                    window_positioned = True
                if cv2.waitKey(1) & 0xFF in (27, ord('q')):
                    stop_event.set()
            elif debug_mode == "overlay":
                debug_overlay.update(res)

            root.update()

    if debug_mode == "window":
        cv2.destroyAllWindows()
    root.destroy()


def parse_args():
    parser = argparse.ArgumentParser(description="roblox fishing bot")
    parser.add_argument(
        "--debug",
        nargs="?",
        const="overlay",
        default=None,
        choices=["overlay", "window"],
        help=(
            "режим debug-визуализации. без флага - выключено (виден только StatusHUD). "
            "'--debug' или '--debug overlay' - click-through рамки поверх реального экрана. "
            "'--debug window' - отдельное окно cv2 с захваченным кадром (старый режим)."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        run_live(args.debug)
    except Exception as e:
        print(f"\nОшибка при работе: {e}")
        input()