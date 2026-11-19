# -*- coding: utf-8 -*-
"""轻量级、鼠标穿透的游戏状态悬浮窗，参考 March7thAssistant 的日志覆盖层。"""
import threading
import win32api
import win32con
import win32gui

_CLASS_NAME = "CodexGameShopStatusOverlay"


class StatusOverlay:
    WIDTH = 380
    HEIGHT = 82

    def __init__(self):
        self._status = "待机"
        self._detail = "F8 开始 / 暂停    F9 退出"
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._hwnd = None
        self._last_geometry = None
        self._closed = False
        self._thread = threading.Thread(target=self._window_thread, daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def _window_thread(self):
        try:
            wc = win32gui.WNDCLASS()
            wc.lpfnWndProc = self._wnd_proc
            wc.hInstance = win32api.GetModuleHandle(None)
            wc.lpszClassName = _CLASS_NAME
            wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
            try:
                win32gui.RegisterClass(wc)
            except win32gui.error:
                pass

            self._hwnd = win32gui.CreateWindowEx(
                win32con.WS_EX_LAYERED
                | win32con.WS_EX_TRANSPARENT
                | win32con.WS_EX_TOOLWINDOW
                | win32con.WS_EX_NOACTIVATE
                | win32con.WS_EX_TOPMOST,
                _CLASS_NAME,
                "",
                win32con.WS_POPUP,
                0, 0, self.WIDTH, self.HEIGHT,
                0, 0, wc.hInstance, None,
            )
            win32gui.SetLayeredWindowAttributes(
                self._hwnd, 0, 225, win32con.LWA_ALPHA
            )
            self._ready.set()
            win32gui.PumpMessages()
        except Exception as exc:
            print(f"状态悬浮窗未能启动：{exc}")
            self._ready.set()

    def _wnd_proc(self, hwnd, message, wparam, lparam):
        if message == win32con.WM_PAINT:
            self._paint(hwnd)
            return 0
        if message == win32con.WM_ERASEBKGND:
            return 1
        if message == win32con.WM_NCHITTEST:
            return win32con.HTTRANSPARENT
        if message == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0
        if message == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, message, wparam, lparam)

    def _paint(self, hwnd):
        hdc, paint = win32gui.BeginPaint(hwnd)
        try:
            rect = win32gui.GetClientRect(hwnd)
            brush = win32gui.CreateSolidBrush(win32api.RGB(22, 25, 36))
            win32gui.FillRect(hdc, rect, brush)
            win32gui.DeleteObject(brush)

            # DEFAULT_GUI_FONT stock object id is 17; not exposed by every pywin32 build.
            font = win32gui.GetStockObject(17)
            old_font = win32gui.SelectObject(hdc, font)
            win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
            with self._lock:
                status, detail = self._status, self._detail
            win32gui.SetTextColor(hdc, win32api.RGB(131, 226, 206))
            win32gui.DrawText(
                hdc, status, -1, (14, 9, rect[2] - 12, 34),
                win32con.DT_LEFT | win32con.DT_SINGLELINE | win32con.DT_VCENTER,
            )
            win32gui.SetTextColor(hdc, win32api.RGB(245, 246, 250))
            win32gui.DrawText(
                hdc, detail, -1, (14, 38, rect[2] - 12, rect[3] - 7),
                win32con.DT_LEFT | win32con.DT_SINGLELINE | win32con.DT_VCENTER | win32con.DT_END_ELLIPSIS,
            )
            win32gui.SelectObject(hdc, old_font)
        finally:
            win32gui.EndPaint(hwnd, paint)

    def attach_to_game(self, game_hwnd):
        if not self._hwnd or not win32gui.IsWindow(game_hwnd):
            return
        left, top, right, bottom = win32gui.GetClientRect(game_hwnd)
        x, y = win32gui.ClientToScreen(game_hwnd, (0, 0))
        overlay_y = y + (bottom - top) - self.HEIGHT - 18
        geometry = (x + 18, overlay_y, self.WIDTH, self.HEIGHT)
        if geometry == self._last_geometry:
            return
        overlay_x, overlay_y, width, height = geometry
        win32gui.SetWindowPos(
            self._hwnd, win32con.HWND_TOPMOST,
            overlay_x, overlay_y, width, height,
            win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
        )
        self._last_geometry = geometry

    def set_status(self, status, detail=""):
        status, detail = str(status), str(detail)
        with self._lock:
            if (status, detail) == (self._status, self._detail):
                return
            self._status, self._detail = status, detail
        if self._hwnd and win32gui.IsWindow(self._hwnd):
            win32gui.InvalidateRect(self._hwnd, None, True)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._hwnd and win32gui.IsWindow(self._hwnd):
            win32gui.PostMessage(self._hwnd, win32con.WM_CLOSE, 0, 0)
        self._thread.join(timeout=1)

