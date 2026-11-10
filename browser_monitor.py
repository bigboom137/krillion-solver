"""Watch the Krillion game in Chrome and report each new prompt.

Opens Chrome with its own profile (kept in %LOCALAPPDATA%\\KrillionSolver\\chrome-profile,
so a sign-in survives restarts) and Chrome's DevTools port on 127.0.0.1, reads the
page's visible text about every 0.6 s, and picks out the prompt:

  * on the daily dive and Unlimited games, "PROMPT 3 OF 7" plus the prompt list from
    the site's API gives the exact prompt;
  * elsewhere (themed packs, archive), the first line that reads like a prompt.

Puffer Party pages are deliberately not read: it's live multiplayer against other people.
Standard library only, including the small WebSocket client DevTools needs.
"""

import base64
import json
import os
import re
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
import urllib.request

import krillion_solver as ks
import screen_reader as sr

POLL_SECONDS = 0.6
PROFILE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "KrillionSolver", "chrome-profile")
CHROME_PATHS = [
    os.path.join(os.environ.get(var, ""), "Google", "Chrome", "Application", "chrome.exe")
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
]
_PROMPT_NUMBER = re.compile(r"\bprompt\s*(\d{1,2})\s*of\s*(\d{1,2})\b", re.I)
_PROMPT_LINE = re.compile(r"^(?:name|list|give|think of|what|which|who)\b", re.I)


class MonitorError(RuntimeError):
    pass


def find_chrome():
    return next((p for p in CHROME_PATHS if os.path.isfile(p)), None)


# --- prompt detection (pure functions, easy to test) -------------------------------

def page_mode(url):
    """('daily', None), ('unlimited', game number), ('party', None) or ('other', None)."""
    parts = urllib.parse.urlsplit(url)
    if "krillion.io" not in parts.netloc:
        return "other", None
    path = parts.path.rstrip("/")
    if path.startswith("/party"):
        return "party", None
    if path == "":
        return "daily", None
    if path.startswith("/unlimited"):
        game = urllib.parse.parse_qs(parts.query).get("game", ["1"])[0]
        return "unlimited", int(game) if game.isdigit() else 1
    return "other", None


def detect_prompt(url, text, prompt_list=None):
    """Pick the current prompt out of the page text. Returns (prompt or None, how it was found).

    prompt_list: the API's prompts for this page (daily or Unlimited game), so "PROMPT 3
    OF 7" maps to the exact text.
    """
    mode, _ = page_mode(url)
    if mode == "party":
        return None, "Puffer Party isn't monitored"
    lines = [re.sub(r"\s+", " ", l).strip() for l in (text or "").splitlines()]
    lines = [l for l in lines if l]
    number = _PROMPT_NUMBER.search(text or "")
    if number and prompt_list:
        n = int(number.group(1))
        if 1 <= n <= len(prompt_list):
            return prompt_list[n - 1], f"prompt {n} of {number.group(2)} + site list"
    # Prefer a prompt-like line right after "PROMPT n OF m"; otherwise the first prompt-like line.
    start = 0
    if number:
        for i, line in enumerate(lines):
            if _PROMPT_NUMBER.search(line):
                start = i + 1
                break
    for i in list(range(start, len(lines))) + list(range(0, start)):
        if _PROMPT_LINE.match(lines[i]) and 8 <= len(lines[i]) <= 240:
            prompt = sr.extract_prompt(lines[i:i + sr.MAX_PROMPT_LINES])
            if prompt:
                return prompt, "page text"
    return None, "no prompt on the page"


# --- minimal WebSocket client (RFC 6455, text frames only) --------------------------

