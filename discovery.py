"""LAN auto-discovery for Find My Mines (UDP beacon).

The server emits a small JSON packet every DISCOVERY_INTERVAL_S seconds;
clients listen for DISCOVERY_LISTEN_S seconds and offer whatever they
heard.  A manual address (``python client.py <ip>`` or SERVER_HOST)
always wins over discovery.

Wire format (one UDP datagram, UTF-8 JSON):
    {"magic": DISCOVERY_MAGIC, "host": <tcp ip>, "port": <tcp port>}

Transport: we send to all three LAN-friendly destinations at once -
multicast group, subnet/limited broadcast, and loopback - because each
covers a case the others miss (VPNs swallow broadcast, some APs block
multicast, loopback only helps same-machine tests).  Only stdlib is used
so behaviour matches the rest of the project.  University Wi-Fi with
client isolation may swallow all of them - that is expected, and the
manual path remains the fallback.
"""

import json
import socket
import struct
import time

import config

MULTICAST_GROUP = "239.255.60.60"
MULTICAST_TTL = 2


def _dest_addrs():
    """Every destination one beacon round is sent to."""
    addrs = {MULTICAST_GROUP, "127.0.0.1", "255.255.255.255"}
    for ip in _local_ips_safe():
        parts = ip.split(".")
        if len(parts) == 4:
            addrs.add("%s.%s.%s.255" % (parts[0], parts[1], parts[2]))
    return sorted(addrs)


# Kept for backwards compatibility with the earlier broadcast-only build.
def _broadcast_addrs():
    return _dest_addrs()


def _local_ips_safe():
    try:
        from protocol import local_ips
        return local_ips()
    except Exception:
        return []


def beacon_payload(host, port):
    return json.dumps({
        "magic": config.DISCOVERY_MAGIC,
        "host": host,
        "port": port,
    }).encode("utf-8")


def parse_beacon(data):
    """Return (host, port) if this datagram is one of ours, else None."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict) or obj.get("magic") != config.DISCOVERY_MAGIC:
        return None
    host, port = obj.get("host"), obj.get("port")
    if not isinstance(host, str) or not host:
        return None
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if not (1 <= port <= 65535):
        return None
    return host, port


def broadcast_once(tcp_host, tcp_port):
    """Send one beacon round.  Returns number of destinations sent to."""
    payload = beacon_payload(tcp_host, tcp_port)
    sent = 0
    for dest in _dest_addrs():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            if dest == MULTICAST_GROUP:
                try:
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL,
                                 MULTICAST_TTL)
                except OSError:
                    pass
            s.sendto(payload, (dest, config.DISCOVERY_PORT))
            sent += 1
        except OSError:
            pass
        finally:
            s.close()
    return sent


def run_beacon(stop_event, tcp_host_fn, tcp_port_fn):
    """Target for the server's beacon thread; runs until stop_event set."""
    while not stop_event.is_set():
        try:
            broadcast_once(tcp_host_fn(), tcp_port_fn())
        except Exception:
            pass
        stop_event.wait(config.DISCOVERY_INTERVAL_S)


def listen(timeout_s=None, port=None):
    """Listen for beacons for timeout_s seconds.

    Returns a list of (host, port) sorted with most-seen first.
    Binds 0.0.0.0 so hotspot and Wi-Fi interfaces are all covered.
    """
    timeout = config.DISCOVERY_LISTEN_S if timeout_s is None else timeout_s
    udp_port = config.DISCOVERY_PORT if port is None else port
    seen = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        s.bind(("0.0.0.0", udp_port))
        # Join the multicast group so beacons arrive even where directed
        # broadcast is filtered (common on VPNs / hotspots).
        try:
            mreq = struct.pack("4s4s", socket.inet_aton(MULTICAST_GROUP),
                               socket.inet_aton("0.0.0.0"))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError:
            pass
        s.settimeout(0.25)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data, _ = s.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            found = parse_beacon(data)
            if found:
                seen[found] = seen.get(found, 0) + 1
    except OSError:
        pass
    finally:
        s.close()
    return sorted(seen, key=lambda k: (-seen[k], k))
