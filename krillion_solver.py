#!/usr/bin/env python3
"""Krillion solver: asks a local Ollama model for rare-but-valid answers
and remembers everything it generates in a JSON lexicon.

Krillion (krillion.io) gives open-ended prompts like "Name a pasta dish".
Accepted answers score by rarity: Plankton 10, Schooler 30, Rare 60,
Deep Cut 85, One in a Krillion 100. A wrong answer scores nothing, so the
solver ranks answers by expected points (tier points x confidence).

Standard library only. Usage: python krillion_solver.py --help
"""

import argparse
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import local_knowledge
import prompt_rules

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LEXICON = os.path.join(HERE, "lexicon.json")
DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

TIERS = {"plankton": 10, "schooler": 30, "rare": 60, "deep_cut": 85, "krillion": 100}
TIER_ALIASES = {
    "p": "plankton", "plankton": "plankton",
    "s": "schooler", "schooler": "schooler",
    "r": "rare", "rare": "rare",
    "d": "deep_cut", "deep": "deep_cut", "deepcut": "deep_cut", "deep_cut": "deep_cut",
    "k": "krillion", "krillion": "krillion", "oiak": "krillion",
}


# --- normalisation -----------------------------------------------------------

def _fold(text):
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def prompt_key(prompt):
    """'Name a Pasta Dish!' -> 'pasta dish'."""
    key = _fold(prompt)
    key = re.sub(r"^(name|list|give|think of)( me)? (a |an |any |one |some )?", "", key)
    return key or _fold(prompt)


_OCR_LOOKALIKES = str.maketrans({"i": "l", "1": "l", "|": "l", "!": "l", "0": "o"})


def ocr_key(prompt):
    """prompt_key with the characters OCR confuses made identical, and spaces dropped.

    Two prompts with the same ocr_key differ only by classic misreads (I/l/1, 0/O) or
    spacing. Anything else counts as a different prompt: similarity scores can't tell a
    misread from "containing micro" vs "containing itch", and a wrong match hands you
    answers for the wrong prompt.
    """
    return prompt_key(prompt).translate(_OCR_LOOKALIKES).replace(" ", "")


def answer_key(answer):
    key = _fold(answer)
    return re.sub(r"^(the|a|an) ", "", key)


def parse_tier(text):
    tier = TIER_ALIASES.get(_fold(text).replace(" ", "_"))
    if tier is None:
        tier = TIER_ALIASES.get(_fold(text).replace(" ", ""))
    return tier


# --- lexicon -----------------------------------------------------------------

