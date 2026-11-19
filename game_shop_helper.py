# -*- coding: utf-8 -*-
"""
游戏轮换商品辅助脚本
F8：开始/暂停    F9：退出
使用 2560x1440 截图作为参考；识别三个轮换商品位中的目标商品名称。
"""
# 必须在图形/鼠标库导入前启用物理屏幕坐标，避免 Windows 缩放造成坐标偏移。
import ctypes
try:
    dpi_ok = ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except (AttributeError, OSError, TypeError, ValueError):
    dpi_ok = False
if not dpi_ok:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        dpi_ok = True
    except (AttributeError, OSError, TypeError, ValueError):
        pass
if not dpi_ok:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError, TypeError, ValueError):
        pass

import time
import threading
import unicodedata

import cv2
import mss
import numpy as np
import pyautogui
import psutil
import win32gui
import win32process
import keyboard
from game_status_overlay import StatusOverlay

REFERENCE_WIDTH, REFERENCE_HEIGHT = 2560, 1440
# 各商品名称行相对于 2560x1440 参考客户区的位置，避开右侧价格和货币图标。
TARGET_LABEL_ROIS = (
    {"left": 735, "top": 516, "width": 180, "height": 52},
    {"left": 1072, "top": 516, "width": 180, "height": 52},
    {"left": 1408, "top": 516, "width": 180, "height": 52},
)
TARGET_ITEM_NAME = "10小队生命值"
OCR_MIN_CONFIDENCE = 0.65
OCR_LINE_HEIGHT = 48
TARGET_CLICKS = ((865, 375), (1199, 375), (1539, 375))
FEATURED_CLICK = (1905, 375)
REFRESH_CLICK = (1200, 683)
GAME_TITLE_HINTS = ("崩坏：星穹铁道", "Honkai: Star Rail")
GAME_PROCESS_NAMES = {"starrail", "starrail.exe"}
FEATURED_CLICKS = 9
POST_CLICK_CHECK_DELAY = 0.5
REFRESH_WAIT = 0.5


active = threading.Event()
quit_requested = threading.Event()
state_lock = threading.Lock()
last_toggle = 0.0
status_overlay = None
game_hwnd = None
hotkey_thread = None

# 保留 click_at 的 75ms 按住时间，只缩短 pyautogui 每次调用后的额外等待。
pyautogui.PAUSE = 0.03
pyautogui.FAILSAFE = True


def game_window_geometry():
    """定位崩铁窗口并确认它当前就在前台；返回客户区坐标。"""
    global game_hwnd
    if not game_hwnd or not win32gui.IsWindow(game_hwnd):
        game_hwnd = find_game_window()
    hwnd = game_hwnd
    if not hwnd:
        raise RuntimeError("没有找到崩坏：星穹铁道窗口（StarRail 进程）。")
    if win32gui.GetForegroundWindow() != hwnd:
        raise RuntimeError("当前前台不是游戏。请先手动点击游戏窗口，再按 F8；程序不会抢焦点或向命令行点击。")

    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    screen_left, screen_top = win32gui.ClientToScreen(hwnd, (0, 0))
    width, height = right - left, bottom - top
    if width < 1280 or height < 720 or abs(width / height - REFERENCE_WIDTH / REFERENCE_HEIGHT) > 0.02:
        raise RuntimeError(f"游戏客户区是 {width}x{height}，当前脚本需要 16:9 且不低于 1280x720。")
    if status_overlay:
        status_overlay.attach_to_game(hwnd)
    return hwnd, screen_left, screen_top, width, height


def find_game_window():
    """按 March7thAssistant 使用的游戏进程名/窗口标题定位游戏。"""
    candidates = []

    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        try:
            title = win32gui.GetWindowText(hwnd)
            if not title and not win32gui.IsIconic(hwnd):
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process_name = psutil.Process(pid).name().lower()
            title_match = any(hint.lower() in title.lower() for hint in GAME_TITLE_HINTS)
            process_match = process_name in GAME_PROCESS_NAMES
            if title_match or process_match:
                rect = win32gui.GetWindowRect(hwnd)
                area = max(0, rect[2] - rect[0]) * max(0, rect[3] - rect[1])
                if area:
                    candidates.append((area, hwnd))
        except (psutil.Error, win32gui.error, win32process.error):
            pass
        return True

    win32gui.EnumWindows(visit, None)
    return max(candidates, default=(0, None))[1]


