"""The server over real TCP: the whole assignment, plus modes and custom."""

import socket
import time

from helpers import ServerRunner, Wire, ok, wait
import config

config.TURN_SECONDS = 2            # keep the timeout check quick
config.RECONNECT_GRACE = 1         # and the leaver check
import game as g
import protocol

run = ServerRunner(55601)
srv, game = run.srv, None
PORT = 55601


def open_slot():
    return next(c for c in game.cells() if c not in game.revealed)


def pick(wire, cell):
    if len(cell) == 3:
        wire.send(protocol.PICK, layer=cell[0], row=cell[1], col=cell[2])
    else:
        wire.send(protocol.PICK, row=cell[0], col=cell[1])


def recreate(wires, mode="classic", custom=None, bot="off", ranked=True):
    global game
    for wire in wires:
        wire.send(protocol.LEAVE_ROOM)
    wait(lambda: all((w.get(protocol.WELCOME) or {}).get("role") == "lobby"
                     for w in wires), "clients back in lobby")
    wires[0].forget(protocol.WELCOME)
    wires[0].send(protocol.CREATE_ROOM, name="Test room", mode=mode,
                   custom=custom or {}, bot_level=bot, ranked=ranked)
    room_id = wait(lambda: (wires[0].get(protocol.WELCOME) or {}).get("room_id"),
                   "configured room")
    for wire in wires[1:]:
        wire.forget(protocol.WELCOME)
        wire.send(protocol.JOIN_ROOM, room_id=room_id, watch=False)
    wait(lambda: all((w.get(protocol.WELCOME) or {}).get("room_id") == room_id
                     for w in wires), "clients rejoined configured room")
    game = srv.rooms[room_id].game


# --- 1. join, welcome, client list -------------------------------------
alice, bob = Wire(PORT, "Alice"), Wire(PORT, "Bob")
game = srv.selected_room.game
w = wait(lambda: alice.get(protocol.WELCOME), "Alice welcome")
assert w["message"] == "Welcome, Alice." and w["role"] == "player"
assert w["grid_size"] == 6 and w["bombs_total"] == 11 and w["token"]
clients = wait(lambda: bob.get(protocol.CLIENTS)
               if (bob.get(protocol.CLIENTS) or {}).get("count") == 2 else None,
               "both clients listed")
assert [c["name"] for c in clients["list"]] == ["Alice", "Bob"]
ok(1, "join, 'Welcome, Alice.', live client list, a reconnect token")

# --- 2. match starts covered, 11 bombs, nothing leaks --------------------
st = wait(lambda: alice.get(protocol.STATE)
          if (alice.get(protocol.STATE) or {}).get("phase") == "playing" else None,
          "match to start")
assert st["bombs_left"] == 11 and len(game.bombs) == 11
assert all(cell is None for row in st["board"] for cell in row)
assert "bomb" not in repr(st["board"]) and st["current_turn"] in (1, 2)
ok(2, "match auto-starts: 11 bombs on 6x6, covered board, first player chosen")

# --- 3. server-side clock ----------------------------------------------
tick = wait(lambda: alice.get(protocol.TICK), "a countdown tick")
assert 0 <= tick["seconds_left"] <= config.TURN_SECONDS
first = game.current_turn
wait(lambda: game.current_turn != first, "turn to pass on timeout", 6)
ok(3, "countdown ticks reach the players; the turn passes when time runs out")

# --- 4. out of turn -----------------------------------------------------
ids = {alice.get(protocol.WELCOME)["client_id"]: alice,
       bob.get(protocol.WELCOME)["client_id"]: bob}
off = next(w_ for cid, w_ in ids.items() if cid != game.current_turn)
off.forget(protocol.ERROR)
pick(off, (0, 0))
assert wait(lambda: off.get(protocol.ERROR), "refusal")["message"] == "not your turn"
ok(4, "a pick out of turn is refused")

