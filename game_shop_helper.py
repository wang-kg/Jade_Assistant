# -*- coding: utf-8 -*-
"""
游戏轮换商品辅助脚本
F8：开始/暂停    F9：退出
使用 2560x1440 截图作为参考；识别三个轮换商品位中的目标商品名称。
"""
# 必须在图形/鼠标库导入前启用物理屏幕坐标，避免 Windows 缩放造成坐标偏移。
import ctypes
import json
import os
import subprocess
import threading
import time
import unicodedata
from pathlib import Path

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
STARTUP_POLL_INTERVAL = 0.45
STARTUP_CLICK_WAIT = 1.5
START_GAME_WAIT = 4.0
CLICK_ENTER_WAIT = 6.0
GUIDE_SELECT_WAIT = 1.5
PARTICIPATE_TIMEOUT = 10.0
STARTUP_OCR_WIDTH = 1920
STARTUP_OCR_MIN_CONFIDENCE = 0.42
STARTUP_STABLE_FRAMES = 2
GAME_LOGIN_TIMEOUT = 180.0
GUIDE_OPEN_MAX_RETRIES = 10
GUIDE_OPEN_ESC_WAIT = 0.5
GUIDE_OPEN_F4_WAIT = 1.5
GUIDE_TITLE_REGION = (0, 0, 900, 260)
RESUME_CLICK = (2255, 1280)
BLACK_MARKET_ICON = (325, 350)
GUIDE_TAB_CLICK = (799, 285)
PARTICIPATE_CLICK = (1987, 1172)
DEFAULT_GAME_PATHS = (
    Path(r"C:\Program Files\HoYoPlay\games\Star Rail Games\StarRail.exe"),
    Path(r"C:\Program Files\Genshin Impact\games\Star Rail Games\StarRail.exe"),
)


active = threading.Event()
quit_requested = threading.Event()
state_lock = threading.Lock()
last_toggle = 0.0
status_overlay = None
game_hwnd = None
hotkey_thread = None
game_started_by_helper = False

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
    """按游戏进程名和窗口标题定位游戏。"""
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


def find_game_executable():
    """按设置或 HoYoPlay 常见安装目录查找游戏本体。"""
    settings_path = Path(__file__).with_name("settings.json")
    configured = os.environ.get("JADE_ASSISTANT_GAME_PATH")
    if not configured and settings_path.is_file():
        try:
            configured = json.loads(settings_path.read_text(encoding="utf-8-sig")).get("game_executable")
        except (OSError, ValueError, AttributeError) as exc:
            print(f"无法读取 settings.json：{exc}")
    candidates = [Path(configured)] if configured else []
    candidates.extend(DEFAULT_GAME_PATHS)
    hoyoplay_games = Path(r"C:\Program Files\HoYoPlay\games")
    if hoyoplay_games.is_dir():
        candidates.extend(hoyoplay_games.glob("*/StarRail.exe"))
    for candidate in candidates:
        if candidate.is_file() and candidate.name.lower() == "starrail.exe":
            return candidate.resolve()
    return None


def launch_or_attach_game():
    """游戏已运行就附着；否则启动本体并等待其主窗口。"""
    global game_started_by_helper
    hwnd = find_game_window()
    if hwnd:
        print("检测到游戏已运行，跳过启动。")
    else:
        process_running = any(
            (process.info.get("name") or "").lower() in GAME_PROCESS_NAMES
            for process in psutil.process_iter(["name"])
        )
        if process_running:
            print("检测到游戏进程已运行但窗口尚未出现，等待现有进程，不重复启动或执行登录步骤。")
        else:
            executable = find_game_executable()
            if not executable:
                raise RuntimeError(
                    "找不到 StarRail.exe。请在项目目录创建 settings.json，填写 game_executable 的完整路径。"
                )
            print(f"未检测到游戏进程，正在启动：{executable}")
            try:
                subprocess.Popen([str(executable)], cwd=str(executable.parent))
            except OSError as exc:
                raise RuntimeError(f"启动游戏失败：{exc}") from exc
            game_started_by_helper = True
    deadline = time.monotonic() + 180
    while not quit_requested.is_set() and time.monotonic() < deadline:
        hwnd = find_game_window()
        if hwnd:
            try:
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
                win32gui.SetForegroundWindow(hwnd)
            except win32gui.error:
                pass
            return hwnd
        time.sleep(0.5)
    if quit_requested.is_set():
        return None
    raise RuntimeError("等待游戏窗口超时。请确认游戏已正常启动，再按 F8 重试。")


