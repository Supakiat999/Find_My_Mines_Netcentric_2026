"""New client features: opponent picker, chat, coach, sound, animation,
scale-to-fit window, auto-reconnect, themes and saved preferences."""

import os
import tempfile
import time

import pygame

from helpers import ServerRunner, ok, wait
import config

config.TURN_SECONDS = 120
config.RECONNECT_GRACE = 6
config.BOT_THINK_SECONDS = (0.02, 0.05)
import client as client_mod
import game as g
import protocol
import sound as sound_mod
import themes

uis = []


def ui_wait(pred, what, timeout=10.0):
    return wait(pred, what, timeout, pump=uis)


def press(ui, pos, button=1):
    ui._on_game_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button,
                                         pos=pos))


def key(ui, k, ch=""):
    ui._on_event(pygame.event.Event(pygame.KEYDOWN, key=k, unicode=ch, mod=0))


def type_text(ui, text):
    for ch in text:
        key(ui, ord(ch), ch)


def new_ui(nickname):
    ui = client_mod.ClientUI()
    uis.append(ui)
    ui_wait(lambda: ui.net.status == "connected", "connect")
    ui.nickname = nickname
    ui.net.send(protocol.JOIN, nickname=nickname)
    ui_wait(lambda: ui.screen_name == client_mod.SCREEN_GAME, nickname + " joined")
    return ui


# =====================================================================
# part 1 - picking an opponent
# =====================================================================
run = ServerRunner(55604)
srv, game = run.srv, run.game
solo = new_ui("Solo")
ui_wait(lambda: solo.phase == "waiting", "waiting for an opponent")

level_buttons = {level: rect for level, _l, rect in solo._opponent_buttons()}
press(solo, level_buttons["hard"].center)
ui_wait(lambda: srv.bot_level == "hard" and game.phase == g.PHASE_PLAYING, "a match")
ui_wait(lambda: solo.state.get("bot_seated"), "the seated computer")
assert [p["bot"] for p in solo.players] == [False, True]
assert solo.clients["bots"][0]["name"] == "Computer (Hard)"
solo.draw()
ok(1, "clicking Hard seats the computer and starts a match")

press(solo, level_buttons["easy"].center)
ui_wait(lambda: srv.bot_level == "easy" and solo.players[1]["name"] == "Computer (Easy)",
        "the level to change")
press(solo, level_buttons["off"].center)
ui_wait(lambda: not solo.state["bot_seated"] and solo.phase == "waiting", "off")
ok(2, "the level can be changed mid-game and switched back to a person")

press(solo, level_buttons["hard"].center)
ui_wait(lambda: solo.phase == "playing", "playing again")
guard = 0
while game.phase == g.PHASE_PLAYING and guard < 400:
    guard += 1
    if game.current_turn == solo.my_id:
        cell = next(c for c in game.cells() if c not in game.revealed)
        ui_wait(lambda: solo.can_click(cell), "my turn on screen")
        before = len(game.revealed)
        press(solo, solo.cell_rect(cell).center)
        ui_wait(lambda: len(game.revealed) > before, "my pick")
    else:
        before = len(game.revealed)
        ui_wait(lambda: game.current_turn == solo.my_id or len(game.revealed) > before
                or game.phase != g.PHASE_PLAYING, "the computer's move", 6)
ui_wait(lambda: solo.match_end is not None, "the end screen")
assert {p["name"] for p in solo.match_end["players"]} == {"Solo", "Computer (Hard)"}
solo.draw()
press(solo, solo.rematch_rect.center)
ui_wait(lambda: solo.match_end is None and game.phase == g.PHASE_PLAYING,
        "an instant rematch")
ok(3, "a whole match against the computer, ended by mouse, rematched with one click")

solo.net.close()
run.stop()
uis.clear()
time.sleep(0.3)

# =====================================================================
# part 2 - two people
# =====================================================================
run = ServerRunner(55605)
srv, game = run.srv, run.game
alice, bob = new_ui("Alice"), new_ui("Bob")
ui_wait(lambda: alice.phase == "playing" and bob.phase == "playing", "match")
by_id = {alice.my_id: alice, bob.my_id: bob}


def me():
    return by_id[game.current_turn]


def other():
    return next(u for u in uis if u is not me())


def play_pick(cell=None):
    ui = me()
    cell = cell or next(c for c in game.cells() if c not in game.revealed)
    ui_wait(lambda: ui.can_click(cell), "the turn on screen")
    before = len(game.revealed)
    press(ui, ui.cell_rect(cell).center)
    ui_wait(lambda: len(game.revealed) > before, "a pick")


