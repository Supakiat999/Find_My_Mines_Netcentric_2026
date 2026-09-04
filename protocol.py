"""Newline-delimited JSON messaging over a plain TCP socket.

Every message is one JSON object on one line:  {"type": ..., ...}\n
That keeps framing trivial: read until "\n", parse what came before it.
"""

import json
import socket

# --- client -> server --------------------------------------------------
JOIN = "join"          # {nickname}
PICK = "pick"          # {row, col}
REMATCH = "rematch"    # {}

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
    ips = local_ips()
    return ips[0] if ips else "127.0.0.1"


def local_ips():
    """All non-loopback IPv4 addresses, best effort, de-duplicated.

    local_ip() above picks the internet-route address, which can be wrong
    when a VPN is up, when Ethernet+Wi-Fi are both connected, or when a
    phone hotspot has no mobile data (no 8.8.8.8 route).  Listing every
    candidate lets the server show the full set so players can try each.
    """
    found = []

    def _add(ip):
        if ip and not ip.startswith("127.") and ip not in found:
            try:
                socket.inet_aton(ip)
            except OSError:
                return
            found.append(ip)

    # 1. The classic routing-table trick (sends no packets).
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        _add(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()
    # 2. Whatever the hostname resolves to (covers hotspot-offline cases
    # where step 1 fails or returns the wrong interface).
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET, socket.SOCK_DGRAM):
            _add(info[4][0])
    except OSError:
        pass
    return found