class Lexicon:
    """Prompt -> answers store, persisted as JSON.

    Each answer has an LLM estimate (est_tier, confidence) plus what you saw
    in the game (status: unverified / accepted / rejected, and tier).
    """

    def __init__(self, path):
        self.path = path
        self.data = {"version": 1, "prompts": {}}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)

    @property
    def prompts(self):
        return self.data["prompts"]

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def find(self, prompt):
        """Return the stored key for a prompt (ignoring case and punctuation).

        Matching is exact on purpose: near-identical prompts like
        "a type of hat" / "a type of bat" must not share answers.
        """
        key = prompt_key(prompt)
        return key if key in self.prompts else None

    def find_ocr(self, text):
        """Stored prompt text that OCR text is a misreading of, or None. See ocr_key."""
        key = self.find(text)
        if key is not None:
            return self.prompts[key]["prompt"]
        wanted = ocr_key(text)
        for entry in self.prompts.values():
            if ocr_key(entry["prompt"]) == wanted:
                return entry["prompt"]
        return None

    def entry(self, prompt):
        key = self.find(prompt) or prompt_key(prompt)
        return self.prompts.setdefault(key, {"prompt": prompt.strip(), "answers": {}})

    def add(self, prompt, answer, est_tier, confidence, source):
        """Add a generated answer; returns True if it was new."""
        answers = self.entry(prompt)["answers"]
        key = answer_key(answer)
        if not key or key in answers:
            return False
        answers[key] = {
            "answer": answer.strip(),
            "est_tier": est_tier if est_tier in TIERS else "schooler",
            "confidence": max(0.0, min(1.0, float(confidence))),
            "status": "unverified",
            "tier": None,
            "source": source,
            "added": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        return True

    def mark(self, prompt, answer, tier=None, rejected=False):
        """Record what the game said about an answer (adds it if unknown)."""
        answers = self.entry(prompt)["answers"]
        key = answer_key(answer)
        if key not in answers:
            self.add(prompt, answer, tier or "schooler", 1.0, "manual")
        item = answers[key]
        item["status"] = "rejected" if rejected else "accepted"
        item["tier"] = None if rejected else tier
        return item

    # Editing, for the answer-history window.

    def reset(self, prompt, answer):
        """Forget the game's verdict on an answer (back to an unverified guess)."""
        item = self.entry(prompt)["answers"].get(answer_key(answer))
        if item:
            item["status"], item["tier"] = "unverified", None

    def rename(self, prompt, answer, new_text):
        """Correct an answer's spelling, keeping its verdict. Returns False if new_text is taken."""
        answers = self.entry(prompt)["answers"]
        old, new = answer_key(answer), answer_key(new_text)
        if old not in answers or not new or (new != old and new in answers):
            return False
        item = answers.pop(old)
        item["answer"] = new_text.strip()
        answers[new] = item
        return True

    def delete(self, prompt, answer=None):
        """Delete one answer, or the whole prompt when answer is None."""
        key = self.find(prompt)
        if key is None:
            return
        if answer is None:
            del self.prompts[key]
        else:
            self.prompts[key]["answers"].pop(answer_key(answer), None)

    def ranked(self, prompt, include_rejected=False):
        """Answers best first, as copies with "violation": the prompt rule an answer breaks, or None.

        Rejected answers and rule breakers are left out unless include_rejected. An answer
        the game accepted is never flagged: the game knows its rules better than our parser.
        """
        key = self.find(prompt)
        if key is None:
            return []
        entry = self.prompts[key]
        rules = prompt_rules.parse(entry["prompt"])
        found = local_knowledge.classify(entry["prompt"], rules)
        checkable = bool(found) and found[0] in local_knowledge.CHECKED_KINDS
        items = []
        for a in entry["answers"].values():
            violation = doubt = None
            if a["status"] != "accepted":
                violation = rules.violation(a["answer"]) if rules else None
                if checkable and not violation:
                    violation, doubt = local_knowledge.check(entry["prompt"], a["answer"], rules, found)
            items.append(dict(a, violation=violation, doubt=doubt))
        if not include_rejected:
            items = [a for a in items if a["status"] != "rejected" and not a["violation"]]
        return sorted(items, key=expected_points, reverse=True)

    def needs_local(self, prompt):
        key = self.find(prompt)
        return not (key and self.prompts[key].get("local_checked"))

    def add_local(self, prompt, found=None):
        """Add built-in answers (category lists, dictionary) once per prompt; returns how many.

        found: answers already computed with local_knowledge.answers (it can take half a
        second for word prompts, so the GUI computes them off the main thread).
        """
        if not self.needs_local(prompt):
            return 0
        if found is None:
            found = local_knowledge.answers(prompt)
        kind = local_knowledge.classify(prompt)
        if found or not (kind and kind[0] == "word" and not local_knowledge.have_dictionary()):
            self.entry(prompt)["local_checked"] = True  # retry word prompts once cmudict is installed
        return sum(self.add(prompt, answer, tier, conf, source) for answer, tier, conf, source in found)

    def model_count(self, prompt):
        """Usable answers that came from a model or from you, not from the built-in lists."""
        return sum(not a["source"].startswith(("local", "dictionary")) for a in self.ranked(prompt))


def expected_points(item):
    if item["status"] == "rejected" or item.get("violation"):
        return 0.0
    if item["status"] == "accepted":
        # A confirmed answer is a sure thing; unknown tier counts as schooler.
        return float(TIERS.get(item["tier"] or "schooler"))
    points = TIERS.get(item["est_tier"], 30) * item["confidence"]
    return points * 0.4 if item.get("doubt") else points


# --- ollama ------------------------------------------------------------------

# Context window. Our requests use well under 1,000 tokens; a small window keeps the
# model's memory (its KV cache) small, which matters on a 16 GB laptop.
NUM_CTX = 2048

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string"},
                    "tier": {"type": "string", "enum": list(TIERS)},
                    "confidence": {"type": "number"},
                },
                "required": ["answer", "tier", "confidence"],
            },
        }
    },
    "required": ["answers"],
}

