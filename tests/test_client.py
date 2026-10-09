"""Two real game windows playing each other through a real server."""

import time

import pygame

from helpers import ServerRunner, ok, wait
import config

config.TURN_SECONDS = 120          # never let the clock race the script
import client as client_mod
import game as g
import protocol

run = ServerRunner(55603)
srv, game = run.srv, None
uis = []


def ui_wait(pred, what, timeout=8.0):
    return wait(pred, what, timeout, pump=uis)


def press(ui, pos, button=1):
    ui._on_game_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button,
                                         pos=pos))


def click_cell(ui, cell, button=1):
    ui_wait(lambda: ui.cell_at(ui._cell_center(cell)) == cell, "geometry")
    if button == 1:
        ui_wait(lambda: ui.can_click(cell), "the client to see its turn")
    press(ui, ui._cell_center(cell), button)


def open_slot():
    return next(c for c in game.cells() if c not in game.revealed)


alice = client_mod.ClientUI(); uis.append(alice)
bob = client_mod.ClientUI(); uis.append(bob)
alice.prefs["rules_seen"] = bob.prefs["rules_seen"] = True

# --- 1. both connect and join -------------------------------------------
ui_wait(lambda: alice.net.status == "connected" and bob.net.status == "connected",
        "sockets")
assert alice.screen_name == client_mod.SCREEN_NICKNAME
alice.net.send(protocol.JOIN, nickname="Alice")
ui_wait(lambda: alice.screen_name == client_mod.SCREEN_LOBBY, "Alice in the lobby")
assert alice.welcome == "Welcome, Alice."
bob.net.send(protocol.JOIN, nickname="Bob")
ui_wait(lambda: bob.screen_name == client_mod.SCREEN_LOBBY, "Bob in the lobby")
assert bob.welcome == "Welcome, Bob."
alice.net.send(protocol.CREATE_ROOM, name="testroom", mode=g.MODE_CLASSIC,
                custom={}, bot_level="off", ranked=True)
ui_wait(lambda: alice.screen_name == client_mod.SCREEN_GAME, "Alice creates testroom")
game = next(iter(srv.rooms.values())).game
ui_wait(lambda: bob.rooms, "room list")
bob.net.send(protocol.JOIN_ROOM, room_id=bob.rooms[0]["id"], watch=False)
ui_wait(lambda: bob.screen_name == client_mod.SCREEN_GAME, "Bob joins testroom")
ok(1, "both windows connect, take a nickname, and are welcomed by name")

# --- 2. same board, one turn --------------------------------------------
ui_wait(lambda: alice.clients["count"] == 2 and bob.clients["count"] == 2, "2 online")
assert [c["name"] for c in alice.clients["list"]] == ["Alice", "Bob"]
ui_wait(lambda: alice.phase == "playing" and bob.phase == "playing", "match")
assert alice.state["board"] == bob.state["board"]
assert alice.my_turn != bob.my_turn and alice.role == bob.role == "player"
ok(2, "both windows show the same board, the client list, and one active turn")

# --- 3. off-turn clicks ---------------------------------------------------
idle = bob if alice.my_turn else alice
idle_cell = open_slot()
assert not idle.can_click(idle_cell)
press(idle, idle.cell_rect(idle_cell).center)
assert "not your turn" in idle.toast.lower()
ok(3, "the window off turn refuses clicks and says why")

# --- 4. a whole match through the mouse ----------------------------------
by_id = {alice.my_id: alice, bob.my_id: bob}
clicks = 0
while game.phase == g.PHASE_PLAYING:
    ui = by_id[game.current_turn]
    cell = open_slot()
    before = len(game.revealed)
    click_cell(ui, cell)
    clicks += 1
    ui_wait(lambda: len(game.revealed) > before, "the click to register")
ui_wait(lambda: alice.match_end and bob.match_end, "match end on both")
scores = {p["name"]: p["score"] for p in alice.match_end["players"]}
assert sum(scores.values()) == 11
assert alice.match_end["winner_id"] == bob.match_end["winner_id"]
assert alice.match_end["stats"] and alice.match_end["leaderboard"]
alice.draw(); bob.draw()
ok(4, "%d mouse clicks played a whole match; both ended together %s"
   % (clicks, scores))

