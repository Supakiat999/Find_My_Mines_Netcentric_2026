"""Auto-discovery: the server announces itself, the client finds it.

Everything runs over real UDP and TCP on loopback.  helpers.py makes every
other suite explicit-address and silent; this one switches discovery on.
"""

import json

import pygame

from helpers import ServerRunner, ok, wait
import config
import discovery
import protocol

config.DISCOVERY_PORT = 55691          # not the real port: never meet a live game
config.DISCOVERY_INTERVAL_S = 0.2
config.DISCOVERY_LISTEN_S = 3.0


def beacon(**fields):
    obj = {"magic": config.DISCOVERY_MAGIC, "id": "ab12cd34", "port": 55550, "name": "Host",
           "rooms": 2, "open": 1}
    obj.update(fields)
    return json.dumps(obj).encode()


def make_auto_ui():
    import client as client_mod
    config.SERVER_HOST = "auto"
    try:
        return client_mod.ClientUI()
    finally:
        config.SERVER_HOST = "127.0.0.1"


# 1. beacon parsing: ours is accepted, everything else is ignored ---------
got = discovery.parse_beacon(beacon(), "192.168.1.9")
assert got == {"id": "ab12cd34", "host": "192.168.1.9", "port": 55550, "name": "Host",
               "rooms": 2, "open": 1}, got
for bad in (b"hello", b"\xff\xfe", b"[]", beacon(magic="other"),
            beacon(port=0), beacon(port=70000), beacon(port="55550"),
            beacon(name=5), beacon(name="a\nb"), beacon(id=""), beacon(id=7),
            beacon(id="x" * 17), beacon(id="a b")):
    assert discovery.parse_beacon(bad, "1.2.3.4") is None, bad
assert discovery.parse_beacon(beacon(rooms=-1, open="x"), "1.2.3.4")["open"] == 0
assert len(discovery.parse_beacon(beacon(name="x" * 99), "1.2.3.4")["name"]) == 24
ok("1", "beacons are parsed strictly; the sender's address is used, not the payload's")

# 2. one server on the network: the client connects with no address -------
config.DISCOVERY = True
server = ServerRunner(55690)
ui = make_auto_ui()
assert ui.auto and ui.net.status == "searching"
wait(lambda: ui.net.status == "connected", "auto-connect", pump=[ui])
assert ui.target == ("127.0.0.1", 55690) and ui.found_by_scan
ui.nickname = "Ada"
ui.net.send(protocol.JOIN, nickname="Ada")
wait(lambda: ui.screen_name == "lobby", "lobby after discovery", pump=[ui])
ui.draw()
seen = discovery.scan(1.0)
assert len(seen) == 1                       # heard on loopback AND LAN, counted once
# helpers bind the server to 127.0.0.1, so it must announce only that address
assert seen[0]["host"] == "127.0.0.1", seen
assert discovery._announce_from("0.0.0.0")[0] is True
assert discovery._announce_from("127.0.0.1") == (True, [])
assert discovery._announce_from("192.168.1.9") == (False, ["192.168.1.9"])
ok("2", "one server found: connects by itself and reaches the room lobby")

# 3. the beacon reports the lobby: rooms and joinable rooms ---------------
ui.net.send(protocol.CREATE_ROOM, name="Alpha", mode="classic", custom={},
            bot_level="off", ranked=True)
wait(lambda: ui.screen_name == "game", "room created", pump=[ui])
wait(lambda: server.srv._beacon_info == (1, 1), "beacon counts the open room")
found = discovery.scan()
assert len(found) == 1 and (found[0]["rooms"], found[0]["open"]) == (1, 1), found
ok("3", "beacon carries room and open-room counts for the picker")

# 4. a dropped link retries the discovered server, not config.py ----------
config.SERVER_PORT = 59999                      # config now points nowhere
ui.net.sock.shutdown(2)
wait(lambda: ui.reconnecting, "reconnect started", pump=[ui])
wait(lambda: ui.net.status == "connected" and ui.joined and not ui.reconnecting,
     "reconnected to the discovered server", pump=[ui])
assert ui.net.address == "127.0.0.1:55690" and ui.screen_name == "game"
ok("4", "reconnect keeps the discovered target and takes the seat back")
ui.net.close()

# 5. an explicit address skips discovery entirely -------------------------
import client as client_mod
config.SERVER_HOST = "127.0.0.1"
explicit = client_mod.ClientUI()
assert not explicit.auto and explicit.net.status in ("connecting", "connected")
explicit.net.close()
ok("5", "an explicit SERVER_HOST / command-line address never searches")

# 6. two servers: the client waits and the player picks -------------------
second = ServerRunner(55692)
config.SERVER_PORT = 55690
wait(lambda: len(discovery.scan(1.0)) == 2, "both servers audible")
two = make_auto_ui()
wait(lambda: len(two.servers) == 2, "scan sees two servers", pump=[two])
assert two.net.status == "searching" and two._picking()
two.draw()
pygame.event.clear()
two._on_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_2, unicode="2", mod=0))
wait(lambda: two.net.status == "connected", "picked server connected", pump=[two])
assert two.target[1] == two.servers[1]["port"]
two.net.close()
ok("6", "several servers: nothing is guessed, the numbered pick connects")

# 7. nothing answers: fall back to the config address ---------------------
second.stop()
server.stop()
config.DISCOVERY = False
config.DISCOVERY_LISTEN_S = 0.6
config.SERVER_PORT = 59999
lonely = make_auto_ui()
wait(lambda: lonely.net.status == "failed", "fallback dial", pump=[lonely])
assert lonely.net.address == "127.0.0.1:59999" and not lonely.found_by_scan
lonely.draw()
ok("7", "no server on the network: falls back to 127.0.0.1 and shows the usual error path")

print("ALL DISCOVERY CHECKS PASSED")
