"""Prompt detection from page text, and the WebSocket client against a local server."""

import base64
import hashlib
import os
import socket
import struct
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import browser_monitor as bm

GAME_TEXT = """KRILLION
DEPTH
0m
the trench floor — a perfect dive ends here ──
PROMPT 1 OF 7
Name a noodle dish
▼ rarer answers sink deeper ▼
SCORE"""


class Detection(unittest.TestCase):
    def test_page_mode(self):
        self.assertEqual(bm.page_mode("https://krillion.io/"), ("daily", None))
        self.assertEqual(bm.page_mode("https://krillion.io/unlimited/classic?game=12"), ("unlimited", 12))
        self.assertEqual(bm.page_mode("https://krillion.io/party/ZBZZ"), ("party", None))
        self.assertEqual(bm.page_mode("https://example.com/"), ("other", None))

    def test_prompt_number_uses_site_list(self):
        prompts = ["Name a noodle dish", "Name a species of great ape"]
        text = GAME_TEXT.replace("PROMPT 1", "PROMPT 2").replace("Name a noodle dish", "Name a species of great ape")
        prompt, method = bm.detect_prompt("https://krillion.io/unlimited/classic?game=1", text, prompts)
        self.assertEqual(prompt, "Name a species of great ape")
        self.assertIn("prompt 2 of 7", method)

    def test_page_text_fallback(self):
        prompt, method = bm.detect_prompt("https://krillion.io/packs/foo", GAME_TEXT)
        self.assertEqual((prompt, method), ("Name a noodle dish", "page text"))

    def test_no_prompt_on_home_page(self):
        text = "KRILLION\nTHE DAILY DIVE\n7 prompts · 25 seconds each · rarer answers sink deeper\n▼ BEGIN DESCENT ▼"
        self.assertIsNone(bm.detect_prompt("https://krillion.io/", text)[0])

    def test_party_is_never_read(self):
        prompt, method = bm.detect_prompt("https://krillion.io/party/ZBZZ", "Name a fruit\nV any answer passes V")
        self.assertIsNone(prompt)
        self.assertEqual(method, "Puffer Party isn't monitored")


class WebSocketClient(unittest.TestCase):
    """A tiny echo server: handshake, then echo each text frame back (unmasked, as servers do)."""

    def _server(self, listener, messages):
        conn, _ = listener.accept()
        with conn:
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(4096)
            key = [l.split(b": ", 1)[1] for l in data.split(b"\r\n") if l.lower().startswith(b"sec-websocket-key")][0]
            accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                         b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
            for _ in range(messages):
                head = conn.recv(2)
                n = head[1] & 0x7F
                if n == 126:
                    n = struct.unpack(">H", conn.recv(2))[0]
                elif n == 127:
                    n = struct.unpack(">Q", conn.recv(8))[0]
                mask = conn.recv(4)
                payload = b""
                while len(payload) < n:
                    payload += conn.recv(n - len(payload))
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
                if len(payload) < 126:
                    frame = bytes([0x81, len(payload)])
                elif len(payload) < 65536:
                    frame = bytes([0x81, 126]) + struct.pack(">H", len(payload))
                else:
                    frame = bytes([0x81, 127]) + struct.pack(">Q", len(payload))
                conn.sendall(frame + payload)

    def test_round_trip_small_medium_large(self):
        messages = ["hi", "x" * 300, "é" * 40000]  # 7-bit, 16-bit and 64-bit length frames
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        server = threading.Thread(target=self._server, args=(listener, len(messages)), daemon=True)
        server.start()
        ws = bm.WebSocket(f"ws://127.0.0.1:{port}/devtools/page/1")
        for text in messages:
            ws.send(text)
            self.assertEqual(ws.recv(), text)
        ws.sock.close()
        listener.close()


if __name__ == "__main__":
    unittest.main()
