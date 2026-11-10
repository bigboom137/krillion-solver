"""Dark theme for the Krillion Solver GUI, built on ttk's "clam" theme (no extra installs)."""

import ctypes
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# Deep-sea palette with Krillion's cyan and pink.
BG = "#0b1018"          # window
SURFACE = "#121a25"     # cards
SURFACE_ALT = "#16202d" # striped rows
FIELD = "#1a2533"       # inputs and buttons
FIELD_HOVER = "#223044"
BORDER = "#253244"
TEXT = "#e4ecf5"
MUTED = "#8494a8"
FAINT = "#56657a"
ACCENT = "#3ec7e0"      # cyan
ACCENT_HOVER = "#67d6ea"
ACCENT_TEXT = "#04212a"
PINK = "#ff5d8f"
SELECT = "#1d4655"
GREEN = "#4cc38a"
GREEN_SOFT = "#12291f"  # badge backgrounds
AMBER = "#e3b341"
RED = "#ff6b6b"
RED_SOFT = "#2e1619"

TIER_COLORS = {
    "plankton": "#8fa3b8",
    "schooler": "#4da3ff",
    "rare": "#a78bfa",
    "deep_cut": "#f472b6",
    "krillion": "#f2cc60",  # gold, like the game's One in a Krillion icon
}

FAMILY = "Segoe UI"


def apply(root):
    """Style every ttk widget dark; returns the row height used by the tables."""
    for name, size, weight in (("TkDefaultFont", 10, "normal"), ("TkTextFont", 10, "normal"),
                               ("TkHeadingFont", 9, "bold"), ("TkMenuFont", 10, "normal")):
        try:
            tkfont.nametofont(name).configure(family=FAMILY, size=size, weight=weight)
        except tk.TclError:
            pass
    linespace = tkfont.nametofont("TkDefaultFont").metrics("linespace")
    rowheight = int(linespace * 1.9)

    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, fieldbackground=FIELD, bordercolor=BORDER,
                    lightcolor=FIELD, darkcolor=FIELD, troughcolor=SURFACE, focuscolor=ACCENT,
                    selectbackground=SELECT, selectforeground=TEXT, insertcolor=TEXT,
                    arrowcolor=MUTED, font=(FAMILY, 10))

    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TLabel", background=SURFACE, foreground=TEXT)
    style.configure("Muted.Card.TLabel", background=SURFACE, foreground=MUTED)
    style.configure("Section.Card.TLabel", background=SURFACE, foreground=MUTED, font=(FAMILY, 8, "bold"))
    style.configure("Title.Card.TLabel", background=SURFACE, foreground=TEXT, font=(FAMILY, 14, "bold"))

    # Buttons
    style.configure("TButton", background=FIELD, foreground=TEXT, bordercolor=BORDER, lightcolor=FIELD,
                    darkcolor=FIELD, padding=(12, 5), relief="flat", focusthickness=0)
    style.map("TButton", background=[("pressed", BORDER), ("active", FIELD_HOVER)],
              lightcolor=[("active", FIELD_HOVER)], darkcolor=[("active", FIELD_HOVER)],
              bordercolor=[("focus", ACCENT)])
    style.configure("Accent.TButton", background=ACCENT, foreground=ACCENT_TEXT, bordercolor=ACCENT,
                    lightcolor=ACCENT, darkcolor=ACCENT, font=(FAMILY, 10, "bold"))
    style.map("Accent.TButton", background=[("pressed", ACCENT), ("active", ACCENT_HOVER)],
              lightcolor=[("active", ACCENT_HOVER)], darkcolor=[("active", ACCENT_HOVER)],
              bordercolor=[("active", ACCENT_HOVER)])
    style.configure("Icon.TButton", padding=(6, 5))
    for tier, color in TIER_COLORS.items():
        name = f"{tier}.Tier.TButton"
        style.configure(name, foreground=color, padding=(4, 5))
        style.map(name, background=[("pressed", BORDER), ("active", FIELD_HOVER)])
    style.configure("Danger.TButton", foreground=RED, padding=(8, 5))
    style.map("Danger.TButton", background=[("pressed", BORDER), ("active", RED_SOFT)])

    # Inputs
    for widget in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(widget, fieldbackground=FIELD, background=FIELD, foreground=TEXT, bordercolor=BORDER,
                        lightcolor=FIELD, darkcolor=FIELD, arrowcolor=MUTED, padding=(6, 4), insertcolor=TEXT)
        style.map(widget, bordercolor=[("focus", ACCENT)], lightcolor=[("focus", FIELD)],
                  fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)],
                  arrowcolor=[("active", TEXT)], background=[("active", FIELD_HOVER)])
    style.configure("Hint.TEntry", foreground=FAINT)
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", SELECT)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*TCombobox*Listbox.font", (FAMILY, 10))

    # Checkboxes
    style.configure("Card.TCheckbutton", background=SURFACE, foreground=TEXT, indicatorbackground=FIELD,
                    indicatorforeground=ACCENT_TEXT, upperbordercolor=BORDER, lowerbordercolor=BORDER,
                    padding=(0, 2, 14, 2), focusthickness=0)
    style.map("Card.TCheckbutton", background=[("active", SURFACE)],
              indicatorbackground=[("selected", ACCENT), ("active", FIELD_HOVER)],
              upperbordercolor=[("selected", ACCENT)], lowerbordercolor=[("selected", ACCENT)])

    # Tables
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT,
                    bordercolor=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE, rowheight=rowheight,
                    font=(FAMILY, 10))
    style.map("Treeview", background=[("selected", SELECT)], foreground=[("selected", TEXT)])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # no outer border
    style.configure("Treeview.Heading", background=SURFACE, foreground=MUTED, bordercolor=SURFACE,
                    lightcolor=SURFACE, darkcolor=SURFACE, relief="flat", padding=(6, 6),
                    font=(FAMILY, 9, "bold"))
    style.map("Treeview.Heading", background=[("active", SURFACE)], foreground=[("active", TEXT)])

    # Slim scrollbars without arrows
    style.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    style.configure("Vertical.TScrollbar", background=FIELD, troughcolor=SURFACE, bordercolor=SURFACE,
                    lightcolor=FIELD, darkcolor=FIELD, arrowsize=10, gripcount=0)
    style.map("Vertical.TScrollbar", background=[("active", FIELD_HOVER)])
    return rowheight