def _normalize_ui_text(text):
    return "".join(
        char for char in unicodedata.normalize("NFKC", str(text))
        if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )


def _read_game_ui(sct, ocr, region=None):
    """对当前客户区做一次文字检测，并把文字框转为 2560x1440 参考坐标。"""
    _, left, top, width, height = game_window_geometry()
    frame = np.asarray(sct.grab({"left": left, "top": top, "width": width, "height": height}))
    image = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    ocr_width = STARTUP_OCR_WIDTH
    ocr_height = round(ocr_width * height / width)
    if (width, height) != (ocr_width, ocr_height):
        image = cv2.resize(image, (ocr_width, ocr_height), interpolation=cv2.INTER_AREA if width > ocr_width else cv2.INTER_CUBIC)

    # 导航指南时只检测入口所在的局部区域，避免对无关页面文字做整屏 OCR。
    crop_left = crop_top = 0
    if region:
        crop_left = max(0, round(region[0] * ocr_width / REFERENCE_WIDTH))
        crop_top = max(0, round(region[1] * ocr_height / REFERENCE_HEIGHT))
        crop_right = min(ocr_width, round(region[2] * ocr_width / REFERENCE_WIDTH))
        crop_bottom = min(ocr_height, round(region[3] * ocr_height / REFERENCE_HEIGHT))
        image = image[crop_top:crop_bottom, crop_left:crop_right]
        if image.size == 0:
            return []

    result = ocr(image, use_det=True, use_cls=False, use_rec=True)
    texts = getattr(result, "txts", ()) or ()
    scores = getattr(result, "scores", ()) or ()
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []

    # OCR 输入统一到 1920 宽，换算到脚本使用的 2560x1440 参考坐标。
    sx = REFERENCE_WIDTH / ocr_width
    sy = REFERENCE_HEIGHT / ocr_height
    records = []
    for index, (text, box) in enumerate(zip(texts, boxes)):
        confidence = float(scores[index]) if index < len(scores) else 0.0
        normalized = _normalize_ui_text(text)
        if not normalized or confidence < STARTUP_OCR_MIN_CONFIDENCE:
            continue
        points = np.asarray(box, dtype=np.float32).reshape(-1, 2)
        x1, y1 = float(points[:, 0].min()), float(points[:, 1].min())
        x2, y2 = float(points[:, 0].max()), float(points[:, 1].max())
        records.append({
            "text": str(text),
            "normalized": normalized,
            "confidence": confidence,
            "box": ((x1 + crop_left) * sx, (y1 + crop_top) * sy,
                    (x2 + crop_left) * sx, (y2 + crop_top) * sy),
            "center": ((x1 + x2 + 2 * crop_left) * 0.5 * sx,
                       (y1 + y2 + 2 * crop_top) * 0.5 * sy),
        })
    return records


def _find_ui_text(records, phrase, region=None):
    """定位指定文案；也合并同一行相邻 OCR 片段，region 为参考屏幕坐标。"""
    phrase = _normalize_ui_text(phrase).casefold()
    candidates = []
    for record in records:
        x1, y1, x2, y2 = record["box"]
        if region and not (region[0] <= (x1 + x2) / 2 <= region[2] and region[1] <= (y1 + y2) / 2 <= region[3]):
            continue
        candidates.append(record)
        if phrase in record["normalized"].casefold():
            return record

    # OCR 有时会把「货币战争」拆成「货币」「战争」两个相邻文字框。
    # 将同一行、相邻的文字拼起来匹配，点击合并后文字框的中心。
    for first in candidates:
        line = [first]
        text = first["normalized"].casefold()
        _, y1, _, y2 = first["box"]
        line_height = max(1.0, y2 - y1)
        right = first["box"][2]
        for record in sorted(candidates, key=lambda item: item["box"][0]):
            if record is first or record["box"][0] < first["box"][0]:
                continue
            rx1, ry1, rx2, ry2 = record["box"]
            record_height = max(1.0, ry2 - ry1)
            same_line = abs((ry1 + ry2 - y1 - y2) / 2) <= max(18.0, 0.55 * max(line_height, record_height))
            gap = rx1 - right
            if gap < 0:
                continue
            if not same_line or gap > max(42.0, 1.1 * max(line_height, record_height)):
                if same_line and gap >= 0:
                    break
                continue
            line.append(record)
            text += record["normalized"].casefold()
            right = max(right, rx2)
            if phrase in text:
                x1 = min(item["box"][0] for item in line)
                y1 = min(item["box"][1] for item in line)
                x2 = max(item["box"][2] for item in line)
                y2 = max(item["box"][3] for item in line)
                merged = dict(first)
                merged["normalized"] = text
                merged["text"] = "".join(item["text"] for item in line)
                merged["box"] = (x1, y1, x2, y2)
                merged["center"] = ((x1 + x2) / 2, (y1 + y2) / 2)
                return merged
    return None


