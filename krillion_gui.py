#!/usr/bin/env python3
"""Desktop GUI for the Krillion solver.

Scans krillion.io for today's prompts (or Unlimited games), pre-generates
answers for all of them with Ollama in the background, and lets you copy
answers and record what the game said, all backed by the same lexicon.json
as the CLI. Prompts with no API (themed packs, private Party rooms) can be
read off the screen with Ctrl+Alt+K.

Usage: python krillion_gui.py [--lexicon PATH] [--host URL] [--model NAME]
"""

import argparse
import itertools
import os
import queue
import threading
import time
import tkinter as tk
from datetime import datetime, timezone
from tkinter import ttk

import browser_monitor
import history_window
import krillion_solver as ks
import local_knowledge
import prompt_rules
import screen_reader as sr
import theme

COUNT = 10          # answers requested per generation
MIN_CACHED = 3      # generate only when fewer usable answers are stored
MAX_RESCAN_MS = 6 * 3600 * 1000
TIER_BUTTONS = [("Plankton", "plankton"), ("Schooler", "schooler"), ("Rare", "rare"),
                ("Deep cut", "deep_cut"), ("Krillion", "krillion")]
TIER_NAMES = {tier: label for label, tier in TIER_BUTTONS}
# How long Ollama keeps the model in memory after the last answer (its keep_alive).
KEEP_ALIVE_LABELS = {"1m": "1 min", "5m": "5 min", "15m": "15 min", "1h": "1 hour"}
COMPACT_SIZE = "560x700"
DEFAULT_PASSES = 3      # generations per prompt; each asks for answers the others didn't give
MAX_LIST_FOR_PASSES = 20  # extra passes only for short lists (a dive, a game), not "scan all games"


