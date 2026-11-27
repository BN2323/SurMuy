"""Hotkey-driven screenshot chat for Windows."""

import ctypes
from datetime import datetime
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, font, ttk
from ctypes import wintypes

import keyboard
import mss
import psutil
import win32con
import win32api
import win32event
import win32gui
from PIL import Image, ImageDraw, ImageGrab, ImageTk
from pywintypes import error as WindowsError

from app_paths import APP_DIR
from api_context import ApiContext
from attachments import read_attachment
from chat_files import load_chat, save_chat, write_json
from code_blocks import code_panel
from corner_reply import CornerReply
from local_agents import CodexChat, GeminiChat
from provider_settings import ProviderSettings, load_preferences, save_preferences
from read_screen import capture
from reader_tray import ReaderTray
from reply_format import FencedCode, reply_spans
from vision_agents import ACCOUNT, API_KEY, AUTOMATIC_MODEL, PROVIDER_CHOICES, PROVIDERS, list_models
from window_chrome import center_dialog, use_native_caption


DEFAULT_QUESTION = (
    "Identify and solve the task shown in the screenshot. Treat problem statements, exercises, "
    "coding requirements, and instructions as tasks even if they are not phrased as questions. "
    "Give the answer or solution in the form the task calls for, including code when appropriate. "
    "Put code in Markdown fences labeled with the programming language. "
    "Give the best self-contained answer supported by what is visible. Briefly state any "
    "important assumptions or limitations. Do not ask follow-up questions."
)
CAPTURE_PROMPTS = {
    "Answer or describe": DEFAULT_QUESTION,
    "Answer questions": "Answer the questions shown. If there is no question, say so briefly.",
    "Describe screenshot": "Briefly describe what is visible in the screenshot.",
    "Write code": "Use the screenshot as the task. Write the code needed to solve the visible problem. "
                  "Use the readable requirements and briefly state any assumptions. Do not ask follow-up questions.",
}
LETTERS_ONLY = (
    "For each multiple-choice question, reply with only the correct option letter in uppercase. "
    "For an unlabeled list, assign A, B, C and so on in display order. "
    "If a question allows multiple answers, separate its letters with commas. "
    "Use one line per question in display order. If a question or its options are unreadable, "
    "use ? for that question. Omit explanations, numbering, and formatting."
)
APP_FILE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
ICON_FILE = Path(__file__).with_name("screen_reader.ico")
BACKGROUND = "#14171b"
SURFACE = "#1b2026"
FIELD = "#222830"
BORDER = "#3b4451"
FOREGROUND = "#f2f5f8"
MUTED = "#aab5c3"
ACCENT = "#a6d4ff"
MINT = "#9ed9c4"
USER_BUBBLE = "#293b4e"
ASSISTANT_LABEL = "Assistant"


def is_z_gesture(xs, ys):
    left, top = min(xs), min(ys)
    width, height = max(xs) - left, max(ys) - top
    if min(width, height) < 54 or max(width, height) > 2.2 * min(width, height):
        return False
    points = [((x - left) / width, (y - top) / height) for x, y in zip(xs, ys)]
    corners = ((0, 0), (1, 0), (0, 1), (1, 1))
    if math.dist(points[0], corners[0]) > 0.22 or math.dist(points[-1], corners[-1]) > 0.22:
        return False
    segment = 0
    for x, y in points[1:]:
        if math.dist((x, y), corners[segment + 1]) <= 0.19:
            if segment == 2:
                return True
            segment += 1
        elif abs((y, x + y - 1, y - 1)[segment]) > 0.22:
            return False
    return False


class MessageBubble(tk.Canvas):
    def __init__(self, transcript, spans):
        super().__init__(transcript, background=SURFACE, borderwidth=0, highlightthickness=0)
        self.body_font = font.Font(root=self, font=("Segoe UI", 11))
        self.body = tk.Text(self, font=self.body_font, background=USER_BUBBLE,
                            foreground=FOREGROUND, selectbackground="#476487", wrap="word",
                            borderwidth=0, highlightthickness=0, padx=0, pady=0)
        self.body.tag_configure("emphasis", font=("Segoe UI", 11, "bold"))
        self.body.tag_configure("code", font=("Consolas", 10))
        for text, style in spans:
            self.body.insert("end", text, (style,) if style else ())
        self.body.configure(state="disabled")
        self.text_width = max(map(self.body_font.measure, self.body.get("1.0", "end-1c").split("\n")))
        self.shape = self.create_polygon(0, 0, 0, 0, smooth=True, fill=USER_BUBBLE)
        self.content = self.create_window(12, 8, anchor="nw", window=self.body)
        self.itemconfigure(self.content, height=self.body_font.metrics("linespace"))
        self.configure(height=self.body_font.metrics("linespace") + 16)
        self.body.bind("<Configure>", self.fit_height)
        for widget in (self, self.body):
            widget.bind("<MouseWheel>", self.scroll_transcript)
        self.fit_width(transcript.winfo_width())

    def scroll_transcript(self, event):
        self.master.yview_scroll(-int(event.delta / 120) * 3, "units")
        return "break"

    def fit_width(self, available):
        width = min(max(62, self.text_width + 30), max(62, (available - 24) * 3 // 4))
        self.configure(width=width)
        self.itemconfigure(self.content, width=width - 24)

    def fit_height(self, _event):
        pixels = int(self.body.tk.call(self.body._w, "count", "-update", "-ypixels", "1.0", "end"))
        if pixels <= 0:
            return
        self.itemconfigure(self.content, height=pixels)
        w, h, r = int(self.cget("width")), pixels + 16, 12
        self.configure(height=h)
        self.coords(self.shape, r, 0, w-r, 0, w, 0, w, r, w, h-r, w, h,
                    w-r, h, r, h, 0, h, 0, h-r, 0, r, 0, 0)


def rounded_control(root, fill, outline):
    tile = Image.new("RGB", (72, 72), BACKGROUND)
    ImageDraw.Draw(tile).rounded_rectangle((1, 1, 70, 70), radius=21,
                                           fill=fill, outline=outline, width=3)
    return ImageTk.PhotoImage(tile.resize((24, 24), Image.Resampling.LANCZOS), master=root)


def style_buttons(root, style):
    images = []
    variants = {
        "TButton": (FIELD, "#303a46", "#394756", FOREGROUND),
        "Accent.TButton": (ACCENT, "#c0e1ff", "#94c2f0", "#142333"),
        "Quiet.TButton": (SURFACE, FIELD, "#303a46", FOREGROUND),
        "Icon.TButton": (SURFACE, FIELD, "#303a46", FOREGROUND),
        "Provider.TMenubutton": (FIELD, "#303a46", "#394756", FOREGROUND),
        "Capture.TMenubutton": (ACCENT, "#c0e1ff", "#94c2f0", "#142333"),
    }
    for name, (fill, hover, pressed, foreground) in variants.items():
        backgrounds = []
        edge = fill if fill == ACCENT else BORDER
        for color, outline in ((fill, edge), (hover, BORDER), (pressed, BORDER),
                               (fill, ACCENT), (SURFACE, SURFACE)):
            backgrounds.append(rounded_control(root, color, outline))
        normal, active, down, focus, disabled = backgrounds
        images.extend(backgrounds)
        element = f"{name}.rounded"
        style.element_create(element, "image", normal, ("disabled", disabled),
                             ("pressed", down), ("focus", focus), ("active", active),
                             ("selected", active),
                             border=8, sticky="nsew")
        widget_type = "Menubutton" if name.endswith("TMenubutton") else "Button"
        style.layout(name, [(element, {"sticky": "nsew", "children": [
            (f"{widget_type}.padding", {"sticky": "nsew", "children": [
                (f"{widget_type}.label", {"sticky": "nsew"}),
            ]}),
        ]})])
        style.configure(name, padding=(12, 7), foreground=foreground,
                        font=("Segoe UI Semibold", 10), anchor="center")
        style.map(name, foreground=[("disabled", MUTED)])
    style.configure("Icon.TButton", padding=(5, 2), font=("Segoe UI Symbol", 12))
    style.configure("Small.Icon.TButton", padding=(2, 0), font=("Segoe UI Symbol", 9))
    style.configure("Send.Accent.TButton", padding=(6, 2), font=("Segoe UI Symbol", 12))
    style.configure("Compact.Quiet.TButton", padding=(6, 4), font=("Segoe UI Semibold", 9))
    for name in ("Provider.TMenubutton", "Capture.TMenubutton"):
        style.configure(name, padding=(6, 4), font=("Segoe UI Semibold", 9))
    return images


def style_scrollbars(root, style):
    images = []
    for axis in ("Vertical", "Horizontal"):
        vertical = axis == "Vertical"
        size = (8, 24) if vertical else (24, 8)
        track = ImageTk.PhotoImage(Image.new("RGB", (8, 8), SURFACE), master=root)
        thumbs = []
        for color in ("#526071", "#78899c"):
            tile = Image.new("RGB", size, SURFACE)
            ImageDraw.Draw(tile).rounded_rectangle((1, 1, size[0] - 2, size[1] - 2),
                                                   radius=3, fill=color)
            thumbs.append(ImageTk.PhotoImage(tile, master=root))
        images.extend([track, *thumbs])
        trough, thumb = f"Clean.{axis}.trough", f"Clean.{axis}.thumb"
        sticky = "ns" if vertical else "ew"
        style.element_create(trough, "image", track, sticky="nsew")
        style.element_create(thumb, "image", thumbs[0], ("active", thumbs[1]),
                             border=(2, 5, 2, 5) if vertical else (5, 2, 5, 2), sticky=sticky)
        style.layout(f"{axis}.TScrollbar", [
            (trough, {"sticky": "nsew", "children": [(thumb, {"sticky": sticky})]})
        ])
    return images


def configure_styles(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=("Segoe UI", 10), background=BACKGROUND,
                    foreground=FOREGROUND, focuscolor=ACCENT)
    style.configure("Surface.TFrame", background=SURFACE)
    style.configure("Muted.TLabel", foreground=MUTED, font=("Segoe UI", 9))
    style.configure("Status.TLabel", foreground=MINT, font=("Segoe UI Semibold", 9))
    style.configure("TSeparator", background=BORDER)
    style.configure("TEntry", fieldbackground=FIELD, foreground=FOREGROUND,
                    insertcolor=FOREGROUND, bordercolor=BORDER, lightcolor=FIELD,
                    darkcolor=FIELD, padding=(8, 7))
    style.map("TEntry", bordercolor=[("focus", ACCENT)])
    style.configure("TCombobox", foreground=FOREGROUND, fieldbackground=FIELD, padding=(7, 6),
                    background=FIELD, arrowcolor=ACCENT, bordercolor=BORDER,
                    lightcolor=FIELD, darkcolor=FIELD)
    style.map("TCombobox", fieldbackground=[("readonly", FIELD)],
              selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FOREGROUND)],
              bordercolor=[("focus", ACCENT)])
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", FOREGROUND)
    root.option_add("*TCombobox*Listbox.selectBackground", "#344756")
    images = style_buttons(root, style) + style_scrollbars(root, style)
    normal = rounded_control(root, FIELD, BORDER)
    focus = rounded_control(root, FIELD, ACCENT)
    style.element_create("Composer.field", "image", normal, ("focus", focus),
                         border=8, sticky="nsew")
    style.layout("Composer.TEntry", [("Composer.field", {"sticky": "nsew", "children": [
        ("Entry.padding", {"sticky": "nsew", "children": [
            ("Entry.textarea", {"sticky": "nsew"}),
        ]}),
    ]})])
    style.configure("Composer.TEntry", padding=(10, 4), insertcolor=FOREGROUND, insertwidth=2)
    style.configure("ComposerHint.TLabel", background=FIELD, foreground=MUTED)
    return [*images, normal, focus]