# ---- chat -----------------------------------------------------------
assert not alice.chat_focus
key(alice, pygame.K_RETURN, "\r")
assert alice.chat_focus, "Enter focuses the chat"
type_text(alice, "hi bob")
key(alice, pygame.K_m, "m")                      # typed as text, not a hotkey
assert alice.chat_input == "hi bobm" and not alice.sound.muted
key(alice, pygame.K_BACKSPACE)
key(alice, pygame.K_RETURN, "\r")
assert alice.chat_input == "" and not alice.chat_focus
ui_wait(lambda: any(m["text"] == "hi bob" for m in bob.chat_lines), "delivery")
mine = [m for m in alice.chat_lines if m["text"] == "hi bob"]
assert mine and mine[0]["name"] == "Alice"
ok(4, "typing in the chat box: Enter opens it, letters go in, Enter sends")

time.sleep(0.7)
_log, quick, field = alice._chat_rects()
press(alice, quick[0].center)                    # the "GG" button
ui_wait(lambda: any(m["text"] == "GG" for m in bob.chat_lines), "quick chat")
press(alice, field.center)
assert alice.chat_focus
press(alice, (client_mod.CX, 150), 1)            # empty space in the game area
assert not alice.chat_focus, "clicking away from the box stops typing"
press(alice, field.center)
assert alice.chat_focus
press(alice, (alice.cards["coach"].x + 5, alice.cards["coach"].y + 5), 1)
assert not alice.chat_focus, "clicking elsewhere in the side panel lets go too"
alice.chat_lines = [{"id": 1, "name": "x", "text": "line %d" % i, "system": False}
                    for i in range(80)]
log_rect = alice._chat_rects()[0]
alice.mouse = lambda: log_rect.center
alice._on_wheel(pygame.event.Event(pygame.MOUSEWHEEL, y=3, x=0))
assert alice.chat_scroll > 0
alice.draw()
del alice.mouse
alice.chat_scroll = 0
ok(5, "quick-chat buttons send, focus behaves, and the log scrolls")

# ---- the coach ------------------------------------------------------
waiting = other()
waiting.toast = ""
waiting._ask_coach()
assert "own turn" in waiting.toast, (waiting.toast, waiting.role, waiting.phase, waiting.my_turn, game.current_turn, waiting.my_id)
for _ in range(4):
    if game.phase == g.PHASE_PLAYING:
        play_pick()
mover = me()
ui_wait(lambda: mover.my_turn and mover.phase == "playing", "the turn on screen")
assert mover._my_hints_left() == config.HINTS_PER_MATCH
ask, odds = mover._coach_buttons()
press(mover, ask.center)
ui_wait(lambda: mover.hint is not None, "the coach's answer")
covered = [c for c in game.cells() if c not in game.revealed]
assert len(mover.hint["heat"]) == len(covered) and mover.hint["cell"] in covered
ui_wait(lambda: mover._my_hints_left() == config.HINTS_PER_MATCH - 1, "count drops")
mover.draw()
assert mover.show_odds
press(mover, odds.center)
assert not mover.show_odds
mover.draw()
press(mover, odds.center)
key(mover, pygame.K_h, "h")                       # the hotkey
ui_wait(lambda: mover._my_hints_left() == config.HINTS_PER_MATCH - 2, "hotkey hint")
ok(6, "the coach: button and H key, odds on every slot, a live count, an odds toggle")

play_pick()                                       # the board changes...
ui_wait(lambda: all(u.hint is None for u in uis), "the stale advice to clear")
ok(7, "advice disappears the moment the board changes")

# ---- sound ----------------------------------------------------------
bank = sound_mod.SoundBank()
effects = sound_mod.build_effects(sound_mod.RATE)
assert set(effects) >= {"click", "bomb", "boom", "turn", "tick", "chat", "hint",
                        "error", "win", "lose", "draw"}
for name, samples in effects.items():
    ms = 1000.0 * len(samples) / sound_mod.RATE
    assert 25 <= ms <= 1200, (name, ms)
    assert 800 < max(abs(v) for v in samples) <= 32767, name
bank.muted = True
assert bank.play("click") is False and "click" not in bank.played
bank.muted = False
bank.play("click")
assert bank.played[-1] == "click"
ok(8, "all eleven effects are synthesised, audible, sensibly short; mute silences them "
      "(audio device available: %s)" % bank.available)

run.call(srv.reset_all)                           # a clean classic board
ui_wait(lambda: game.phase == g.PHASE_PLAYING and alice.mode == "classic"
        and bob.mode == "classic" and not alice.reveal_times
        and not any(v is not None for row in alice.board for v in row),
        "fresh classic match")