SYSTEM_PROMPT = """You are an expert player of Krillion, a daily trivia game.
Each prompt is open-ended (e.g. "Name a pasta dish"). An answer only scores if
it is a correct, well-established member of the category; rarer answers score
more. Rarity tiers, from most to least common:
  plankton (10 pts) - the first answers almost everyone thinks of
  schooler (30 pts) - common but not the obvious top picks
  rare (60 pts) - correct answers most people would not think of
  deep_cut (85 pts) - obscure but real and verifiable
  krillion (100 pts) - extremely obscure yet still clearly correct
Rules for your answers:
- Every answer must be factually correct and a real, verifiable member of the category.
- Use the most standard, commonly-typed spelling. Keep answers short (the name only).
- Prefer rare and deep_cut answers that you are confident are correct.
- Never invent things. If unsure an answer is real, give it low confidence.
- confidence is your probability (0 to 1) that the game accepts the answer.
Reply with JSON only."""


class OllamaError(RuntimeError):
    pass


def _request(host, path, payload=None, timeout=300):
    url = host.rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise OllamaError(f"Ollama returned {e.code}: {e.read().decode(errors='replace')}") from e
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise OllamaError(f"Could not reach Ollama at {host} ({e}). Is `ollama serve` running?") from e


class GenerationCancelled(Exception):
    pass


class CancelToken:
    """Lets another thread stop a generation, even one still waiting for its first token.

    Cancelling shuts the connection's socket, which unblocks the reading thread
    and makes Ollama stop generating for this request.
    """

    def __init__(self):
        self.cancelled = False
        self._conn = None

    def attach(self, conn):
        self._conn = conn

    def cancel(self):
        self.cancelled = True
        sock = self._conn.sock if self._conn else None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def check(self):
        if self.cancelled:
            raise GenerationCancelled()


def _stream(host, path, payload, timeout=300, cancel=None):
    """POST and yield each JSON line of an Ollama streaming reply."""
    url = urllib.parse.urlsplit(host if "://" in host else "http://" + host)
    conn_cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(url.hostname, url.port, timeout=timeout)
    cancel = cancel or CancelToken()
    cancel.attach(conn)
    try:
        conn.request("POST", path, body=json.dumps(payload), headers={"Content-Type": "application/json"})
        cancel.check()  # cancelled while connecting, before there was a socket to shut
        resp = conn.getresponse()
        if resp.status != 200:
            raise OllamaError(f"Ollama returned {resp.status}: {resp.read().decode(errors='replace')}")
        for line in resp:
            cancel.check()
            if line.strip():
                chunk = json.loads(line)
                if "error" in chunk:
                    raise OllamaError(f"Ollama error: {chunk['error']}")
                yield chunk
        cancel.check()  # a shut socket can look like a normal end of stream
    except (OSError, http.client.HTTPException) as e:
        cancel.check()
        raise OllamaError(f"Could not reach Ollama at {host} ({e}). Is `ollama serve` running?") from e
    finally:
        conn.close()


def preload_model(host, model, keep_alive=None):
    """Load a model into memory now so the first prompt doesn't wait for it; returns seconds taken.

    Uses the same context size as real requests, or Ollama would load it again for them.
    """
    payload = {"model": model, "prompt": "", "stream": False, "options": {"num_ctx": NUM_CTX}}
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    started = time.time()
    _request(host, "/api/generate", payload, timeout=300)
    return time.time() - started