def message_field(parent, value, placeholder):
    entry = ttk.Entry(parent, textvariable=value, style="Composer.TEntry")
    hint = ttk.Label(entry, text=placeholder, style="ComposerHint.TLabel", cursor="xterm")
    entry.placeholder_label = hint
    hint.bind("<Button-1>", lambda event: entry.focus_set())

    def update_hint(*_args):
        if value.get():
            hint.place_forget()
        else:
            hint.place(x=24, rely=0.5, anchor="w")

    trace = value.trace_add("write", update_hint)
    entry.bind("<Destroy>", lambda event: value.trace_remove("write", trace), add="+")
    update_hint()
    return entry


def add_tooltip(widget, message):
    scheduled = None
    tip = None

    def show():
        nonlocal scheduled, tip
        scheduled = None
        tip = tk.Toplevel(widget)
        tip.overrideredirect(True)
        tip.transient(widget.winfo_toplevel())
        tip.geometry(f"+{widget.winfo_rootx() - 92}+{widget.winfo_rooty() + widget.winfo_height() + 3}")
        ttk.Label(tip, text=message, padding=(7, 4)).pack()

    def schedule(_event):
        nonlocal scheduled
        scheduled = widget.after(450, show)

    def hide(_event):
        nonlocal scheduled, tip
        if scheduled is not None:
            widget.after_cancel(scheduled)
            scheduled = None
        if tip is not None:
            tip.destroy()
            tip = None

    widget.bind("<Enter>", schedule, add="+")
    widget.bind("<Leave>", hide, add="+")
    widget.bind("<ButtonPress>", hide, add="+")
    widget.bind("<Destroy>", hide, add="+")