# --- 5. a whole match ---------------------------------------------------
kept = passed = False
while game.phase == g.PHASE_PLAYING:
    who, cell = game.current_turn, open_slot()
    before = len(game.revealed)
    pick(ids[who], cell)
    wait(lambda: len(game.revealed) > before, "the pick to land")
    if game.phase == g.PHASE_PLAYING:
        if cell in game.bombs:
            assert game.current_turn == who
            kept = True
        else:
            assert game.current_turn != who
            passed = True
assert kept and passed
end = wait(lambda: alice.get(protocol.MATCH_END), "match end")
wait(lambda: bob.get(protocol.MATCH_END), "bob's match end")
scores = {p["name"]: p["score"] for p in end["players"]}
assert sum(scores.values()) == 11
assert len(end["stats"]) == 2 and all("best_chain" in s_ for s_ in end["stats"])
assert end["leaderboard"] and end["leaderboard"][0]["wins"] in (0, 1)
ok(5, "bomb keeps the turn, empty passes it, match ends on the 11th bomb; "
      "the end message carries stats and the leaderboard")

# --- 6. rematch ---------------------------------------------------------
winner = end["winner_id"]
alice.send(protocol.REMATCH)
time.sleep(0.3)
assert game.phase == g.PHASE_ENDED, "one vote must not restart"
bob.send(protocol.REMATCH)
wait(lambda: game.phase == g.PHASE_PLAYING, "rematch to start")
if winner is not None:
    assert game.current_turn == winner
assert game.bombs_found == 0 and sum(game.scores.values()) == 0
ok(6, "rematch needs both, the winner starts, a fresh board, scores level")

# --- 7. reset -----------------------------------------------------------
alice.forget(protocol.SERVER_RESET)
run.call(srv.reset_all)
wait(lambda: alice.get(protocol.SERVER_RESET), "reset broadcast")
assert sum(game.scores.values()) == 0
wait(lambda: game.phase == g.PHASE_PLAYING, "fresh match after reset")
ok(7, "reset clears scores and the board, then deals again")

# --- 8. spectators and leavers -----------------------------------------
carol = Wire(PORT, "Carol")
w3 = wait(lambda: carol.get(protocol.WELCOME), "Carol welcome")
assert w3["role"] == "spectator"
wait(lambda: (carol.get(protocol.CLIENTS) or {}).get("count") == 3, "3 online")
alice.close()
wait(lambda: (carol.get(protocol.CLIENTS) or {}).get("count") == 2, "2 online")
listing = carol.get(protocol.CLIENTS)["list"]
assert any(c["name"] == "Alice" and c["away"] for c in listing), listing
# Watching is explicit; wait for the held seat to expire, then join it.
wait(lambda: alice.get(protocol.WELCOME)["client_id"] not in game.players,
     "away seat released")
carol.send(protocol.LEAVE_ROOM)
wait(lambda: (carol.get(protocol.WELCOME) or {}).get("role") == "lobby", "Carol lobby")
carol.send(protocol.JOIN_ROOM, room_id=alice.get(protocol.WELCOME).get("room_id"))
wait(lambda: w3["client_id"] in game.players, "Carol joins vacant seat")
ok(8, "a spectator watches; a leaver is marked away; an opted-in watcher takes vacancy")
bob.close(); carol.close()
time.sleep(1.4)

# --- 9. the browser reachability page ----------------------------------
s = socket.create_connection(("127.0.0.1", PORT), 5)
s.sendall(b"GET / HTTP/1.1\r\nHost: t\r\n\r\n")
s.settimeout(3)
raw = b""
try:
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        raw += chunk
except socket.timeout:
    pass
s.close()
assert raw.startswith(b"HTTP/1.1 200 OK") and b"Connection works" in raw
head, _, body = raw.partition(b"\r\n\r\n")
declared = int([l.split(b":")[1] for l in head.split(b"\r\n")
                if l.lower().startswith(b"content-length")][0])
assert declared == len(body)
assert not any(c.name == "" for c in srv._joined_clients())
ok(9, "a browser gets a valid page and never takes a player seat")