def toggle():
    global last_toggle
    now = time.monotonic()
    with state_lock:
        if now - last_toggle < 0.12:
            return
        last_toggle = now
        if active.is_set():
            active.clear()
            if status_overlay:
                status_overlay.set_status("已暂停", "按 F8 继续    F9 退出")
            print("已暂停。F8 继续，F9 退出。")
        else:
            active.set()
            if status_overlay:
                status_overlay.set_status("运行中", "正在定位游戏窗口")
            print("开始运行。快捷键可在命令行或游戏窗口处于前台时使用。")


def stop():
    active.clear()
    quit_requested.set()
    if status_overlay:
        status_overlay.set_status("正在退出", "")
    print("收到 F9，正在退出。")


def pause_with_error(message):
    active.clear()
    if status_overlay:
        status_overlay.set_status("已暂停", str(message))
    print(f"已暂停：{message}")


def hotkey_listener():
    """使用与 March7thAssistant 相同的全局键盘钩子，并以系统轮询作备用。"""
    user32 = ctypes.windll.user32
    f8_was_down = False
    f9_was_down = False
    handles = []
    try:
        def hook_f8(_event):
            print("检测到 F8 全局键盘钩子事件。")
            toggle()

        def hook_f9(_event):
            print("检测到 F9 全局键盘钩子事件。")
            stop()

        handles.append(keyboard.on_press_key("f8", hook_f8, suppress=False))
        handles.append(keyboard.on_press_key("f9", hook_f9, suppress=False))
        print("全局热键监听已启动（键盘钩子 + 系统按键轮询，F8 开始/暂停，F9 退出）。")
    except Exception as exc:
        print(f"系统级键盘钩子不可用，将只使用备用轮询：{exc}")

    while not quit_requested.is_set():
        f8_is_down = bool(user32.GetAsyncKeyState(0x77) & 0x8000)
        f9_is_down = bool(user32.GetAsyncKeyState(0x78) & 0x8000)
        if f8_is_down and not f8_was_down:
            print("检测到 F8 系统按键状态。")
            toggle()
        if f9_is_down and not f9_was_down:
            print("检测到 F9 系统按键状态。")
            stop()
        f8_was_down = f8_is_down
        f9_was_down = f9_is_down
        time.sleep(0.01)

    for handle in handles:
        try:
            keyboard.unhook(handle)
        except Exception:
            pass


def _label_roi(slot, sx, sy):
    roi = TARGET_LABEL_ROIS[slot]
    return (
        round(roi["left"] * sx), round(roi["top"] * sy),
        max(1, round(roi["width"] * sx)), max(1, round(roi["height"] * sy)),
    )


def _recognize_target_label(ocr, image):
    """在已知商品名称行上直接做文字识别，不执行整图文字检测。"""
    if image.shape[0] < OCR_LINE_HEIGHT:
        scale = min(2.0, OCR_LINE_HEIGHT / image.shape[0])
        image = cv2.resize(
            image,
            (max(1, round(image.shape[1] * scale)), OCR_LINE_HEIGHT),
            interpolation=cv2.INTER_CUBIC,
        )

    result = ocr(image, use_det=False, use_cls=False, use_rec=True)
    texts = getattr(result, "txts", ()) or ()
    scores = getattr(result, "scores", ()) or ()
    normalized_target = unicodedata.normalize("NFKC", TARGET_ITEM_NAME)
    best_score = 0.0
    for index, text in enumerate(texts):
        confidence = float(scores[index]) if index < len(scores) else 0.0
        best_score = max(best_score, confidence)
        normalized_text = "".join(
            char for char in unicodedata.normalize("NFKC", str(text))
            if char.isalnum() or "\u4e00" <= char <= "\u9fff"
        )
        if normalized_target in normalized_text and confidence >= OCR_MIN_CONFIDENCE:
            return True, confidence
    return False, best_score