class WebSocket:
    def __init__(self, url, timeout=10):
        parts = urllib.parse.urlsplit(url)
        self.sock = socket.create_connection((parts.hostname, parts.port or 80), timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {parts.hostname}:{parts.port}\r\n"
                           f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                           f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
        self.buffer = b""
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("DevTools closed the connection during the handshake")
            self.buffer += chunk
        head, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"DevTools refused the WebSocket: {head[:200]!r}")

    def _read(self, n):
        while len(self.buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("DevTools connection closed")
            self.buffer += chunk
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def _send_frame(self, opcode, payload):
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)  # clients must mask every frame
        masked = (int.from_bytes(payload, "big") ^ int.from_bytes((mask * (n // 4 + 1))[:n], "big")).to_bytes(n, "big") if n else b""
        self.sock.sendall(header + mask + masked)

    def send(self, text):
        self._send_frame(0x1, text.encode("utf-8"))

    def recv(self):
        message = b""
        while True:
            first, second = self._read(2)
            opcode, n = first & 0x0F, second & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            if second & 0x80:  # servers don't mask, but handle it anyway
                mask = self._read(4)
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(self._read(n)))
            else:
                payload = self._read(n)
            if opcode == 0x8:
                raise ConnectionError("DevTools closed the connection")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode in (0x1, 0x0):
                message += payload
                if first & 0x80:
                    return message.decode("utf-8", "replace")

    def close(self):
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        self.sock.close()


class DevTools:
    """One page's DevTools session: call("Runtime.evaluate", {...}) -> result."""

    def __init__(self, ws_url):
        self.ws = WebSocket(ws_url)
        self.next_id = 0

    def call(self, method, params=None):
        self.next_id += 1
        self.ws.send(json.dumps({"id": self.next_id, "method": method, "params": params or {}}))
        while True:
            reply = json.loads(self.ws.recv())
            if reply.get("id") == self.next_id:
                if "error" in reply:
                    raise MonitorError(reply["error"].get("message", str(reply["error"])))
                return reply.get("result", {})

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return result.get("result", {}).get("value")

    def close(self):
        self.ws.close()


# --- the monitor --------------------------------------------------------------------

class Monitor:
    """Runs Chrome and a polling thread. on_event(kind, data) is called from that thread:
    ("status", text), ("prompt", text), ("diagnostics", dict), ("stopped", reason)."""

    def __init__(self, on_event):
        self.on_event = on_event
        self.process = None
        self.port = None
        self.thread = None
        self.stopping = threading.Event()
        self.prompt_lists = {}  # ("daily", day) or ("unlimited", n) -> list of prompts

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, url="https://krillion.io/"):
        if self.running:
            return
        chrome = find_chrome()
        if not chrome:
            raise MonitorError("Chrome isn't installed in the usual places")
        os.makedirs(PROFILE_DIR, exist_ok=True)
        with socket.socket() as s:  # a free local port for DevTools
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.process = subprocess.Popen([
            chrome, f"--remote-debugging-port={self.port}", "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={PROFILE_DIR}", "--no-first-run", "--no-default-browser-check",
            "--new-window", url])
        self.stopping.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        """Close the monitored Chrome window and stop watching."""
        self.stopping.set()
        try:
            if self.port:
                version = self._json("/json/version", timeout=2)
                session = DevTools(version["webSocketDebuggerUrl"])
                try:
                    session.call("Browser.close")
                except (ConnectionError, OSError, MonitorError):
                    pass  # Chrome closing the socket is the expected reply
        except (OSError, ValueError, KeyError, MonitorError):
            pass
        if self.process and self.process.poll() is None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=3)
        self.process = self.thread = None

    def _json(self, path, timeout=5):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=timeout) as resp:
            return json.load(resp)

    def _page(self):
        """DevTools session for the game tab (the first krillion.io tab, else any page)."""
        pages = [t for t in self._json("/json/list") if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        pages.sort(key=lambda t: "krillion.io" not in t.get("url", ""))
        if not pages:
            raise MonitorError("No open tab to watch")
        return DevTools(pages[0]["webSocketDebuggerUrl"])

    def _prompt_list(self, url):
        mode, game = page_mode(url)
        key = (mode, time.strftime("%Y-%m-%d") if mode == "daily" else game)
        if mode not in ("daily", "unlimited"):
            return None
        if key not in self.prompt_lists:
            try:
                data = ks.fetch_today() if mode == "daily" else ks.fetch_unlimited(game)
                self.prompt_lists[key] = data["prompts"]
            except ks.ScanError:
                return None  # fall back to reading the prompt from the page text
        return self.prompt_lists[key]

    def _run(self):
        deadline = time.time() + 20
        while not self.stopping.is_set():  # wait for Chrome's DevTools to come up
            try:
                self._json("/json/version", timeout=1)
                break
            except OSError:
                if time.time() > deadline or (self.process and self.process.poll() is not None):
                    self.on_event("stopped", "Chrome didn't start (is another copy using the profile?)")
                    return
                time.sleep(0.3)
        self.on_event("status", "Watching Chrome")
        session, last_prompt = None, None
        while not self.stopping.is_set():
            started = time.time()
            if self.process and self.process.poll() is not None:
                self.on_event("stopped", "Chrome was closed")
                return
            info = {"url": "", "text": "", "prompt": None, "method": "", "error": None}
            try:
                if session is None:
                    session = self._page()
                page = self.json_page(session)
                info.update(url=page.get("url", ""), text=page.get("text", ""))
                info["prompt"], info["method"] = detect_prompt(info["url"], info["text"], self._prompt_list(info["url"]))
            except (OSError, ConnectionError, ValueError, MonitorError) as e:
                info["error"] = str(e) or e.__class__.__name__
                if session:
                    session.close()
                session = None  # tab closed or navigated away; find it again next time
            self.on_event("diagnostics", info)
            if info["prompt"] and info["prompt"] != last_prompt:
                last_prompt = info["prompt"]
                self.on_event("prompt", info["prompt"])
            self.stopping.wait(max(0.05, POLL_SECONDS - (time.time() - started)))
        if session:
            session.close()

    @staticmethod
    def json_page(session):
        value = session.evaluate("JSON.stringify({url: location.href, text: document.body ? document.body.innerText : ''})")
        return json.loads(value or "{}")
