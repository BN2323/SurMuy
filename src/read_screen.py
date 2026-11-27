"""
Screen OCR reader using the built-in Windows OCR engine (Windows.Media.Ocr).

Captures the screen (full, a specific monitor, or a region), runs OCR, and
writes the recognized text to screen_text.txt so it can be read back.

Usage:
    python read_screen.py                  # OCR the primary monitor
    python read_screen.py --monitor 2      # OCR monitor #2
    python read_screen.py --monitor 0      # OCR all monitors stitched together
    python read_screen.py --region 100 200 800 600   # x y width height
    python read_screen.py --lang fr        # force a language tag (e.g. fr, en-US)
    python read_screen.py --save shot.png  # also save the captured image
    python read_screen.py --list-langs     # list installed OCR languages
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import mss
from PIL import Image

from winrt.windows.graphics.imaging import (
    SoftwareBitmap,
    BitmapPixelFormat,
    BitmapAlphaMode,
)
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.globalization import Language
from winrt.windows.storage.streams import Buffer

from app_paths import APP_DIR

OUTPUT_FILE = Path(os.environ.get("SCREEN_READER_DATA_DIR", APP_DIR)) / "screen_text.txt"


def capture(monitor: int, region):
    """Capture the screen and return a PIL RGB image."""
    with mss.mss() as sct:
        if region is not None:
            x, y, w, h = region
            bbox = {"left": x, "top": y, "width": w, "height": h}
            raw = sct.grab(bbox)
        else:
            # mss monitors: index 0 = all, 1 = primary, 2+ = others
            mons = sct.monitors
            if monitor < 0 or monitor >= len(mons):
                raise SystemExit(
                    f"Monitor {monitor} not available. "
                    f"Valid: 0..{len(mons) - 1} (found {len(mons) - 1} display(s))."
                )
            raw = sct.grab(mons[monitor])
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    return img


def pick_region():
    """Show a fullscreen overlay; user drags a box. Returns (x, y, w, h) or None."""
    import tkinter as tk

    sel = {}
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.25)
    root.configure(bg="black")
    root.attributes("-topmost", True)
    root.config(cursor="cross")

    canvas = tk.Canvas(root, bg="black", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_text(
        root.winfo_screenwidth() // 2, 40,
        text="Drag a box over the question, then release.  (Esc to cancel)",
        fill="white", font=("Segoe UI", 16),
    )

    start = {}
    rect = {"id": None}

    def on_press(e):
        start["x"], start["y"] = e.x_root, e.y_root
        rect["id"] = canvas.create_rectangle(e.x, e.y, e.x, e.y,
                                             outline="red", width=2)

    def on_drag(e):
        if rect["id"] is not None:
            x0 = start["x"] - root.winfo_rootx()
            y0 = start["y"] - root.winfo_rooty()
            canvas.coords(rect["id"], x0, y0, e.x, e.y)

    def on_release(e):
        x1, y1 = e.x_root, e.y_root
        x0, y0 = start["x"], start["y"]
        sel["x"] = min(x0, x1)
        sel["y"] = min(y0, y1)
        sel["w"] = abs(x1 - x0)
        sel["h"] = abs(y1 - y0)
        root.destroy()

    def on_cancel(_e):
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", on_cancel)
    root.mainloop()

    if not sel or sel["w"] < 5 or sel["h"] < 5:
        return None
    return (sel["x"], sel["y"], sel["w"], sel["h"])


def notify(title, msg):
    """Tiny tray/balloon popup so you get feedback without a terminal."""
    try:
        import tkinter as tk
        t = tk.Tk()
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        sw = t.winfo_screenwidth()
        t.geometry(f"320x60+{sw-340}+40")
        t.configure(bg="#222")
        tk.Label(t, text=title, fg="#6cf", bg="#222",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=12, pady=(8, 0))
        tk.Label(t, text=msg, fg="white", bg="#222",
                 font=("Segoe UI", 10)).pack(anchor="w", padx=12)
        t.after(1400, t.destroy)
        t.mainloop()
    except Exception:
        pass


async def pil_to_software_bitmap(img: Image.Image) -> SoftwareBitmap:
    """Convert a PIL image to a WinRT SoftwareBitmap (BGRA8) via a raw pixel buffer."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    w, h = img.size

    # WinRT SoftwareBitmap BGRA8 expects byte order B, G, R, A.
    r, g, b, a = img.split()
    bgra = Image.merge("RGBA", (b, g, r, a))
    raw = bgra.tobytes()  # bytes in B,G,R,A order

    buffer = Buffer(len(raw))
    buffer.length = len(raw)
    memoryview(buffer)[: len(raw)] = raw

    bitmap = SoftwareBitmap.create_copy_from_buffer(
        buffer, BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.PREMULTIPLIED
    )
    return bitmap