def start_ollama():
    """Start Ollama if it's installed but not running. Returns True if a start was attempted."""
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama")
    app = os.path.join(base, "ollama app.exe")  # tray app; it starts the server
    cli = shutil.which("ollama") or os.path.join(base, "ollama.exe")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        if os.path.isfile(app):
            subprocess.Popen([app], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        elif os.path.isfile(cli):
            subprocess.Popen([cli, "serve"], creationflags=flags)
        else:
            return False
    except OSError:
        return False
    return True


def wait_for_ollama(host, seconds=30):
    """Installed models once Ollama answers; raises OllamaError if it doesn't within seconds."""
    deadline = time.time() + seconds
    while True:
        try:
            return list_models(host)
        except OllamaError:
            if time.time() > deadline:
                raise
            time.sleep(0.5)


def unload_model(host, model):
    """Free a model's memory now instead of after Ollama's default 5-minute keep-alive."""
    _request(host, "/api/generate", {"model": model, "keep_alive": 0}, timeout=15)


def list_models(host):
    return [m["name"] for m in _request(host, "/api/tags", timeout=10).get("models", [])]


def pick_model(host, requested):
    if requested:
        return requested
    if os.environ.get("OLLAMA_MODEL"):
        return os.environ["OLLAMA_MODEL"]
    models = list_models(host)
    if not models:
        raise OllamaError("No Ollama models installed. Try: ollama pull llama3.1")
    return models[0]


_thinking_support = {}


def supports_reasoning(host, model):
    """True if the model can reason before answering (Qwen3, DeepSeek-R1, …), per Ollama."""
    if model not in _thinking_support:
        try:
            caps = _request(host, "/api/show", {"model": model}, timeout=15).get("capabilities", [])
        except OllamaError:
            return False  # don't cache; the real request will report the problem
        _thinking_support[model] = "thinking" in caps
    return _thinking_support[model]


_ANSWER_OBJ = re.compile(r"\{[^{}]*\}")


def _complete_answers(text):
    """Answer objects that have fully arrived in a (possibly partial) JSON reply."""
    start = text.find("[")
    if start < 0:
        return []
    found = []
    for m in _ANSWER_OBJ.finditer(text, start):
        try:
            obj = json.loads(m.group())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and str(obj.get("answer", "")).strip():
            found.append(obj)
    return found


def user_message(prompt, count, avoid=()):
    """The user turn sent to the model (also used for fine-tuning examples)."""
    user = f'Prompt: "{prompt}"\nGive {count} different answers, rarest confident answers first.'
    if avoid:
        user += "\nDo not repeat any of these (already known): " + "; ".join(avoid)
    return user


def export_training(lex, folder):
    """Write fine-tuning data from your feedback. Returns (examples, rejected answers).

    training_examples.jsonl: one chat example per prompt with accepted answers, in the
    exact format the app sends and expects, so a model trained on it keeps working here.
    rejected_answers.jsonl: answers the game rejected, for reference or other training.
    """
    os.makedirs(folder, exist_ok=True)
    examples = rejected = 0
    with open(os.path.join(folder, "training_examples.jsonl"), "w", encoding="utf-8") as good, \
            open(os.path.join(folder, "rejected_answers.jsonl"), "w", encoding="utf-8") as bad:
        for entry in lex.prompts.values():
            accepted = [a for a in entry["answers"].values() if a["status"] == "accepted"]
            # Rarest first, as the app asks the model to answer.
            accepted.sort(key=lambda a: TIERS.get(a["tier"] or "schooler", 30), reverse=True)
            if accepted:
                reply = {"answers": [{"answer": a["answer"], "tier": a["tier"] or "schooler", "confidence": 0.95}
                                     for a in accepted]}
                good.write(json.dumps({"messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message(entry["prompt"], len(accepted))},
                    {"role": "assistant", "content": json.dumps(reply, ensure_ascii=False)},
                ]}, ensure_ascii=False) + "\n")
                examples += 1
            for a in entry["answers"].values():
                if a["status"] == "rejected":
                    bad.write(json.dumps({"prompt": entry["prompt"], "answer": a["answer"]}, ensure_ascii=False) + "\n")
                    rejected += 1
    return examples, rejected