# --- 5. rematch -----------------------------------------------------------
winner = alice.match_end["winner_id"]
time.sleep(client_mod.END_DELAY + client_mod.END_FADE + 0.05)
alice.draw(); bob.draw()
press(alice, alice.rematch_rect.center)
assert alice.voted_rematch
time.sleep(0.3)
assert game.phase == g.PHASE_ENDED, "one vote must not restart"
press(bob, bob.rematch_rect.center)
ui_wait(lambda: game.phase == g.PHASE_PLAYING, "rematch")
ui_wait(lambda: alice.match_end is None and bob.match_end is None, "overlays gone")
if winner is not None:
    assert game.current_turn == winner
assert sum(p["score"] for p in alice.players) == 0
ok(5, "a rematch needs both clicks, the winner starts, scores start level")

# --- 6. server reset ------------------------------------------------------
run.call(srv.reset_all)
ui_wait(lambda: "reset" in alice.toast.lower(), "reset notice")
ok(6, "the server's Reset reaches both windows")


# --- 7. room modes, flags, and custom creation ---------------------------
def recreate(mode, custom=None):
    global game, by_id
    alice.net.send(protocol.LEAVE_ROOM)
    bob.net.send(protocol.LEAVE_ROOM)
    ui_wait(lambda: alice.screen_name == client_mod.SCREEN_LOBBY
             and bob.screen_name == client_mod.SCREEN_LOBBY, "leave old room")
    alice.net.send(protocol.CREATE_ROOM, name="testroom", mode=mode,
                    custom=custom or {}, bot_level="off", ranked=mode != g.MODE_CUSTOM)
    ui_wait(lambda: alice.screen_name == client_mod.SCREEN_GAME, "create " + mode)
    game = srv.rooms[alice.room_id].game
    ui_wait(lambda: bob.rooms, "room list")
    bob.net.send(protocol.JOIN_ROOM, room_id=alice.room_id, watch=False)
    ui_wait(lambda: bob.screen_name == client_mod.SCREEN_GAME, "join " + mode)
    ui_wait(lambda: game.phase == g.PHASE_PLAYING, "match in " + mode)
    by_id = {alice.my_id: alice, bob.my_id: bob}


recreate(g.MODE_CUBE)
ui_wait(lambda: alice.is_3d, "cube layout")
spots = {alice.cell_at(alice._cell_center(c)) for c in alice.board_cells()}
assert None not in spots and len(spots) == 64
cube_cell = open_slot()
who = by_id[game.current_turn]
before = len(game.revealed)
click_cell(who, cube_cell)
ui_wait(lambda: len(game.revealed) > before, "a click into a cube layer")
ok(7, "cube room has 64 clickable slots and layers round-trip")

recreate(g.MODE_SWEEPER)
who = by_id[game.current_turn]
safe = next(c for c in game.cells() if c not in game.revealed
            and c not in game.bombs)
click_cell(who, safe, button=3)
ui_wait(lambda: safe in game.flags, "a right-click flag")
ui_wait(lambda: safe in who.flags, "the flag to reach the client")
assert not who.can_click(safe)
click_cell(who, safe, button=3)
ui_wait(lambda: safe not in game.flags, "the flag to lift")
ok(8, "right-click plants and lifts a flag, and a flagged slot cannot be opened")

custom = dict(config.DEFAULT_CUSTOM)
custom["size"] = 4
custom["shape"] = "cube"
recreate(g.MODE_CUSTOM, custom)
ui_wait(lambda: alice.custom == custom and alice.dims == (custom["size"],) * 3,
        "custom room settings")
ui_wait(lambda: alice.is_3d and bob.is_3d, "a custom cube")
assert None not in {alice.cell_at(alice._cell_center(c))
                    for c in alice.board_cells()}
ok(9, "custom room settings resize the board and switch it to a cube; "
      "every slot stays clickable")

run.stop()
print("\nALL CLIENT CHECKS PASSED")