for u in uis:
    u.sound.played.clear()
mover = me()
bomb = next(c for c in sorted(game.bombs) if c not in game.revealed)
play_pick(bomb)
ui_wait(lambda: all("bomb" in u.sound.played for u in uis), "the bomb chime")
empty = next(c for c in game.cells() if c not in game.bombs and c not in game.revealed)
play_pick(empty)
ui_wait(lambda: all("click" in u.sound.played for u in uis), "the click")
ui_wait(lambda: "turn" in other().sound.played or "turn" in me().sound.played,
        "the turn ding")
run.call(srv.set_mode, g.MODE_SWEEPER)
ui_wait(lambda: game.mode == "sweeper" and game.phase == g.PHASE_PLAYING
        and alice.mode == "sweeper" and bob.mode == "sweeper", "sweeper")
for u in uis:
    u.sound.played.clear()
play_pick(next(iter(sorted(game.bombs))))
ui_wait(lambda: all("boom" in u.sound.played for u in uis), "the boom")
assert all("bomb" not in u.sound.played for u in uis), "a bad bomb sounds different"
ok(9, "sounds follow the game: chime for a bomb you want, click for a slot, "
      "ding on your turn, a thump when a bomb is bad")

# ---- reveal animation ----------------------------------------------
run.call(srv.set_mode, g.MODE_CLASSIC)
ui_wait(lambda: game.phase == g.PHASE_PLAYING and alice.mode == "classic"
        and bob.mode == "classic", "classic")
cell = next(c for c in game.cells() if c not in game.revealed)
play_pick(cell)
ui_wait(lambda: cell in alice.reveal_times or cell in bob.reveal_times, "animation")
watcher = alice if cell in alice.reveal_times else bob
now = time.monotonic()
assert watcher._reveal_progress(cell, now) < 1.0
assert watcher._reveal_progress(cell, now + client_mod.REVEAL_SECONDS + 0.1) == 1.0
late = new_ui("Late")
assert late.role == "spectator" and not late.reveal_times, "no animation on first look"
late.net.close()
uis.remove(late)
ok(10, "a newly opened slot animates for a moment; a late joiner's first look does not")

# ---- scale-to-fit window -------------------------------------------
ui = me()
target = next(c for c in game.cells() if c not in game.revealed)
ui_wait(lambda: ui.can_click(target), "my turn")
ui.window = pygame.display.set_mode((600, 440), pygame.RESIZABLE)
ui.draw()
ox, oy, scale = ui._view
assert abs(scale - 0.5) < 0.01, scale
canvas = ui.cell_rect(target).center
inverse = (int(ox + canvas[0] * scale), int(oy + canvas[1] * scale))
assert all(abs(a - b) <= 2 for a, b in zip(ui._to_canvas(inverse), canvas))
before = len(game.revealed)
ui._on_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=inverse))
ui_wait(lambda: target in game.revealed, "a click through the scaled window")
ok(11, "in a half-size window the canvas scales and clicks still land on the right slot")

ui = me()
target = next(c for c in game.cells() if c not in game.revealed)
ui_wait(lambda: ui.can_click(target), "my turn")
ui.window = pygame.display.set_mode((1500, 640), pygame.RESIZABLE)
ui.draw()
ox, oy, scale = ui._view
assert ox > 100 and scale < 0.75, (ox, scale)      # letterboxed, not stretched
canvas = ui.cell_rect(target).center
inverse = (int(ox + canvas[0] * scale), int(oy + canvas[1] * scale))
ui._on_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=inverse))
ui_wait(lambda: target in game.revealed, "a click through the letterboxed window")
pygame.display.set_mode((client_mod.WIN_W, client_mod.WIN_H), pygame.RESIZABLE)
ok(12, "in an odd-shaped window the game is letterboxed, and clicks still map correctly")

# ---- reconnecting ---------------------------------------------------
run.call(srv.set_mode, g.MODE_CLASSIC)
run.call(srv.reset_all)
ui_wait(lambda: game.phase == g.PHASE_PLAYING, "a match")
for _ in range(4):
    play_pick()
victim = alice
old_id, token = victim.my_id, victim.token
names = srv._names()
score_before = {names[pid]: game.scores[pid] for pid in game.players}
ui_wait(lambda: {p["name"]: p["score"] for p in victim.players} == score_before
        and {p["name"]: p["score"] for p in bob.players} == score_before,
        "both windows to show the server's scores")