def generate(host, model, prompt, count, avoid, on_answer=None, cancel=None, reasoning=False,
             keep_alive=None):
    """Ask the model for answers. on_answer(a) is called for each answer as it streams in.

    Pass a CancelToken as cancel to be able to stop it; it then raises GenerationCancelled.
    reasoning turns a reasoning model's thinking step on or off (off is much faster);
    models that can't reason are left alone. keep_alive ("1m", "15m", …) is how long
    Ollama keeps the model in memory afterwards; None leaves Ollama's default (5 minutes).
    """
    user = user_message(prompt, count, avoid)
    payload = {
        "model": model,
        "stream": True,
        "format": ANSWER_SCHEMA,
        "options": {"temperature": 0.7, "num_ctx": NUM_CTX},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
    }
    if supports_reasoning(host, model):
        payload["think"] = reasoning
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    content, answers = "", []
    for chunk in _stream(host, "/api/chat", payload, cancel=cancel):
        content += chunk.get("message", {}).get("content", "")
        found = _complete_answers(content)
        for a in found[len(answers):]:
            if on_answer:
                on_answer(a)
        answers = found
    if not answers and content.strip():
        raise OllamaError(f"Model returned unusable output: {content[:300]!r}")
    return answers


def answer_confidence(a):
    conf = a.get("confidence", 0.5)
    return float(conf) if isinstance(conf, (int, float)) else 0.5


def add_generated(lex, prompt, answers, model):
    """Store model answers in the lexicon; returns how many were new."""
    added = 0
    for a in answers:
        added += lex.add(prompt, str(a["answer"]), a.get("tier"), answer_confidence(a), f"ollama:{model}")
    return added


# --- krillion.io -------------------------------------------------------------

SITE = "https://krillion.io"


class ScanError(RuntimeError):
    pass