class ScreenChat:
    def __init__(self, root):
        self.root = root
        self.events = queue.SimpleQueue()
        self.image = None
        self.api_context = ApiContext()
        self.attachment_text = ""
        self.attachment_name = tk.StringVar()
        self.history = []
        self.conversation_id = 0
        self.gemini_chat = GeminiChat()
        self.codex_chat = CodexChat()
        self.region = None
        self.busy = False
        self.reply_timer = None
        self.capturing = False
        self.chat_file_dialog = False
        self.session_path = None
        self.recent_sessions = self.read_recent_sessions()
        self.auto_save_new = tk.BooleanVar(value=self.read_auto_save_preference())
        self.provider = tk.StringVar(value="OpenAI")
        self.profiles = {}
        self.catalogs = {}
        self.catalog_errors = {}
        self.settings = None
        for provider in PROVIDER_CHOICES:
            try:
                preferences = load_preferences(provider)
            except (WindowsError, ValueError, KeyError, TypeError):
                continue
            if preferences:
                self.profiles[provider] = preferences
        self.ai_instructions = tk.StringVar()
        self.question_text = tk.StringVar()
        self.capture_response = tk.StringVar(value="Answer or describe")
        self.letters_only = tk.BooleanVar()
        self.show_corner_replies = tk.BooleanVar(value=True)
        self.corner_copy = tk.BooleanVar(value=True)
        self.corner_paste = tk.BooleanVar(value=False)
        self.corner_reply = CornerReply(root, SURFACE, FOREGROUND, BORDER)
        self.status = tk.StringVar(value="Text chat")
        root.withdraw()
        root.title("SurMuy")
        root.iconbitmap(str(ICON_FILE))
        root.configure(background=BACKGROUND)
        root.attributes("-toolwindow", True)
        use_native_caption(root)
        root.geometry("520x472-24-56")
        root.minsize(450, 380)
        root.protocol("WM_DELETE_WINDOW", root.withdraw)
        root.bind("<Escape>", lambda event: root.withdraw())
        root.bind("<Control-n>", lambda event: self.new_chat())
        root.bind("<Control-s>", lambda event: self.chat_file_action(save=True))
        root.bind("<Control-o>", lambda event: self.chat_file_action(save=False))

        self.button_images = configure_styles(root)
        panel = ttk.Frame(root, padding=(12, 9, 12, 8))
        panel.pack(fill="both", expand=True)
        header = ttk.Frame(panel)
        header.pack(fill="x")
        self.capture_label = tk.StringVar(value="Capture ▾")
        capture_box = ttk.Menubutton(header, textvariable=self.capture_label, width=0,
                                     style="Capture.TMenubutton")
        capture_box.pack(side="left")
        capture_menu = tk.Menu(capture_box, tearoff=False)
        capture_box.configure(menu=capture_menu)
        for title, mode, key in (("Capture region", "select", "Ctrl+Shift+1"),
                                 ("Full desktop", "desktop", "Ctrl+Shift+2"),
                                 ("Repeat region", "repeat", "Ctrl+Shift+3")):
            capture_menu.add_command(label=title, accelerator=key,
                                     command=lambda mode=mode: self.start_capture(mode))
        capture_menu.add_separator()
        mouse_capture = tk.BooleanVar(root, value=False)
        gesture_shape = tk.StringVar(root, value="Circle")
        gesture_points = []
        gesture_timer = None
        gesture_poll = None
        gesture_meter = ttk.Frame(panel)
        gesture_hint = tk.StringVar(root, value="Draw a quick circle anywhere on the desktop")
        ttk.Label(gesture_meter, textvariable=gesture_hint, style="Muted.TLabel").pack(anchor="w")
        gesture_progress = ttk.Progressbar(gesture_meter, maximum=100)
        gesture_progress.pack(fill="x", pady=(3, 0))

        def reset_gesture():
            gesture_points.clear()
            gesture_progress["value"] = 0
            gesture_hint.set(f"Draw a quick {gesture_shape.get().lower()} anywhere on the desktop")

        def toggle_mouse_capture():
            nonlocal gesture_poll, gesture_timer
            reset_gesture()
            if mouse_capture.get():
                gesture_meter.pack(fill="x", after=header, pady=(4, 0))
                gesture_poll = root.after(16, poll_circle_capture)
            else:
                if gesture_poll is not None:
                    root.after_cancel(gesture_poll)
                    gesture_poll = None
                if gesture_timer is not None:
                    root.after_cancel(gesture_timer)
                    gesture_timer = None
                    self.corner_reply.dismiss()
                gesture_meter.pack_forget()

        def poll_circle_capture():
            nonlocal gesture_poll
            gesture_poll = root.after(16, poll_circle_capture)
            try:
                x, y = win32gui.GetCursorPos()
            except WindowsError:
                gesture_points.clear()
                return
            capture_on_circle(x, y)

        def finish_circle_capture():
            nonlocal gesture_timer
            gesture_timer = None
            self.corner_reply.dismiss()
            if mouse_capture.get():
                self.start_capture("desktop")

        def capture_on_circle(x, y):
            nonlocal gesture_timer
            if not mouse_capture.get() or gesture_timer is not None or self.busy or self.capturing:
                gesture_points.clear()
                return
            now = time.monotonic()
            while gesture_points and now - gesture_points[0][0] > 1.35:
                gesture_points.pop(0)
            if gesture_points and math.hypot(x - gesture_points[-1][1], y - gesture_points[-1][2]) < 5:
                return
            gesture_points.append((now, x, y))
            if len(gesture_points) < 12:
                gesture_progress["value"] = len(gesture_points) * 3
                gesture_hint.set("Tracking mouse movement…")
                return
            xs = [point[1] for point in gesture_points]
            ys = [point[2] for point in gesture_points]
            if gesture_shape.get() == "Z":
                gesture_hint.set("Draw Z from upper left to lower right")
                gesture_progress["value"] = min(90, len(xs) * 3)
                if is_z_gesture(xs, ys):
                    confirm_gesture(x, y)
                return
            width, height = max(xs) - min(xs), max(ys) - min(ys)
            if min(width, height) < 54 or max(width, height) > 1.8 * min(width, height):
                gesture_progress["value"] = 20
                gesture_hint.set("Make a wider, rounder loop")
                return
            center_x, center_y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            angles = [math.atan2(py - center_y, px - center_x) for _, px, py in gesture_points]
            turns = [math.remainder(b - a, math.tau) for a, b in zip(angles, angles[1:])]
            rotation = sum(turns)
            gesture_progress["value"] = min(96, round(abs(rotation) / math.tau * 100))
            gesture_hint.set("Complete the loop")
            if (abs(rotation) >= 5.7 and abs(rotation) >= 0.9 * sum(map(abs, turns))
                    and math.hypot(x - xs[0], y - ys[0]) <= min(width, height) * 0.28):
                confirm_gesture(x, y)

        def confirm_gesture(x, y):
            nonlocal gesture_timer
            gesture_points.clear()
            gesture_progress["value"] = 100
            message = f"Mouse {gesture_shape.get()} detected"
            gesture_hint.set(message)
            self.corner_reply.show(message, (x, y), copyable=False)
            gesture_timer = root.after(650, finish_circle_capture)

        capture_menu.add_checkbutton(label="Mouse gesture captures desktop", variable=mouse_capture,
                                     command=toggle_mouse_capture)
        shapes = tk.Menu(capture_menu, tearoff=False)
        capture_menu.add_cascade(label="Mouse gesture shape", menu=shapes)
        for shape in ("Circle", "Z"):
            shapes.add_radiobutton(label=shape, value=shape, variable=gesture_shape,
                                   command=reset_gesture)
        response_menu = tk.Menu(capture_menu, tearoff=False)
        capture_menu.add_cascade(label="Capture response", menu=response_menu)
        for response in (*CAPTURE_PROMPTS, "Attach only"):
            response_menu.add_radiobutton(label=response, variable=self.capture_response, value=response)
        capture_menu.add_command(label="AI instructions…", command=self.toggle_instructions)
        capture_menu.add_checkbutton(label="Answer letters only", variable=self.letters_only)
        capture_menu.add_checkbutton(label="Show answers in corner", variable=self.show_corner_replies)
        capture_menu.add_checkbutton(label="Show Copy in corner replies", variable=self.corner_copy)
        capture_menu.add_checkbutton(label="Enable Paste after Copy", variable=self.corner_paste)
        capture_menu.add_separator()
        capture_menu.add_command(label="Hide chat", accelerator="Ctrl+Shift+Space", command=self.toggle)
        self.letters_only.trace_add("write", self.update_capture_label)
        self.ai_instructions.trace_add("write", self.on_instructions_changed)

        settings_button = ttk.Button(header, text="⚙", width=2, style="Icon.TButton",
                                     command=self.open_settings)
        settings_button.pack(side="right")
        add_tooltip(settings_button, "AI settings and models")
        self.model_label = tk.StringVar()
        self.model_value = tk.StringVar()
        model_box = ttk.Menubutton(header, textvariable=self.model_label, width=0,
                                   style="Provider.TMenubutton")
        model_box.pack(side="right", padx=(5, 6))
        self.model_menu = tk.Menu(model_box, tearoff=False, postcommand=self.refresh_model_menu)
        model_box.configure(menu=self.model_menu)
        add_tooltip(model_box, "Choose a model")
        self.provider_label = tk.StringVar()
        self.provider_box = ttk.Menubutton(header, textvariable=self.provider_label, width=0,
                                          style="Provider.TMenubutton")
        self.provider_menu = tk.Menu(self.provider_box, tearoff=False)
        self.provider_box.configure(menu=self.provider_menu)
        self.provider_box.pack(side="right")
        for provider in PROVIDER_CHOICES:
            self.provider_menu.add_radiobutton(label=provider, variable=self.provider, value=provider,
                                               command=self.on_provider_selected)
        self.provider.trace_add("write", self.update_provider_labels)
        self.update_provider_labels()
        self.ocr_status = tk.StringVar(value="Starting OCR…")
        ttk.Separator(panel).pack(fill="x", pady=(7, 7))
        self.instructions_row = ttk.Frame(panel)
        ttk.Label(self.instructions_row, text="AI instructions", style="Muted.TLabel").pack(side="left")
        hide_instructions = ttk.Button(self.instructions_row, text="×", width=2, style="Icon.TButton",
                                       command=self.toggle_instructions)
        hide_instructions.pack(side="right", fill="y", padx=(6, 0))
        add_tooltip(hide_instructions, "Collapse AI instructions")
        self.instructions_entry = message_field(
            self.instructions_row, self.ai_instructions, "How should the AI respond?")
        self.instructions_entry.pack(side="left", fill="both", expand=True, padx=(8, 0))
        transcript = ttk.Frame(panel, style="Surface.TFrame")
        self.transcript = transcript
        self.messages = tk.Text(transcript, wrap="word", state="disabled",
                                height=4, width=1,
                                font=("Segoe UI", 11), padx=12, pady=10, borderwidth=0,
                                highlightthickness=0, background=SURFACE, foreground=FOREGROUND,
                                selectbackground="#354e68", selectforeground=FOREGROUND)
        self.messages.tag_configure("you", foreground=ACCENT, font=("Segoe UI Semibold", 10),
                                    spacing1=7, spacing3=3)
        self.messages.tag_configure("agent", foreground=MINT, font=("Segoe UI Semibold", 10),
                                    spacing1=7, spacing3=5)
        self.messages.tag_configure("body", spacing2=3)
        self.messages.tag_configure("emphasis", font=("Segoe UI", 11, "bold"))
        self.messages.tag_configure("code", font=("Consolas", 10), background=FIELD)
        self.user_message_inset = 110
        self.messages.tag_configure("user_alignment", justify="right", rmargin=4,
                                    lmargin1=self.user_message_inset, lmargin2=self.user_message_inset)
        self.messages.tag_configure("user_bubble", justify="right", rmargin=4, spacing3=7)
        self.messages.tag_configure("agent_alignment", rmargin=34)
        self.messages.tag_configure("empty", foreground=MUTED)
        self.messages.tag_configure("pending", foreground=MUTED)
        self.show_empty_transcript()
        self.scrollbar = ttk.Scrollbar(transcript, command=self.messages.yview)
        self.messages.pack(side="left", fill="both", expand=True)
        self.messages.configure(yscrollcommand=self.update_scrollbar)
        self.messages.bind("<Configure>", self.resize_code_panels, add="+")
        health = ttk.Frame(panel)
        health.pack(side="bottom", fill="x")
        ttk.Label(health, textvariable=self.ocr_status, style="Muted.TLabel").pack(side="right")
        footer = ttk.Frame(panel)
        footer.pack(side="bottom", fill="x", pady=(6, 10))
        self.new_chat_button = ttk.Button(health, text="New chat", width=0, style="Compact.Quiet.TButton",
                                          command=self.new_chat)
        self.new_chat_button.pack(side="left", padx=(0, 6))
        add_tooltip(self.new_chat_button, "New chat  ·  Ctrl+N")
        chat_files = ttk.Menubutton(health, text="Sessions ▾", width=0, style="Provider.TMenubutton")
        chat_files.pack(side="left", padx=(0, 6))
        self.session_menu = tk.Menu(chat_files, tearoff=False, postcommand=self.update_session_menu)
        chat_files.configure(menu=self.session_menu)
        ttk.Label(health, textvariable=self.status, style="Status.TLabel").pack(side="left", padx=(4, 0))
        footer.columnconfigure(1, weight=1)
        self.attachment_row = ttk.Frame(footer)
        self.attachment_row.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 3))
        self.attachment_row.grid_remove()
        self.attachment_label = ttk.Label(self.attachment_row, style="Muted.TLabel")
        self.attachment_label.pack(side="left", padx=(2, 6))
        remove_attachment = ttk.Button(self.attachment_row, text="×", width=1, style="Small.Icon.TButton",
                                       command=self.remove_attachment)
        remove_attachment.pack(side="left")
        add_tooltip(remove_attachment, "Remove attachment")
        attach_button = ttk.Button(footer, text="📎", width=2, style="Icon.TButton",
                                   command=self.choose_attachment)
        attach_button.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        add_tooltip(attach_button, "Attach an image or document")
        self.question = message_field(footer, self.question_text, "Type a message…")
        self.question.grid(row=1, column=1, sticky="nsew")
        self.question.bind("<Return>", lambda event: self.send_message())
        self.question.bind("<Control-v>", self.paste_attachment)
        self.desktop_paste = tk.BooleanVar(root, value=False)
        self.desktop_paste_timer = None
        self.desktop_press = None
        self.desktop_button_down = False
        self.bind_hold_to_paste()
        plane = Image.new("RGBA", (72, 72))
        draw = ImageDraw.Draw(plane)
        draw.polygon(((7, 28), (64, 7), (27, 36)), fill="#142333")
        draw.polygon(((33, 41), (64, 7), (47, 64)), fill="#142333")
        self.send_icon = ImageTk.PhotoImage(plane.resize((18, 18), Image.Resampling.LANCZOS), master=root)
        send = ttk.Button(footer, text="Send", image=self.send_icon, compound="none", width=2,
                          style="Send.Accent.TButton", command=self.send_message)
        send.grid(row=1, column=2, sticky="nsew", padx=(6, 0))
        add_tooltip(send, "Send message  ·  Enter")
        transcript.pack(fill="both", expand=True)

    def settings_open(self):
        return self.settings is not None and self.settings.window.winfo_exists()

    def open_settings(self):
        if self.capturing:
            return
        if self.settings_open():
            self.settings.window.lift()
            return
        self.settings = ProviderSettings(
            self.root, self.provider.get(), self.profiles, self.events, self.update_profile,
        )

    def connection(self, provider):
        profile = self.profiles.get(provider)
        if profile:
            mode = profile["mode"]
            return mode, profile["connections"].get(mode, {})
        if provider == "Gemini" and os.environ.get("GEMINI_API_KEY"):
            return API_KEY, {
                "key": os.environ["GEMINI_API_KEY"],
                "model": os.environ.get("SCREEN_CHAT_GEMINI_MODEL", "gemini-3.8-flash"),
            }
        return next(iter(PROVIDER_CHOICES[provider])), {}

    def provider_ready(self, provider):
        mode, connection = self.connection(provider)
        return mode == ACCOUNT or bool(connection.get("model"))

    def on_provider_selected(self):
        if self.provider_ready(self.provider.get()):
            self.load_model_catalog()
        else:
            self.open_settings()

    def update_provider_labels(self, *_args):
        provider = self.provider.get()
        mode, connection = self.connection(provider)
        model = connection.get("model", AUTOMATIC_MODEL if mode == ACCOUNT else "")
        self.model_value.set(model)
        label = "Auto" if model == AUTOMATIC_MODEL else model or "Model"
        if len(label) > 11:
            label = label[:10] + "…"
        self.provider_label.set(f"{provider} ▾")
        self.model_label.set(f"{label} ▾")

    def update_profile(self, provider, profile, activate, models):
        self.profiles[provider] = profile
        identifier = (provider, profile["mode"])
        self.catalogs.pop(identifier, None)
        self.catalog_errors.pop(identifier, None)
        if models:
            self.catalogs[identifier] = tuple(models)
        if activate:
            self.provider.set(provider)
        self.update_provider_labels()

    def refresh_model_menu(self):
        self.model_menu.delete(0, "end")
        provider = self.provider.get()
        mode, connection = self.connection(provider)
        if not self.provider_ready(provider):
            self.model_menu.add_command(label="Connect provider…", command=self.open_settings)
            return
        identifier = (provider, mode)
        if identifier not in self.catalogs and identifier not in self.catalog_errors:
            self.load_model_catalog()
        models = self.catalogs.get(identifier)
        current = connection.get("model", AUTOMATIC_MODEL if mode == ACCOUNT else "")
        for model in dict.fromkeys([*([current] if current else []), *(models or ())]):
            self.model_menu.add_radiobutton(
                label=model, variable=self.model_value, value=model,
                command=lambda model=model: self.select_model(model),
            )
        self.model_menu.add_separator()
        if identifier in self.catalog_errors:
            self.model_menu.add_command(label="Retry loading models", command=self.load_model_catalog)
        elif models is None:
            self.model_menu.add_command(label="Loading models…", state="disabled")
        else:
            self.model_menu.add_command(label="Refresh models", command=self.load_model_catalog)
        self.model_menu.add_command(label="Connection settings…", command=self.open_settings)

    def load_model_catalog(self):
        provider = self.provider.get()
        mode, connection = self.connection(provider)
        identifier = (provider, mode)
        if (not self.provider_ready(provider) or
                (identifier in self.catalogs and self.catalogs[identifier] is None)):
            return
        self.catalog_errors.pop(identifier, None)
        self.catalogs[identifier] = None
        backend = PROVIDER_CHOICES[provider][mode]

        def load():
            try:
                models = list_models(backend, connection.get("key", ""), base_url=connection.get("base_url", ""))
            except Exception:
                self.events.put((self.model_catalog_loaded, (identifier, connection, None)))
            else:
                self.events.put((self.model_catalog_loaded, (identifier, connection, tuple(models))))

        threading.Thread(target=load, daemon=True).start()

    def model_catalog_loaded(self, identifier, connection, models):
        provider, mode = identifier
        current_mode, current = self.connection(provider)
        if current_mode != mode or current != connection:
            if self.catalogs.get(identifier) is None:
                self.catalogs.pop(identifier, None)
            return
        self.catalogs[identifier] = models or ()
        if models is None:
            self.catalog_errors[identifier] = True
            if self.provider.get() == provider and not self.settings_open() and not self.busy:
                self.status.set("Could not load models. Open connection settings.")

    def select_model(self, model):
        provider = self.provider.get()
        mode, connection = self.connection(provider)
        if model == connection.get("model", AUTOMATIC_MODEL):
            return
        connections = dict(self.profiles.get(provider, {}).get("connections", {}))
        connections[mode] = {**connection, "model": model}
        profile = {"mode": mode, "connections": connections}
        try:
            save_preferences(provider, profile)
        except (WindowsError, TypeError):
            self.status.set("Could not save model choice.")
            self.update_provider_labels()
            return
        self.profiles[provider] = profile
        self.update_provider_labels()

    def update_capture_label(self, *_args):
        custom = self.letters_only.get() or self.ai_instructions.get().strip()
        self.capture_label.set("Capture · ▾" if custom else "Capture ▾")

    def on_instructions_changed(self, *_args):
        self.conversation_id += 1
        self.update_capture_label()

    def update_scrollbar(self, first, last):
        self.scrollbar.set(first, last)
        if float(first) == 0 and float(last) == 1:
            self.scrollbar.pack_forget()
        elif not self.scrollbar.winfo_manager():
            self.scrollbar.pack(side="right", fill="y", before=self.messages)

    def toggle_instructions(self):
        if self.instructions_row.winfo_manager():
            self.instructions_row.pack_forget()
        else:
            self.instructions_row.pack(fill="x", before=self.transcript, pady=(0, 6))
            self.instructions_entry.focus_set()

    def show_empty_transcript(self):
        self.messages.configure(state="normal")
        self.messages.insert("1.0", "Type a message or capture an area to start.", "empty")
        self.messages.configure(state="disabled")
        self.transcript_empty = True

    def reset_chat(self, image=None):
        self.clear_pending_reply()
        self.api_context = ApiContext()
        self.set_attachment("Screenshot" if image is not None else "", image=image)
        self.session_path = None
        self.history.clear()
        self.messages.configure(state="normal")
        self.messages.delete("1.0", "end")
        self.show_empty_transcript()

    def new_chat(self):
        if self.busy or self.capturing:
            return
        self.corner_reply.dismiss()
        self.reset_chat()
        self.question.delete(0, "end")
        self.status.set("Text chat")
        self.question.focus_set()

    def set_attachment(self, name, *, image=None, text="", track=True):
        if track:
            self.api_context.attach(name, image, text)
        self.image = image
        self.attachment_text = text
        self.attachment_name.set(name)
        self.conversation_id += 1
        if name:
            label = name if len(name) <= 48 else name[:45] + "…"
            self.attachment_label.configure(text=label)
            self.attachment_row.grid()
        else:
            self.attachment_row.grid_remove()

    def remove_attachment(self):
        if not self.busy:
            self.set_attachment("")

    def load_attachment_file(self, path):
        try:
            image, text = read_attachment(path)
        except Exception as problem:
            self.status.set(f"Could not attach file: {problem}")
            return
        self.set_attachment(Path(path).name, image=image, text=text)
        self.status.set("Attachment ready")
        self.question.focus_set()

    def choose_attachment(self):
        if self.busy or self.capturing or self.settings_open() or self.chat_file_dialog:
            return
        self.chat_file_dialog = True
        try:
            path = filedialog.askopenfilename(
                parent=self.root, title="Attach an image or document",
                filetypes=[("All files", "*")],
            )
            if path:
                self.load_attachment_file(path)
        finally:
            self.chat_file_dialog = False

    def paste_into_composer(self):
        if self.busy or self.capturing:
            return
        self.question.focus_set()
        if self.paste_attachment() != "break":
            self.question.event_generate("<<Paste>>")

    def bind_hold_to_paste(self):
        timer = None
        origin = (0, 0)

        def paste():
            nonlocal timer
            timer = None
            if self.question.focus_get() == self.question:
                self.paste_into_composer()

        def cancel(_event=None):
            nonlocal timer
            if timer is not None:
                self.question.after_cancel(timer)
                timer = None

        def arm(event):
            nonlocal timer, origin
            cancel()
            if self.desktop_paste.get():
                return
            origin = event.x_root, event.y_root
            timer = self.question.after(3000, paste)

        def cancel_drag(event):
            if math.hypot(event.x_root - origin[0], event.y_root - origin[1]) > 8:
                cancel()

        for widget in (self.question, self.question.placeholder_label):
            widget.bind("<ButtonPress-1>", arm, add="+")
            widget.bind("<B1-Motion>", cancel_drag, add="+")
            for event in ("<ButtonRelease-1>", "<Leave>", "<FocusOut>"):
                widget.bind(event, cancel, add="+")

    def toggle_desktop_paste(self):
        if self.desktop_paste.get():
            self.desktop_button_down = bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)
            self.desktop_paste_timer = self.root.after(30, self.poll_desktop_paste)
            self.status.set("Desktop hold-to-paste enabled")
        else:
            if self.desktop_paste_timer is not None:
                self.root.after_cancel(self.desktop_paste_timer)
                self.desktop_paste_timer = None
            self.desktop_press = None
            self.status.set("Desktop hold-to-paste disabled")

    def poll_desktop_paste(self):
        self.desktop_paste_timer = self.root.after(30, self.poll_desktop_paste)
        down = bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)
        try:
            if not down:
                press, self.desktop_press = self.desktop_press, None
                if press is not None:
                    started, origin, hwnd = press
                    modifiers = (win32con.VK_CONTROL, win32con.VK_SHIFT, win32con.VK_MENU,
                                 win32con.VK_LWIN, win32con.VK_RWIN)
                    if (time.monotonic() - started >= 3 and hwnd == win32gui.GetForegroundWindow()
                            and math.dist(origin, win32gui.GetCursorPos()) <= 10
                            and not any(win32api.GetAsyncKeyState(key) & 0x8000 for key in modifiers)):
                        keyboard.send("ctrl+v")
            elif not self.desktop_button_down:
                hwnd = win32gui.GetForegroundWindow()
                self.desktop_press = (time.monotonic(), win32gui.GetCursorPos(), hwnd) if hwnd else None
            elif self.desktop_press is not None:
                started, origin, hwnd = self.desktop_press
                elapsed = time.monotonic() - started
                foreground = win32gui.GetForegroundWindow()
                if math.dist(origin, win32gui.GetCursorPos()) > 10:
                    self.desktop_press = None
                elif foreground != hwnd:
                    self.desktop_press = (started, origin, foreground) if elapsed < 0.35 and foreground else None
        except (WindowsError, OSError):
            self.desktop_press = None
        finally:
            self.desktop_button_down = down

    def paste_attachment(self, _event=None):
        if self.busy or self.capturing:
            return None
        try:
            clipboard = ImageGrab.grabclipboard()
        except OSError:
            return None
        if isinstance(clipboard, Image.Image):
            self.set_attachment("Pasted image", image=clipboard.convert("RGB"))
            self.status.set("Image attached")
            return "break"
        if isinstance(clipboard, list) and clipboard:
            self.load_attachment_file(clipboard[0])
            return "break"
        return None

    def save_session_file(self, path):
        save_chat(path, self.history, self.image, self.question.get(),
                  context=self.api_context, instructions=self.ai_instructions.get())

    def update_session_menu(self):
        menu = self.session_menu
        menu.delete(0, "end")
        menu.add_checkbutton(label="Desktop hold-to-paste", variable=self.desktop_paste,
                             command=self.toggle_desktop_paste)
        menu.add_separator()
        if self.session_path is not None:
            menu.add_command(label=f"Auto-saving · {self.session_path.name}", state="disabled")
            menu.add_separator()
        menu.add_command(label="Save session", accelerator="Ctrl+S",
                         command=lambda: self.chat_file_action(save=True))
        if self.session_path is not None:
            menu.add_command(label="Save session as…",
                             command=lambda: self.chat_file_action(save=True, save_as=True))
        menu.add_command(label="Saved sessions…", accelerator="Ctrl+O",
                         command=lambda: self.chat_file_action(save=False))
        menu.add_checkbutton(label="Auto-save new sessions", variable=self.auto_save_new,
                             command=self.save_auto_save_preference)
        menu.add_command(label="AI instructions…", command=self.toggle_instructions)

    @staticmethod
    def read_auto_save_preference():
        try:
            saved = json.loads((APP_DIR / "chats" / ".preferences.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(saved, dict) and saved.get("auto_save_new") is True

    def save_auto_save_preference(self):
        try:
            folder = APP_DIR / "chats"
            folder.mkdir(exist_ok=True)
            write_json(folder / ".preferences.json", {"auto_save_new": self.auto_save_new.get()})
        except OSError:
            self.status.set("Could not save the auto-save preference.")

    @staticmethod
    def automatic_session_path(question):
        folder = APP_DIR / "chats"
        folder.mkdir(exist_ok=True)
        clean = re.sub(r"[^\w -]", " ", question)
        title = " ".join(clean.split()[:7])[:55].strip(" .-")
        if not title or os.path.isreserved(title):
            title = datetime.now().strftime("Chat %Y-%m-%d %H%M")
        candidate = folder / f"{title}.chat.json"
        index = 2
        while candidate.exists():
            candidate = folder / f"{title} ({index}).chat.json"
            index += 1
        return candidate

    def read_recent_sessions(self):
        try:
            index = json.loads((APP_DIR / "chats" / ".recent.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [Path(name) for name in index if isinstance(name, str)] if isinstance(index, list) else []

    def available_sessions(self):
        try:
            candidates = sorted((APP_DIR / "chats").glob("*.chat.json"),
                                key=lambda path: path.stat().st_mtime, reverse=True)
        except OSError:
            candidates = []
        return [path for path in dict.fromkeys(self.recent_sessions + candidates) if path.is_file()]

    def show_sessions(self):
        paths = self.available_sessions()
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.title("Saved sessions")
        window.transient(self.root)
        window.attributes("-toolwindow", True)
        use_native_caption(window)
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        if paths:
            listing = ttk.Frame(frame)
            listing.pack(fill="both", expand=True)
            sessions = tk.Listbox(listing, height=min(9, len(paths)), width=38, exportselection=False,
                                  background=SURFACE, foreground=FOREGROUND,
                                  selectbackground="#354e68", selectforeground=FOREGROUND,
                                  font=("Segoe UI", 11), borderwidth=0, highlightthickness=0)
            if len(paths) > 9:
                scroll = ttk.Scrollbar(listing, command=sessions.yview)
                sessions.configure(yscrollcommand=scroll.set)
                scroll.pack(side="right", fill="y")
            sessions.pack(side="left", fill="both", expand=True)
            for path in paths:
                sessions.insert("end", path.name.removesuffix(".chat.json"))
            sessions.selection_set(0)
        else:
            ttk.Label(frame, text="No saved sessions yet.", style="Status.TLabel").pack(anchor="w")
            ttk.Label(frame, text="Start a chat, then use Sessions → Save session.",
                      style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        def close():
            self.chat_file_dialog = False
            window.destroy()

        def open_selected(_event=None):
            selected = sessions.curselection()
            if paths and selected:
                path = paths[selected[0]]
                close()
                self.chat_file_action(save=False, path=path)

        window.protocol("WM_DELETE_WINDOW", close)
        window.bind("<Escape>", lambda event: close())
        if paths:
            ttk.Button(frame, text="Open", style="Accent.TButton", command=open_selected).pack(
                anchor="e", pady=(8, 0))
            sessions.bind("<Double-Button-1>", open_selected)
            sessions.bind("<Return>", open_selected)
        else:
            ttk.Button(frame, text="Close", command=close).pack(anchor="e", pady=(8, 0))
        self.chat_file_dialog = True
        center_dialog(window, self.root)
        window.grab_set()
        if paths:
            sessions.focus_set()

    def choose_session_name(self, folder):
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.title("Save session")
        window.transient(self.root)
        window.attributes("-toolwindow", True)
        use_native_caption(window)
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Session name").pack(anchor="w")
        name = ttk.Entry(frame, width=32)
        name.pack(fill="x", pady=(4, 8))
        name.insert(0, datetime.now().strftime("Chat %Y-%m-%d %H%M"))
        name.selection_range(0, "end")
        message = tk.StringVar(value="Includes any attached screenshot.")
        ttk.Label(frame, textvariable=message, style="Muted.TLabel").pack(anchor="w")
        path = None

        def apply(_event=None):
            nonlocal path
            title = name.get().strip().removesuffix(".chat.json").strip()
            filename = f"{title}.chat.json"
            if not title or os.path.isreserved(filename):
                message.set("Enter a valid session name.")
                return
            candidate = folder / filename
            if candidate.exists():
                message.set("That name is already used.")
                return
            path = candidate
            window.destroy()

        ttk.Button(frame, text="Save", style="Accent.TButton", command=apply).pack(anchor="e", pady=(8, 0))
        window.bind("<Return>", apply)
        window.bind("<Escape>", lambda event: window.destroy())
        center_dialog(window, self.root)
        window.grab_set()
        name.focus_set()
        self.root.wait_window(window)
        return path

    def set_session_path(self, path):
        self.session_path = Path(path)
        if self.session_path in self.recent_sessions:
            self.recent_sessions.remove(self.session_path)
        self.recent_sessions.insert(0, self.session_path)
        try:
            folder = APP_DIR / "chats"
            folder.mkdir(exist_ok=True)
            write_json(folder / ".recent.json", [str(session) for session in self.recent_sessions])
        except OSError:
            pass

    def chat_file_action(self, *, save, save_as=False, path=None):
        if self.busy or self.capturing or self.settings_open() or self.chat_file_dialog:
            return
        if not save and path is None:
            self.show_sessions()
            return
        draft = self.question.get()
        if save and not (self.history or self.attachment_name.get() or draft):
            self.status.set("There is no chat to save yet.")
            return
        folder = APP_DIR / "chats"
        self.chat_file_dialog = True
        try:
            if save:
                path = self.session_path if not save_as else None
                if path is None:
                    path = self.choose_session_name(folder)
                if path:
                    folder.mkdir(exist_ok=True)
                    self.save_session_file(path)
                    self.set_session_path(path)
                    self.status.set("Session saved · Auto-save on")
            else:
                if path:
                    turns, draft, context, missing, instructions = load_chat(path)
                    self.reset_chat()
                    self.api_context = context
                    item = context.attachments.get(context.active)
                    if item:
                        self.set_attachment(item.name, image=item.image, text=item.text, track=False)
                    if instructions is not None:
                        self.ai_instructions.set(instructions)
                    self.set_session_path(path)
                    self.history.extend(turns)
                    for question, answer in turns:
                        self.add_message("You", question, format_reply=True)
                        self.add_message(ASSISTANT_LABEL, answer, format_reply=True)
                    self.question.delete(0, "end")
                    self.question.insert(0, draft)
                    self.status.set("Session opened · Some attachment files are missing" if missing else
                                    "Session opened · Auto-save on")
        except (OSError, ValueError, Image.DecompressionBombError) as problem:
            self.status.set(f"Could not {'save' if save else 'open'} chat: {problem}")
        finally:
            self.chat_file_dialog = False

    def add_message(self, speaker, message, *, format_reply=False):
        self.messages.configure(state="normal")
        if self.transcript_empty:
            self.messages.delete("1.0", "end")
            self.transcript_empty = False
        user_turn = speaker == "You"
        alignment = ("user_alignment",) if user_turn else ("agent_alignment",)
        self.messages.insert("end", f"{speaker}\n", ("you" if user_turn else "agent", *alignment))
        spans = tuple(reply_spans(message)) if format_reply else ((message, None),)
        if user_turn and not any(isinstance(style, FencedCode) for _, style in spans):
            bubble = MessageBubble(self.messages, spans)
            index = self.messages.index("end-1c")
            self.messages.window_create(index, window=bubble)
            self.messages.insert("end", "\n", "user_bubble")
            self.messages.tag_add("user_bubble", index, f"{index} + 1 chars")
            self.messages.configure(state="disabled")
            self.messages.see("end")
            return
        for text, style in spans:
            if isinstance(style, FencedCode):
                margin = self.user_message_inset + 8 if user_turn else 34
                panel = code_panel(self.messages, text, style.language,
                                   width=self.code_panel_width(margin), outline=BORDER,
                                   foreground=FOREGROUND)
                panel.user_turn = user_turn
                index = self.messages.index("end-1c")
                self.messages.window_create(index, window=panel, pady=4)
                self.messages.tag_add(alignment[0], index, f"{index} + 1 chars")
                self.messages.insert("end", "\n", ("body", *alignment))
                continue
            tags = ("body", *alignment, *((style,) if style else ()))
            self.messages.insert("end", text, tags)
        self.messages.insert("end", "\n", ("body", *alignment))
        self.messages.configure(state="disabled")
        self.messages.see("end")

    def code_panel_width(self, margin):
        return max(100, self.messages.winfo_width() - 28 - margin)

    def resize_code_panels(self, _event):
        self.user_message_inset = max(80, self.messages.winfo_width() // 4)
        self.messages.tag_configure("user_alignment", lmargin1=self.user_message_inset,
                                    lmargin2=self.user_message_inset)
        for name in self.messages.window_names():
            panel = self.messages.nametowidget(name)
            if isinstance(panel, MessageBubble):
                panel.fit_width(self.messages.winfo_width())
                continue
            margin = self.user_message_inset + 8 if panel.user_turn else 34
            panel.configure(width=self.code_panel_width(margin))

    def show_pending_reply(self):
        self.messages.configure(state="normal")
        self.messages.mark_set("pending_reply", "end-1c")
        self.messages.mark_gravity("pending_reply", "left")
        self.messages.insert("end", f"{ASSISTANT_LABEL}\n", ("agent", "agent_alignment"))
        self.messages.insert("end", "Thinking", ("body", "pending", "agent_alignment"))
        self.messages.mark_set("pending_dots", "end-1c")
        self.messages.mark_gravity("pending_dots", "left")
        self.messages.insert("end", ".  \n\n", ("body", "pending", "agent_alignment"))
        self.messages.configure(state="disabled")
        self.messages.see("end")
        self.reply_timer = self.root.after(450, self.animate_pending_reply, 2)

    def animate_pending_reply(self, count):
        self.messages.configure(state="normal")
        self.messages.replace("pending_dots", "pending_dots + 3 chars",
                              ("." * count).ljust(3), ("body", "pending", "agent_alignment"))
        self.messages.configure(state="disabled")
        self.reply_timer = self.root.after(450, self.animate_pending_reply, count % 3 + 1)

    def clear_pending_reply(self):
        if self.reply_timer is None:
            return
        self.root.after_cancel(self.reply_timer)
        self.reply_timer = None
        self.messages.configure(state="normal")
        self.messages.delete("pending_reply", "end-1c")
        self.messages.mark_unset("pending_reply", "pending_dots")
        self.messages.configure(state="disabled")

    def toggle(self):
        if self.capturing or self.settings_open() or self.chat_file_dialog:
            return
        if self.root.state() == "withdrawn":
            self.show()
        else:
            self.root.withdraw()

    def show(self):
        self.corner_reply.dismiss()
        self.root.deiconify()
        self.root.lift()
        self.root.after_idle(self.question.focus_force)

    def start_capture(self, mode):
        if self.busy or self.capturing or self.settings_open() or self.chat_file_dialog:
            return
        if self.capture_response.get() != "Attach only" and not self.provider_ready(self.provider.get()):
            self.show()
            self.open_settings()
            return
        if mode == "repeat" and self.region is None:
            self.status.set("Select a region with Ctrl+Shift+1 first.")
            return
        self.capturing = True
        self.corner_reply.dismiss()
        was_visible = self.root.state() != "withdrawn"
        self.root.withdraw()
        self.root.after(110, self.finish_capture, mode, was_visible)

    def finish_capture(self, mode, was_visible):
        restore_chat = was_visible
        try:
            if mode == "select":
                with mss.mss() as desktop:
                    bounds = desktop.monitors[0]
                snapshot = capture(0, None)
                box = self.pick_region(snapshot, bounds)
                if box is None:
                    return
                x1, y1, x2, y2 = box
                self.region = (bounds["left"] + x1, bounds["top"] + y1, x2 - x1, y2 - y1)
                image = snapshot.crop(box)
            else:
                image = capture(0, self.region if mode == "repeat" else None)
            if self.capture_response.get() == "Attach only":
                self.set_attachment("Screenshot", image=image)
                self.status.set("Screenshot attached")
                self.show()
                return
            self.reset_chat(image)
            question = self.capture_prompt()
            self.question.delete(0, "end")
            corner = self.show_corner_replies.get()
            region = self.region if mode in ("select", "repeat") else None
            point = (region[0] + region[2] // 2, region[1] + region[3] // 2) if region else None
            self.send(question, corner=corner, point=point)
            restore_chat = was_visible and not corner
        except Exception as error:
            self.status.set("Capture failed.")
            self.add_message("Capture", str(error))
        finally:
            self.capturing = False
            if restore_chat:
                self.root.deiconify()

    def capture_prompt(self):
        question = self.question.get().strip()
        response = self.capture_response.get()
        code_commands = {"code", "write code", "do the code"}
        if (question.casefold() in code_commands or
                (not question and (response == "Write code" or
                                   self.ai_instructions.get().strip().casefold() in code_commands))):
            return CAPTURE_PROMPTS["Write code"]
        if response == "Write code":
            return f"{CAPTURE_PROMPTS['Write code']}\n\nUser request: {question}"
        if self.letters_only.get() and response != "Describe screenshot":
            return (question or "Answer the multiple-choice questions shown.") + "\n\n" + LETTERS_ONLY
        return question or CAPTURE_PROMPTS[response]

    def pick_region(self, snapshot, bounds):
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.geometry(f'{bounds["width"]}x{bounds["height"]}+0+0')
        canvas = tk.Canvas(overlay, highlightthickness=0, cursor="cross")
        canvas.pack(fill="both", expand=True)
        preview = ImageTk.PhotoImage(snapshot, master=overlay)
        canvas.create_image(0, 0, image=preview, anchor="nw")
        overlay.update_idletasks()
        user32 = ctypes.windll.user32
        hwnd = int(overlay.frame(), 0)
        if not user32.SetWindowPos(ctypes.c_void_p(hwnd), None, bounds["left"], bounds["top"],
                                   0, 0, 0x0001 | 0x0004):
            overlay.destroy()
            raise RuntimeError("Could not position the region selector.")
        overlay.focus_force()
        start = None
        selection = None
        rectangle = canvas.create_rectangle(0, 0, 0, 0, outline="#55caff", width=2)

        def press(event):
            nonlocal start
            start = (event.x, event.y)

        def drag(event):
            if start is not None:
                canvas.coords(rectangle, *start, event.x, event.y)

        def release(event):
            nonlocal selection
            if start is not None:
                x = min(max(event.x, 0), snapshot.width)
                y = min(max(event.y, 0), snapshot.height)
                left, right = sorted((start[0], x))
                top, bottom = sorted((start[1], y))
                if right - left >= 5 and bottom - top >= 5:
                    selection = left, top, right, bottom
            overlay.destroy()

        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", release)
        overlay.bind("<Escape>", lambda event: overlay.destroy())
        self.root.wait_window(overlay)
        return selection

    def send_message(self):
        question = self.question.get().strip()
        if self.busy or self.capturing or not question:
            return
        if not self.provider_ready(self.provider.get()):
            self.open_settings()
            return
        self.question.delete(0, "end")
        self.send(question)

    def send(self, question, *, corner=False, point=None):
        provider = self.provider.get()
        mode, connection = self.connection(provider)
        backend = PROVIDER_CHOICES[provider][mode]
        image = self.image
        history = tuple(self.history)
        attachment = {"name": self.attachment_name.get(), "text": self.attachment_text}
        api_state = self.api_context
        conversation_id = self.conversation_id
        instructions = self.ai_instructions.get().strip()
        prompt = f"{question}\n\nAdditional instructions:\n{instructions}" if instructions else question
        self.busy = True
        self.new_chat_button.state(["disabled"])
        self.status.set(f"Waiting for {provider}…")
        self.add_message("You", question, format_reply=True)
        self.show_pending_reply()

        def request():
            try:
                options = {"key": connection.get("key", "")} if mode == API_KEY else {}
                if connection.get("model"):
                    options["model"] = connection["model"]
                if mode == API_KEY:
                    options.update(api_state=api_state, instructions=instructions,
                                   context_budget=connection.get("context_budgets", {}).get(connection.get("model")),
                                   diagnostics=self.record_api_usage)
                elif attachment["text"]:
                    options["attachment"] = attachment
                if provider == "Custom API":
                    options["base_url"] = connection["base_url"]
                if backend == "Gemini sign-in":
                    options.update(client=self.gemini_chat, conversation_id=conversation_id)
                elif backend == "Codex":
                    options.update(client=self.codex_chat, conversation_id=conversation_id)
                answer = PROVIDERS[backend](image, history, question if mode == API_KEY else prompt, **options)
                if not answer:
                    raise RuntimeError("The agent returned no text.")
                if mode == ACCOUNT:
                    api_state.note_attachment(api_state.pending, len(history))
            except Exception as error:
                self.events.put((self.show_error, (provider, question, str(error), corner, point)))
            else:
                self.events.put((self.show_answer, (provider, question, answer, corner, point)))

        threading.Thread(target=request, daemon=True).start()

    @staticmethod
    def record_api_usage(usage):
        try:
            folder = APP_DIR / "logs"
            folder.mkdir(exist_ok=True)
            with (folder / "api-usage.jsonl").open("a", encoding="utf-8") as output:
                output.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **usage}) + "\n")
        except OSError:
            pass

    def show_error(self, provider, question, error, corner, point):
        self.clear_pending_reply()
        self.busy = False
        self.new_chat_button.state(["!disabled"])
        self.status.set(f"{provider} request failed.")
        self.add_message(ASSISTANT_LABEL, error)
        if not self.question.get():
            self.question.insert(0, question)
        if corner and self.root.state() == "withdrawn":
            self.corner_reply.show(f"{provider} request failed. Open chat for details.", point)

    def show_answer(self, provider, question, answer, corner, point):
        self.clear_pending_reply()
        self.busy = False
        self.new_chat_button.state(["!disabled"])
        self.history.append((question, answer))
        self.status.set(f"{provider} replied." if self.image is None else
                        f"{provider} replied · Screenshot attached")
        self.add_message(ASSISTANT_LABEL, answer, format_reply=True)
        if corner and self.root.state() == "withdrawn":
            self.corner_reply.show(answer, point, show_copy=self.corner_copy.get(),
                                   allow_paste=self.corner_paste.get())
        if self.session_path is not None:
            try:
                self.save_session_file(self.session_path)
            except (OSError, ValueError) as problem:
                self.status.set(f"Could not auto-save session: {problem}")
            else:
                self.status.set(f"{provider} replied · Session saved")
        elif self.auto_save_new.get():
            try:
                path = self.automatic_session_path(self.history[0][0])
                self.save_session_file(path)
                self.set_session_path(path)
            except (OSError, ValueError) as problem:
                self.status.set(f"Could not auto-save session: {problem}")
            else:
                self.status.set(f"{provider} replied · Session saved")

    def drain_events(self):
        while not self.events.empty():
            handler, arguments = self.events.get_nowait()
            handler(*arguments)
        self.root.after(30, self.drain_events)


def open_app_mutex(kernel, build_name, show_event):
    mutex_name = "Local\\ScreanReaderScreenChat"
    deadline = time.monotonic() + 10
    exit_requested = False
    while True:
        mutex = kernel.CreateMutexW(None, False, mutex_name)
        error = ctypes.get_last_error()
        if not mutex:
            raise ctypes.WinError(error)
        if error != 183:
            return mutex
        kernel.CloseHandle(mutex)

        current_build = kernel.OpenEventW(0x00100000, False, build_name)
        if current_build:
            kernel.CloseHandle(current_build)
            kernel.SetEvent(show_event)
            return None

        # Earlier versions can exit gracefully through the same command as their tray menu.
        window = win32gui.FindWindow("ScreanReaderTrayWindow", None)
        if window and not exit_requested:
            try:
                win32gui.PostMessage(window, win32con.WM_COMMAND, 2, 0)
            except WindowsError:
                pass
            exit_requested = True
        if time.monotonic() >= deadline:
            win32gui.MessageBox(0, "An older copy is still running. Exit it from the tray and reopen this shortcut.",
                                "SurMuy", win32con.MB_ICONINFORMATION)
            return None
        time.sleep(0.15)


def main():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateEventW.restype = wintypes.HANDLE
    kernel.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.OpenEventW.restype = wintypes.HANDLE
    kernel.SetEvent.argtypes = (wintypes.HANDLE,)
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    show_event = kernel.CreateEventW(None, False, False, "Local\\ScreanReaderShowChat")
    if not show_event:
        raise ctypes.WinError(ctypes.get_last_error())
    mutex = None
    build_marker = None
    try:
        info = APP_FILE.stat()
        build_name = f"Local\\ScreanReaderBuild-{info.st_mtime_ns:x}-{info.st_size:x}"
        mutex = open_app_mutex(kernel, build_name, show_event)
        if not mutex:
            return
        build_marker = kernel.CreateEventW(None, True, False, build_name)
        if not build_marker:
            raise ctypes.WinError(ctypes.get_last_error())
        run_chat(lambda: kernel.WaitForSingleObject(show_event, 0) == 0)
    finally:
        if build_marker:
            kernel.CloseHandle(build_marker)
        if mutex:
            kernel.CloseHandle(mutex)
        kernel.CloseHandle(show_event)


def run_chat(show_requested):
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    root = tk.Tk()
    chat = ScreenChat(root)
    ocr_worker = None
    tray = None
    quit_hook = None

    def check_launcher():
        if not chat.capturing and show_requested():
            chat.show()
        if ocr_worker is not None and ocr_worker[0].poll() is not None:
            chat.ocr_status.set("OCR stopped")
        root.after(150, check_launcher)

    def retry_tray():
        if not tray.registered:
            tray.add_icon()
        root.after(5000, retry_tray)

    try:
        hotkeys = {
            win32con.VK_SPACE: ("Space", lambda: chat.events.put((chat.toggle, ()))),
            ord("1"): ("1", lambda: chat.events.put((chat.start_capture, ("select",)))),
            ord("2"): ("2", lambda: chat.events.put((chat.start_capture, ("desktop",)))),
            ord("3"): ("3", lambda: chat.events.put((chat.start_capture, ("repeat",)))),
        }
        tray = ReaderTray(ICON_FILE, lambda: chat.events.put((chat.show, ())),
                          lambda: chat.events.put((root.quit, ())), hotkeys)
        if tray.unavailable_keys:
            chat.status.set("Unavailable shortcuts: " + ", ".join(tray.unavailable_keys))
        try:
            ocr_worker = start_ocr_worker()
            chat.ocr_status.set("OCR ready")
        except OSError as error:
            chat.ocr_status.set("OCR could not start.")
            chat.add_message("OCR", str(error))
        quit_hook = keyboard.add_hotkey("shift+f7", chat.events.put,
                                        args=((root.quit, ()),), trigger_on_release=True)
        root.after(30, chat.drain_events)
        root.after(150, check_launcher)
        root.after(5000, retry_tray)
        root.after_idle(chat.load_model_catalog)
        chat.show()
        root.mainloop()
    finally:
        if chat.settings is not None:
            chat.settings.cancel.set()
        try:
            try:
                chat.gemini_chat.close()
            finally:
                chat.codex_chat.close()
        finally:
            if quit_hook is not None:
                keyboard.remove_hotkey(quit_hook)
            try:
                if ocr_worker is not None:
                    stop_ocr_worker(*ocr_worker)
            finally:
                if tray is not None:
                    tray.close()
                root.destroy()


def start_ocr_worker():
    legacy_scripts = {APP_DIR / "reader_daemon.py", APP_DIR / "src" / "reader_daemon.py"}
    for process in psutil.process_iter(["name", "cmdline"]):
        info = process.info
        if (info["name"] or "").lower() not in ("python.exe", "pythonw.exe"):
            continue
        if any(Path(arg).is_absolute() and Path(arg) in legacy_scripts
               for arg in (info["cmdline"] or ())[1:]):
            return None

    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.extend(["-B", str(Path(__file__).resolve())])
    env = {**os.environ, "SCREEN_READER_DATA_DIR": str(APP_DIR)}
    event_name = f"Local\\ScreanReaderOcrStop-{os.getpid()}"
    stop_event = win32event.CreateEvent(None, True, False, event_name)
    process = subprocess.Popen([*command, "--ocr-worker", event_name], cwd=APP_DIR, env=env,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    return process, stop_event


def stop_ocr_worker(process, stop_event):
    win32event.SetEvent(stop_event)
    try:
        process.wait(timeout=6)
    except subprocess.TimeoutExpired:
        worker = psutil.Process(process.pid)
        children = worker.children(recursive=True)
        for child in reversed(children):
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        process.terminate()
        process.wait(timeout=3)


def run_ocr_worker(event_name):
    import reader_daemon

    shutdown = threading.Event()
    shutdown_handle = win32event.OpenEvent(win32con.SYNCHRONIZE, False, event_name)

    def await_shutdown():
        win32event.WaitForSingleObject(shutdown_handle, win32event.INFINITE)
        shutdown.set()

    threading.Thread(target=await_shutdown, daemon=True).start()
    logs = APP_DIR / "logs"
    logs.mkdir(exist_ok=True)
    with (logs / "daemon.log").open("a", encoding="utf-8", buffering=1) as log:
        sys.stdout = sys.stderr = log
        reader_daemon.main(shutdown)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--ocr-worker":
        run_ocr_worker(sys.argv[2])
    else:
        main()