def find_target_slots(sct, ocr):
    """一次截取三个商品名称行，再逐行识别目标名称。"""
    _, left, top, width, height = game_window_geometry()
    sx, sy = width / REFERENCE_WIDTH, height / REFERENCE_HEIGHT
    regions = [_label_roi(slot, sx, sy) for slot in range(len(TARGET_LABEL_ROIS))]
    capture_left = min(region[0] for region in regions)
    capture_top = min(region[1] for region in regions)
    capture_right = max(region[0] + region[2] for region in regions)
    capture_bottom = max(region[1] + region[3] for region in regions)
    frame = np.asarray(sct.grab({
        "left": left + capture_left,
        "top": top + capture_top,
        "width": capture_right - capture_left,
        "height": capture_bottom - capture_top,
    }))
    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

    matches = []
    for slot, (x, y, roi_width, roi_height) in enumerate(regions):
        crop = frame[
            y - capture_top:y - capture_top + roi_height,
            x - capture_left:x - capture_left + roi_width,
        ]
        found, score = _recognize_target_label(ocr, crop)
        if found:
            matches.append((slot, score))
            break
    return matches


def target_still_at_slot(sct, ocr, slot):
    """购买回执只 OCR 刚点击的商品名称行。"""
    _, left, top, width, height = game_window_geometry()
    sx, sy = width / REFERENCE_WIDTH, height / REFERENCE_HEIGHT
    x, y, roi_width, roi_height = _label_roi(slot, sx, sy)
    frame = np.asarray(sct.grab({
        "left": left + x, "top": top + y,
        "width": roi_width, "height": roi_height,
    }))
    image = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    found, score = _recognize_target_label(ocr, image)
    return found, score


def click_at(position):
    if quit_requested.is_set() or not active.is_set():
        return False
    try:
        _, left, top, width, height = game_window_geometry()
    except RuntimeError as exc:
        pause_with_error(exc)
        return False
    if quit_requested.is_set() or not active.is_set():
        return False
    x = left + round(position[0] * width / REFERENCE_WIDTH)
    y = top + round(position[1] * height / REFERENCE_HEIGHT)
    # 货币战争 _tap 使用 75ms 按住后释放；沿用其 press_duration 逻辑。
    try:
        pyautogui.moveTo(x, y)
        pyautogui.mouseDown(x, y)
        time.sleep(0.075)
    except Exception as exc:
        active.clear()
        if status_overlay:
            status_overlay.set_status("已暂停", f"鼠标输入失败：{exc}")
        print(f"鼠标输入失败，已暂停：{exc}")
        return False
    finally:
        try:
            pyautogui.mouseUp()
        except Exception:
            pass
    return True


def wait_active():
    while not active.is_set() and not quit_requested.is_set():
        time.sleep(0.1)
    return not quit_requested.is_set()


def click_target_and_check(sct, ocr, slot):
    if not click_at(TARGET_CLICKS[slot]):
        return None, 0.0
    time.sleep(POST_CLICK_CHECK_DELAY)
    if quit_requested.is_set() or not active.is_set():
        return None, 0.0
    score = 0.0
    try:
        still_present, score = target_still_at_slot(sct, ocr, slot)
    except RuntimeError as exc:
        pause_with_error(exc)
        return None, score
    return not still_present, score


