"""Shared plumbing for the test suites.

Importing this module points Python at the project, switches pygame to its
headless drivers (no window, no sound card needed) and turns the saved
leaderboard off so tests never touch a real stats.json.
"""

import os
import socket
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import config                                    # noqa: E402

config.STATS_FILE = None
config.PREFS_FILE = None
config.DATABASE_URL = None

import protocol                                  # noqa: E402


def wait(pred, what, timeout=8.0, pump=()):
    """Poll until pred() is truthy.  `pump` are ClientUIs to keep draining."""
    end = time.time() + timeout
    while time.time() < end:
        value = pred()
        if value:
            return value
        for ui in pump:
            ui.pump_network()
        time.sleep(0.02)
    raise AssertionError("timed out waiting for: " + what)


def ok(number, text):
    print("%s OK  %s" % (number, text), flush=True)


class ServerRunner:
    """A real Server on a real port, pumped from its own thread.

    Server methods are not thread-safe - in the real program only the main
    loop touches them.  Tests that need to call one (set_mode, reset_all...)
    hand it to call(), which runs it on the pump thread and waits.
    """

    def __init__(self, port):
        config.SERVER_PORT = port
        import server as server_mod
        self.module = server_mod
        self.srv = server_mod.Server()
        self.srv.start_network()
        self._jobs = []
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self):
        while self.srv.running:
            self.srv.handle_events()
            self.srv.update_clock()
            while self._jobs:
                job, done, box = self._jobs.pop(0)
                try:
                    box.append(job())
                except Exception as exc:          # surface it in the test
                    box.append(exc)
                done.set()
            time.sleep(0.01)

    def call(self, fn, *args, **kwargs):
        done, box = threading.Event(), []
        self._jobs.append((lambda: fn(*args, **kwargs), done, box))
        if not done.wait(10):
            raise AssertionError("server thread did not run the call")
        if box and isinstance(box[0], Exception):
            raise box[0]
        return box[0] if box else None

    def stop(self):
        self.srv.running = False
        time.sleep(0.15)
        self.srv.shutdown()

    @property
    def game(self):
        return self.srv.game


class Wire:
    """A bare socket client - speaks the protocol with no window."""

    def __init__(self, port, nickname=None, token=None, vs=None):
        self.sock = socket.create_connection(("127.0.0.1", port), 5)
        self.latest = {}
        self.history = []
        self.lock = threading.Lock()
        self.closed = False
        threading.Thread(target=self._read, daemon=True).start()
        if nickname is not None:
            self.join(nickname, token, vs)

    def _read(self):
        for msg in protocol.MessageReader(self.sock).messages():
            with self.lock:
                self.latest[msg["type"]] = msg
                self.history.append(msg)
        self.closed = True

    def join(self, nickname, token=None, vs=None):
        extra = {"token": token} if token else {}
        if vs:
            extra["vs"] = vs
        protocol.send(self.sock, protocol.JOIN, nickname=nickname, **extra)

    def send(self, msg_type, **payload):
        protocol.send(self.sock, msg_type, **payload)

    def get(self, msg_type):
        with self.lock:
            return self.latest.get(msg_type)

    def forget(self, msg_type):
        with self.lock:
            self.latest.pop(msg_type, None)

    def all(self, msg_type):
        with self.lock:
            return [m for m in self.history if m["type"] == msg_type]

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def make_ui():
    import client as client_mod
    return client_mod.ClientUI()