def _find_currency_start(records):
    for record in records:
        x, y = record["center"]
        if "开始" in record["normalized"] and "货币战争" in record["normalized"] and x > 1600 and y > 1100:
            return record
    return None


def _click_startup(position, detail, wait=STARTUP_CLICK_WAIT):
    if not click_at(position):
        return False
    if status_overlay:
        status_overlay.set_status("自动进入游戏", detail)
    time.sleep(wait)
    return True


def _startup_click_text(record, detail, wait=STARTUP_CLICK_WAIT):
    return _click_startup(record["center"], detail, wait)


def _set_navigation_status(status, detail):
    if status_overlay:
        status_overlay.set_status(status, detail)


def _send_game_key(key):
    if not active.is_set() or quit_requested.is_set() or win32gui.GetForegroundWindow() != game_hwnd:
        return False
    pyautogui.press(key)
    return True


def save_guide_failure(sct):
    """保存未能打开指南页时的游戏截图，供排查导航状态。"""
    _, left, top, width, height = game_window_geometry()
    frame = np.asarray(sct.grab({"left": left, "top": top, "width": width, "height": height}))
    image = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    folder = Path(__file__).resolve().parent / "troubleshoot"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"guide_open_failure_{time.strftime('%Y%m%d_%H%M%S')}.png"
    if not cv2.imwrite(str(path), image):
        raise OSError(f"截图保存失败：{path}")
    return path


def login_to_game(sct, ocr):
    """只负责新启动游戏后的登录页；进入下一阶段后不再识别登录按钮。"""
    deadline = time.monotonic() + GAME_LOGIN_TIMEOUT
    login_step = "start_game"
    stable_key = None
    stable_count = 0

    while not quit_requested.is_set() and time.monotonic() < deadline:
        if not wait_active():
            return False
        try:
            records = _read_game_ui(sct, ocr)
        except RuntimeError as exc:
            if "当前前台不是游戏" in str(exc):
                try:
                    win32gui.SetForegroundWindow(game_hwnd)
                except win32gui.error:
                    pass
                time.sleep(STARTUP_POLL_INTERVAL)
                continue
            pause_with_error(exc)
            return False

        click_enter = _find_ui_text(records, "点击进入", (650, 850, 1900, 1425))
        if login_step == "await_click_enter" and click_enter:
            action_key = "login_click_enter"
            if action_key == stable_key:
                stable_count += 1
            else:
                stable_key, stable_count = action_key, 1
            _set_navigation_status("游戏入场页", "确认“点击进入”")
            if stable_count >= STARTUP_STABLE_FRAMES:
                print("登录阶段识别到“点击进入”，点击后开始打开指南页。")
                if _startup_click_text(click_enter, "点击进入游戏", wait=CLICK_ENTER_WAIT):
                    return True
                stable_key, stable_count = None, 0
        elif login_step == "start_game":
            start_game = _find_ui_text(records, "开始游戏", (500, 300, 2100, 1370))
            candidate = start_game or click_enter
            if candidate:
                action_key = "login_start_game" if start_game else "login_click_enter"
                if action_key == stable_key:
                    stable_count += 1
                else:
                    stable_key, stable_count = action_key, 1
                label = "开始游戏" if start_game else "点击进入"
                _set_navigation_status("游戏登录页", f"确认“{label}”")
                if stable_count >= STARTUP_STABLE_FRAMES:
                    if start_game:
                        print("登录阶段识别到“开始游戏”，点击后只等待入场页。")
                        if _startup_click_text(start_game, "开始游戏", wait=START_GAME_WAIT):
                            login_step = "await_click_enter"
                            stable_key, stable_count = None, 0
                    else:
                        print("登录阶段已出现“点击进入”，点击后开始打开指南页。")
                        if _startup_click_text(click_enter, "点击进入游戏", wait=CLICK_ENTER_WAIT):
                            return True
                        stable_key, stable_count = None, 0
            else:
                stable_key, stable_count = None, 0
                _set_navigation_status("游戏启动中", "等待登录页或入场提示")
        else:
            stable_key, stable_count = None, 0
            _set_navigation_status("游戏入场页", "等待“点击进入”")

        time.sleep(STARTUP_POLL_INTERVAL)

    if not quit_requested.is_set():
        _set_navigation_status("登录阶段超时", "未能确认开始游戏或点击进入")
        print("新启动的游戏在登录阶段超时，停止自动导航。")
    return False


