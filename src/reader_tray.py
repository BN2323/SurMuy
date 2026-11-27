"""Windows notification-area controls for SurMuy."""

import win32api
import win32con
import win32gui
from pywintypes import error


class ReaderTray:
    CALLBACK = win32con.WM_APP + 1

    def __init__(self, icon_path, show, quit_app, hotkeys):
        self.show = show
        self.quit_app = quit_app
        self.hotkeys = {}
        self.unavailable_keys = []
        self.registered = False
        self.icon = win32gui.LoadImage(0, str(icon_path), win32con.IMAGE_ICON, 0, 0,
                                       win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
        self.taskbar_created = win32gui.RegisterWindowMessage("TaskbarCreated")
        window_class = win32gui.WNDCLASS()
        window_class.hInstance = win32api.GetModuleHandle(None)
        window_class.lpszClassName = "ScreanReaderTrayWindow"
        window_class.lpfnWndProc = {
            self.CALLBACK: self.on_click,
            self.taskbar_created: self.on_taskbar_created,
            win32con.WM_COMMAND: self.on_command,
            win32con.WM_HOTKEY: self.on_hotkey,
        }
        atom = win32gui.RegisterClass(window_class)
        self.window = win32gui.CreateWindow(atom, "SurMuy notification icon",
                                            0, 0, 0, 0, 0, 0, 0,
                                            window_class.hInstance, None)
        self.add_icon()
        modifiers = win32con.MOD_CONTROL | win32con.MOD_SHIFT | win32con.MOD_NOREPEAT
        for identifier, (key, (label, action)) in enumerate(hotkeys.items(), start=1):
            try:
                win32gui.RegisterHotKey(self.window, identifier, modifiers, key)
            except error:
                self.unavailable_keys.append(label)
            else:
                self.hotkeys[identifier] = action

    def on_hotkey(self, _hwnd, _message, hotkey, _parameter):
        action = self.hotkeys.get(hotkey)
        if action is not None:
            action()
        return 0

    def add_icon(self):
        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, (
                self.window, 1, flags, self.CALLBACK, self.icon, "SurMuy",
            ))
        except error:
            self.registered = False
        else:
            self.registered = True

    def on_taskbar_created(self, *_args):
        self.add_icon()
        return 0

    def on_click(self, hwnd, _message, _icon, mouse_message):
        if mouse_message == win32con.WM_LBUTTONUP:
            self.show()
        elif mouse_message == win32con.WM_RBUTTONUP:
            menu = win32gui.CreatePopupMenu()
            try:
                win32gui.AppendMenu(menu, win32con.MF_STRING, 1, "Open SurMuy")
                win32gui.AppendMenu(menu, win32con.MF_STRING, 2, "Exit")
                x, y = win32gui.GetCursorPos()
                win32gui.SetForegroundWindow(hwnd)
                command = win32gui.TrackPopupMenu(
                    menu, win32con.TPM_RIGHTBUTTON | win32con.TPM_RETURNCMD,
                    x, y, 0, hwnd, None,
                )
                self.on_command(hwnd, win32con.WM_COMMAND, command, 0)
                win32gui.PostMessage(hwnd, win32con.WM_NULL, 0, 0)
            finally:
                win32gui.DestroyMenu(menu)
        return 0

    def on_command(self, _hwnd, _message, command, _parameter):
        if win32api.LOWORD(command) == 1:
            self.show()
        elif win32api.LOWORD(command) == 2:
            self.quit_app()
        return 0

    def close(self):
        try:
            for identifier in self.hotkeys:
                win32gui.UnregisterHotKey(self.window, identifier)
            if self.registered:
                win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self.window, 1))
        finally:
            win32gui.DestroyWindow(self.window)
            win32gui.DestroyIcon(self.icon)
