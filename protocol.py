"""Newline-delimited JSON messaging over a plain TCP socket.

Every message is one JSON object on one line:  {"type": ..., ...}\n
That keeps framing trivial: read until "\n", parse what came before it.
"""

import json
import socket

# --- client -> server --------------------------------------------------
JOIN = "join"          # {nickname}
PICK = "pick"          # {row, col} - plus {layer} in the cube mode
FLAG = "flag"          # {row, col, layer} - plant or lift a marker
REMATCH = "rematch"    # {}
SET_MODE = "set_mode"  # {mode} - players can switch the game from the client
SET_CUSTOM = "set_custom"  # {settings} - board size, bombs, rules for Custom

# --- server -> client --------------------------------------------------
WELCOME = "welcome"        # {client_id, role, message}
CLIENTS = "clients"        # {count, list:[{id,name,role}]}
STATE = "state"            # full game snapshot
TICK = "tick"              # {seconds_left}
MATCH_END = "match_end"    # {winner_id, draw, players}
SERVER_RESET = "server_reset"  # {} - admin pressed Reset
ERROR = "error"            # {message}


def encode(msg_type, **payload):
    """Build one wire-ready line."""
    payload["type"] = msg_type
    return (json.dumps(payload) + "\n").encode("utf-8")


def send(sock, msg_type, **payload):
    """Send one message.  Returns False if the peer is gone."""
    try:
        sock.sendall(encode(msg_type, **payload))
        return True
    except OSError:
        return False


class MessageReader:
    """Turns a socket's byte stream back into whole JSON messages.

    TCP gives us a stream, not messages: one recv() may hold two messages
    or half of one.  This buffers the leftovers between calls.
    """

    def __init__(self, sock):
        self.sock = sock
        self._buffer = b""

    def messages(self):
        """Yield decoded messages until the peer disconnects."""
        while True:
            try:
                chunk = self.sock.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            self._buffer += chunk
            while b"\n" in self._buffer:
                line, self._buffer = self._buffer.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue  # ignore malformed input rather than dying


def local_ip():
    """Best-effort LAN IP of this machine, for display on the server UI."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packets sent; just picks a route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
