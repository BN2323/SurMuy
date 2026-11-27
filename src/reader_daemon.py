"""
Resident screen-reader daemon (watch mode).

Starts ONCE, pre-loads everything (imports + OCR engine), then listens for
global hotkeys. You select a box ONE time; it then keeps watching that same
region and auto-captures whenever the question text changes -- no re-selecting.

Hotkeys:
    Ctrl + Alt + Q   Select a box and START auto-watching (re-reads on change).
    Ctrl + Alt + W   Select a box but DON'T auto-watch (manual mode; idle until F8).
    F8               Re-read the locked region once, right now (force capture).
    Ctrl + Alt + S   STOP watching (forget the locked region).
    Ctrl + Alt + X   Quit the daemon.

In manual mode (Ctrl+Alt+W) nothing is processed until you press F8,
so it uses no CPU between questions. In watch mode (Ctrl+Alt+Q) it polls and
auto-captures whenever the text changes.

When the watched text changes it is written to screen_text.txt and appended to
qcm_log.txt, and a popup appears. Then just tell the agent "go" to get the answer.
"""

import asyncio
import re
import sys
import threading
import time

import keyboard

import read_screen as rs

SELECT_KEY = "ctrl+alt+q"
MANUAL_KEY = "ctrl+alt+w"
REREAD_KEY = "f8"
SHOW_KEY = "ctrl+alt+v"
STOP_KEY = "ctrl+alt+s"
QUIT_KEY = "ctrl+alt+x"

LANG = "fr"              # default OCR language; falls back automatically
POLL_SECONDS = 1.5       # how often to re-read the locked region
MIN_CHANGE_RATIO = 0.15  # fraction of text that must differ to count as "new"

LOG_FILE = rs.OUTPUT_FILE.with_name("qcm_log.txt")

_loop = asyncio.new_event_loop()

# Shared watch state (guarded by _state_lock).
_state_lock = threading.Lock()
_region = None            # (x, y, w, h) currently being watched, or None
_last_norm = ""           # last normalized text we captured
_watch_thread = None
_stop_flag = threading.Event()


def _prewarm():
    try:
        rs.make_engine(LANG)
    except SystemExit:
        try:
            rs.make_engine(None)
        except Exception:
            pass
    print("[daemon] OCR engine ready.", flush=True)