# --- 10. a fresh table for the modes ------------------------------------
dave, erin = Wire(PORT, "Dave"), Wire(PORT, "Erin")
wait(lambda: game.phase == g.PHASE_PLAYING and len(game.players) == 2, "new table")
by_id = {dave.get(protocol.WELCOME)["client_id"]: dave,
         erin.get(protocol.WELCOME)["client_id"]: erin}


def switch(mode):
    dave.forget(protocol.ERROR)
    dave.send(protocol.SET_MODE, mode=mode)
    assert "fixed" in wait(lambda: dave.get(protocol.ERROR), "fixed room settings")["message"]
    recreate((dave, erin), mode=mode)
    wait(lambda: game.mode == mode and game.phase == g.PHASE_PLAYING, "mode " + mode)


switch(g.MODE_RADIUS2)
seen = 0
for _ in range(20):
    if game.phase != g.PHASE_PLAYING:
        break
    who, cell = game.current_turn, open_slot()
    before = len(game.revealed)
    pick(by_id[who], cell)
    wait(lambda: len(game.revealed) > before, "radius-2 pick")
    seen = max([seen] + [v for v in game.revealed.values() if isinstance(v, int)])
for cell, value in game.revealed.items():
    if isinstance(value, int):
        assert value == 2 * game._bombs_at(cell, 1) + game._bombs_at(cell, 2)
assert sum(game.scores.values()) >= 0
ok(10, "radius 2 over the wire: hints are the 2/1 weighting (largest seen %d)" % seen)

switch(g.MODE_CUBE)
st = wait(lambda: dave.get(protocol.STATE)
          if (dave.get(protocol.STATE) or {}).get("dims") == [4, 4, 4] else None,
          "cube state")
assert len(st["board"]) == 4 and st["bombs_total"] == 19
who, cell = game.current_turn, open_slot()
before = len(game.revealed)
pick(by_id[who], cell)
wait(lambda: len(game.revealed) > before, "cube pick")
assert cell in game.revealed
ok(11, "the cube over the wire: 4x4x4 state, a layered pick round-trips")

switch(g.MODE_SWEEPER)
who = game.current_turn
bomb = next(c for c in game.bombs if c not in game.revealed)
pick(by_id[who], bomb)
wait(lambda: bomb in game.revealed, "the bomb to open")
assert game.current_turn != who and game.scores[who] == 0
who = game.current_turn
before = len(game.revealed)
spot = next(c for c in game.cells() if c not in game.revealed
            and c not in game.bombs)
by_id[who].send(protocol.FLAG, row=spot[0], col=spot[1])
wait(lambda: spot in game.flags, "the flag")
by_id[who].send(protocol.FLAG, row=spot[0], col=spot[1])
wait(lambda: spot not in game.flags, "the flag to lift")
ok(12, "minesweeper over the wire: a bomb ends the turn; flags plant and lift")

switch(g.MODE_CUSTOM)
recreate((dave, erin), mode=g.MODE_CUSTOM,
         custom={"size": 7, "bombs": 12, "turn_seconds": 20})
wait(lambda: game.custom["size"] == 7 and game.dims == (7, 7), "custom size")
assert len(game.bombs) == 12 and game.turn_seconds == 20
dave.forget(protocol.ERROR)
dave.send(protocol.SET_CUSTOM, settings={"size": 500, "bombs": 99999})
assert "fixed" in wait(lambda: dave.get(protocol.ERROR), "custom settings immutable")["message"]
assert game.custom["size"] == 7 and game.bomb_count < game.cell_count
frank = Wire(PORT, "Frank")
wait(lambda: frank.get(protocol.WELCOME), "spectator")
before = dict(game.custom)
frank.send(protocol.SET_CUSTOM, settings={"size": 4})
time.sleep(0.4)
assert game.custom == before
ok(13, "custom settings apply at room creation and cannot change in-room")

run.stop()
print("\nALL NETWORK CHECKS PASSED")
