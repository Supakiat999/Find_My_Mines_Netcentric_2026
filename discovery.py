"""LAN auto-discovery: the server shouts "I am here", clients listen.

The server sends one small UDP datagram every DISCOVERY_INTERVAL_S seconds:

    {"magic": "find-my-mines-v1", "id": "9f3a1c02", "port": 55550,
     "name": "Book-Mac", "rooms": 3, "open": 1}

`id` is random per server run.  A server on several interfaces (or the same
computer as the client) is heard more than once; the id lets the client count
it as one.

The client never trusts an address inside the packet.  It uses the address
the datagram actually came from, which is by definition one that reaches the
server.  Rooms are not listed here: the lobby already sends them over TCP once
you are connected, so a beacon only says "a server is here, with N rooms, M of
them joinable".

Standard library only.  Networks that isolate clients (some campus Wi-Fi) drop
broadcast; the manual address in config.py / on the command line still works.
"""

import json
import secrets
import socket
import threading
import time

import config


_ips_cache = (0.0, [])


def local_ips():
    """Non-loopback IPv4 addresses of this machine (best effort).

    Cached for a few seconds: the beacon asks twice a second and resolving the
    host name can be slow on some machines.  A few seconds is still quick
    enough to follow a network change.
    """
    global _ips_cache
    stamp, cached = _ips_cache
    if time.time() - stamp < 5:
        return list(cached)
    found = _find_local_ips()
    _ips_cache = (time.time(), found)
    return list(found)


def _find_local_ips():
    found = []

    def add(ip):
        if ip and not ip.startswith("127.") and ip not in found:
            found.append(ip)

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))      # picks a route, sends nothing
        add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    return found


def make_beacon(sid, port, name, rooms, open_rooms):
    return json.dumps({"magic": config.DISCOVERY_MAGIC, "id": sid, "port": port,
                       "name": name, "rooms": rooms,
                       "open": open_rooms}).encode("utf-8")


def parse_beacon(data, source_ip):
    """Return a server dict for one of our datagrams, else None."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict) or obj.get("magic") != config.DISCOVERY_MAGIC:
        return None
    sid, port, name = obj.get("id"), obj.get("port"), obj.get("name", "")
    if not isinstance(sid, str) or not 0 < len(sid) <= 16 or not sid.isalnum():
        return None
    if type(port) is not int or not 1 <= port <= 65535:
        return None
    if not isinstance(name, str) or not name.isprintable():
        return None
    counts = []
    for key in ("rooms", "open"):
        value = obj.get(key, 0)
        counts.append(value if type(value) is int and 0 <= value <= 9999 else 0)
    return {"id": sid, "host": source_ip, "port": port, "name": name[:24],
            "rooms": counts[0], "open": counts[1]}


# ----------------------------------------------------------------------
# server side
# ----------------------------------------------------------------------
def send_beacon(payload):
    """Send one beacon out of every interface, plus loopback."""
    port = config.DISCOVERY_PORT
    for ip in [None] + local_ips():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            if ip is None:
                s.sendto(payload, ("127.0.0.1", port))   # same-computer play
                continue
            s.bind((ip, 0))      # send *from* this interface so it routes out
            s.sendto(payload, ("255.255.255.255", port))
            a, b, c, _ = ip.split(".")
            s.sendto(payload, ("%s.%s.%s.255" % (a, b, c), port))
        except OSError:
            pass
        finally:
            s.close()


class Beacon:
    """Background thread that keeps announcing the server."""

    def __init__(self, info_fn, port):
        """info_fn() -> (rooms, open_rooms); called from the beacon thread,
        so it must only read a ready-made snapshot."""
        self.info_fn = info_fn
        self.port = port
        self.name = socket.gethostname()[:24]
        self.sid = secrets.token_hex(4)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                rooms, open_rooms = self.info_fn()
                send_beacon(make_beacon(self.sid, self.port, self.name,
                                        rooms, open_rooms))
            except Exception:
                pass
            self._stop.wait(config.DISCOVERY_INTERVAL_S)


# ----------------------------------------------------------------------
# client side
# ----------------------------------------------------------------------
def scan(timeout=None, settle=None):
    """Listen for servers.  Returns a list of server dicts, best first.

    Stops early: once a first beacon has arrived, waits `settle` seconds more
    (a bit longer than one beacon interval, so every other server gets heard),
    then returns.  With nothing heard it waits the full `timeout`.
    """
    timeout = config.DISCOVERY_LISTEN_S if timeout is None else timeout
    settle = config.DISCOVERY_INTERVAL_S * 1.2 if settle is None else settle
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        s.bind(("", config.DISCOVERY_PORT))
        s.settimeout(0.2)
        start = time.time()
        first = None
        while time.time() - start < timeout:
            if first is not None and time.time() - first > settle:
                break
            try:
                data, addr = s.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            server = parse_beacon(data, addr[0])
            if server:
                found.setdefault(server["id"], server)   # first address heard
                if first is None:
                    first = time.time()
    except OSError:
        pass
    finally:
        s.close()
    # servers with open rooms first, then by name, so the list is stable
    return sorted(found.values(),
                  key=lambda v: (-v["open"], v["name"], v["host"], v["port"]))
