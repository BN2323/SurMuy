"""Shared native Windows caption styling."""

import ctypes

import win32api
import win32con
import win32gui


def use_native_caption(window):
    def mapped(event):
        if event.widget is window:
            enabled = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(int(window.frame(), 0)), 20,
                ctypes.byref(enabled), ctypes.sizeof(enabled),
            )

    window.bind("<Map>", mapped, add="+")


def center_dialog(window, parent):
    def position():
        if not window.winfo_exists():
            return
        owner = int(parent.frame(), 0)
        dialog = int(window.frame(), 0)
        x1, y1, x2, y2 = win32gui.GetWindowRect(owner)
        center = ((x1 + x2) // 2, (y1 + y2) // 2)
        x1, y1, x2, y2 = win32gui.GetWindowRect(dialog)
        width, height = x2 - x1, y2 - y1
        monitor = win32api.MonitorFromWindow(owner, win32con.MONITOR_DEFAULTTONEAREST)
        left, top, right, bottom = win32api.GetMonitorInfo(monitor)["Work"]
        x = max(left, min(center[0] - width // 2, right - width))
        y = max(top, min(center[1] - height // 2, bottom - height))
        win32gui.SetWindowPos(dialog, 0, x, y, 0, 0,
                              win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)

    window.deiconify()
    window.update_idletasks()
    position()
    window.after_idle(position)
