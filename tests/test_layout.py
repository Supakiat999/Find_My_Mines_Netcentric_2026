"""Layout audit for the game window and the server console.

Every string drawn in a frame is recorded with its exact on-screen rectangle,
then checked:

  * nothing is drawn outside the window
  * no two pieces of text overlap within the same visual layer
  * no text sits on the board (other than a slot's own number)
  * text never crosses the footer, and never sits on a button it does not label
  * the board fits the window and stays clear of the status text
  * text that belongs to a card or panel stays inside it

Overlays (settings, end of match, reconnecting, toast) are drawn on purpose
over the game, so each opens a fresh layer and is checked against itself.

The client is fed real server payloads: a Server object with stand-in sockets
builds them, and they go through JSON exactly as they would on the wire.
Screenshots of every scenario are written to a temp folder for eyeballing.
"""

import json
import os
import random
import sys
import tempfile
import time

from helpers import ok
import config
config.SERVER_PORT = 55581

import ai
import client as client_mod
import game as game_rules
import protocol
import server as server_mod
import pygame

SHOTS = os.path.join(tempfile.gettempdir(), "find_my_mines_layout")
os.makedirs(SHOTS, exist_ok=True)

LONG_A = "Supakiat_Longnam"      # 16 characters - the longest a nickname can be
LONG_B = "Manusirivithaya"


class DummySock:
    def sendall(self, data):
        pass

    def close(self):
        pass


# ----------------------------------------------------------------------
# recording
# ----------------------------------------------------------------------
class Recorder:
    def __init__(self):
        self.reset()

    def reset(self):
        self.layer = 0
        self.items = []             # (layer, rect, text)

    def add(self, rect, text):
        self.items.append((self.layer, pygame.Rect(rect), str(text)))

    def bump(self, label):
        self.layer += 1


def text_rect(font, s, pos, center=False, right=False):
    w, h = font.size(str(s))
    rect = pygame.Rect(0, 0, w, h)
    if center:
        rect.center = pos
    elif right:
        rect.topright = pos
    else:
        rect.topleft = pos
    return rect


