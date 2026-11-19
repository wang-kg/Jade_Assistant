# -*- coding: utf-8 -*-
"""
游戏轮换商品辅助脚本
F8：开始/暂停    F9：退出
使用 2560x1440 截图作为参考；识别三个轮换商品位中的目标图标。
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

from pathlib import Path
import time
import threading

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
# 坐标相对于 2560x1440 参考截图；在每个商品位内留出图标移动余量。
TARGET_SEARCH_ROIS = (
    {"left": 794, "top": 305, "width": 140, "height": 140},
    {"left": 1129, "top": 305, "width": 140, "height": 140},
    {"left": 1468, "top": 305, "width": 140, "height": 140},
)
TARGET_TEMPLATE_SIZE = (112, 112)
TARGET_CLICKS = ((865, 375), (1199, 375), (1539, 375))
FEATURED_CLICK = (1905, 375)
REFRESH_CLICK = (1200, 683)
GAME_TITLE_HINTS = ("崩坏：星穹铁道", "Honkai: Star Rail")
GAME_PROCESS_NAMES = {"starrail", "starrail.exe"}
FEATURED_CLICKS = 9
MATCH_THRESHOLD = 0.82
POLL_DELAY = 0.25
POST_CLICK_WAIT = 1.6

BASE = Path(__file__).resolve().parent
TEMPLATE_PATHS = (
    BASE / "target_template_left.png",
    BASE / "target_template_mid.png",
    BASE / "target_template_right.png",
)

active = threading.Event()
quit_requested = threading.Event()
state_lock = threading.Lock()
last_toggle = 0.0
status_overlay = None
game_hwnd = None
hotkey_thread = None
_scaled_template_cache = {}

pyautogui.PAUSE = 0.12
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


def _scaled_templates(templates, sx, sy):
    """按客户区尺寸缓存模板缩放结果，避免每轮重复 resize。"""
    width = max(1, round(TARGET_TEMPLATE_SIZE[0] * sx))
    height = max(1, round(TARGET_TEMPLATE_SIZE[1] * sy))
    cache_key = (width, height)
    cached = _scaled_template_cache.get(cache_key)
    if cached is None:
        interpolation = cv2.INTER_AREA if sx < 1 or sy < 1 else cv2.INTER_CUBIC
        cached = tuple(
            template if template.shape[:2] == (height, width)
            else cv2.resize(template, (width, height), interpolation=interpolation)
            for template in templates
        )
        _scaled_template_cache[cache_key] = cached
    return cached


def _score_search_area(gray, offset_x, offset_y, search_roi, sx, sy, templates):
    x = round(search_roi["left"] * sx) - offset_x
    y = round(search_roi["top"] * sy) - offset_y
    width = max(1, round(search_roi["width"] * sx))
    height = max(1, round(search_roi["height"] * sy))
    area = gray[y:y + height, x:x + width]
    score = 0.0
    for template in templates:
        result = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
        score = max(score, float(result.max()))
    return score


def find_target_slots(sct, templates):
    """一次截取三个商品位的总区域并转灰度，再扫描各位，返回匹配的 (槽位, 分数)。"""
    _, left, top, width, height = game_window_geometry()
    sx, sy = width / REFERENCE_WIDTH, height / REFERENCE_HEIGHT
    roi_left = min(roi["left"] for roi in TARGET_SEARCH_ROIS)
    roi_top = min(roi["top"] for roi in TARGET_SEARCH_ROIS)
    roi_right = max(roi["left"] + roi["width"] for roi in TARGET_SEARCH_ROIS)
    roi_bottom = max(roi["top"] + roi["height"] for roi in TARGET_SEARCH_ROIS)
    scaled_left, scaled_top = round(roi_left * sx), round(roi_top * sy)
    scaled_right, scaled_bottom = round(roi_right * sx), round(roi_bottom * sy)
    capture = {
        "left": left + scaled_left,
        "top": top + scaled_top,
        "width": scaled_right - scaled_left,
        "height": scaled_bottom - scaled_top,
    }
    frame = np.asarray(sct.grab(capture))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
    templates = _scaled_templates(templates, sx, sy)

    matches = []
    for slot, search_roi in enumerate(TARGET_SEARCH_ROIS):
        score = _score_search_area(
            gray, scaled_left, scaled_top, search_roi, sx, sy, templates
        )
        if score >= MATCH_THRESHOLD:
            matches.append((slot, score))
    return matches


def target_still_at_slot(sct, templates, slot):
    """购买回执只检查刚点击的商品位，避免重复扫描另外两格。"""
    _, left, top, width, height = game_window_geometry()
    sx, sy = width / REFERENCE_WIDTH, height / REFERENCE_HEIGHT
    search_roi = TARGET_SEARCH_ROIS[slot]
    x, y = round(search_roi["left"] * sx), round(search_roi["top"] * sy)
    roi_width = max(1, round(search_roi["width"] * sx))
    roi_height = max(1, round(search_roi["height"] * sy))
    frame = np.asarray(sct.grab({
        "left": left + x, "top": top + y,
        "width": roi_width, "height": roi_height,
    }))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
    templates = _scaled_templates(templates, sx, sy)
    score = _score_search_area(gray, x, y, search_roi, sx, sy, templates)
    return score >= MATCH_THRESHOLD, score


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


def click_target_and_check(sct, template, slot):
    if not click_at(TARGET_CLICKS[slot]):
        return None, 0.0
    deadline = time.monotonic() + POST_CLICK_WAIT
    score = 1.0
    while time.monotonic() < deadline:
        if quit_requested.is_set() or not active.is_set():
            return None, score
        try:
            still_present, score = target_still_at_slot(sct, template, slot)
        except RuntimeError as exc:
            pause_with_error(exc)
            return None, score
        if not still_present:
            return True, score
        time.sleep(POLL_DELAY)
    try:
        still_present, score = target_still_at_slot(sct, template, slot)
    except RuntimeError as exc:
        pause_with_error(exc)
        return None, score
    return not still_present, score


def main():
    missing_templates = [str(path) for path in TEMPLATE_PATHS if not path.exists()]
    if missing_templates:
        print("找不到目标识别图：" + ", ".join(missing_templates))
        return 2
    templates = [cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) for path in TEMPLATE_PATHS]
    if any(template is None for template in templates):
        print("至少一张目标识别图无法读取。")
        return 2
    if any(template.shape[:2] != (TARGET_TEMPLATE_SIZE[1], TARGET_TEMPLATE_SIZE[0]) for template in templates):
        print(f"目标识别图尺寸必须是 {TARGET_TEMPLATE_SIZE[0]}x{TARGET_TEMPLATE_SIZE[1]}。")
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
                matches = find_target_slots(sct, templates)
            except RuntimeError as exc:
                pause_with_error(exc)
                continue
            if matches:
                slot, score = matches[0]
                if status_overlay:
                    status_overlay.set_status("发现目标", f"第 {slot + 1} 格，匹配度 {score:.2f}，尝试购买")
                print(f"识别到第 {slot + 1} 格目标商品（匹配度 {score:.2f}），点击对应商品。")
                success, score = click_target_and_check(sct, templates, slot)
                if success is None:
                    continue
                if not success:
                    if status_overlay:
                        status_overlay.set_status("购买未确认", "开始点击招牌商品 9 次")
                    print("商品仍在，点击招牌商品九次后重试。")
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
                    success, score = click_target_and_check(sct, templates, slot)
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
            time.sleep(0.9)

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

