# Krillion solver

Suggests rare but valid answers for [Krillion](https://krillion.io) prompts using a local
[Ollama](https://ollama.com) model, built-in word and category lists, and everything
you've taught it. Every answer is saved in `lexicon.json`, so a prompt you've seen before
comes back instantly. That matters because you only get about 25 seconds per prompt.

It never types or submits anything in the game. You choose an answer and paste it yourself.

## Setup

1. Install [Ollama](https://ollama.com) and pull a model:

   ```bash
   ollama pull llama3.2:3b
   ```

2. Optional, for English-word prompts (instant dictionary answers, real-word and syllable
   checks):

   ```bash
   py -m pip install cmudict
   ```

Everything else uses Python's standard library plus Windows' built-in text recognition,
spell checker and your installed Chrome.

## Starting

Double-click **Krillion Solver.pyw**: no terminal window, and errors show in the status bar.
Or run `python krillion_gui.py`. The app:

- **Ollama:** starts Ollama if it isn't running, then loads your model so the first prompt
  doesn't wait. **Load model at start** controls this.
- **Today's dive:** reads today's 7 prompts from krillion.io and prepares answers for all of
  them in the background. It does this again when the dive resets.

## Getting prompts in

| How | When to use it |
|---|---|
| **Scan today's dive** / **Scan game** / **Scan all games** | The daily dive and Unlimited games, read from the site's API ahead of time. Unlimited games after the first need Krillion's $5 unlock to play. |
| **Browser → Start** | Opens the game in Chrome and detects each prompt as it appears (about every 0.6 s). With **Automatic answers** on, it opens that prompt and copies the best answer. |
| **Read screen** (Ctrl+Alt+K) | Reads the prompt off your screen, for anything else, such as themed packs. |
| **Add** | Type a prompt yourself. |

A prompt you add, read or detect jumps ahead of background work. A background prompt that's
generating is paused and resumes afterwards.

### Browser monitor

**Start** opens krillion.io in Chrome with its own profile, so it doesn't touch your normal
Chrome. That profile is kept in `%LOCALAPPDATA%\KrillionSolver\chrome-profile`, so a sign-in
there survives restarts. Play in that window. **Stop** closes it, **Restart** reopens it,
and closing the app closes it too.

On the daily dive and Unlimited games, the page's "PROMPT 3 OF 7" and the site's prompt
list give the exact prompt. Elsewhere it reads the prompt from the page text.
**Diagnostics…** shows the URL, the page text it read, the prompt it picked, how it picked
it, and any error.

Puffer Party pages are never read, because Party is live multiplayer against other people.
There's also no feature to remove answers that other players have already given.

### Reading the screen

1. Click **Set read area…** and drag a box around where the game shows the prompt. Leave
   room for prompts that wrap onto 2–3 lines.
2. When a prompt appears, press **Ctrl+Alt+K** from any window. The badge at the top right
   shows whether the hotkey is working. If the app is open twice, the second copy shows a
   red badge and takes over when the first one closes.

It ignores the text around the prompt, such as "PROMPT 1 OF 7", "▼ rarer answers sink
deeper ▼" and "▼ any answer passes the puffer ▼". If another line keeps getting stuck onto
the prompt, add part of it to `settings.json`:

```json
{ "ignore_phrases": ["only rare answers count"] }
```

A screen-read prompt only reuses saved answers if it's identical apart from typical
misreads (I/l/1, 0/O, spacing).

## Answers

Click a prompt to see its answers, best first. The best one is copied to your clipboard;
double-click another to copy it instead. Each answer is labelled with where it came from:

- **Model guesses:** coloured by estimated tier, with the model's confidence.
- **Built-in:** from hand-written category lists, available instantly and when Ollama
  isn't running. The lists are countries (by region and landlocked), animals (including
  ones with stripes, spots, shells, horns, wings or scales), fruits, vegetables, sports,
  instruments, jobs, vehicles, flowers, trees, planets, gemstones, board games, dinosaurs,
  body parts, interjections, filler words, sitcoms, foods, and *How I Met Your Mother* and
  *SpongeBob* characters. They're only used when the prompt is just the category plus
  rules the app understands. "An animal that can run faster than a human" is left to the
  model.
- **Dictionary:** real English words for word prompts like 'containing "itch"',
  "ending in -ology", "a 5-letter word", "a 3-syllable word starting with B" or 'a compound
  word containing "sun"'. Windows' spell checker filters out the surnames and place names
  that fill much of the dictionary.

Built-in answers don't stop the model from running. They're there first and as a fallback.

**Prompt rules.** When a prompt states a spelling rule or an exclusion ("starts with B",
'ends in "stan"', "contains J, K, or V", "(apple doesn't count)", "other than Earth"), the
rules show under the prompt. Answers that break one are greyed at the bottom with the
reason and never copied. For word prompts, answers that aren't real words are marked
"not in the dictionary" and ranked lower, and wrong syllable counts are flagged. If a rule
is ambiguous ("starts with a vowel"), it's left to the model.

**Passes.** Each prompt gets several generations (**Passes**, default 3). Each one is told
the answers already found, so it adds new ones. Extra passes apply to short lists like a
dive or a single game, and to whatever prompt you're looking at. **More answers** starts a
fresh set. If too few valid answers remain, because of broken rules or your rejections,
more are requested automatically.

**Recording what the game said.** Select an answer, or type the one you used, then click its
tier or **Rejected ✗**. Accepted answers rank by their real points; rejected ones drop to
the bottom.

Also:
- **Cancel** (Delete key) stops a prompt; **Cancel all** clears the queue.
- **Clear** empties the answers pane.
- **Compact view** gives a small always-on-top window with **‹ ›** to step through prompts.

## Model settings

- **Model** and **↻:** choosing another model unloads the old one, so two aren't in memory
  at once.
- **Keep loaded** (1 min to 1 hour, default 5): how long Ollama keeps the model in memory
  after the last answer. Requests also use a 2048-token context instead of Ollama's 4096,
  which saves memory.
- **Reasoning** (off by default): lets reasoning models like Qwen3 think first. It's much
  slower, so leave it off for the game.
- **Closing the app** stops generating and unloads the model right away.

## Answer history and fine-tuning

**Answer history…** opens an editor for everything you've saved:
- **Search and filter:** search prompts and answers, and show only prompts with feedback or
  with rejections.
- **Edit verdicts:** mark answers accepted (with a tier) or rejected, clear a verdict, fix a
  spelling, or delete an answer or a whole prompt.
- **Export:** everything to CSV, or your feedback as training data.

To teach the model your accepted answers, export training data and follow
[training/README.md](training/README.md). It uses a Google Colab notebook, since training
needs an NVIDIA GPU.

## Command line

```bash
python krillion_solver.py scan
```

```bash
python krillion_solver.py solve "Name a country starting with B"
```

```bash
python krillion_solver.py
```

| Command | What it does |
|---|---|
| `scan` / `scan --unlimited 12` | Today's prompts (or Unlimited game 12): generate and print answers. |
| `solve "…"` (`--more`) | Answers for one prompt. Shows built-in answers if Ollama is down. |
| *(no command)* | Interactive mode. After the answers, give feedback: `1 rare`, `3 x`, `orecchiette d`, `+` for more. |
| `mark "prompt" "answer" rare` / `-x` | Record the game's verdict. |
| `show "…"`, `list`, `models`, `prefetch file.txt` | Inspect the lexicon, list models, prepare a list of prompts. |
| `export-training [folder]` | Write fine-tuning data. |

Global options (before the command): `--model`, `--host`, `--lexicon`, `--count`,
`--min-cached`, `--limit`, `--reasoning`, `--keep-alive`. Tiers: `p` plankton,
`s` schooler, `r` rare, `d` deep_cut, `k` krillion.

## Ranking

Answers are sorted by expected points:

| Answer | Score |
|---|---|
| Accepted by the game | Its tier's points (10/30/60/85/100) |
| Guesses | Estimated tier × confidence |
| Not a real word | Score × 0.4 |
| Rejected, or breaks a rule | 0 |

An answer the game accepted is never flagged, because the game knows its rules better than
the app. Prompts are matched ignoring case and punctuation. The GUI and the command line
share `lexicon.json`.

## Tests

```bash
py -m unittest discover -s tests -v
```

These cover rules, screen-text cleanup, prompt matching, the built-in lists, word checks,
browser prompt detection, the DevTools WebSocket client, ranking, history edits and training
export. They don't use Ollama or the internet.

## Credits

Several features were inspired by [DannyYPan/KrillionSolver](https://github.com/DannyYPan/KrillionSolver):
prompt rules and exclusions, built-in category and dictionary answers, multi-pass generation,
browser monitoring with diagnostics, the answer history editor and fine-tuning. They were
written from scratch here; no code was copied. Word data comes from the
[CMU Pronouncing Dictionary](https://github.com/cmusphinx/cmudict) via the `cmudict` package.