def dark_title_bar(root):
    """Ask Windows 10/11 for a dark title bar; harmless elsewhere."""
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        on = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (newer, older builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(on),
                                                          ctypes.sizeof(on)) == 0:
                break
    except (AttributeError, OSError):
        pass


def card(parent, **pack):
    """A bordered surface panel. Children should use the Card.* styles."""
    frame = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightcolor=BORDER,
                     highlightthickness=1, bd=0)
    if pack:
        frame.pack(**pack)
    return frame


class HintEntry(ttk.Entry):
    """ttk.Entry that shows grey hint text while it's empty and not focused."""

    def __init__(self, master, hint, **kwargs):
        super().__init__(master, **kwargs)
        self.hint = hint
        self.showing_hint = False
        self.bind("<FocusIn>", lambda e: self._hide_hint(), add="+")
        self.bind("<FocusOut>", lambda e: self._show_hint(), add="+")
        self._show_hint()

    def _show_hint(self):
        if not self.showing_hint and not super().get():
            self.showing_hint = True
            super().insert(0, self.hint)
            self.configure(style="Hint.TEntry")

    def _hide_hint(self):
        if self.showing_hint:
            self.showing_hint = False
            super().delete(0, "end")
            self.configure(style="TEntry")

    def get(self):
        return "" if self.showing_hint else super().get()

    def insert(self, index, text):
        self._hide_hint()
        super().insert(index, text)

    def delete(self, first, last=None):
        if self.showing_hint:
            return
        super().delete(first, last)
        try:
            focused = self.focus_get() is self
        except (KeyError, tk.TclError):
            focused = False
        if not focused:
            self._show_hint()