class App:
    def __init__(self, root, lexicon_path, host, model):
        self.root = root
        self.lex = ks.Lexicon(lexicon_path)
        self.host = host
        self.results = queue.Queue()      # (callback, value) to run on the Tk thread
        self.gen_jobs = queue.PriorityQueue()  # (priority, seq, (prompt, model, known)) for the Ollama worker
        self.job_seq = itertools.count()
        self.pending = {}                 # prompt -> job seqs queued or running for it
        self.cancelled_jobs = set()       # seqs the worker should skip or stop
        self.active = None                # (seq, prompt, CancelToken, priority) while generating
        self.preempted = set()            # background seqs stopped for an urgent prompt, to requeue
        self.models_used = set()          # unloaded from Ollama when the window closes
        self.settings = sr.load_settings()
        self.reading = False
        self.status = {}                  # prompt -> status text in the prompt list
        self.prompts = []                 # prompts shown in the list, in order
        self.shown_answers = []           # answers shown in the right pane, in order
        self.stream_added = {}            # prompt -> new answers so far in the current generation
        self.rescan_after = None

        self.model_var = tk.StringVar(value=model or os.environ.get("OLLAMA_MODEL", ""))
        self.game_var = tk.IntVar(value=1)
        self.auto_scan_var = tk.BooleanVar(value=True)
        self.auto_copy_var = tk.BooleanVar(value=True)
        self.on_top_var = tk.BooleanVar(value=False)
        self.reasoning = bool(self.settings.get("reasoning", False))  # plain bool: read by the worker thread
        self.reasoning_var = tk.BooleanVar(value=self.reasoning)
        self.keep_alive = self.settings.get("keep_alive", "5m")  # plain str: read by the worker thread
        self.keep_alive_var = tk.StringVar(value=KEEP_ALIVE_LABELS.get(self.keep_alive, "5 min"))
        self.passes = int(self.settings.get("passes", DEFAULT_PASSES))
        self.passes_var = tk.IntVar(value=self.passes)
        self.preload_var = tk.BooleanVar(value=self.settings.get("preload", True))
        self.auto_answers_var = tk.BooleanVar(value=self.settings.get("auto_answers", True))
        self.compact = False
        self.topped_up = set()            # prompts already given one automatic extra round
        self.passes_done = {}             # prompt -> generations finished this session
        self.local_jobs = queue.Queue()   # prompts to look up in the built-in lists
        self.local_queued = set()
        self.loaded_model = None          # model this app preloaded, unloaded on switching
        self.tried_starting_ollama = False
        self.monitor = browser_monitor.Monitor(lambda kind, data: self.results.put((self._monitor_event, (kind, data))))
        self.last_diagnostics = {}
        self.diag_window = None
        self.history_window = None
        self.source_var = tk.StringVar(value="No prompts yet")
        self.status_var = tk.StringVar(value="Ready")

        self._build()
        local_knowledge.start_loading()
        threading.Thread(target=self._gen_worker, daemon=True).start()
        threading.Thread(target=self._local_worker, daemon=True).start()
        self.root.after(100, self._poll)
        self.refresh_models()
        sr.start_hotkey(lambda: self.results.put((lambda _: self.read_screen(), None)),
                        lambda ok: self.results.put((self._show_hotkey, ok)))
        if self.auto_scan_var.get():
            self.scan_today()

    # --- layout --------------------------------------------------------------

    def _build(self):
        root = self.root
        root.title("Krillion Solver")
        root.geometry("1200x930")
        root.minsize(940, 580)
        theme.apply(root)
        theme.dark_title_bar(root)
        pad = 14

        # Status bar first so it keeps its place at the bottom when the window shrinks.
        status = tk.Frame(root, bg=theme.SURFACE, highlightbackground=theme.BORDER, highlightthickness=1)
        status.pack(fill="x", side="bottom")
        tk.Label(status, textvariable=self.status_var, bg=theme.SURFACE, fg=theme.MUTED, anchor="w",
                 font=(theme.FAMILY, 9), padx=pad, pady=5).pack(fill="x")

        # Header: title, where the prompts came from, compact toggle, hotkey badge
        header = self.header = ttk.Frame(root, padding=(pad, pad, pad, 8))
        header.pack(fill="x")
        title = tk.Frame(header, bg=theme.BG)
        title.pack(side="left")
        tk.Label(title, text="KRILLION", bg=theme.BG, fg=theme.ACCENT,
                 font=(theme.FAMILY, 17, "bold")).pack(side="left")
        tk.Label(title, text=" SOLVER", bg=theme.BG, fg=theme.PINK,
                 font=(theme.FAMILY, 17, "bold")).pack(side="left")
        self.source_label = tk.Label(title, textvariable=self.source_var, bg=theme.BG, fg=theme.MUTED,
                                     font=(theme.FAMILY, 10))
        self.source_label.pack(side="left", padx=(14, 0), pady=(5, 0))
        self.hotkey_label = tk.Label(header, text=f"●  {sr.HOTKEY_LABEL} starting…", bg=theme.FIELD,
                                     fg=theme.MUTED, font=(theme.FAMILY, 9, "bold"), padx=10, pady=4)
        self.hotkey_label.pack(side="right")
        self.compact_button = ttk.Button(header, text="Compact view", command=self.toggle_compact)
        self.compact_button.pack(side="right", padx=(0, 10))

        # Controls card: sources, model, screen reading, options
        controls = self.controls = theme.card(root, fill="x", padx=pad)
        self.pad = pad
        inner = ttk.Frame(controls, style="Card.TFrame", padding=(12, 10))
        inner.pack(fill="x")
        inner.columnconfigure(1, weight=1)

        def section(row, text):
            ttk.Label(inner, text=text, style="Section.Card.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 14), pady=4)
            line = ttk.Frame(inner, style="Card.TFrame")
            line.grid(row=row, column=1, sticky="ew", pady=4)
            return line

        line = section(0, "PROMPTS")
        ttk.Button(line, text="Scan today's dive", style="Accent.TButton",
                   command=self.scan_today).pack(side="left")
        ttk.Label(line, text="Unlimited game", style="Muted.Card.TLabel").pack(side="left", padx=(18, 6))
        ttk.Spinbox(line, from_=1, to=9999, width=5, textvariable=self.game_var).pack(side="left")
        ttk.Button(line, text="Scan game", command=self.scan_unlimited).pack(side="left", padx=(6, 0))
        ttk.Button(line, text="Scan all games", command=self.scan_all_unlimited).pack(side="left", padx=(6, 0))
        ttk.Button(line, text="Answer history…", command=self.open_history).pack(side="right")

        line = section(1, "BROWSER")
        ttk.Button(line, text="Start", style="Accent.TButton", command=self.start_browser).pack(side="left")
        ttk.Button(line, text="Stop", command=self.stop_browser).pack(side="left", padx=(6, 0))
        ttk.Button(line, text="Restart", command=self.restart_browser).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(line, text="Automatic answers", variable=self.auto_answers_var, style="Card.TCheckbutton",
                        command=lambda: self._save_setting("auto_answers", self.auto_answers_var.get())
                        ).pack(side="left", padx=(14, 0))
        ttk.Button(line, text="Diagnostics…", command=self.open_diagnostics).pack(side="left")
        self.browser_label = ttk.Label(line, text="Opens the game in Chrome and reads each prompt",
                                       style="Muted.Card.TLabel")
        self.browser_label.pack(side="left", padx=(12, 0))

        line = section(2, "SCREEN")
        ttk.Button(line, text=f"Read screen   {sr.HOTKEY_LABEL}", style="Accent.TButton",
                   command=self.read_screen).pack(side="left")
        ttk.Button(line, text="Set read area…", command=self.set_read_area).pack(side="left", padx=(6, 0))
        self.area_label = ttk.Label(line, style="Muted.Card.TLabel")
        self.area_label.pack(side="left", padx=(12, 0))
        self._show_area()

        line = section(3, "MODEL")
        self.model_box = ttk.Combobox(line, textvariable=self.model_var, width=20)
        self.model_box.pack(side="left")
        self.model_box.bind("<<ComboboxSelected>>", lambda e: self._model_changed())
        ttk.Button(line, text="↻", style="Icon.TButton", width=3,
                   command=self.refresh_models).pack(side="left", padx=(6, 0))
        ttk.Label(line, text="Passes", style="Muted.Card.TLabel").pack(side="left", padx=(18, 6))
        passes = ttk.Spinbox(line, from_=1, to=8, width=3, textvariable=self.passes_var,
                             command=self._passes_changed)
        passes.pack(side="left")
        passes.bind("<FocusOut>", lambda e: self._passes_changed())
        ttk.Label(line, text="Keep loaded", style="Muted.Card.TLabel").pack(side="left", padx=(18, 6))
        keep = ttk.Combobox(line, textvariable=self.keep_alive_var, width=7, state="readonly",
                            values=list(KEEP_ALIVE_LABELS.values()))
        keep.pack(side="left")
        keep.bind("<<ComboboxSelected>>", lambda e: self._keep_alive_changed())
        ttk.Checkbutton(line, text="Reasoning (Qwen3 etc., slower)", variable=self.reasoning_var,
                        style="Card.TCheckbutton", command=self._reasoning_toggled).pack(side="left", padx=(18, 0))
        ttk.Checkbutton(line, text="Load model at start", variable=self.preload_var, style="Card.TCheckbutton",
                        command=lambda: self._save_setting("preload", self.preload_var.get())).pack(side="left")

        line = section(4, "OPTIONS")
        for text, var, command in (
                ("Auto-scan daily dive", self.auto_scan_var, self._auto_scan_toggled),
                ("Copy best answer when I pick a prompt", self.auto_copy_var, None),
                ("Always on top", self.on_top_var, lambda: root.attributes("-topmost", self.on_top_var.get()))):
            ttk.Checkbutton(line, text=text, variable=var, style="Card.TCheckbutton",
                            command=command).pack(side="left")

        panes = self.panes = tk.PanedWindow(root, orient="horizontal", bg=theme.BG, bd=0, sashwidth=pad,
                                            sashrelief="flat", showhandle=False)
        panes.pack(fill="both", expand=True, padx=pad, pady=pad)

        # Left: prompts
        left = self.left_card = theme.card(panes)
        body = ttk.Frame(left, style="Card.TFrame", padding=(12, 10))
        body.pack(fill="both", expand=True)
        top = ttk.Frame(body, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="PROMPTS", style="Section.Card.TLabel").pack(side="left")
        ttk.Button(top, text="Cancel all", style="Danger.TButton", command=self.cancel_all).pack(side="right")
        ttk.Button(top, text="Cancel  Del", command=self.cancel_selected).pack(side="right", padx=6)
        # Packed before the table so the table, not these controls, shrinks when space is short.
        manual = ttk.Frame(body, style="Card.TFrame")
        manual.pack(fill="x", side="bottom", pady=(10, 0))
        self.prompt_tree = self._scrolled_tree(body, ("n", "prompt", "status"))
        for col, text, width, stretch, anchor in (("n", "#", 34, False, "center"), ("prompt", "PROMPT", 300, True, "w"),
                                                  ("status", "STATUS", 110, False, "w")):
            self.prompt_tree.heading(col, text=text, anchor=anchor)
            self.prompt_tree.column(col, width=width, stretch=stretch, anchor=anchor)
        self._stripes(self.prompt_tree)
        self.prompt_tree.tag_configure("error", foreground=theme.RED)
        self.prompt_tree.tag_configure("busy", foreground=theme.AMBER)
        self.prompt_tree.tag_configure("cancelled", foreground=theme.FAINT)
        self.prompt_tree.bind("<<TreeviewSelect>>", lambda e: self._prompt_selected())
        self.prompt_tree.bind("<Delete>", lambda e: self.cancel_selected())

        self.manual_entry = theme.HintEntry(manual, "Type a prompt, e.g. Name a pasta dish")
        self.manual_entry.pack(side="left", fill="x", expand=True)
        self.manual_entry.bind("<Return>", lambda e: self.add_manual_prompt())
        ttk.Button(manual, text="Add", command=self.add_manual_prompt).pack(side="left", padx=(6, 0))
        panes.add(left, minsize=360, width=500, stretch="always")

        # Right: answers
        right = self.right_card = theme.card(panes)
        body = ttk.Frame(right, style="Card.TFrame", padding=(14, 10))
        body.pack(fill="both", expand=True)
        top = ttk.Frame(body, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="ANSWERS", style="Section.Card.TLabel").pack(side="left")
        # Prompt stepping, shown only in compact view where the prompt list is hidden.
        self.nav = ttk.Frame(top, style="Card.TFrame")
        ttk.Button(self.nav, text="›", style="Icon.TButton", width=3,
                   command=lambda: self.step_prompt(1)).pack(side="right")
        ttk.Button(self.nav, text="‹", style="Icon.TButton", width=3,
                   command=lambda: self.step_prompt(-1)).pack(side="right", padx=(0, 4))
        self.prompt_title = ttk.Label(body, text="Pick a prompt", style="Title.Card.TLabel",
                                      wraplength=560, justify="left")
        self.prompt_title.pack(anchor="w", pady=(2, 0))
        self.rules_label = ttk.Label(body, style="Muted.Card.TLabel", wraplength=560, justify="left")
        self.rules_label.pack(anchor="w")
        body.bind("<Configure>", lambda e: [w.configure(wraplength=max(200, e.width - 30))
                                            for w in (self.prompt_title, self.rules_label)])
        footer = ttk.Frame(body, style="Card.TFrame")  # packed before the table, as on the left
        footer.pack(fill="x", side="bottom")
        self.answer_tree = self._scrolled_tree(body, ("answer", "pts", "info"))
        for col, text, width, stretch, anchor in (("answer", "ANSWER", 260, True, "w"),
                                                  ("pts", "EXP. PTS", 80, False, "e"),
                                                  ("info", "STATUS", 190, False, "w")):
            self.answer_tree.heading(col, text=text, anchor=anchor)
            self.answer_tree.column(col, width=width, stretch=stretch, anchor=anchor)
        self._stripes(self.answer_tree)
        for tier, color in theme.TIER_COLORS.items():
            self.answer_tree.tag_configure(f"tier_{tier}", foreground=color)
        self.answer_tree.tag_configure("doubt", foreground=theme.MUTED)
        self.answer_tree.tag_configure("accepted", foreground=theme.GREEN)
        self.answer_tree.tag_configure("rejected", foreground=theme.FAINT)
        self.answer_tree.bind("<Double-1>", lambda e: self.copy_selected())

        actions = ttk.Frame(footer, style="Card.TFrame")
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Copy answer", style="Accent.TButton", command=self.copy_selected).pack(side="left")
        ttk.Button(actions, text="More answers", command=self.more_answers).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Clear", command=self.clear_answers).pack(side="left", padx=(6, 0))
        ttk.Label(actions, text="Double-click an answer to copy it", style="Muted.Card.TLabel").pack(side="right")

        tk.Frame(footer, bg=theme.BORDER, height=1).pack(fill="x", pady=(14, 12))
        ttk.Label(footer, text="WHAT DID THE GAME SAY?", style="Section.Card.TLabel").pack(anchor="w")
        self.record_entry = theme.HintEntry(footer, "Answer you typed (leave blank to use the selected one)")
        self.record_entry.pack(fill="x", pady=(6, 8))
        buttons = ttk.Frame(footer, style="Card.TFrame")
        buttons.pack(fill="x")
        # Equal-width columns so the row always fits the card.
        for i, (label, tier) in enumerate(TIER_BUTTONS):
            buttons.columnconfigure(i, weight=1, uniform="tier")
            ttk.Button(buttons, text=label, style=f"{tier}.Tier.TButton",
                       command=lambda t=tier: self.record(t)).grid(row=0, column=i, sticky="ew", padx=(0, 6))
        buttons.columnconfigure(len(TIER_BUTTONS), weight=1, uniform="tier")
        ttk.Button(buttons, text="Rejected ✗", style="Danger.TButton",
                   command=lambda: self.record(None, rejected=True)).grid(row=0, column=len(TIER_BUTTONS), sticky="ew")
        panes.add(right, minsize=440, stretch="always")

    @staticmethod
    def _scrolled_tree(parent, columns):
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.pack(fill="both", expand=True, pady=(8, 0))
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        bar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y", padx=(4, 0))
        return tree

    @staticmethod
    def _stripes(tree):
        tree.tag_configure("odd", background=theme.SURFACE_ALT)
        tree.tag_configure("even", background=theme.SURFACE)

    @staticmethod
    def _stripe(i):
        return "odd" if i % 2 else "even"

    # --- background work ------------------------------------------------------

    def _bg(self, fn, done, fail=None):
        """Run fn() on a thread, then done(result) or fail(error) on the Tk thread."""
        def run():
            try:
                self.results.put((done, fn()))
            except Exception as e:  # report every failure in the status bar
                self.results.put((fail or self._report_error, e))
        threading.Thread(target=run, daemon=True).start()

    def _poll(self):
        while True:
            try:
                callback, value = self.results.get_nowait()
            except queue.Empty:
                break
            callback(value)
        self.root.after(100, self._poll)

    def _gen_worker(self):
        # One generation at a time so a laptop isn't asked to run several models at once.
        while True:
            priority, seq, (prompt, model, known) = self.gen_jobs.get()
            if seq in self.cancelled_jobs:
                self.results.put((self._job_done, (prompt, seq)))
                continue
            token = ks.CancelToken()
            self.active = (seq, prompt, token, priority)
            if seq in self.cancelled_jobs:  # cancelled between the check above and now
                token.cancel()
            self.results.put((self._generation_started, prompt))
            try:
                model = ks.pick_model(self.host, model or None)
                self.models_used.add(model)
                ks.generate(self.host, model, prompt, COUNT, known, cancel=token, reasoning=self.reasoning,
                            keep_alive=self.keep_alive,
                            on_answer=lambda a, m=model: self.results.put((self._streamed, (prompt, m, a))))
                self.results.put((self._generated, (prompt, model, seq)))
            except ks.GenerationCancelled:
                self.results.put((self._generation_cancelled, (prompt, seq)))
            except Exception as e:
                self.results.put((self._generate_failed, (prompt, e, seq)))
            finally:
                self.active = None

    def _report_error(self, error):
        self.status_var.set(f"Error: {error}")

    # --- scanning -------------------------------------------------------------

    def scan_today(self):
        self.status_var.set("Scanning krillion.io…")
        self._bg(ks.fetch_today, self._today_scanned)

    def _today_scanned(self, day):
        self.source_var.set(f"Daily dive #{day['dayNumber']}")
        self._show_prompts(day["prompts"])
        self.status_var.set(f"Found {len(day['prompts'])} prompts for dive #{day['dayNumber']}")
        self._schedule_rescan(day.get("resetsAt"))

    def scan_unlimited(self):
        try:
            game = int(self.game_var.get())
        except (tk.TclError, ValueError):
            self.status_var.set("Enter an Unlimited game number")
            return
        self.status_var.set(f"Scanning Unlimited game {game}…")
        self._bg(lambda: ks.fetch_unlimited(game), self._unlimited_scanned)

    def _unlimited_scanned(self, game):
        self.source_var.set(f"Unlimited game {game['game']} of {game['lastGame']}")
        self._show_prompts(game["prompts"])
        self.status_var.set(f"Found {len(game['prompts'])} prompts")

    def scan_all_unlimited(self):
        self.status_var.set("Scanning every Unlimited game…")

        def fetch_all():
            first = ks.fetch_unlimited(1)
            prompts = list(first["prompts"])
            for n in range(2, (first["lastGame"] or 1) + 1):
                time.sleep(0.3)  # go easy on the site
                prompts += ks.fetch_unlimited(n)["prompts"]
                self.results.put((self.status_var.set, f"Scanning Unlimited game {n} of {first['lastGame']}…"))
            return first["lastGame"], list(dict.fromkeys(prompts))

        def done(result):
            last, prompts = result
            self.source_var.set(f"Unlimited games 1–{last} ({len(prompts)} prompts)")
            self._show_prompts(prompts)
            queued = sum(self.status[p] == "queued" for p in prompts)
            self.status_var.set(f"Found {len(prompts)} unique prompts; generating answers for {queued}")

        self._bg(fetch_all, done)

    def _schedule_rescan(self, resets_at):
        if self.rescan_after:
            self.root.after_cancel(self.rescan_after)
            self.rescan_after = None
        if not (resets_at and self.auto_scan_var.get()):
            return
        try:
            reset = datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
        except ValueError:
            return
        delay = int((reset - datetime.now(timezone.utc)).total_seconds() * 1000) + 60_000
        self.rescan_after = self.root.after(max(60_000, min(delay, MAX_RESCAN_MS)), self.scan_today)

    def _auto_scan_toggled(self):
        if self.auto_scan_var.get():
            self.scan_today()
        elif self.rescan_after:
            self.root.after_cancel(self.rescan_after)
            self.rescan_after = None

    # --- prompts ----------------------------------------------------------------

    def _show_prompts(self, prompts):
        self.prompts = list(prompts)
        self.prompt_tree.delete(*self.prompt_tree.get_children())
        for i, prompt in enumerate(self.prompts):
            self.prompt_tree.insert("", "end", iid=str(i), values=(i + 1, prompt, ""))
            if self._needs_answers(prompt):
                self._ensure_answers(prompt)
            self._set_status(prompt, self.status[prompt])
        if self.prompts:
            self.prompt_tree.selection_set("0")

    def add_manual_prompt(self):
        prompt = self.manual_entry.get().strip()
        if prompt:
            self.manual_entry.delete(0, "end")
            self.open_prompt(prompt)

    def open_prompt(self, prompt):
        """Show a prompt (adding it to the list if needed) and jump its generation to the front."""
        if prompt not in self.prompts:
            self.prompts.append(prompt)
            i = len(self.prompts) - 1
            self.prompt_tree.insert("", "end", iid=str(i), values=(i + 1, prompt, ""))
        if self._needs_answers(prompt):
            self._ensure_answers(prompt, urgent=True)
        i = self.prompts.index(prompt)
        self._set_status(prompt, self.status[prompt])
        self.prompt_tree.selection_set(str(i))
        self.prompt_tree.see(str(i))
        # Selecting an already-selected row fires no event, so show (and copy) directly.
        self._show_answers(prompt, copy_best=self.auto_copy_var.get())

    def _ensure_answers(self, prompt, force=False, urgent=False, resume=False):
        self._queue_local(prompt)
        usable = self.lex.ranked(prompt)
        # Built-in answers don't count here, so the model still runs for prompts that have them.
        if force or self.lex.model_count(prompt) < MIN_CACHED:
            known = [a["answer"] for a in self.lex.ranked(prompt, include_rejected=True)]
            self.status[prompt] = "queued"
            seq = next(self.job_seq)
            self.pending.setdefault(prompt, set()).add(seq)
            job = (prompt, self.model_var.get().strip(), known)
            # Urgent first, then a background prompt that was paused for one, then everything else.
            self.gen_jobs.put((0 if urgent else 0.5 if resume else 1, seq, job))
            if urgent:
                self._preempt_background()
        else:
            self.status[prompt] = f"ready · {len(usable)}"

    def _preempt_background(self):
        """Pause a running background generation so an urgent prompt starts now; it is requeued."""
        active = self.active
        if active and active[3] > 0 and active[0] not in self.cancelled_jobs:
            self.preempted.add(active[0])
            self.cancelled_jobs.add(active[0])
            active[2].cancel()

    # --- built-in answers ----------------------------------------------------------

    def _queue_local(self, prompt):
        if prompt not in self.local_queued and self.lex.needs_local(prompt):
            self.local_queued.add(prompt)
            self.local_jobs.put(prompt)

    def _local_worker(self):
        # Word prompts scan the dictionary (about half a second), so do it off the Tk thread.
        while True:
            prompt = self.local_jobs.get()
            try:
                found = local_knowledge.answers(prompt)
            except Exception:
                found = []
            self.results.put((self._local_done, (prompt, found)))

    def _local_done(self, result):
        prompt, found = result
        self.local_queued.discard(prompt)
        if not self.lex.add_local(prompt, found):
            return
        self.lex.save()
        if not self.status.get(prompt, "").startswith(("queued", "thinking", "cancel")):
            self._set_status(prompt, f"ready · {len(self.lex.ranked(prompt))}")
        if prompt == self.current_prompt():
            self._show_answers(prompt, copy_best=self.auto_copy_var.get() and not self.shown_answers)

    def _needs_answers(self, prompt):
        """True for prompts never generated, or whose last generation failed or was cancelled."""
        return self.status.get(prompt, "error").startswith(("error", "cancelled"))

    # --- cancelling ---------------------------------------------------------------

    def cancel_selected(self):
        prompt = self.current_prompt()
        if prompt and not self.cancel(prompt):
            self.status_var.set("That prompt isn't queued or generating")

    def cancel_all(self):
        count = sum(self.cancel(p, quiet=True) for p in list(self.pending))
        self.status_var.set(f"Cancelled {count} prompt(s)" if count else "Nothing to cancel")

    def cancel(self, prompt, quiet=False):
        """Stop a prompt's queued or running generation; answers that already arrived are kept."""
        seqs = self.pending.get(prompt)
        if not seqs:
            return False
        self.cancelled_jobs.update(seqs)
        self.preempted -= seqs  # a paused background job you cancel must not come back
        active = self.active
        if active and active[0] in seqs:
            # Only the running job stays pending; the worker reports back through _generation_cancelled.
            self.pending[prompt] = {active[0]}
            active[2].cancel()
            self._set_status(prompt, "cancelling…")
        else:
            del self.pending[prompt]  # queued jobs are just skipped when the worker reaches them
            self._mark_cancelled(prompt)
        if not quiet:
            self.status_var.set(f"Cancelled “{prompt}”")
        return True

    def _mark_cancelled(self, prompt):
        count = len(self.lex.ranked(prompt))
        self._set_status(prompt, f"cancelled · {count}" if count else "cancelled")

    def _job_done(self, result):
        prompt, seq = result
        self.cancelled_jobs.discard(seq)
        seqs = self.pending.get(prompt)
        if seqs:
            seqs.discard(seq)
            if not seqs:
                del self.pending[prompt]

    def _generation_cancelled(self, result):
        prompt, seq = result
        self._job_done((prompt, seq))
        self.lex.save()  # keep the answers that streamed in before the cancel
        self.stream_added.pop(prompt, None)
        if seq in self.preempted:  # paused for an urgent prompt, not cancelled by you
            self.preempted.discard(seq)
            if prompt not in self.pending:
                self._ensure_answers(prompt, resume=True)
                self._set_status(prompt, self.status[prompt])
        elif prompt not in self.pending:
            self._mark_cancelled(prompt)

    # --- reading the screen -------------------------------------------------------

    def _show_area(self):
        area = self.settings.get("read_area")
        self.area_label.configure(text=f"Reading a {area[2]}×{area[3]} area at ({area[0]}, {area[1]})" if area
                                  else "No read area yet. Set one around where the game shows prompts")

    def _save_setting(self, key, value):
        settings = sr.load_settings()  # keep anything edited by hand, like ignore_phrases
        settings[key] = value
        sr.save_settings(settings)
        self.settings[key] = value

    def _keep_alive_changed(self):
        label = self.keep_alive_var.get()
        self.keep_alive = next(k for k, v in KEEP_ALIVE_LABELS.items() if v == label)
        self._save_setting("keep_alive", self.keep_alive)
        self.status_var.set(f"The model now stays loaded for {label} after the last answer "
                            "(less memory between prompts, but a slower first answer after a break)"
                            if self.keep_alive == "1m" else
                            f"The model now stays loaded for {label} after the last answer")

    def _reasoning_toggled(self):
        self.reasoning = self.reasoning_var.get()
        self._save_setting("reasoning", self.reasoning)
        self.status_var.set("Reasoning on: may be more accurate, much slower start" if self.reasoning
                            else "Reasoning off: answers start sooner")

    def _show_hotkey(self, ok):
        # A label rather than the status bar, which other messages overwrite right away.
        if ok:
            self.hotkey_label.configure(text=f"●  {sr.HOTKEY_LABEL} ready", fg=theme.GREEN, bg=theme.GREEN_SOFT)
        else:
            self.hotkey_label.configure(
                text=f"●  {sr.HOTKEY_LABEL} in use by another program (Krillion Solver open twice?), retrying",
                fg=theme.RED, bg=theme.RED_SOFT)

    def close(self):
        """Stop generating and free the model's memory, then quit."""
        try:
            self.cancel_all()
            self.root.withdraw()  # disappear right away; unloading takes a moment
            self.root.update()
            if self.monitor.running:
                self.monitor.stop()  # closes the monitored Chrome window
            for model in list(self.models_used):
                try:
                    ks.unload_model(self.host, model)
                except Exception:  # Ollama stopped or misbehaving: still quit
                    pass
        finally:
            # Always exit; a hidden window that keeps running would also keep the hotkey.
            self.root.destroy()

    def set_read_area(self, then_read=False):
        def done(area):
            self.root.deiconify()
            if area:
                self._save_setting("read_area", list(area))
                self._show_area()
                self.status_var.set("Read area saved")
                if then_read:
                    self.read_screen()
            else:
                self.status_var.set("Read area unchanged")
        self.root.withdraw()  # get this window out of the way of the game
        self.root.after(200, lambda: sr.pick_region(self.root, done))

    def read_screen(self):
        area = self.settings.get("read_area")
        if not area:
            self.status_var.set("First drag a box around where the game shows its prompt")
            self.set_read_area(then_read=True)
            return
        if self.reading:
            return
        self.reading = True
        self.status_var.set("Reading the screen…")

        def done(lines):
            self.reading = False
            # Re-read so phrases added to settings.json apply without restarting.
            text = sr.extract_prompt(lines, sr.load_settings().get("ignore_phrases", []))
            if not text:
                self.status_var.set("No prompt found in the read area. Is it still over the prompt?")
                return
            # Reuse a known prompt only when the difference is a classic OCR misread (see ks.ocr_key).
            listed = {ks.ocr_key(p): p for p in self.prompts}
            prompt = listed.get(ks.ocr_key(text)) or self.lex.find_ocr(text) or text
            self.open_prompt(prompt)
            note = "" if prompt == text else f" (read as “{text}”)"
            self.status_var.set(f"Read “{prompt}”{note}")

        def fail(error):
            self.reading = False
            self.status_var.set(f"Could not read the screen: {error}")

        self._bg(lambda: sr.read_region(area), done, fail)

    def _set_status(self, prompt, text):
        self.status[prompt] = text
        tag = ("error" if text == "error" else "busy" if text.startswith(("queued", "thinking"))
               else "cancelled" if text.startswith("cancel") else "")
        for i, p in enumerate(self.prompts):
            if p == prompt:
                self.prompt_tree.item(str(i), values=(i + 1, p, text), tags=(tag, self._stripe(i)))

    def _generation_started(self, prompt):
        self.stream_added[prompt] = 0
        if not self.status.get(prompt, "").startswith("cancel"):
            self._set_status(prompt, "thinking…")

    def _streamed(self, result):
        """One answer arrived from the model: store it and show it right away."""
        prompt, model, answer = result
        if not ks.add_generated(self.lex, prompt, [answer], model):
            return
        self.stream_added[prompt] = self.stream_added.get(prompt, 0) + 1
        if not self.status.get(prompt, "").startswith("cancel"):
            self._set_status(prompt, f"thinking · {len(self.lex.ranked(prompt))}")
        if prompt == self.current_prompt():
            # Copy now if the prompt was picked before it had any answers.
            self._show_answers(prompt, copy_best=self.auto_copy_var.get() and not self.shown_answers)

    def _generated(self, result):
        prompt, model, seq = result
        self._job_done((prompt, seq))
        self.lex.save()
        if prompt in self.pending:  # another generation for it is still queued
            return
        usable = len(self.lex.ranked(prompt))
        self._set_status(prompt, f"ready · {usable}")
        self.status_var.set(f"{model}: {self.stream_added.pop(prompt, 0)} new answer(s) for “{prompt}”")
        done = self.passes_done[prompt] = self.passes_done.get(prompt, 0) + 1
        current = prompt == self.current_prompt()
        if done < self.passes and (len(self.prompts) <= MAX_LIST_FOR_PASSES or current):
            # Another pass: the model is told every answer it already gave, so it finds new ones.
            self._ensure_answers(prompt, force=True, resume=current)
            self._set_status(prompt, f"queued · pass {done + 1}/{self.passes}")
        elif usable < MIN_CACHED and prompt not in self.topped_up:
            # Most answers broke the prompt's rules or were known: one automatic extra round.
            self.topped_up.add(prompt)
            self._ensure_answers(prompt, force=True, urgent=prompt == self.current_prompt())
            self._set_status(prompt, "queued")
            self.status_var.set(f"Only {usable} valid answer(s) for “{prompt}”, asking for more")

    def _generate_failed(self, result):
        prompt, error, seq = result
        self._job_done((prompt, seq))
        self.lex.save()  # keep any answers that streamed in before the failure
        self._set_status(prompt, "error")
        self.status_var.set(f"Error on “{prompt}”: {error}")

    def current_prompt(self):
        sel = self.prompt_tree.selection()
        return self.prompts[int(sel[0])] if sel else None

    def _prompt_selected(self):
        prompt = self.current_prompt()
        if prompt:
            self._show_answers(prompt, copy_best=self.auto_copy_var.get())

    # --- answers ----------------------------------------------------------------

    def _show_answers(self, prompt, copy_best):
        same_prompt = self.prompt_title.cget("text") == prompt
        keep = self.selected_answer() if same_prompt else None
        self.prompt_title.configure(text=prompt)
        rules = prompt_rules.parse(prompt).describe()
        self.rules_label.configure(text=f"Rules: {rules}" if rules else "")
        self.answer_tree.delete(*self.answer_tree.get_children())
        self.shown_answers = self.lex.ranked(prompt, include_rejected=True)
        for i, a in enumerate(self.shown_answers):
            if a["status"] == "accepted":
                info, color = f"✓ accepted · {TIER_NAMES.get(a['tier'], '?')}", "accepted"
            elif a["status"] == "rejected":
                info, color = "✗ rejected", "rejected"
            elif a["violation"]:
                info, color = f"✗ {a['violation']}", "rejected"
            else:
                tier = a["est_tier"]
                info, color = f"{TIER_NAMES.get(tier, tier)} guess · {a['confidence']:.0%}", f"tier_{tier}"
                if a["source"] == "local":
                    info += " · built-in"
                elif a["source"] == "dictionary":
                    info += " · dictionary"
                if a.get("doubt"):
                    info, color = f"{info} · {a['doubt']}", "doubt"
            self.answer_tree.insert("", "end", iid=str(i), tags=(color, self._stripe(i)),
                                    values=(a["answer"], f"{ks.expected_points(a):.0f}", info))
        if not same_prompt:
            self.answer_tree.yview_moveto(0)  # a new prompt starts at the top of the list
        names = [a["answer"] for a in self.shown_answers]
        best_ok = bool(self.shown_answers) and ks.expected_points(self.shown_answers[0]) > 0
        if keep in names:
            self.answer_tree.selection_set(str(names.index(keep)))
        elif best_ok:
            self.answer_tree.selection_set("0")
        if copy_best and best_ok:
            self._copy(self.shown_answers[0]["answer"])

    def selected_answer(self):
        sel = self.answer_tree.selection()
        return self.shown_answers[int(sel[0])]["answer"] if sel else None

    def copy_selected(self):
        answer = self.selected_answer()
        if answer:
            self._copy(answer)

    def _copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set(f"Copied “{text}” to the clipboard")

    def more_answers(self):
        prompt = self.current_prompt()
        if prompt:
            self.passes_done[prompt] = 0  # a fresh set of passes
            self._ensure_answers(prompt, force=True, urgent=True)
            self._set_status(prompt, "queued")

    def clear_answers(self):
        """Empty the answers pane (nothing is deleted; pick the prompt again to see them)."""
        self.prompt_tree.selection_remove(*self.prompt_tree.selection())
        self.prompt_title.configure(text="Pick a prompt")
        self.rules_label.configure(text="")
        self.answer_tree.delete(*self.answer_tree.get_children())
        self.shown_answers = []

    def record(self, tier, rejected=False):
        prompt = self.current_prompt()
        answer = self.record_entry.get().strip() or self.selected_answer()
        if not (prompt and answer):
            self.status_var.set("Pick a prompt and an answer (or type one) first")
            return
        self.lex.mark(prompt, answer, tier, rejected)
        self.lex.save()
        self.record_entry.delete(0, "end")
        usable = len(self.lex.ranked(prompt))
        self._set_status(prompt, f"ready · {usable}")
        self._show_answers(prompt, copy_best=False)
        self.status_var.set(f"Recorded “{answer}” as {'rejected' if rejected else tier}")
        if rejected and usable < MIN_CACHED and prompt not in self.pending:
            self._ensure_answers(prompt, force=True, urgent=True)
            self._set_status(prompt, "queued")
            self.status_var.set(f"Recorded “{answer}” as rejected; only {usable} left, fetching replacements")

    # --- compact view -------------------------------------------------------------

    def toggle_compact(self):
        """Small always-on-top window with just the prompt and answers, to sit beside the game."""
        self.compact = not self.compact
        root = self.root
        if self.compact:
            # A maximized window ignores new sizes, so un-maximize first and remember it.
            self.was_zoomed = root.state() == "zoomed"
            if self.was_zoomed:
                root.state("normal")
                root.update_idletasks()
            self.full_geometry = root.geometry()
            self.source_label.pack_forget()  # no room in the compact header
            self.controls.pack_forget()
            self.panes.forget(self.left_card)
            self.nav.pack(side="right")
            root.minsize(440, 460)
            root.geometry(COMPACT_SIZE)
            root.attributes("-topmost", True)
            self.compact_button.configure(text="Full view")
        else:
            self.source_label.pack(side="left", padx=(14, 0), pady=(5, 0))
            self.controls.pack(fill="x", padx=self.pad, after=self.header)
            self.panes.add(self.left_card, before=self.right_card, minsize=360, width=500, stretch="always")
            self.nav.pack_forget()
            root.minsize(940, 580)
            root.geometry(self.full_geometry)
            if self.was_zoomed:
                root.state("zoomed")
            root.attributes("-topmost", self.on_top_var.get())
            self.compact_button.configure(text="Compact view")

    def step_prompt(self, delta):
        if not self.prompts:
            return
        current = self.current_prompt()
        i = self.prompts.index(current) + delta if current in self.prompts else 0
        i = max(0, min(len(self.prompts) - 1, i))
        self.prompt_tree.selection_set(str(i))
        self.prompt_tree.see(str(i))

    # --- models -----------------------------------------------------------------

    def refresh_models(self):
        def fetch():
            try:
                return ks.list_models(self.host)
            except ks.OllamaError:
                if self.tried_starting_ollama or not ks.start_ollama():
                    raise
                self.tried_starting_ollama = True
                self.results.put((self.status_var.set, "Starting Ollama…"))
                return ks.wait_for_ollama(self.host, 30)

        def done(models):
            self.model_box.configure(values=models)
            if not self.model_var.get() and models:
                self.model_var.set(models[0])
            if not models:
                self.status_var.set("Ollama has no models installed. Try: ollama pull llama3.2:3b")
            elif self.preload_var.get() and self.loaded_model != self.model_var.get():
                self.preload()

        def fail(error):
            self.status_var.set(f"Ollama not reachable at {self.host}; built-in answers still work. "
                                "Start Ollama, then press ↻")

        self._bg(fetch, done, fail)

    def preload(self):
        """Load the model now, so the first prompt doesn't wait for it."""
        model = self.model_var.get().strip()
        if not model:
            return
        self.loaded_model = model
        self.models_used.add(model)  # unloaded again when the window closes
        self.status_var.set(f"Loading {model}…")
        self._bg(lambda: ks.preload_model(self.host, model, self.keep_alive),
                 lambda secs: self.status_var.set(f"{model} is loaded and ready ({secs:.1f}s)"),
                 lambda e: self.status_var.set(f"Couldn't load {model}: {e}"))

    def _model_changed(self):
        # Free the previous model first: on 16 GB, two loaded models can push memory to the limit.
        old, new = self.loaded_model, self.model_var.get().strip()
        if old and old != new and old in self.models_used:
            self._bg(lambda: ks.unload_model(self.host, old), lambda _: None, lambda e: None)
            self.loaded_model = None
        if self.preload_var.get():
            self.preload()

    def _passes_changed(self):
        try:
            self.passes = max(1, min(8, int(self.passes_var.get())))
        except (tk.TclError, ValueError):
            return
        self.passes_var.set(self.passes)
        self._save_setting("passes", self.passes)

    # --- browser monitor --------------------------------------------------------------

    def start_browser(self):
        try:
            self.monitor.start()
            self.browser_label.configure(text="Starting Chrome…")
        except browser_monitor.MonitorError as e:
            self.browser_label.configure(text=str(e))

    def stop_browser(self):
        self.browser_label.configure(text="Stopping…")
        self._bg(self.monitor.stop, lambda _: self.browser_label.configure(text="Stopped"))

    def restart_browser(self):
        self.browser_label.configure(text="Restarting…")
        self._bg(lambda: (self.monitor.stop(), self.monitor.start()), lambda _: None,
                 lambda e: self.browser_label.configure(text=f"Couldn't restart: {e}"))

    def _monitor_event(self, event):
        kind, data = event
        if kind == "diagnostics":
            self.last_diagnostics = data
            if data.get("method") == "Puffer Party isn't monitored":
                self.browser_label.configure(text="Puffer Party isn't monitored")
            self._update_diagnostics()
        elif kind == "prompt":
            self.browser_label.configure(text=f"Prompt: {data}")
            if self.auto_answers_var.get():
                self.open_prompt(data)
            else:
                self.status_var.set(f"Detected “{data}” (automatic answers are off)")
        elif kind == "status":
            self.browser_label.configure(text=data)
        elif kind == "stopped":
            self.browser_label.configure(text=data)

    def open_diagnostics(self):
        if self.diag_window and self.diag_window.winfo_exists():
            self.diag_window.lift()
            return
        win = self.diag_window = tk.Toplevel(self.root, bg=theme.BG)
        win.title("Browser diagnostics")
        win.geometry("760x560")
        theme.dark_title_bar(win)
        body = theme.card(win, fill="both", expand=True, padx=14, pady=14)
        self.diag_fields = {}
        for name in ("URL", "Method", "Selected prompt", "Error"):
            row = tk.Frame(body, bg=theme.SURFACE)
            row.pack(fill="x", padx=12, pady=(8, 0))
            tk.Label(row, text=name.upper(), bg=theme.SURFACE, fg=theme.MUTED, width=16, anchor="w",
                     font=(theme.FAMILY, 8, "bold")).pack(side="left")
            value = tk.Label(row, bg=theme.SURFACE, fg=theme.TEXT, anchor="w", justify="left",
                             wraplength=560, font=(theme.FAMILY, 10))
            value.pack(side="left", fill="x", expand=True)
            self.diag_fields[name] = value
        tk.Label(body, text="EXTRACTED PAGE TEXT", bg=theme.SURFACE, fg=theme.MUTED, anchor="w",
                 font=(theme.FAMILY, 8, "bold")).pack(fill="x", padx=12, pady=(12, 4))
        self.diag_text = tk.Text(body, bg=theme.FIELD, fg=theme.TEXT, insertbackground=theme.TEXT, relief="flat",
                                 font=("Consolas", 9), wrap="word", highlightthickness=0)
        self.diag_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._update_diagnostics()

    def _update_diagnostics(self):
        if not (self.diag_window and self.diag_window.winfo_exists()):
            return
        d = self.last_diagnostics
        running = self.monitor.running
        for name, value in (("URL", d.get("url") or ("—" if running else "Browser not started")),
                            ("Method", d.get("method") or "—"), ("Selected prompt", d.get("prompt") or "—"),
                            ("Error", d.get("error") or "—")):
            self.diag_fields[name].configure(text=value, fg=theme.RED if name == "Error" and d.get("error") else theme.TEXT)
        text = d.get("text") or ""
        if self.diag_text.get("1.0", "end-1c") != text:
            self.diag_text.delete("1.0", "end")
            self.diag_text.insert("1.0", text)

    # --- answer history -----------------------------------------------------------------

    def open_history(self):
        if self.history_window and self.history_window.exists():
            self.history_window.lift()
            return
        self.history_window = history_window.HistoryWindow(self.root, self.lex, on_change=self._history_changed)

    def _history_changed(self, prompt=None):
        """The history editor changed the lexicon: refresh what's on screen."""
        for p in self.prompts:
            if not self.status.get(p, "").startswith(("queued", "thinking", "cancel")) and self.lex.find(p):
                self._set_status(p, f"ready · {len(self.lex.ranked(p))}")
        current = self.current_prompt()
        if current:
            self._show_answers(current, copy_best=False)


def main():
    p = argparse.ArgumentParser(description="Krillion solver GUI")
    p.add_argument("--lexicon", default=ks.DEFAULT_LEXICON)
    p.add_argument("--host", default=ks.DEFAULT_HOST)
    p.add_argument("--model")
    args = p.parse_args()
    sr.enable_dpi_awareness()  # so the read area is in the same pixels the OCR captures
    root = tk.Tk()
    app = App(root, args.lexicon, args.host, args.model)
    # Without a terminal (Krillion Solver.pyw) errors would vanish, so show them in the status bar.
    root.report_callback_exception = lambda exc, value, tb: app.status_var.set(f"Error: {value!r}")
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()


if __name__ == "__main__":
    main()