def _normalize(text: str) -> str:
    """Collapse whitespace/case so trivial OCR jitter doesn't look like a change."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _diff_ratio(a: str, b: str) -> float:
    """Rough fraction of difference between two strings (0 = identical)."""
    if not a and not b:
        return 0.0
    if not a or not b:
        return 1.0
    import difflib
    return 1.0 - difflib.SequenceMatcher(None, a, b).ratio()


def _ocr_region(region) -> str:
    img = rs.capture(1, region)
    return _loop.run_until_complete(rs.run_ocr(img, LANG))


def _publish(text: str):
    """Save the captured question and notify."""
    rs.OUTPUT_FILE.write_text(text, encoding="utf-8")
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n===== {stamp} =====\n{text}\n")
    except Exception:
        pass
    rs.notify("New question \u2713", 'Captured \u2014 tell the agent "go".')
    print("-" * 60, flush=True)
    print(text, flush=True)


def _watch_loop(region):
    """Poll the locked region until stopped; publish on meaningful change."""
    global _last_norm
    print(f"[daemon] watching region {region} (every {POLL_SECONDS}s). "
          f"{STOP_KEY.upper()} to stop.", flush=True)
    while not _stop_flag.is_set():
        try:
            text = _ocr_region(region)
            norm = _normalize(text)
            if norm and _diff_ratio(_last_norm, norm) >= MIN_CHANGE_RATIO:
                _last_norm = norm
                _publish(text)
        except Exception as e:
            print(f"[daemon] watch error: {e}", file=sys.stderr, flush=True)
        _stop_flag.wait(POLL_SECONDS)
    print("[daemon] watch stopped.", flush=True)


def _start_watch():
    """Pick a region (one time), then start the watch loop on it."""
    global _region, _watch_thread, _last_norm
    region = rs.pick_region()
    if region is None:
        print("[daemon] selection cancelled.", flush=True)
        return
    # Stop any existing watch first.
    _stop_watch(silent=True)
    with _state_lock:
        _region = region
        _last_norm = ""  # force first capture to publish
    _stop_flag.clear()
    _watch_thread = threading.Thread(target=_watch_loop, args=(region,), daemon=True)
    _watch_thread.start()
    rs.notify("Watching region", "Auto-reads when the question changes.")


def _select_manual():
    """Pick a region (one time) and lock it WITHOUT auto-watching.
    Nothing is read until Ctrl+Alt+R is pressed."""
    global _region, _last_norm
    region = rs.pick_region()
    if region is None:
        print("[daemon] selection cancelled.", flush=True)
        return
    # Stop any running watch loop; keep the region locked for manual re-reads.
    _stop_flag.set()
    t = _watch_thread
    if t and t.is_alive():
        t.join(timeout=POLL_SECONDS + 1)
    with _state_lock:
        _region = region
        _last_norm = ""
    rs.notify("Manual mode", "Box set. Press F8 to read.")
    print(f"[daemon] manual mode: region {region} locked. "
          f"{REREAD_KEY.upper()} to read.", flush=True)


def _stop_watch(silent=False):
    global _region, _watch_thread
    _stop_flag.set()
    t = _watch_thread
    if t and t.is_alive():
        t.join(timeout=POLL_SECONDS + 1)
    _watch_thread = None
    with _state_lock:
        _region = None
    if not silent:
        rs.notify("Stopped", "No longer watching. Ctrl+Alt+Q to pick again.")
        print("[daemon] no longer watching.", flush=True)


def _show_box():
    """Flash the locked box outline on screen for ~1.5s (no capture)."""
    with _state_lock:
        region = _region
    if region is None:
        rs.notify("No region", "Press Ctrl+Alt+W to select one first.")
        return
    x, y, w, h = region
    try:
        import tkinter as tk
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        # Transparent fill, visible red border: use a slightly larger window and
        # a color-key transparency so only the outline shows.
        pad = 4
        root.geometry(f"{w + pad * 2}x{h + pad * 2}+{x - pad}+{y - pad}")
        trans = "magenta"
        root.configure(bg=trans)
        root.attributes("-transparentcolor", trans)
        cv = tk.Canvas(root, bg=trans, highlightthickness=0)
        cv.pack(fill="both", expand=True)
        cv.create_rectangle(pad, pad, w + pad, h + pad,
                            outline="red", width=3)
        cv.create_text(w // 2 + pad, pad + 12,
                       text="OCR box", fill="red",
                       font=("Segoe UI", 11, "bold"))
        root.after(1500, root.destroy)
        root.mainloop()
    except Exception as e:
        print(f"[daemon] show-box error: {e}", file=sys.stderr, flush=True)


def _reread_now():
    """Force an immediate capture of the locked region, ignoring the change check."""
    print("[daemon] re-read key pressed.", flush=True)
    with _state_lock:
        region = _region
    if region is None:
        rs.notify("No region", "Press Ctrl+Alt+W to select one first.")
        print("[daemon] re-read ignored: no region set.", flush=True)
        return
    try:
        text = _ocr_region(region)
        global _last_norm
        _last_norm = _normalize(text)
        _publish(text)
    except Exception as e:
        print(f"[daemon] reread error: {e}", file=sys.stderr, flush=True)


def _fire(fn):
    """Run a handler on a worker thread so the keyboard hook stays responsive."""
    threading.Thread(target=fn, daemon=True).start()


def main(quit_event=None):
    asyncio.set_event_loop(_loop)
    _prewarm()
    if quit_event is None:
        quit_event = threading.Event()
    keyboard.add_hotkey(SELECT_KEY, lambda: _fire(_start_watch))
    keyboard.add_hotkey(MANUAL_KEY, lambda: _fire(_select_manual))
    keyboard.add_hotkey(REREAD_KEY, lambda: _fire(_reread_now))
    keyboard.add_hotkey(SHOW_KEY, lambda: _fire(_show_box))
    keyboard.add_hotkey(STOP_KEY, lambda: _fire(_stop_watch))
    keyboard.add_hotkey(QUIT_KEY, quit_event.set)
    print(
        "[daemon] Ready.\n"
        f"  {SELECT_KEY.upper()}  select a box + AUTO-watch (reads on change)\n"
        f"  {MANUAL_KEY.upper()}  select a box, MANUAL mode (read only on F8)\n"
        f"  {REREAD_KEY.upper()}  re-read the box now\n"
        f"  {SHOW_KEY.upper()}  show where the box is\n"
        f"  {STOP_KEY.upper()}  stop watching\n"
        f"  {QUIT_KEY.upper()}  quit",
        flush=True,
    )
    quit_event.wait()
    _stop_watch(silent=True)
    print("[daemon] stopped.", flush=True)


if __name__ == "__main__":
    main()