seen_paused = [False]


def recovered():
    if bob.paused:
        seen_paused[0] = True
    return victim.welcome.startswith("Welcome back") and not victim.reconnecting


victim.net.sock.close()                      # her Wi-Fi drops
ui_wait(lambda: victim.reconnecting or recovered(), "the client to notice")
victim.draw()
ui_wait(recovered, "automatic reconnection", 15)
assert victim.token == token and victim.my_id != old_id
assert {p["name"]: p["score"] for p in victim.players} == score_before
assert victim.screen_name == client_mod.SCREEN_GAME
by_id = {alice.my_id: alice, bob.my_id: bob}
ui_wait(lambda: not bob.paused, "the pause to lift")
play_pick()
ok(13, "a dropped window reconnects by itself, keeps its seat and score, and play resumes "
       "(other player saw the pause: %s)" % seen_paused[0])

victim.net.close()
victim.net.status = "lost"
victim.reconnect_until = time.time() - 1
victim.pump_network()
assert victim.screen_name == client_mod.SCREEN_ERROR
victim.draw()
key(victim, pygame.K_r, "r")
assert victim.screen_name == client_mod.SCREEN_NICKNAME and not victim.joined
ok(14, "when the seat cannot be recovered the error screen appears, and R starts over")

config.SERVER_PORT = 59999                    # nothing is listening here
lost = client_mod.ClientUI()
uis.append(lost)
ui_wait(lambda: lost.net.status == "failed", "a refused connection")
lost.pump_network()
assert lost.screen_name == client_mod.SCREEN_ERROR
lost.draw()
config.SERVER_PORT = 55605
ok(15, "no server at all: the client shows the friendly error screen, not a traceback")

run.stop()

# =====================================================================
# part 3 - themes and remembered choices
# =====================================================================
ui = client_mod.ClientUI()
seen = []
for _ in range(3):
    seen.append(client_mod.THEME_NAME)
    ui._next_theme()
    ui.screen_name = client_mod.SCREEN_ERROR
    ui.draw()
assert seen == ["dark", "light", "colorblind"] and client_mod.THEME_NAME == "dark"
ok(16, "the theme button cycles dark -> light -> colour-blind -> dark and redraws")

problems = []
for name, t in themes.THEMES.items():
    def need(label, a, b, minimum):
        ratio = themes.contrast(a, b)
        if ratio < minimum:
            problems.append("%s: %s is %.1f:1 (needs %.1f)" % (name, label, ratio, minimum))
    need("text on background", t["text"], t["bg"], 7)
    need("text on panels", t["text"], t["panel"], 7)
    need("muted text on panels", t["muted"], t["panel"], 4.5)
    need("muted text on background", t["muted"], t["bg"], 4.5)
    need("button text", t["btn_text"], t["btn"], 4.5)
    need("hot button text", t["btn_text"], t["btn_hot"], 4.5)
    for label in ("accent", "good", "warn", "bad"):
        need(label + " on panel", t[label], t["panel"], 4.5)
        need(label + " on background", t[label], t["bg"], 4.5)
    for value, colour in t["digits"].items():
        if value:
            need("digit %d on an opened slot" % value, colour, t["opened"], 4.5)
    for label in ("p1", "p2"):
        need(label + " ring on a bomb slot", t[label], t["bomb_cell"], 3)
    dist = sum((a - b) ** 2 for a, b in zip(t["p1"], t["p2"])) ** 0.5
    if dist < 120:
        problems.append("%s: the two player colours are too alike (%.0f)" % (name, dist))
assert not problems, "\n" + "\n".join(problems)
ok(17, "every theme meets contrast targets for text, buttons, digits and player colours")

path = os.path.join(tempfile.gettempdir(), "find_my_mines_test_prefs.json")
with open(path, "w", encoding="utf-8") as handle:
    handle.write("{}")
config.PREFS_FILE = path
first = client_mod.ClientUI()
first._next_theme()                             # dark -> light
first._toggle_sound()                           # mute
client_mod.apply_theme("dark")
second = client_mod.ClientUI()
assert client_mod.THEME_NAME == "light" and second.sound.muted
with open(path, "w", encoding="utf-8") as handle:
    handle.write("{ not json")
client_mod.apply_theme("dark")
third = client_mod.ClientUI()
assert client_mod.THEME_NAME == "dark" and not third.sound.muted
config.PREFS_FILE = None
client_mod.apply_theme("dark")
ok(18, "theme and mute are remembered between runs; a damaged file is ignored")

print("\nALL CLIENT-FEATURE CHECKS PASSED")