def open_guide_page(sct, ocr):
    """用 ESC→F4 重置/打开星际和平指南，并用标题文字确认页面已打开。"""
    attempts = 0
    while not quit_requested.is_set():
        if not wait_active():
            return False
        try:
            if not _send_game_key("esc"):
                _set_navigation_status("等待游戏焦点", "确认游戏窗口在前台后再打开指南")
                time.sleep(STARTUP_POLL_INTERVAL)
                continue
            time.sleep(GUIDE_OPEN_ESC_WAIT)
            if not _send_game_key("f4"):
                _set_navigation_status("等待游戏焦点", "确认游戏窗口在前台后再打开指南")
                time.sleep(STARTUP_POLL_INTERVAL)
                continue
            attempts += 1
            _set_navigation_status("打开星际和平指南", f"ESC → F4，尝试 {attempts}/{GUIDE_OPEN_MAX_RETRIES}")
            time.sleep(GUIDE_OPEN_F4_WAIT)

            for check in range(STARTUP_STABLE_FRAMES):
                records = _read_game_ui(sct, ocr, GUIDE_TITLE_REGION)
                title = _find_ui_text(records, "星际和平指南", GUIDE_TITLE_REGION)
                if not title:
                    break
                if check == STARTUP_STABLE_FRAMES - 1:
                    _set_navigation_status("指南已打开", "已识别“星际和平指南”标题")
                    print("已识别星际和平指南标题，继续进入货币战争。")
                    return True
                time.sleep(STARTUP_POLL_INTERVAL)
            _set_navigation_status("确认指南页面", "尚未识别到“星际和平指南”，重新尝试 ESC → F4")
        except RuntimeError as exc:
            if "当前前台不是游戏" in str(exc):
                try:
                    win32gui.SetForegroundWindow(game_hwnd)
                except win32gui.error:
                    pass
                time.sleep(STARTUP_POLL_INTERVAL)
                continue
            pause_with_error(exc)
            return False

        if attempts >= GUIDE_OPEN_MAX_RETRIES:
            try:
                screenshot_path = save_guide_failure(sct)
                detail = f"{GUIDE_OPEN_MAX_RETRIES} 次 ESC → F4 后仍未识别到指南标题；截图：{screenshot_path}"
                print(f"指南页面确认失败，停止自动导航。{detail}")
            except Exception as exc:
                detail = f"{GUIDE_OPEN_MAX_RETRIES} 次 ESC → F4 后仍未识别到指南标题；截图保存失败：{exc}"
                print(detail)
            active.clear()
            _set_navigation_status("指南识别失败", detail)
            return False
    return False