_ENGINE_CACHE = {}


def make_engine(lang: str | None) -> OcrEngine:
    key = lang or "__default__"
    if key in _ENGINE_CACHE:
        return _ENGINE_CACHE[key]
    engine = None
    if lang:
        try:
            language = Language(lang)
            if OcrEngine.is_language_supported(language):
                engine = OcrEngine.try_create_from_language(language)
            else:
                print(f"[warn] OCR language '{lang}' not installed; using default.",
                      file=sys.stderr)
        except Exception as e:
            print(f"[warn] could not use language '{lang}': {e}", file=sys.stderr)
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise SystemExit(
            "No OCR engine available. Install a language pack: "
            "Settings > Time & Language > Language, add a language with "
            "'Optical character recognition' support."
        )
    _ENGINE_CACHE[key] = engine
    return engine


async def run_ocr(img: Image.Image, lang: str | None) -> str:
    engine = make_engine(lang)
    bitmap = await pil_to_software_bitmap(img)
    result = await engine.recognize_async(bitmap)
    # Rebuild text line by line to preserve layout reasonably.
    lines = [line.text for line in result.lines]
    return "\n".join(lines)


def list_languages():
    langs = OcrEngine.available_recognizer_languages
    print("Installed OCR languages:")
    for lang in langs:
        print(f"  {lang.language_tag}  ({lang.display_name})")


def main():
    ap = argparse.ArgumentParser(description="Read the screen with Windows OCR.")
    ap.add_argument("--monitor", type=int, default=1,
                    help="Monitor index (0=all, 1=primary, 2+=others). Default 1.")
    ap.add_argument("--region", type=int, nargs=4, metavar=("X", "Y", "W", "H"),
                    help="Capture a region instead of a full monitor.")
    ap.add_argument("--lang", type=str, default="fr",
                    help="OCR language tag, e.g. fr, en-US. Default fr (falls back to en).")
    ap.add_argument("--save", type=str, default=None,
                    help="Also save the captured screenshot to this path.")
    ap.add_argument("--list-langs", action="store_true",
                    help="List installed OCR languages and exit.")
    ap.add_argument("--pick", action="store_true",
                    help="Drag a box on screen to choose the region to read.")
    args = ap.parse_args()

    if args.list_langs:
        list_languages()
        return

    region = args.region
    if args.pick:
        picked = pick_region()
        if picked is None:
            print("[info] selection cancelled.")
            return
        region = picked
        print(f"[info] region selected: {region}")

    img = capture(args.monitor, region)
    if args.save:
        img.save(args.save)
        print(f"[info] screenshot saved to {args.save}")

    text = asyncio.run(run_ocr(img, args.lang))
    OUTPUT_FILE.write_text(text, encoding="utf-8")
    notify("Screen read ✓", "Text captured — tell Kiro \"go\".")
    print(f"[info] OCR text written to {OUTPUT_FILE}")
    print("-" * 60)
    print(text)


if __name__ == "__main__":
    main()