def _fetch_site(path):
    req = urllib.request.Request(SITE + path, headers={"User-Agent": "krillion-solver/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
        raise ScanError(f"Could not read {SITE}{path} ({e})") from e


def fetch_today():
    """Today's daily dive: {'dayNumber', 'resetsAt', 'prompts': [text, ...]}."""
    data = _fetch_site("/api/today")
    return {"dayNumber": data.get("dayNumber"), "resetsAt": data.get("resetsAt"),
            "prompts": [p["text"] for p in data.get("prompts", [])]}


def fetch_unlimited(game):
    """One Unlimited game: {'game', 'lastGame', 'prompts': [text, ...]}."""
    data = _fetch_site(f"/api/unlimited/{int(game)}")
    return {"game": data.get("game"), "lastGame": data.get("lastGame"),
            "prompts": [p["text"] for p in data.get("prompts", [])]}


# --- solver ------------------------------------------------------------------

def solve(lex, prompt, args, force_generate=False):
    """Return ranked answers, generating with Ollama when the lexicon is short.

    Built-in answers (category lists, dictionary) are added first; they don't count
    toward min_cached, so the model still runs, but they're there if Ollama isn't.
    """
    if lex.add_local(prompt):
        lex.save()
    usable = lex.ranked(prompt)
    if force_generate or lex.model_count(prompt) < args.min_cached:
        model = pick_model(args.host, args.model)
        known = [a["answer"] for a in lex.ranked(prompt, include_rejected=True)]
        print(f"  asking {model}...", file=sys.stderr)
        added = 0

        def got(a):
            nonlocal added
            if add_generated(lex, prompt, [a], model):
                added += 1
                broken = rules.violation(a["answer"])
                note = f"  ✗ {broken}" if broken else ""
                print(f"  + {a['answer']}  ({a.get('tier', '?')}, {answer_confidence(a):.0%}){note}", flush=True)

        rules = prompt_rules.parse(prompt)
        try:
            generate(args.host, model, prompt, args.count, known, on_answer=got, reasoning=args.reasoning,
                     keep_alive=args.keep_alive)
        finally:
            lex.save()
        print(f"  {added} new answer(s) saved to lexicon", file=sys.stderr)
        usable = lex.ranked(prompt)
    return usable


def show_answers(items, limit):
    if not items:
        print("  (no answers)")
        return
    for i, a in enumerate(items[:limit], 1):
        if a["status"] == "accepted":
            tag = f"ACCEPTED {a['tier'] or '?'}"
        elif a["status"] == "rejected":
            tag = "REJECTED"
        elif a.get("violation"):
            tag = f"BREAKS RULE: {a['violation']}"
        else:
            tag = f"guess {a['est_tier']} @{a['confidence']:.0%}"
            if a["source"] in ("local", "dictionary"):
                tag += f", {a['source']}"
            if a.get("doubt"):
                tag += f", {a['doubt']}"
        print(f"  {i:>2}. {a['answer']:<32} {expected_points(a):5.1f} pts  [{tag}]")


def apply_feedback(lex, prompt, items, line):
    """Parse '1 rare', '2 x', or 'cacio e pepe deep' and record it."""
    parts = line.strip().split()
    if not parts:
        return
    verdict = parts[-1].lower()
    rejected = verdict in ("x", "no", "rejected", "wrong")
    tier = None if rejected else parse_tier(verdict)
    if not rejected and tier is None:
        print("  feedback: end with a tier (p/s/r/d/k) or x for rejected")
        return
    target = " ".join(parts[:-1])
    if target.isdigit() and 1 <= int(target) <= len(items):
        target = items[int(target) - 1]["answer"]
    if not target:
        print("  feedback: say which answer, e.g. '1 rare' or 'orecchiette d'")
        return
    lex.mark(prompt, target, tier, rejected)
    lex.save()
    print(f"  recorded: {target} -> {'rejected' if rejected else tier}")


# --- commands ----------------------------------------------------------------

def cmd_solve(lex, args):
    prompt = " ".join(args.prompt)
    try:
        items = solve(lex, prompt, args, force_generate=args.more)
    except OllamaError as e:
        print(f"  {e}\n  Showing built-in and saved answers only.", file=sys.stderr)
        items = lex.ranked(prompt)
    print(f"\n{prompt}")
    show_answers(items, args.limit)


def cmd_play(lex, args):
    print("Krillion solver - type a prompt, Enter on empty line to quit.")
    print("After answers show, give feedback like '1 rare', '3 x', 'orecchiette deep', or '+' for more.\n")
    while True:
        try:
            prompt = input("prompt> ").strip()
        except EOFError:
            break
        if not prompt:
            break
        try:
            items = solve(lex, prompt, args)
        except OllamaError as e:
            print(f"  error: {e} (showing built-in and saved answers)")
            items = lex.ranked(prompt)
        show_answers(items, args.limit)
        while True:
            try:
                line = input("feedback> ").strip()
            except EOFError:
                return
            if not line:
                break
            if line == "+":
                try:
                    items = solve(lex, prompt, args, force_generate=True)
                except OllamaError as e:
                    print(f"  error: {e}")
                show_answers(items, args.limit)
                continue
            apply_feedback(lex, prompt, items, line)
            items = lex.ranked(prompt)
        print()


def cmd_mark(lex, args):
    rejected = args.rejected
    tier = None if rejected else parse_tier(args.tier or "schooler")
    if not rejected and tier is None:
        sys.exit(f"unknown tier {args.tier!r}; use one of {', '.join(TIERS)}")
    lex.mark(args.prompt, args.answer, tier, rejected)
    lex.save()
    print(f"recorded: {args.answer} -> {'rejected' if rejected else tier}")


def cmd_show(lex, args):
    prompt = " ".join(args.prompt)
    key = lex.find(prompt)
    if key is None:
        sys.exit(f"'{prompt}' is not in the lexicon")
    print(lex.prompts[key]["prompt"])
    show_answers(lex.ranked(prompt, include_rejected=True), 10**6)


def cmd_list(lex, args):
    if not lex.prompts:
        print("lexicon is empty")
    for key, entry in sorted(lex.prompts.items()):
        answers = entry["answers"].values()
        confirmed = sum(a["status"] == "accepted" for a in answers)
        print(f"{entry['prompt']:<50} {len(entry['answers']):>3} answers, {confirmed} confirmed")


def cmd_prefetch(lex, args):
    with open(args.file, encoding="utf-8") as f:
        prompts = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    _prefetch(lex, args, prompts)


def cmd_scan(lex, args):
    if args.unlimited is None:
        day = fetch_today()
        print(f"Daily dive #{day['dayNumber']}")
        prompts = day["prompts"]
    else:
        game = fetch_unlimited(args.unlimited)
        print(f"Unlimited game {game['game']} (of {game['lastGame']})")
        prompts = game["prompts"]
    _prefetch(lex, args, prompts)
    for prompt in prompts:
        print(f"\n{prompt}")
        show_answers(lex.ranked(prompt), args.limit)


def _prefetch(lex, args, prompts):
    for prompt in prompts:
        print(prompt)
        try:
            solve(lex, prompt, args)
        except OllamaError as e:
            print(f"  error: {e}")


def cmd_models(lex, args):
    for name in list_models(args.host):
        print(name)


def cmd_export_training(lex, args):
    examples, rejected = export_training(lex, args.folder)
    print(f"Wrote {examples} training example(s) and {rejected} rejected answer(s) to {os.path.abspath(args.folder)}")
    print("Next: upload training_examples.jsonl to training/KrillionSolver_Colab.ipynb (see training/README.md)")


def build_parser():
    p = argparse.ArgumentParser(description="Krillion solver backed by Ollama and a saved lexicon.")
    p.add_argument("--lexicon", default=DEFAULT_LEXICON, help="lexicon JSON path (default: next to this script)")
    p.add_argument("--host", default=DEFAULT_HOST, help="Ollama URL (default: $OLLAMA_HOST or localhost:11434)")
    p.add_argument("--model", help="Ollama model (default: $OLLAMA_MODEL or first installed)")
    p.add_argument("--count", type=int, default=10, help="answers to request per generation (default 10)")
    p.add_argument("--min-cached", type=int, default=3,
                   help="generate only when fewer usable answers are cached (default 3)")
    p.add_argument("--limit", type=int, default=8, help="answers to display (default 8)")
    p.add_argument("--reasoning", action=argparse.BooleanOptionalAction, default=False,
                   help="let reasoning models like Qwen3 think before answering (slower; default off)")
    p.add_argument("--keep-alive", metavar="TIME",
                   help="how long Ollama keeps the model loaded afterwards, e.g. 1m, 15m, 0 (default: Ollama's 5m)")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("solve", help="answer one prompt")
    s.add_argument("prompt", nargs="+")
    s.add_argument("--more", action="store_true", help="always ask Ollama for more answers")
    s.set_defaults(func=cmd_solve)

    s = sub.add_parser("play", help="interactive loop with feedback (default)")
    s.set_defaults(func=cmd_play)

    s = sub.add_parser("mark", help="record the game's verdict on an answer")
    s.add_argument("prompt")
    s.add_argument("answer")
    s.add_argument("tier", nargs="?", help="plankton/schooler/rare/deep_cut/krillion (or p/s/r/d/k)")
    s.add_argument("-x", "--rejected", action="store_true", help="the game rejected this answer")
    s.set_defaults(func=cmd_mark)

    s = sub.add_parser("show", help="show every stored answer for a prompt")
    s.add_argument("prompt", nargs="+")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("list", help="list prompts in the lexicon")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("prefetch", help="pre-generate answers for prompts in a text file (one per line)")
    s.add_argument("file")
    s.set_defaults(func=cmd_prefetch)

    s = sub.add_parser("scan", help="read today's prompts from krillion.io and answer them all")
    s.add_argument("--unlimited", type=int, metavar="N", help="scan Unlimited game N instead")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("models", help="list installed Ollama models")
    s.set_defaults(func=cmd_models)

    s = sub.add_parser("export-training", help="write fine-tuning data from your accepted/rejected answers")
    s.add_argument("folder", nargs="?", default=os.path.join(HERE, "training", "data"))
    s.set_defaults(func=cmd_export_training)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd is None:
        args.func = cmd_play
    lex = Lexicon(args.lexicon)
    try:
        args.func(lex, args)
    except (OllamaError, ScanError) as e:
        sys.exit(f"error: {e}")
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