def navigate_to_shop(sct, ocr):
    """指南页确认打开后，继续货币战争导航与黑市商店流程。"""
    global game_hwnd
    guide_step = "await_guide_tab"
    guide_step_at = time.monotonic()
    world_interaction_sent = False
    awaiting_blank_continue = False
    stable_key = None
    stable_count = 0

    while not quit_requested.is_set():
        if not wait_active():
            return False
        # 指南页已由前一阶段确认打开；等页面稳定后点击第三个页签。
        if guide_step == "await_guide_tab":
            if time.monotonic() - guide_step_at < 2.0:
                _set_navigation_status("打开指南栏目", "等待页面展开")
                time.sleep(STARTUP_POLL_INTERVAL)
                continue
            if _click_startup(GUIDE_TAB_CLICK, "切换到旷宇纷争指南页", wait=GUIDE_SELECT_WAIT):
                print(f"按固定参考坐标 {GUIDE_TAB_CLICK} 点击第三个指南图标。")
                guide_step = "await_participate"
                guide_step_at = time.monotonic()
                stable_key, stable_count = None, 0
                continue
            time.sleep(STARTUP_POLL_INTERVAL)
            continue

        if guide_step == "await_participate":
            _set_navigation_status("进入货币战争", "点击指南页右下角“前往参与”")
            if _click_startup(PARTICIPATE_CLICK, "前往参与货币战争", wait=1.5):
                print(f"按固定参考坐标 {PARTICIPATE_CLICK} 点击指南页右下角“前往参与”。")
                guide_step = "await_homepage"
                guide_step_at = time.monotonic()
                stable_key, stable_count = None, 0
                continue
            time.sleep(STARTUP_POLL_INTERVAL)
            continue

        try:
            records = _read_game_ui(sct, ocr)

            # 只有前置指南页确认通过后，主程序才会调用本函数。
            shop_button = _find_ui_text(records, "刷新轮换商品", (900, 560, 1500, 820))
            if shop_button:
                print("已确认黑市商店打开，开始自动刷新。")
                _set_navigation_status("黑市商店已就绪", "自动刷新已启动")
                return True

            save_continue = _find_ui_text(records, "继续进度", (1950, 1150, 2535, 1390))
            if save_continue:
                action_key = "save_continue"
                if action_key == stable_key:
                    stable_count += 1
                else:
                    stable_key, stable_count = action_key, 1
                _set_navigation_status("找到已有存档", "确认“继续进度”后点击右侧按钮")
                if stable_count >= STARTUP_STABLE_FRAMES:
                    print("连续识别到“继续进度”，点击右侧继续按钮；不会选择结束并结算。")
                    stable_key, stable_count = None, 0
                    if _click_startup(RESUME_CLICK, "继续已有存档", wait=1.5):
                        awaiting_blank_continue = True
                        continue
                time.sleep(STARTUP_POLL_INTERVAL)
                continue

            # 仅在点击存档「继续进度」后等待这条提示，避免登录页误点。
            if awaiting_blank_continue:
                continue_prompt = _find_ui_text(records, "点击空白处继续", (700, 1000, 1850, 1425))
                if continue_prompt:
                    action_key = "blank_continue_after_resume"
                    if action_key == stable_key:
                        stable_count += 1
                    else:
                        stable_key, stable_count = action_key, 1
                    _set_navigation_status("等待加载提示", "确认“点击空白处继续”")
                    if stable_count >= STARTUP_STABLE_FRAMES:
                        print("存档加载后识别到“点击空白处继续”，点击提示文字。")
                        stable_key, stable_count = None, 0
                        if _startup_click_text(continue_prompt, "继续进入货币战争", wait=CLICK_ENTER_WAIT):
                            awaiting_blank_continue = False
                            continue
                else:
                    stable_key, stable_count = None, 0
                    _set_navigation_status("等待加载提示", "等待“点击空白处继续”出现")
                time.sleep(STARTUP_POLL_INTERVAL)
                continue

            # 货币战争入口右下角的主按钮。点击后等待存档页稳定显示，再识别“继续进度”。
            start_war = _find_currency_start(records)
            if start_war:
                action_key = "start_currency_war"
                if action_key == stable_key:
                    stable_count += 1
                else:
                    stable_key, stable_count = action_key, 1
                _set_navigation_status("货币战争入口", "确认开始按钮")
                if stable_count >= STARTUP_STABLE_FRAMES:
                    print("连续识别到货币战争开始按钮，点击并等待存档页加载。")
                    stable_key, stable_count = None, 0
                    if _startup_click_text(start_war, "开始货币战争", wait=1.5):
                        continue
                time.sleep(STARTUP_POLL_INTERVAL)
                continue

            # 游戏内“黑市主理人”羁绊的标签变亮时，点标签旁边的金色图标。
            bond_label = _find_ui_text(records, "黑市主理人", (70, 240, 500, 470))
            if bond_label:
                action_key = "black_market_bond"
                if action_key == stable_key:
                    stable_count += 1
                else:
                    stable_key, stable_count = action_key, 1
                _set_navigation_status("找到黑市主理人", "确认羁绊后点击旁边的金色图标")
                if stable_count >= STARTUP_STABLE_FRAMES:
                    print("连续识别到“黑市主理人”，点击其旁边的金色图标。")
                    stable_key, stable_count = None, 0
                    if _click_startup(BLACK_MARKET_ICON, "打开黑市主理人商店"):
                        continue
                time.sleep(STARTUP_POLL_INTERVAL)
                continue

            if guide_step == "await_homepage":
                if time.monotonic() - guide_step_at >= PARTICIPATE_TIMEOUT and not world_interaction_sent:
                    # 传送后补按一次 F，完成货币战争入口交互。
                    print("货币战争页面等待超时，按一次 F 完成交互。")
                    if _send_game_key("f"):
                        world_interaction_sent = True
                        guide_step_at = time.monotonic()
                        _set_navigation_status("传送后交互", "已按一次 F，等待货币战争界面")
                        time.sleep(1.0)
                        continue
                else:
                    _set_navigation_status("等待传送", "正在等待货币战争首页")

            # 不输出整屏 OCR 内容；只提示当前导航阶段，避免记录与本工具无关的游戏文字。
            stable_key, stable_count = None, 0
            if status_overlay:
                status_overlay.set_status("等待游戏界面", "识别页面后再执行下一步")
        except RuntimeError as exc:
            if "当前前台不是游戏" in str(exc):
                try:
                    win32gui.SetForegroundWindow(game_hwnd)
                except win32gui.error:
                    pass
            else:
                pause_with_error(exc)
                return False
        time.sleep(STARTUP_POLL_INTERVAL)
    return False


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
    """监听全局 F8/F9；键盘钩子不可用时使用系统按键轮询。"""
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
    print("正在初始化中文文字识别……")
    try:
        ocr_engine = RapidOCR()
        # 预热文字检测和识别模型，避免首次导航扫描时卡顿。
        ocr_engine(np.zeros((540, 960, 3), dtype=np.uint8),
                   use_det=True, use_cls=False, use_rec=True)
        ocr_engine(np.zeros((OCR_LINE_HEIGHT, OCR_LINE_HEIGHT * 4, 3), dtype=np.uint8),
                   use_det=False, use_cls=False, use_rec=True)
    except Exception as exc:
        print(f"中文文字识别初始化失败：{exc}")
        return 2
    print("文字识别已就绪，目标商品：" + TARGET_ITEM_NAME)
    global status_overlay, game_hwnd, hotkey_thread
    status_overlay = StatusOverlay()
    status_overlay.set_status("正在准备游戏", "检测运行状态并进入货币战争")
    hotkey_thread = threading.Thread(target=hotkey_listener, daemon=True)
    hotkey_thread.start()
    active.set()

    with mss.MSS() as sct:
        try:
            # 阶段 1：定位/启动游戏。若游戏已运行，跳过登录阶段。
            game_hwnd = launch_or_attach_game()
            if not game_hwnd:
                return 0
            left, top, right, bottom = win32gui.GetClientRect(game_hwnd)
            client_size = (right - left, bottom - top)
            if client_size[0] < 1280 or client_size[1] < 720 or abs(client_size[0] / client_size[1] - REFERENCE_WIDTH / REFERENCE_HEIGHT) > 0.02:
                raise RuntimeError(f"游戏客户区是 {client_size[0]}x{client_size[1]}，需要 16:9 且不低于 1280x720。")
            print(f"已定位游戏窗口：{win32gui.GetWindowText(game_hwnd) or 'StarRail'}")
            print(f"游戏客户区：{client_size[0]}x{client_size[1]}；点击和识别坐标会按此尺寸缩放。")
            if status_overlay:
                status_overlay.attach_to_game(game_hwnd)

            # 阶段 2：仅对本程序刚启动的游戏执行登录页操作；完成后不再识别登录按钮。
            if game_started_by_helper:
                if not login_to_game(sct, ocr_engine):
                    active.clear()
                    return 0
            else:
                print("使用已运行的游戏，跳过开始游戏/点击进入登录步骤。")

            # 阶段 3：按 ESC → F4，识别“星际和平指南”标题后继续。
            if not open_guide_page(sct, ocr_engine):
                active.clear()
                return 0

            # 阶段 4：指南页确认后只运行货币战争导航；不会回头检查登录状态。
            if not navigate_to_shop(sct, ocr_engine):
                if not quit_requested.is_set():
                    active.clear()
                return 0
        except RuntimeError as exc:
            pause_with_error(exc)
            return 2

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



