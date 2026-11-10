"""Answer history editor: search the lexicon, fix verdicts and spellings, delete, export."""

import csv
import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import krillion_solver as ks
import theme

FILTERS = ("All prompts", "With feedback", "With rejections")
TIER_LABELS = {"Plankton": "plankton", "Schooler": "schooler", "Rare": "rare", "Deep cut": "deep_cut",
               "Krillion": "krillion"}


class HistoryWindow:
    def __init__(self, master, lex, on_change):
        self.lex = lex
        self.on_change = on_change
        self.keys = []      # lexicon keys shown in the prompt list, in order
        self.answers = []   # answers shown on the right, in order
        win = self.win = tk.Toplevel(master, bg=theme.BG)
        win.title("Answer history")
        win.geometry("1100x680")
        win.minsize(820, 480)
        theme.dark_title_bar(win)

        top = ttk.Frame(win, padding=(14, 14, 14, 8))
        top.pack(fill="x")
        self.search = theme.HintEntry(top, "Search prompts and answers")
        self.search.pack(side="left", fill="x", expand=True)
        self.search.bind("<KeyRelease>", lambda e: self.refresh())
        self.filter_var = tk.StringVar(value=FILTERS[0])
        box = ttk.Combobox(top, textvariable=self.filter_var, values=FILTERS, state="readonly", width=16)
        box.pack(side="left", padx=(8, 0))
        box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Button(top, text="Export training data…", style="Accent.TButton",
                   command=self.export_training).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Export CSV…", command=self.export_csv).pack(side="left", padx=(6, 0))

        panes = tk.PanedWindow(win, orient="horizontal", bg=theme.BG, bd=0, sashwidth=14, sashrelief="flat")
        panes.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        left = theme.card(panes)
        body = ttk.Frame(left, style="Card.TFrame", padding=(12, 10))
        body.pack(fill="both", expand=True)
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x")
        self.count_label = ttk.Label(row, style="Section.Card.TLabel")
        self.count_label.pack(side="left")
        ttk.Button(row, text="Delete prompt", style="Danger.TButton", command=self.delete_prompt).pack(side="right")
        self.prompt_tree = self._tree(body, (("prompt", "PROMPT", 240, True), ("n", "ANSWERS", 76, False),
                                             ("ok", "ACCEPTED", 82, False)))
        self.prompt_tree.bind("<<TreeviewSelect>>", lambda e: self.show_answers())
        panes.add(left, minsize=380, width=520, stretch="always")

        right = theme.card(panes)
        body = ttk.Frame(right, style="Card.TFrame", padding=(12, 10))
        body.pack(fill="both", expand=True)
        self.title = ttk.Label(body, text="Pick a prompt", style="Title.Card.TLabel", wraplength=480, justify="left")
        self.title.pack(anchor="w")
        actions = ttk.Frame(body, style="Card.TFrame")
        actions.pack(fill="x", side="bottom", pady=(10, 0))
        self.tier_var = tk.StringVar(value="Rare")
        ttk.Combobox(actions, textvariable=self.tier_var, values=list(TIER_LABELS), state="readonly",
                     width=9).pack(side="left")
        ttk.Button(actions, text="Mark accepted", command=self.mark_accepted).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Mark rejected", style="Danger.TButton",
                   command=self.mark_rejected).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Clear verdict", command=self.clear_verdict).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Fix spelling…", command=self.rename).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Delete", style="Danger.TButton", command=self.delete_answer).pack(side="right")
        self.answer_tree = self._tree(body, (("answer", "ANSWER", 220, True), ("status", "STATUS", 150, False),
                                             ("source", "SOURCE", 120, False), ("added", "ADDED", 100, False)))
        self.answer_tree.tag_configure("accepted", foreground=theme.GREEN)
        self.answer_tree.tag_configure("rejected", foreground=theme.FAINT)
        panes.add(right, minsize=420, stretch="always")

        self.status = tk.Label(win, bg=theme.SURFACE, fg=theme.MUTED, anchor="w", padx=14, pady=5,
                               font=(theme.FAMILY, 9))
        self.status.pack(fill="x", side="bottom")
        self.refresh()

    @staticmethod
    def _tree(parent, columns):
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.pack(fill="both", expand=True, pady=(8, 0))
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", selectmode="browse")
        for name, text, width, stretch in columns:
            tree.heading(name, text=text, anchor="w")
            tree.column(name, width=width, stretch=stretch, anchor="w")
        tree.tag_configure("odd", background=theme.SURFACE_ALT)
        bar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y", padx=(4, 0))
        return tree

    def exists(self):
        return bool(self.win.winfo_exists())

    def lift(self):
        self.win.deiconify()
        self.win.lift()
        self.refresh()

    # --- listing ---------------------------------------------------------------------

    def refresh(self):
        query = self.search.get().strip().lower()
        mode = self.filter_var.get()
        selected = self.current_key()
        self.prompt_tree.delete(*self.prompt_tree.get_children())
        self.keys = []
        for key, entry in sorted(self.lex.prompts.items(), key=lambda kv: kv[1]["prompt"].lower()):
            answers = entry["answers"].values()
            accepted = sum(a["status"] == "accepted" for a in answers)
            rejected = sum(a["status"] == "rejected" for a in answers)
            if mode == FILTERS[1] and not (accepted or rejected) or mode == FILTERS[2] and not rejected:
                continue
            if query and query not in entry["prompt"].lower() and not any(query in a["answer"].lower() for a in answers):
                continue
            i = len(self.keys)
            self.keys.append(key)
            self.prompt_tree.insert("", "end", iid=str(i), values=(entry["prompt"], len(entry["answers"]), accepted),
                                    tags=("odd",) if i % 2 else ())
        self.count_label.configure(text=f"{len(self.keys)} OF {len(self.lex.prompts)} PROMPTS")
        if selected in self.keys:
            self.prompt_tree.selection_set(str(self.keys.index(selected)))
        self.show_answers()

    def current_key(self):
        sel = self.prompt_tree.selection()
        return self.keys[int(sel[0])] if sel and int(sel[0]) < len(self.keys) else None

    def current_prompt(self):
        key = self.current_key()
        return self.lex.prompts[key]["prompt"] if key in self.lex.prompts else None

    def show_answers(self):
        keep = self.selected_answer()
        self.answer_tree.delete(*self.answer_tree.get_children())
        prompt = self.current_prompt()
        self.title.configure(text=prompt or "Pick a prompt")
        self.answers = self.lex.ranked(prompt, include_rejected=True) if prompt else []
        for i, a in enumerate(self.answers):
            if a["status"] == "accepted":
                status = f"accepted · {a['tier'] or '?'}"
            elif a["status"] == "rejected":
                status = "rejected"
            else:
                status = "unverified" + (" · breaks rule" if a.get("violation") else "")
            tags = [a["status"]] + (["odd"] if i % 2 else [])
            self.answer_tree.insert("", "end", iid=str(i), tags=tags,
                                    values=(a["answer"], status, a.get("source", ""), (a.get("added") or "")[:10]))
        names = [a["answer"] for a in self.answers]
        if keep in names:
            self.answer_tree.selection_set(str(names.index(keep)))

    def selected_answer(self):
        sel = self.answer_tree.selection()
        return self.answers[int(sel[0])]["answer"] if sel and int(sel[0]) < len(self.answers) else None

    # --- edits -------------------------------------------------------------------------

    def _changed(self, message):
        self.lex.save()
        self.refresh()
        self.status.configure(text=message)
        self.on_change()

    def _target(self):
        prompt, answer = self.current_prompt(), self.selected_answer()
        if not (prompt and answer):
            self.status.configure(text="Pick a prompt and an answer first")
        return prompt, answer

    def mark_accepted(self):
        prompt, answer = self._target()
        if answer:
            tier = TIER_LABELS[self.tier_var.get()]
            self.lex.mark(prompt, answer, tier)
            self._changed(f"“{answer}” marked accepted ({self.tier_var.get()})")

    def mark_rejected(self):
        prompt, answer = self._target()
        if answer:
            self.lex.mark(prompt, answer, rejected=True)
            self._changed(f"“{answer}” marked rejected")

    def clear_verdict(self):
        prompt, answer = self._target()
        if answer:
            self.lex.reset(prompt, answer)
            self._changed(f"“{answer}” is an unverified guess again")

    def rename(self):
        prompt, answer = self._target()
        if not answer:
            return
        new = simpledialog.askstring("Fix spelling", "Correct spelling:", initialvalue=answer, parent=self.win)
        if new and new.strip() and new.strip() != answer:
            if self.lex.rename(prompt, answer, new):
                self._changed(f"“{answer}” is now “{new.strip()}”")
            else:
                self.status.configure(text=f"“{new.strip()}” is already an answer to this prompt")

    def delete_answer(self):
        prompt, answer = self._target()
        if answer:
            self.lex.delete(prompt, answer)
            self._changed(f"Deleted “{answer}”")

    def delete_prompt(self):
        prompt = self.current_prompt()
        if not prompt:
            self.status.configure(text="Pick a prompt first")
            return
        if messagebox.askyesno("Delete prompt", f"Delete “{prompt}” and all its answers?\n\n"
                                  "This can't be undone.", parent=self.win):
            self.lex.delete(prompt)
            self._changed(f"Deleted “{prompt}”")

    # --- export ----------------------------------------------------------------------

    def export_training(self):
        folder = filedialog.askdirectory(parent=self.win, title="Folder for the training files",
                                         initialdir=os.path.join(ks.HERE, "training"))
        if folder:
            examples, rejected = ks.export_training(self.lex, folder)
            self.status.configure(text=f"Wrote {examples} training example(s) and {rejected} rejected answer(s) "
                                       f"to {folder}. Next: training/README.md")

    def export_csv(self):
        path = filedialog.asksaveasfilename(parent=self.win, title="Export answer history",
                                            defaultextension=".csv", initialfile="answer_history.csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        rows = 0
        with open(path, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel reads accents
            writer = csv.writer(f)
            writer.writerow(["prompt", "answer", "status", "tier", "estimated tier", "confidence", "source", "added"])
            for entry in self.lex.prompts.values():
                for a in entry["answers"].values():
                    writer.writerow([entry["prompt"], a["answer"], a["status"], a["tier"] or "", a["est_tier"],
                                     a["confidence"], a["source"], a["added"]])
                    rows += 1
        self.status.configure(text=f"Exported {rows} answers to {path}")