def trim(rect, font):
    """Glyph boxes include line spacing; shave it so touching rows are not
    reported as overlapping."""
    pad = max(1, font.get_height() // 6)
    return rect.inflate(-2, -2 * pad)


# ----------------------------------------------------------------------
# a real server, seated with stand-in players
# ----------------------------------------------------------------------
class Table:
    def __init__(self):
        self.srv = server_mod.Server()

    def seat(self, names, extra=0, away=None):
        srv = self.srv
        srv.clients.clear()
        now = time.time()
        everyone = list(names) + ["Watcher_%02d_long" % k for k in range(extra)]
        for cid, name in enumerate(everyone, start=1):
            rec = server_mod.ClientRecord(
                cid, DummySock(), ("192.168.100.%d" % (100 + cid), 51000 + cid))
            rec.name = name
            rec.connected_at = rec.joined_at = now + cid
            srv.clients[cid] = rec
        if away is not None:
            rec = srv.clients[away]
            rec.alive = False
            rec.away_since = time.time() - 6
        srv._reseat()

    def stage(self, mode, names=("Alice", "Bob"), custom=None, reveal=8,
              extra=0, bot=None, away=None, ended=False):
        srv = self.srv
        srv.bot_level = bot or "off"
        srv.game.rng = random.Random(5)
        if custom:
            srv.game.set_custom(custom)
        srv.game.set_mode(mode)
        self.seat(names[:1] if bot else names, extra, away)
        srv.game.start_match(1)
        srv.turn_deadline = time.time() + 7
        opened = 0
        for cell in list(srv.game.cells()):
            if opened >= reveal or srv.game.phase != game_rules.PHASE_PLAYING:
                break
            srv.game.pick(srv.game.current_turn, cell)
            opened += 1
        if srv.game.phase == game_rules.PHASE_PLAYING:
            srv.game.current_turn = srv.game.players[0]
        if away is not None:
            srv._pause_clock()
        if ended:
            srv.game.phase = game_rules.PHASE_ENDED
            srv.game.current_turn = None
        srv.log.clear()
        return json.loads(json.dumps(srv._state_payload())), \
            json.loads(json.dumps(srv._clients_payload()))


TABLE = Table()
BOARD = [{"name": LONG_A, "wins": 12, "losses": 10, "draws": 1, "points": 240,
          "matches": 23, "best_streak": 4},
         {"name": LONG_B, "wins": 9, "losses": 8, "draws": 0, "points": 180,
          "matches": 17, "best_streak": 3},
         {"name": "Alice", "wins": 5, "losses": 2, "draws": 0, "points": 60,
          "matches": 7, "best_streak": 3},
         {"name": "Bob", "wins": 3, "losses": 3, "draws": 1, "points": 41,
          "matches": 7, "best_streak": 2},
         {"name": "Carol", "wins": 1, "losses": 0, "draws": 0, "points": 9,
          "matches": 1, "best_streak": 1}]


# ----------------------------------------------------------------------
# client
# ----------------------------------------------------------------------
def instrument_client(ui, rec):
    original = ui.text

    def text(s, pos, font=None, color=None, center=False, right=False):
        font_used = font or ui.f_body
        rec.add(trim(text_rect(font_used, s, pos, center, right), font_used), s)
        return original(s, pos, font, color, center=center, right=right)

    ui.text = text
    for name in ("_draw_settings", "_draw_end_overlay", "_draw_toast",
                 "_draw_reconnect_overlay"):
        inner = getattr(ui, name)

        def wrapped(*a, _inner=inner, _name=name, **k):
            rec.bump(_name)
            return _inner(*a, **k)

        setattr(ui, name, wrapped)


def load(ui, state, clients, me=1, role="player"):
    """Put a server payload into the client the way the wire would."""
    ui.reveal_times.clear()
    ui._known = {}
    ui.screen_name = client_mod.SCREEN_GAME
    ui.joined = True
    ui.my_id = me
    ui.role = role
    ui.clients = {"count": clients["count"], "list": clients["list"],
                  "bots": clients.get("bots", [])}
    ui._on_state(state)
    ui.reveal_times.clear()
    ui.role = role
    ui.match_end = None
    ui.settings_open = False
    ui.hint = None
    ui.toast = ""
    ui.reconnect_until = None
    ui.chat_lines = []
    ui.chat_input = ""
    ui.chat_focus = False
    ui.seconds_left = 7


def client_scenarios(ui):
    S = []

    def stage(label, mode, **kw):
        def setup():
            state, clients = TABLE.stage(mode, **kw)
            load(ui, state, clients)
        S.append((label, setup))

    def nickname():
        ui.state = None
        ui.joined = False
        ui.screen_name = client_mod.SCREEN_NICKNAME
        ui.nickname = LONG_A
    S.append(("nickname screen", nickname))

    def error():
        ui.screen_name = client_mod.SCREEN_ERROR
        ui.net.status = "failed"
        ui.net.error = ("[WinError 10061] No connection could be made because "
                        "the target machine actively refused it")
    S.append(("error screen, long reason", error))

    stage("classic waiting", game_rules.MODE_CLASSIC, names=("Alice",), reveal=0)
    stage("classic, long names", game_rules.MODE_CLASSIC, names=(LONG_A, LONG_B),
          reveal=12)
    stage("radius 2", game_rules.MODE_RADIUS2, reveal=14)
    stage("minesweeper", game_rules.MODE_SWEEPER, reveal=6)
    stage("custom avoid", game_rules.MODE_CUSTOM, custom={"goal": "avoid"}, reveal=5)
    stage("3D cube", game_rules.MODE_CUBE, reveal=9)
    for size in (4, 8, 10):
        stage("custom flat %d" % size, game_rules.MODE_CUSTOM,
              custom={"size": size, "bombs": max(1, size * size // 4)},
              names=(LONG_A, LONG_B), reveal=8)
    for size in (3, 5):
        stage("custom cube %d" % size, game_rules.MODE_CUSTOM,
              custom={"shape": "cube", "size": size,
                      "bombs": max(1, size ** 3 // 5)}, reveal=8)
    stage("eight spectators", game_rules.MODE_CLASSIC, names=(LONG_A, LONG_B),
          extra=8, reveal=6)
    stage("playing the computer", game_rules.MODE_CLASSIC, names=(LONG_A,),
          bot="hard", reveal=8)

    def settings():
        state, clients = TABLE.stage(game_rules.MODE_CUSTOM,
                                     custom={"size": 10, "bombs": 30})
        load(ui, state, clients)
        ui.settings_open = True
    S.append(("settings panel", settings))

    def ended(winner, draw=False, spectator=False, bot=False):
        def setup():
            names = (LONG_A,) if bot else (LONG_A, LONG_B)
            state, clients = TABLE.stage(game_rules.MODE_CLASSIC, names=names,
                                         bot="hard" if bot else None,
                                         reveal=20, ended=True)
            load(ui, state, clients, role="spectator" if spectator else "player")
            ids = [p["id"] for p in state["players"]]
            players = [{"id": p["id"], "name": p["name"], "score": p["score"]}
                       for p in state["players"]]
            stats = [{"id": p["id"], "name": p["name"], "score": p["score"],
                      "picks": 27, "best_chain": 4, "rate": 41}
                     for p in state["players"]]
            ui.match_end = {"winner_id": None if draw else ids[winner],
                            "draw": draw, "players": players, "stats": stats,
                            "leaderboard": BOARD}
        return setup
    S.append(("end - you win", ended(0)))
    S.append(("end - you lost", ended(1)))
    S.append(("end - draw", ended(0, draw=True)))
    S.append(("end - spectator", ended(0, spectator=True)))
    S.append(("end - versus computer", ended(1, bot=True)))

    def toast():
        state, clients = TABLE.stage(game_rules.MODE_CLASSIC, reveal=12)
        load(ui, state, clients)
        ui.say("Right-click to lift your flag first and then try again please", 30)
    S.append(("toast showing", toast))

    def paused():
        state, clients = TABLE.stage(game_rules.MODE_CLASSIC,
                                     names=(LONG_A, LONG_B), reveal=8, away=2)
        load(ui, state, clients)
    S.append(("paused, a player is away", paused))

    def reconnecting():
        state, clients = TABLE.stage(game_rules.MODE_CLASSIC, reveal=8)
        load(ui, state, clients)
        ui.reconnect_until = time.time() + 30
    S.append(("reconnecting overlay", reconnecting))

    def chat():
        state, clients = TABLE.stage(game_rules.MODE_CLASSIC, names=(LONG_A, LONG_B),
                                     reveal=8)
        load(ui, state, clients)
        ui.chat_lines = [
            {"id": 1, "name": LONG_A, "text": "hello there how are you doing today "
             "this is a fairly long message that has to wrap", "system": False},
            {"id": 2, "name": LONG_B, "text": "x" * 120, "system": False},
            {"id": 0, "name": "", "text": "Mode is now Radius 2", "system": True},
            {"id": 3, "name": "Watcher", "text": "GG", "system": False},
        ] * 5
        ui.chat_input = "a very long message being typed right now " * 3
        ui.chat_focus = True
    S.append(("busy chat, long input", chat))

    def hall():
        state, clients = TABLE.stage(game_rules.MODE_CLASSIC, names=(LONG_A, LONG_B),
                                     reveal=8)
        state["leaderboard"] = BOARD
        load(ui, state, clients)
    S.append(("full hall of fame", hall))

    def coach(mode, custom=None, label=""):
        def setup():
            state, clients = TABLE.stage(mode, custom=custom, reveal=10)
            load(ui, state, clients)
            info = TABLE.srv.game.public_info()
            advice = ai.advise(info["view"], info["dims"], info["weighted"],
                               info["bombs_left"], info["bombs_are_bad"])
            ui._handle({"type": protocol.HINT_RESULT,
                        "cell": list(advice["cell"]), "p": advice["p"],
                        "exact": advice["exact"],
                        "goal": "avoid" if info["bombs_are_bad"] else "collect",
                        "left": 2,
                        "heat": [{"cell": list(c), "p": p}
                                 for c, p in advice["probs"].items()]})
            ui.hint["opened"] = len(ui._known_open())
        return setup
    S.append(("coach odds, classic", coach(game_rules.MODE_CLASSIC)))
    S.append(("coach odds, cube", coach(game_rules.MODE_CUBE)))
    S.append(("coach odds, custom 10", coach(game_rules.MODE_CUSTOM,
                                             {"size": 10, "bombs": 25})))
    return S


def check_client(ui, rec, label):
    problems = []
    W, H, GW = client_mod.WIN_W, client_mod.WIN_H, client_mod.GAME_W
    window = pygame.Rect(0, 0, W, H)
    texts = list(rec.items)

    for layer, r, t in texts:
        if not window.contains(r):
            problems.append("off-window: %r at %s" % (t, tuple(r)))

    by_layer = {}
    for layer, r, t in texts:
        by_layer.setdefault(layer, []).append((r, t))
    for layer, items in by_layer.items():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                (ra, ta), (rb, tb) = items[i], items[j]
                if ra.colliderect(rb):
                    problems.append("text overlap: %r x %r" % (ta, tb))

    if ui.screen_name == client_mod.SCREEN_GAME:
        cells = [ui.cell_rect(c) for c in ui.board_cells()]
        board = cells[0].unionall(cells[1:])
        centers = {c.center for c in cells}
        footer = pygame.Rect(24, H - 56, GW - 48, 44)
        if not window.contains(board):
            problems.append("board off-window: %s" % (tuple(board),))
        if board.bottom > footer.top - 90:
            problems.append("board too low - no room for the status text")
        buttons = [b for _m, b in ui.mode_rects]
        buttons += [ui.sound_rect, ui.theme_rect] + ([ui.ranked_rect] if hasattr(ui, "ranked_rect") else [])
        buttons += [r for _l, _t, r in ui._opponent_buttons()]
        buttons += list(ui._coach_buttons())
        buttons += ui._chat_rects()[1]
        cards = ui.cards
        for r, t in by_layer.get(0, []):
            # a slot's own label (its number, the coach's odds, the player
            # marker on a bomb) is fine; text spread over the board is not
            if any(r.colliderect(c) for c in cells) and not any(
                    c.contains(r) for c in cells):
                problems.append("text on the board: %r" % t)
            if r.colliderect(footer) and not footer.contains(r):
                problems.append("text crosses the footer: %r" % t)
            for box in buttons:
                if r.colliderect(box) and r.center != box.center:
                    problems.append("text on a button: %r" % t)
            for name, card in cards.items():
                if card.collidepoint(r.center) and not card.contains(r):
                    problems.append("text spills out of the %s card: %r" % (name, t))
        # the two halves of the window must not bleed into each other
        for r, t in by_layer.get(0, []):
            if r.x < GW < r.right:
                problems.append("text straddles the side panel: %r" % t)
    return ["[client] %s: %s" % (label, p) for p in problems]


def audit_client():
    ui = client_mod.ClientUI()
    rec = Recorder()
    instrument_client(ui, rec)
    problems = []
    scenarios = client_scenarios(ui)
    for label, setup in scenarios:
        ui.match_end = None
        ui.settings_open = False
        ui.toast = ""
        ui.role = "player"
        setup()
        rec.reset()
        ui.draw()
        problems += check_client(ui, rec, label)
        pygame.image.save(ui.screen, os.path.join(
            SHOTS, "client_%s.png" % label.replace(" ", "_").replace(",", "")))
    ui.net.close()
    return problems, len(scenarios)


# ----------------------------------------------------------------------
# server console
# ----------------------------------------------------------------------
def instrument_server(ui, rec):
    state = {"panel": None}
    original_text, original_panel = ui.text, ui.panel
    original_slots, original_draw = ui._draw_slots, ui.draw

    def text(s, pos, font=None, color=None, center=False, right=False):
        font_used = font or ui.f_body
        r = trim(text_rect(font_used, s, pos, center, right), font_used)
        rec.items.append((0, r, str(s), state["panel"]))
        kwargs = dict(center=center, right=right)
        if color is not None:
            return original_text(s, pos, font, color, **kwargs)
        return original_text(s, pos, font, **kwargs)

    def panel(rect, title=None):
        state["panel"] = pygame.Rect(rect)
        return original_panel(rect, title)

    def slots(rows, ox, oy, size, gap):
        cols = len(rows[0]) if rows else 0
        box = pygame.Rect(ox, oy, cols * (size + gap) - gap,
                          len(rows) * (size + gap) - gap)
        rec.items.append((0, box, "<slots>", state["panel"]))
        return original_slots(rows, ox, oy, size, gap)

    def draw():
        state["panel"] = None
        return original_draw()

    ui.text, ui.panel, ui._draw_slots, ui.draw = text, panel, slots, draw


def check_server(ui, rec, label):
    problems = []
    W, H = server_mod.WIN_W, server_mod.WIN_H
    window = pygame.Rect(0, 0, W, H)
    texts, boxes = [], []
    for _layer, r, t, owner in rec.items:
        (boxes if t == "<slots>" else texts).append((r, t, owner))

    for r, t, owner in texts:
        if not window.contains(r):
            problems.append("off-window: %r" % t)
        if isinstance(owner, pygame.Rect) and not owner.contains(r):
            problems.append("spills out of its panel: %r" % t)
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            if texts[i][0].colliderect(texts[j][0]):
                problems.append("text overlap: %r x %r" % (texts[i][1], texts[j][1]))
    for box, _t, owner in boxes:
        if not window.contains(box):
            problems.append("board off-window: %s" % (tuple(box),))
        if isinstance(owner, pygame.Rect) and not owner.contains(box):
            problems.append("board spills out of its panel")
        for r, t, _o in texts:
            if r.colliderect(box):
                problems.append("text on the board: %r" % t)
    buttons = [b for _m, b in ui.mode_rects] + [ui.reset_rect]
    for r, t, _o in texts:
        for box in buttons:
            if r.colliderect(box) and r.center != box.center:
                problems.append("text on a button: %r" % t)
    for i, (_m, a) in enumerate(ui.mode_rects):
        if not window.contains(a):
            problems.append("mode button off-window")
        for _m2, b in ui.mode_rects[i + 1:]:
            if a.colliderect(b):
                problems.append("mode buttons overlap")
    return ["[server] %s: %s" % (label, p) for p in problems]


def audit_server():
    srv = TABLE.srv
    ui = server_mod.ServerUI(srv)
    rec = Recorder()
    instrument_server(ui, rec)
    lines = ["Listening on 0.0.0.0:55555  (LAN 192.168.100.147)",
             "Address changed - players must now use 192.168.100.147",
             "%s set 10x10x10, 45 bombs, 60s" % LONG_A,
             "%s cleared 17 slots from (9,9)" % LONG_B,
             "%s found a BOMB at (4,4,4) - keeps the turn" % LONG_A,
             "Network unreachable - still showing 192.168.100.147"]
    scenarios = []
    for mode in game_rules.MODES:
        scenarios.append(("mode %s" % mode, dict(mode=mode)))
    scenarios += [
        ("custom flat 10", dict(mode="custom", custom={"size": 10, "bombs": 40})),
        ("custom flat 4", dict(mode="custom", custom={"size": 4, "bombs": 4})),
        ("custom cube 5", dict(mode="custom", custom={"shape": "cube", "size": 5,
                                                       "bombs": 25})),
        ("long names, crowded", dict(mode="classic", names=(LONG_A, LONG_B),
                                     extra=6)),
        ("match ended", dict(mode="classic", ended=True)),
        ("versus the computer", dict(mode="classic", names=(LONG_A,), bot="hard")),
        ("a player away", dict(mode="classic", names=(LONG_A, LONG_B), away=2)),
    ]
    problems = []
    for label, kw in scenarios:
        TABLE.stage(kw.pop("mode"), **kw)
        for line in lines:
            srv.log.append(time.strftime("%H:%M:%S") + "  " + line)
        rec.reset()
        ui.draw()
        problems += check_server(ui, rec, label)
        pygame.image.save(ui.screen, os.path.join(
            SHOTS, "server_%s.png" % label.replace(" ", "_").replace(",", "")))
    return problems, len(scenarios)


if __name__ == "__main__":
    client_problems, client_count = audit_client()
    server_problems, server_count = audit_server()
    seen = []
    for p in client_problems + server_problems:
        if p not in seen:
            seen.append(p)
    for p in seen:
        print(p)
    print("screenshots in", SHOTS)
    if seen:
        print("\n%d distinct layout problems" % len(seen))
        sys.exit(1)
    ok(1, "no overlaps, spills or off-window text across %d client and %d "
          "server scenarios" % (client_count, server_count))
    print("\nALL LAYOUT CHECKS PASSED")