def main():
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        print(f"缺少 RapidOCR 依赖：{exc}")
        print("请在项目目录运行：py -m pip install -r requirements.txt")
        return 2
    hwnd = find_game_window()
    if not hwnd:
        print("没有找到崩坏：星穹铁道窗口（StarRail 进程）。请先打开游戏并进入轮换商品界面。")
        return 2
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    client_size = (right - left, bottom - top)
    if client_size[0] < 1280 or client_size[1] < 720 or abs(client_size[0] / client_size[1] - REFERENCE_WIDTH / REFERENCE_HEIGHT) > 0.02:
        print(f"游戏客户区是 {client_size[0]}x{client_size[1]}，当前脚本需要 16:9 且不低于 1280x720。")
        return 2
    print(f"已定位游戏窗口：{win32gui.GetWindowText(hwnd) or 'StarRail'}")
    print(f"游戏客户区：{client_size[0]}x{client_size[1]}；点击和识别坐标会按此尺寸缩放。")

    print("正在初始化中文文字识别……")
    try:
        ocr_engine = RapidOCR()
        # 预热识别模型，避免第一次扫描商品时卡顿。
        ocr_engine(np.zeros((OCR_LINE_HEIGHT, OCR_LINE_HEIGHT * 4, 3), dtype=np.uint8),
                   use_det=False, use_cls=False, use_rec=True)
    except Exception as exc:
        print(f"中文文字识别初始化失败：{exc}")
        return 2
    print("文字识别已就绪，目标商品：" + TARGET_ITEM_NAME)
    global status_overlay, game_hwnd, hotkey_thread
    game_hwnd = hwnd
    status_overlay = StatusOverlay()
    status_overlay.attach_to_game(hwnd)
    status_overlay.set_status("待机", "先点击游戏窗口，再按 F8")

    print("已就绪。F8 全局开始/暂停，F9 全局退出；操作会绑定到游戏窗口。")
    print("请先手动点击游戏窗口，再按 F8。游戏前台时 F8/F9 均可全局使用。")
    hotkey_thread = threading.Thread(target=hotkey_listener, daemon=True)
    hotkey_thread.start()

    with mss.MSS() as sct:
        while not quit_requested.is_set():
            if not wait_active():
                break

            try:
                if status_overlay:
                    status_overlay.set_status("识别中", "检查三个轮换商品位")
                matches = find_target_slots(sct, ocr_engine)
            except RuntimeError as exc:
                pause_with_error(exc)
                continue
            if matches:
                slot, score = matches[0]
                if status_overlay:
                    status_overlay.set_status("发现目标", f"第 {slot + 1} 格，文字置信度 {score:.2f}，尝试购买")
                print(f"识别到第 {slot + 1} 格目标商品（文字置信度 {score:.2f}），点击对应商品。")
                success, score = click_target_and_check(sct, ocr_engine, slot)
                if success is None:
                    continue
                if not success:
                    if status_overlay:
                        status_overlay.set_status(f"第 {slot + 1} 格购买未确认", "开始点击招牌商品 9 次")
                    print(f"第 {slot + 1} 格目标仍存在或消失未稳定确认（文字置信度 {score:.2f}），点击招牌商品九次后重试。")
                    for _ in range(FEATURED_CLICKS):
                        if quit_requested.is_set() or not active.is_set():
                            break
                        if status_overlay:
                            status_overlay.set_status("招牌商品", f"第 {_ + 1}/9 次点击")
                        if not click_at(FEATURED_CLICK):
                            break
                        time.sleep(0.25)
                    if quit_requested.is_set() or not active.is_set():
                        continue
                    time.sleep(0.7)
                    success, score = click_target_and_check(sct, ocr_engine, slot)
                    if success is None:
                        continue
                    if not success:
                        active.clear()
                        if status_overlay:
                            status_overlay.set_status("已暂停", "重试未成功，请检查游戏状态")
                        print("重试后目标仍在，已暂停。请检查窗口焦点和界面状态，再按 F8 继续或按 F9 退出。")
                        continue
                if status_overlay:
                    status_overlay.set_status("购买成功", "目标商品已消失，准备刷新")
                print("目标商品已消失，视为购买成功。")

            if not active.is_set() or quit_requested.is_set():
                continue
            if status_overlay:
                status_overlay.set_status("正在刷新", "点击刷新轮换商品")
            if not click_at(REFRESH_CLICK):
                continue
            time.sleep(REFRESH_WAIT)

    print("程序已退出。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已中断。")
    finally:
        quit_requested.set()
        if hotkey_thread and hotkey_thread.is_alive():
            hotkey_thread.join(timeout=1.0)
        if status_overlay:
            status_overlay.close()



