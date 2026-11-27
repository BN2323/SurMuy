"""Read-only code panels with an independent clipboard action."""

import tkinter as tk
from tkinter import font, ttk
from PIL import Image, ImageDraw, ImageTk

from pygments.lexers.data import JsonLexer
from pygments.lexers.javascript import JavascriptLexer, TypeScriptLexer
from pygments.lexers.python import PythonLexer
from pygments.lexers.shell import BashLexer, PowerShellLexer
from pygments.lexers.sql import SqlLexer
from pygments.lexers.html import HtmlLexer
from pygments.lexers.css import CssLexer
from pygments.token import Comment, Keyword, Name, Number, String


LEXERS = {
    "python": PythonLexer, "py": PythonLexer, "javascript": JavascriptLexer, "js": JavascriptLexer,
    "typescript": TypeScriptLexer, "ts": TypeScriptLexer, "json": JsonLexer, "html": HtmlLexer,
    "css": CssLexer, "sql": SqlLexer, "bash": BashLexer, "sh": BashLexer,
    "powershell": PowerShellLexer, "ps1": PowerShellLexer,
}
COLORS = {
    Keyword: "#c8b5ff",
    String: "#a9d6a3",
    Number: "#f5c28b",
    Comment: "#9ca9b7",
    Name.Function: "#8dcaff",
}
HEADING = "#27303a"


def insert_highlighted_code(editor, code, language, base_tags=()):
    lexer = LEXERS.get(language.casefold())
    if lexer is None:
        editor.insert("end", code, base_tags)
        return
    for kind, color in COLORS.items():
        editor.tag_configure(str(kind), foreground=color)
    for token, value in lexer(stripnl=False, ensurenl=False).get_tokens(code):
        tags = tuple(str(kind) for kind in COLORS if token in kind)
        editor.insert("end", value, (*base_tags, *tags))


def copy_images(parent):
    normal = Image.new("RGB", (54, 54), HEADING)
    shape = ImageDraw.Draw(normal)
    shape.rounded_rectangle((18, 8, 44, 35), radius=6, outline="#c9d4df", width=4)
    shape.rounded_rectangle((9, 18, 35, 45), radius=6, fill=HEADING, outline="#c9d4df", width=4)
    copied = Image.new("RGB", (54, 54), HEADING)
    ImageDraw.Draw(copied).line(((10, 27), (23, 40), (45, 13)), fill="#9ed9c4", width=5, joint="curve")
    return [ImageTk.PhotoImage(image.resize((18, 18), Image.Resampling.LANCZOS), master=parent)
            for image in (normal, copied)]


def code_panel(parent, code, language, *, width, outline, foreground):
    panel = tk.Frame(parent, background=outline, borderwidth=0)
    panel.columnconfigure(0, weight=1)
    panel.rowconfigure(1, weight=1)
    heading = tk.Frame(panel, background=HEADING)
    heading.grid(row=0, column=0, sticky="ew", padx=1, pady=(1, 0))
    tk.Label(heading, text=language or "Code", background=HEADING, foreground="#c9d4df",
             font=("Segoe UI Semibold", 9), padx=9).pack(side="left")
    normal, copied = copy_images(heading)
    copy = tk.Button(heading, image=normal, text="Copy code", compound="none", cursor="hand2",
                     background=HEADING, activebackground=HEADING, relief="flat", overrelief="flat",
                     bd=0, padx=5, pady=3, highlightthickness=1,
                     highlightbackground=HEADING, highlightcolor="#a6d4ff")
    copy.icons = (normal, copied)
    copy.pack(side="right", padx=4, pady=3)

    def copy_code():
        panel.clipboard_clear()
        panel.clipboard_append(code)
        copy.configure(image=copied)
        parent.after(1400, lambda: copy.configure(image=normal) if copy.winfo_exists() else None)

    copy.configure(command=copy_code)
    body = tk.Frame(panel, background="#141a22")
    body.grid(row=1, column=0, sticky="nsew", padx=1, pady=(0, 1))
    lexer = LEXERS.get(language.casefold())
    code_font = font.Font(root=parent, family="Consolas", size=10)
    editor = tk.Text(body, wrap="none" if lexer else "word", width=1, height=1,
                     font=code_font, background="#141a22", foreground=foreground,
                     selectbackground="#364e69", padx=9, pady=7, borderwidth=0, highlightthickness=0)
    editor.pack(side="left", fill="both", expand=True)
    scrollbar = ttk.Scrollbar(body, command=editor.yview)

    def scroll_changed(first, last):
        scrollbar.set(first, last)
        if float(first) == 0 and float(last) == 1:
            scrollbar.pack_forget()
        elif not scrollbar.winfo_manager():
            scrollbar.pack(side="right", fill="y", before=editor)

    editor.configure(yscrollcommand=scroll_changed)
    if lexer:
        across = ttk.Scrollbar(panel, orient="horizontal", command=editor.xview)
        across.grid(row=2, column=0, sticky="ew", padx=1, pady=(0, 1))

        def across_changed(first, last):
            across.set(first, last)
            if float(first) == 0 and float(last) == 1:
                across.grid_remove()
            else:
                across.grid()

        editor.configure(xscrollcommand=across_changed)
    insert_highlighted_code(editor, code, language)
    editor.configure(state="disabled")
    panel.update_idletasks()
    lines = min(12, max(1, len(code.splitlines())))
    if not lexer and code_font.measure(max(code.splitlines(), key=len, default="")) > width - 38:
        lines = min(12, max(lines, code_font.measure(code) // max(1, width - 38) + 1))
    height = heading.winfo_reqheight() + lines * code_font.metrics("linespace") + 16
    if lexer:
        height += across.winfo_reqheight()
    panel.configure(width=width, height=height)
    panel.grid_propagate(False)
    panel.code_font = code_font
    return panel
