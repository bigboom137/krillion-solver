"""Read a Krillion prompt off the screen with the OCR built into Windows.

Pieces used by the GUI:
  enable_dpi_awareness()  - call before creating Tk so screen coordinates are physical pixels
  pick_region(root, done) - full-screen overlay to drag a box around where prompts appear
  read_region(region)     - OCR that box, returns the recognised lines
  extract_prompt(lines)   - pull the prompt text out of those lines
  start_hotkey(callback)  - global Ctrl+Alt+K, works while the browser has focus
"""

import ctypes
import json
import os
import re
import subprocess
import threading
import time
import tkinter as tk
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
OCR_SCRIPT = os.path.join(HERE, "ocr_screen.ps1")
SETTINGS = os.path.join(HERE, "settings.json")
HOTKEY_LABEL = "Ctrl+Alt+K"

# Text around the prompt in the game that is not part of it. Hint lines are framed by ▼
# arrows, which OCR usually reads as a lone "V": "V any answer passes the puffer V".
NOISE = re.compile(
    r"prompt\s*\d|\brarer\b|\bsink\b|descending|\bclock\b|\bpuffer\b|^depth\b|^score\b"
    r"|^[v▼▽▲]\s|\s[▼▽▲]$"     # starts with a lone V/arrow, or ends with an arrow
    r"|^[v▼▲\W\d]*$",           # only arrows, symbols or digits
    re.I)
MAX_PROMPT_LINES = 5
PROMPT_START = re.compile(r"(name|list|give|think of)\b", re.I)


def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system DPI aware
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


# --- settings ----------------------------------------------------------------

def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(settings):
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


# --- OCR ---------------------------------------------------------------------

class OcrError(RuntimeError):
    pass


def read_region(region):
    """OCR the screen box (x, y, width, height); returns a list of text lines."""
    x, y, w, h = (int(v) for v in region)
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", OCR_SCRIPT,
           "-X", str(x), "-Y", str(y), "-Width", str(w), "-Height", str(h)]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise OcrError(f"OCR did not run ({e})") from e
    if out.returncode != 0:
        err = out.stderr.decode("utf-8", "replace").strip().splitlines()
        raise OcrError(err[0] if err else "OCR failed")
    words = []
    for row in out.stdout.decode("utf-8", "replace").splitlines():
        parts = row.split("\t", 3)
        if len(parts) == 4 and parts[3].strip():
            words.append((int(parts[0]), int(parts[1]), int(parts[2]), parts[3].strip()))
    return group_lines(words)


def group_lines(words):
    """Rebuild text lines from (x, y, height, text) words: rows top to bottom, words left to right."""
    rows = []
    for x, y, h, text in sorted(words, key=lambda w: w[1]):
        cy = y + h / 2
        for row in rows:
            if abs(row["cy"] - cy) < max(row["h"], h) * 0.6:
                row["words"].append((x, text))
                break
        else:
            rows.append({"cy": cy, "h": h, "words": [(x, text)]})
    rows.sort(key=lambda r: r["cy"])
    return [" ".join(t for _, t in sorted(r["words"])) for r in rows]


def _clean(text):
    text = text.replace("|", "I").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip(" .•·-")


def extract_prompt(lines, ignore=()):
    """Find the prompt in OCR lines: from the line starting 'Name…' up to the next noise line.

    ignore: extra phrases (from settings.json "ignore_phrases") that mark a line as not
    part of the prompt, case-insensitive.
    """
    ignore = [p.lower() for p in ignore if p.strip()]

    def noise(line):
        return bool(NOISE.search(line)) or any(p in line.lower() for p in ignore)

    lines = [_clean(l) for l in lines if _clean(l)]
    for i, line in enumerate(lines):
        if PROMPT_START.match(line) and not noise(line):
            parts = [line]
            for nxt in lines[i + 1:i + MAX_PROMPT_LINES]:  # long prompts wrap onto several lines
                if noise(nxt) or PROMPT_START.match(nxt):
                    break
                parts.append(nxt)
            return " ".join(parts)
    rest = [l for l in lines if not noise(l)]
    return max(rest, key=len) if rest else ""


# --- region picker -----------------------------------------------------------

def _virtual_screen():
    m = ctypes.windll.user32.GetSystemMetrics
    return m(76), m(77), m(78), m(79)  # x, y, width, height across all monitors


def pick_region(root, done):
    """Show a dimmed full-screen overlay; drag a box, then done((x, y, w, h)) or done(None)."""
    vx, vy, vw, vh = _virtual_screen()
    top = tk.Toplevel(root)
    top.overrideredirect(True)
    top.attributes("-topmost", True)
    top.attributes("-alpha", 0.35)
    top.geometry(f"{vw}x{vh}{vx:+d}{vy:+d}")
    canvas = tk.Canvas(top, bg="black", highlightthickness=0, cursor="crosshair")
    canvas.pack(fill="both", expand=True)
    canvas.create_text(vw // 2 - vx, 60, fill="white", font=("Segoe UI", 18),
                       text="Drag a box around where Krillion shows the prompt   ·   Esc to cancel")
    state = {"start": None, "rect": None}

    def press(e):
        state["start"] = (e.x_root, e.y_root)
        state["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="#ff4d6d", width=3)

    def drag(e):
        if state["rect"]:
            sx, sy = state["start"]
            canvas.coords(state["rect"], sx - vx, sy - vy, e.x_root - vx, e.y_root - vy)

    def release(e):
        if not state["start"]:
            return
        sx, sy = state["start"]
        x, y = min(sx, e.x_root), min(sy, e.y_root)
        w, h = abs(e.x_root - sx), abs(e.y_root - sy)
        top.destroy()
        done((x, y, w, h) if w > 10 and h > 10 else None)

    def cancel(e=None):
        top.destroy()
        done(None)

    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", release)
    top.bind("<Escape>", cancel)
    top.focus_force()


# --- global hotkey -----------------------------------------------------------

HOTKEY_MODS = 0x1 | 0x2 | 0x4000  # Alt + Ctrl, no auto-repeat
HOTKEY_VK = 0x4B                   # K
HOTKEY_RETRY_SECS = 2


def start_hotkey(callback, on_state):
    """Call callback() on Ctrl+Alt+K from any app. Runs on its own thread.

    on_state(True/False) reports whether the hotkey is registered. Only one program can
    own it, often a second copy of this app, so on failure keep retrying until it's free.
    """
    def loop():
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, 1, HOTKEY_MODS, HOTKEY_VK):
            on_state(False)
            while not user32.RegisterHotKey(None, 1, HOTKEY_MODS, HOTKEY_VK):
                time.sleep(HOTKEY_RETRY_SECS)
        on_state(True)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                callback()

    threading.Thread(target=loop, daemon=True).start()
