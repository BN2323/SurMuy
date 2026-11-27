"""Non-activating answers and notifications in the corner of the capture monitor."""

import re
import tkinter as tk
from tkinter import font, ttk

import keyboard
import win32api
import win32con
import win32gui
from pywintypes import error
from code_blocks import insert_highlighted_code
from reply_format import FencedCode, reply_spans


class CornerReply:
    def __init__(self, root, background, foreground, border):
        self.root = root
        self.background = background
        self.foreground = foreground
        self.border = border
        self.window = None
        self.timer = None

    def show(self, answer, point=None, *, copyable=True, show_copy=True, allow_paste=False):
        self.dismiss()
        if point is None:
            try:
                point = win32gui.GetCursorPos()
            except error:
                point = (0, 0)
        monitor = win32api.MonitorFromPoint(point, win32con.MONITOR_DEFAULTTONEAREST)
        left, top, right, bottom = win32api.GetMonitorInfo(monitor)["Work"]
        gap = 20
        width = max(100, min(360, right - left - 2 * gap - 24))

        spans = tuple(reply_spans(answer))
        text = "".join(value for value, _ in spans).strip()
        if not any(style for _, style in spans) and looks_like_python(text):
            spans = ((text, FencedCode("python")),)
        lines = text.splitlines()
        has_code = any(isinstance(style, FencedCode) for _, style in spans)
        code_only = has_code and all(not value.strip() or isinstance(style, FencedCode)
                                     for value, style in spans)
        if has_code:
            width = max(100, min(430, (right - left) * 2 // 5, right - left - 2 * gap - 48))
        persistent = copyable and (has_code or len(text) > 320 or len(lines) > 6)

        window = tk.Toplevel(self.root)
        self.window = window
        window.withdraw()
        window.overrideredirect(True)
        window.configure(background=self.border, padx=1, pady=1)
        content = tk.Frame(window, background=self.background)
        content.pack(fill="both", expand=True)
        if persistent or any(style for _, style in spans):
            column_width = font.Font(root=window, font=("Segoe UI", 11)).measure("0")
            columns = max(8, (width - 38) // max(1, column_width))
            height = len(lines) if code_only else sum(max(1, (len(line) + columns - 1) // columns) for line in lines)
            body = tk.Text(content, wrap="none" if code_only else "word", width=columns,
                           height=min(12, max(1, height)),
                           background=self.background, foreground=self.foreground,
                           font=("Segoe UI", 11),
                           padx=12, pady=9, borderwidth=0, highlightthickness=0)
            body.tag_configure("emphasis", font=("Segoe UI", 11, "bold"))
            body.tag_configure("code", font=("Consolas", 10), background="#252e39",
                               foreground="#d0e5fa")
            body.tag_configure("code_block", wrap="none")
            for value, style in spans:
                if isinstance(style, FencedCode):
                    language = style.language or ("python" if looks_like_python(value) else "")
                    insert_highlighted_code(body, value, language, ("code", "code_block"))
                else:
                    body.insert("end", value, (style,) if style else ())
            body.configure(state="disabled")
            body.pack(side="left", fill="both", expand=True)
            scrollbar = ttk.Scrollbar(content, command=body.yview)

            def scroll_changed(first, last):
                scrollbar.set(first, last)
                if float(first) == 0 and float(last) == 1:
                    scrollbar.pack_forget()
                elif not scrollbar.winfo_manager():
                    scrollbar.pack(side="right", fill="y", before=body)

            body.configure(yscrollcommand=scroll_changed)
            if has_code:
                across = ttk.Scrollbar(window, orient="horizontal", command=body.xview)

                def horizontal_changed(first, last):
                    across.set(first, last)
                    if float(first) == 0 and float(last) == 1:
                        across.pack_forget()
                    elif not across.winfo_manager():
                        across.pack(fill="x", after=content)

                body.configure(xscrollcommand=horizontal_changed)
        else:
            label = tk.Label(content, text=text, justify="left", anchor="w", wraplength=width,
                             background=self.background, foreground=self.foreground,
                             font=("Segoe UI", 11), padx=14, pady=9)
            label.pack(side="left", fill="both", expand=True)
            label.bind("<Button-1>", lambda event: self.dismiss())

        def position():
            window.update_idletasks()
            x = max(left, right - gap - window.winfo_reqwidth())
            y = max(top, bottom - gap - window.winfo_reqheight())
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, x, y, 0, 0,
                                  win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

        if copyable:
            def paste_reply():
                foreground = win32gui.GetForegroundWindow()
                if foreground and foreground != int(window.frame(), 0):
                    keyboard.send("ctrl+v")

            def copy_reply():
                self.root.clipboard_clear()
                self.root.clipboard_append(text)
                if allow_paste:
                    copy.configure(text="Paste", command=paste_reply)
                else:
                    copy.configure(text="Copied")

            actions = tk.Frame(window, background=self.background)
            actions.pack(fill="x")
            controls = [("×", self.dismiss)]
            if show_copy:
                controls.append(("Copy", copy_reply))
            for title, command in controls:
                button = tk.Button(actions, text=title, command=command,
                                   background=self.background, foreground=self.foreground,
                                   activebackground=self.background, activeforeground=self.foreground,
                                   relief="flat", borderwidth=0, highlightthickness=0,
                                   cursor="hand2", font=("Segoe UI Symbol", 12), padx=8)
                button.pack(side="right", padx=(0, 4), pady=(0, 3))
                if command == copy_reply:
                    copy = button
        window.update_idletasks()
        hwnd = int(window.frame(), 0)
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                               style | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE)
        window.deiconify()
        position()
        if not persistent:
            duration = max(8, min(24, 5 + len(text) // 16))
            self.timer = self.root.after(duration * 1000, self.expire)

    def expire(self):
        self.timer = None
        self.dismiss()

    def dismiss(self):
        if self.timer is not None:
            self.root.after_cancel(self.timer)
            self.timer = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


def looks_like_python(text):
    return bool(re.match(r"\s*(?:async\s+)?def\s+[A-Za-z_]\w*\([^\n]*\)(?:\s*->[^\n:]+)?:\s*\n[ \t]+", text))
