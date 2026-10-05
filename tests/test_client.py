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
srv, game = run.srv, run.game
uis = []


def ui_wait(pred, what, timeout=8.0):
    return wait(pred, what, timeout, pump=uis)


def press(ui, pos, button=1):
    ui._on_game_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button,
                                         pos=pos))


def click_cell(ui, cell, button=1):
    ui_wait(lambda: ui.cell_at(ui.cell_rect(cell).center) == cell, "geometry")
    if button == 1:
        ui_wait(lambda: ui.can_click(cell), "the client to see its turn")
    press(ui, ui.cell_rect(cell).center, button)


def open_slot():
    return next(c for c in game.cells() if c not in game.revealed)


alice = client_mod.ClientUI(); uis.append(alice)
bob = client_mod.ClientUI(); uis.append(bob)

# --- 1. both connect and join -------------------------------------------
ui_wait(lambda: alice.net.status == "connected" and bob.net.status == "connected",
        "sockets")
assert alice.screen_name == client_mod.SCREEN_NICKNAME
alice.net.send(protocol.JOIN, nickname="Alice")
ui_wait(lambda: alice.screen_name == client_mod.SCREEN_GAME, "Alice in the game")
assert alice.welcome == "Welcome, Alice."
bob.net.send(protocol.JOIN, nickname="Bob")
ui_wait(lambda: bob.screen_name == client_mod.SCREEN_GAME, "Bob in the game")
assert bob.welcome == "Welcome, Bob."
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
assert "Not your turn" in idle.toast
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


# --- 7. the mode bar, flags, and the custom panel ------------------------
def choose(mode):
    box = dict(alice.mode_rects)[mode]
    press(alice, box.center)
    ui_wait(lambda: game.mode == mode and game.phase == g.PHASE_PLAYING, mode)
    ui_wait(lambda: alice.mode == mode and bob.mode == mode, "clients on " + mode)


choose(g.MODE_RADIUS2)
choose(g.MODE_CUBE)
ui_wait(lambda: alice.is_3d, "cube layout")
spots = {alice.cell_at(alice.cell_rect(c).center) for c in alice.board_cells()}
assert None not in spots and len(spots) == 64
cube_cell = open_slot()
who = by_id[game.current_turn]
before = len(game.revealed)
click_cell(who, cube_cell)
ui_wait(lambda: len(game.revealed) > before, "a click into a cube layer")
ok(7, "mode buttons work; all 64 cube slots are clickable and layers round-trip")

choose(g.MODE_SWEEPER)
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

choose(g.MODE_CUSTOM)
ui_wait(lambda: alice.settings_open and alice.custom.get("size"), "settings panel")


def panel(key, want):
    ui_wait(lambda: alice.custom == game.custom, "settings in sync")
    _card, controls, _close = alice._settings_widgets()
    for rect, ckey, value, is_step in controls:
        if ckey != key:
            continue
        if is_step and (value > 0) != (want > alice.custom[key]):
            continue
        if not is_step and value != want:
            continue
        press(alice, rect.center)
        return
    raise AssertionError("no control for " + key)


size = alice.custom["size"]
panel("size", size + 1)
ui_wait(lambda: game.custom["size"] == size + 1 and alice.dims == (size + 1,) * 2,
        "a bigger board")
panel("shape", "cube")
ui_wait(lambda: alice.is_3d and bob.is_3d, "a custom cube")
assert None not in {alice.cell_at(alice.cell_rect(c).center)
                    for c in alice.board_cells()}
ok(9, "the custom panel resizes the board and switches it to a cube; "
      "every slot stays clickable")

run.stop()
print("\nALL CLIENT CHECKS PASSED")
