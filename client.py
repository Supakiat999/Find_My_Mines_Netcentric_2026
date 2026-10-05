"""Find My Mines - game client.

Connects to the server named in config.py (the player never types an address),
asks for a nickname, then draws the board the server sends.

The client is deliberately dumb: it sends "I clicked here" and draws whatever
state comes back.  It never decides what is a bomb, whose turn it is, or how
much time is left - the server owns all of that.

Threading model
    reader thread : blocking recv, pushes decoded messages onto a queue
    main thread   : pygame loop - drains the queue, draws, sends clicks

The window is drawn at one fixed logical size onto an off-screen canvas, then
scaled to whatever window the desktop can fit, so it works on small laptop
screens and can be resized freely.

Run:  python client.py
"""

import json
import os
import queue
import socket
import sys
import threading
import time

import pygame

import config
import game as game_rules
import protocol
import themes
from sound import SoundBank

GAME_W = 860                      # the board column
SIDE_W = 340                      # opponent, coach, hall of fame, chat
WIN_W, WIN_H = GAME_W + SIDE_W, 880
CX = GAME_W // 2                  # centre of the board column
FPS = 30

SCREEN_NICKNAME = "nickname"
SCREEN_GAME = "game"
SCREEN_ERROR = "error"

CELL = 68
GAP = 8
BOARD_TOP = 252                   # where a flat board starts
BOARD_LIMIT = 700                 # ...and the lowest it may reach
CUBE_TOP = 320

RECONNECT_WINDOW = config.RECONNECT_GRACE + 8      # how long we keep trying
REVEAL_SECONDS = 0.30

# The custom game's controls: steppers first, then either/or choices.
CUSTOM_STEPS = [
    ("Board size", "size", 1),
    ("Bombs", "bombs", 1),
    ("Seconds per turn", "turn_seconds", 5),
]
CUSTOM_CHOICES = [
    ("Shape", "shape", [("flat", "Flat"), ("cube", "Cube")]),
    ("Hints", "hints", [("simple", "Touching"), ("radius2", "Two rings")]),
    ("Bombs are", "goal", [("collect", "Points"), ("avoid", "Hazards")]),
]

OPPONENTS = [("off", "Player"), ("easy", "Easy"), ("medium", "Medium"),
             ("hard", "Hard")]
QUICK_CHAT = ["GG", "Nice!", "Oops", "Again?"]


# ----------------------------------------------------------------------
# themes
# ----------------------------------------------------------------------
def apply_theme(name):
    """Point the colour names at a palette.  Drawing code reads these names
    each frame, so switching takes effect on the next draw."""
    theme = themes.THEMES.get(name) or themes.DARK
    g = globals()
    g["THEME_NAME"] = name if name in themes.THEMES else "dark"
    g["THEME_LABEL"] = theme["label"]
    g["BG"] = theme["bg"]
    g["PANEL"] = theme["panel"]
    g["PANEL_2"] = theme["panel2"]
    g["LINE"] = theme["line"]
    g["TEXT"] = theme["text"]
    g["MUTED"] = theme["muted"]
    g["ACCENT"] = theme["accent"]
    g["GOOD"] = theme["good"]
    g["WARN"] = theme["warn"]
    g["BAD"] = theme["bad"]
    g["COVERED"] = theme["covered"]
    g["COVERED_HOVER"] = theme["covered_hover"]
    g["OPENED"] = theme["opened"]
    g["BOMB_CELL"] = theme["bomb_cell"]
    g["BTN"] = theme["btn"]
    g["BTN_HOT"] = theme["btn_hot"]
    g["BTN_TEXT"] = theme["btn_text"]
    g["VEIL"] = theme["veil"]
    g["VEIL_ALPHA"] = theme["veil_alpha"]
    g["P1"] = theme["p1"]
    g["P2"] = theme["p2"]
    g["DIGIT_COLOURS"] = theme["digits"]


apply_theme("dark")


# ----------------------------------------------------------------------
# remembered choices
# ----------------------------------------------------------------------
def _prefs_path():
    if config.PREFS_FILE == "auto":
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "client_prefs.json")
    return config.PREFS_FILE


def load_prefs():
    path = _prefs_path()
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_prefs(prefs):
    path = _prefs_path()
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(prefs, handle)
    except OSError:
        pass


def flatten_board(board, dims):
    """Nested board lists -> {slot: value}."""
    cells = {}
    try:
        if len(dims) == 3:
            for l, layer in enumerate(board):
                for r, row in enumerate(layer):
                    for c, value in enumerate(row):
                        cells[(l, r, c)] = value
        else:
            for r, row in enumerate(board):
                for c, value in enumerate(row):
                    cells[(r, c)] = value
    except TypeError:
        pass
    return cells


class NetworkClient:
    """One TCP connection to the server, read on its own thread."""

    def __init__(self):
        self.inbox = queue.Queue()
        self.sock = None
        self.status = "connecting"   # connecting | connected | failed | lost
        self.error = ""

    @property
    def address(self):
        return "%s:%d" % (config.SERVER_HOST, config.SERVER_PORT)

    def connect_async(self):
        self.status = "connecting"
        self.error = ""
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.sock = socket.create_connection(
                (config.SERVER_HOST, config.SERVER_PORT), timeout=5)
            self.sock.settimeout(None)          # back to blocking for recv
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as exc:
            self.error = str(exc)
            self.status = "failed"
            return
        self.status = "connected"
        for msg in protocol.MessageReader(self.sock).messages():
            self.inbox.put(msg)
        self.status = "lost"                    # server closed or link dropped

    def send(self, msg_type, **payload):
        if self.sock is not None and self.status == "connected":
            protocol.send(self.sock, msg_type, **payload)

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass


class ClientUI:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Find My Mines")
        self.prefs = load_prefs()
        apply_theme(self.prefs.get("theme", "dark"))

        # Draw everything on a fixed-size canvas and scale it to the window,
        # so a small laptop screen still shows the whole game.
        info = pygame.display.Info()
        desk_w, desk_h = info.current_w, info.current_h
        scale = 1.0
        if desk_w > 0 and desk_h > 0:
            scale = min(1.0, (desk_h - 90) / WIN_H, (desk_w - 30) / WIN_W)
            scale = max(0.5, scale)
        self.window = pygame.display.set_mode(
            (int(WIN_W * scale), int(WIN_H * scale)), pygame.RESIZABLE)
        self.screen = pygame.Surface((WIN_W, WIN_H))
        self._view = (0, 0, scale)           # canvas offset x, y and scale
        self.clock = pygame.time.Clock()

        self.f_title = self._font(40, bold=True)
        self.f_clock = self._font(34, bold=True)
        self.f_head = self._font(22, bold=True)
        self.f_body = self._font(18)
        self.f_small = self._font(15)
        self.f_card = self._font(15, bold=True)
        self.f_tiny = self._font(12, bold=True)
        self.f_cell = self._font(30, bold=True)
        self.f_cell_m = self._font(22, bold=True)
        self.f_cell_s = self._font(16, bold=True)
        self.f_huge = self._font(52, bold=True)

        self.sound = SoundBank(muted=bool(self.prefs.get("muted", False)))

        self.net = NetworkClient()
        self.net.connect_async()

        self.screen_name = SCREEN_NICKNAME
        self.nickname = ""
        self.my_id = None
        self.role = None
        self.token = None                    # proves who we are on reconnect
        self.joined = False
        self.reconnect_until = None
        self.next_retry = 0.0
        self.rejoin_pending = False
        self.welcome = ""
        self.welcome_dims = None             # board shape, until the first state
        self.clients = {"count": 0, "list": [], "bots": []}
        self.state = None
        self.seconds_left = 0
        self.match_end = None
        self.voted_rematch = False
        self.toast = ""
        self.toast_until = 0
        self.running = True

        self.chat_lines = []
        self.chat_input = ""
        self.chat_focus = False
        self.chat_scroll = 0
        self.leaderboard = []
        self.hint = None                     # the coach's last answer
        self.show_odds = True
        self.reveal_times = {}               # slot -> when it was opened
        self._known = {}                     # slot -> value at the last state

        self.mode_rects = self._mode_rects()
        self.settings_open = False
        self.cards = self._side_layout()
        self.rematch_rect = pygame.Rect(CX - 100, 601, 200, 52)
        self.sound_rect = pygame.Rect(24, 20, 112, 32)
        self.ranked_rect = pygame.Rect(144, 20, 130, 32)
        self.theme_rect = pygame.Rect(GAME_W - 24 - 140, 20, 140, 32)
        self.match_end_time = 0.0

    # ------------------------------------------------------------------
    # small helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _font(size, bold=False):
        for name in ("Segoe UI", "Arial", "DejaVu Sans"):
            try:
                return pygame.font.SysFont(name, size, bold=bold)
            except Exception:
                continue
        return pygame.font.Font(None, size)

    @staticmethod
    def _mode_rects():
        """A button per mode, centred under the title."""
        width, gap = 116, 8
        total = len(game_rules.MODES) * width + (len(game_rules.MODES) - 1) * gap
        x = (GAME_W - total) // 2
        rects = []
        for mode in game_rules.MODES:
            rects.append((mode, pygame.Rect(x, 58, width, 30)))
            x += width + gap
        return rects

    def text(self, s, pos, font=None, color=None, center=False, right=False):
        surf = (font or self.f_body).render(str(s), True, TEXT if color is None
                                            else color)
        rect = surf.get_rect()
        if center:
            rect.center = pos
        elif right:
            rect.topright = pos
        else:
            rect.topleft = pos
        self.screen.blit(surf, rect)
        return rect

    def fit(self, s, font, max_width):
        """Shorten with '...' so a line never leaves its box."""
        s = str(s)
        if font.size(s)[0] <= max_width:
            return s
        while s and font.size(s + "...")[0] > max_width:
            s = s[:-1]
        return s + "..."

    def wrap(self, s, font, width):
        """Break text into lines no wider than `width`."""
        lines, current = [], ""
        for word in str(s).split(" "):
            trial = (current + " " + word).strip()
            if font.size(trial)[0] <= width:
                current = trial
                continue
            if current:
                lines.append(current)
            while font.size(word)[0] > width and len(word) > 1:
                cut = len(word)
                while cut > 1 and font.size(word[:cut])[0] > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        if current:
            lines.append(current)
        return lines or [""]

    def say(self, message, seconds=2.5):
        self.toast = message
        self.toast_until = pygame.time.get_ticks() + int(seconds * 1000)

    def mouse(self):
        """The pointer, in canvas coordinates."""
        return self._to_canvas(pygame.mouse.get_pos())

    def _to_canvas(self, pos):
        ox, oy, scale = self._view
        return (int((pos[0] - ox) / scale), int((pos[1] - oy) / scale))

    def _button(self, rect, label, active=False, enabled=True, small=True):
        """A themed button; returns nothing - hit-testing is done separately."""
        hot = enabled and rect.collidepoint(self.mouse())
        if active:
            fill = BTN_HOT if hot else BTN
        else:
            fill = PANEL_2 if hot else PANEL
        pygame.draw.rect(self.screen, fill, rect, border_radius=8)
        pygame.draw.rect(self.screen, ACCENT if active else LINE, rect, width=1,
                         border_radius=8)
        colour = BTN_TEXT if active else (TEXT if enabled else MUTED)
        self.text(self.fit(label, self.f_small if small else self.f_body,
                           rect.width - 10),
                  rect.center, self.f_small if small else self.f_body, colour,
                  center=True)

    def _card(self, rect, title):
        pygame.draw.rect(self.screen, PANEL, rect, border_radius=12)
        pygame.draw.rect(self.screen, LINE, rect, width=1, border_radius=12)
        self.text(title, (rect.x + 14, rect.y + 12), self.f_card, MUTED)

    def _remember(self):
        self.prefs["theme"] = THEME_NAME
        self.prefs["muted"] = self.sound.muted
        save_prefs(self.prefs)

    # ------------------------------------------------------------------
    # connection upkeep
    # ------------------------------------------------------------------
    def _maintain_connection(self):
        """If the link drops mid-game, keep trying to get back to the seat."""
        now = time.time()
        if self.screen_name != SCREEN_GAME or not self.joined:
            return
        if self.net.status in ("lost", "failed"):
            if self.reconnect_until is None:
                self.reconnect_until = now + RECONNECT_WINDOW
                self.next_retry = now + 0.4
                self.net.close()
            if now > self.reconnect_until:
                self.reconnect_until = None
                self.screen_name = SCREEN_ERROR
                return
            if now >= self.next_retry:
                self.net = NetworkClient()
                self.net.connect_async()
                self.rejoin_pending = True
                self.next_retry = now + 2.0
        elif self.net.status == "connected" and self.rejoin_pending:
            self.rejoin_pending = False
            self.net.send(protocol.JOIN, nickname=self.nickname, token=self.token)

    @property
    def reconnecting(self):
        return self.reconnect_until is not None

    # ------------------------------------------------------------------
    # incoming messages
    # ------------------------------------------------------------------
    def pump_network(self):
        self._maintain_connection()
        if (self.net.status in ("failed", "lost") and not self.joined
                and self.screen_name != SCREEN_ERROR):
            self.screen_name = SCREEN_ERROR
        while True:
            try:
                msg = self.net.inbox.get_nowait()
            except queue.Empty:
                return
            self._handle(msg)

    def _handle(self, msg):
        kind = msg.get("type")
        if kind == protocol.WELCOME:
            self.my_id = msg.get("client_id")
            self.role = msg.get("role")
            self.token = msg.get("token") or self.token
            self.welcome = msg.get("message", "")
            self.welcome_dims = msg.get("dims")
            self.joined = True
            self.reconnect_until = None
            self.rejoin_pending = False
            self.screen_name = SCREEN_GAME
            self.leaderboard = msg.get("leaderboard", self.leaderboard)
            self.chat_lines = list(msg.get("chat", []))[-60:]
            self.chat_scroll = 0
            self.say(self.welcome, 4)
        elif kind == protocol.CLIENTS:
            self.clients = {"count": msg.get("count", 0),
                            "list": msg.get("list", []),
                            "bots": msg.get("bots", [])}
        elif kind == protocol.STATE:
            self._on_state(msg)
        elif kind == protocol.TICK:
            self.seconds_left = msg.get("seconds_left", 0)
            if (self.seconds_left <= 3 and self.my_turn
                    and self.phase == game_rules.PHASE_PLAYING):
                self.sound.play("tick")
        elif kind == protocol.MATCH_END:
            self.match_end = msg
            self.match_end_time = time.time()
            self.leaderboard = msg.get("leaderboard", self.leaderboard)
            if self.role != "player" or msg.get("draw"):
                self.sound.play("draw")
            elif msg.get("winner_id") == self.my_id:
                self.sound.play("win")
            else:
                self.sound.play("lose")
        elif kind == protocol.SERVER_RESET:
            self.match_end = None
            self.voted_rematch = False
            self.hint = None
            self.reveal_times.clear()
            self._known = {}
            self.say("The server reset the game", 3)
        elif kind == protocol.CHAT_MSG:
            self.chat_lines.append(msg)
            del self.chat_lines[:-60]
            self.chat_scroll = 0
            if not msg.get("system") and msg.get("id") != self.my_id:
                self.sound.play("chat")
        elif kind == protocol.HINT_RESULT:
            heat = {tuple(h["cell"]): h["p"] for h in msg.get("heat", [])}
            self.hint = {"cell": tuple(msg["cell"]), "p": msg.get("p", 0.0),
                         "goal": msg.get("goal", "collect"),
                         "exact": msg.get("exact", True),
                         "left": msg.get("left", 0), "heat": heat,
                         "opened": len(self._known_open())}
            self.sound.play("hint")
        elif kind == protocol.ERROR:
            self.say(msg.get("message", "not allowed"))
            self.sound.play("error")

    def _known_open(self):
        return [c for c, v in self._known.items() if v is not None]

    def _on_state(self, msg):
        old_cells = self._known
        self.state = msg
        self.seconds_left = msg.get("seconds_left", 0)
        if msg.get("phase") != game_rules.PHASE_ENDED:
            self.match_end = None
            self.voted_rematch = False
        self.role = self._my_role()
        self.leaderboard = msg.get("leaderboard", self.leaderboard)

        cells = flatten_board(msg.get("board", []), tuple(msg.get("dims", ())))
        fresh = [c for c, v in cells.items()
                 if v is not None and old_cells.get(c) is None]
        if len(fresh) == len(cells) and cells:
            fresh = []                     # first look at a finished board
        now = time.monotonic()
        if len(old_cells) != len(cells):
            self.reveal_times.clear()      # a different board altogether
            fresh = [] if not old_cells else fresh
        for c in fresh:
            self.reveal_times[c] = now
        if fresh:
            bad = bool(msg.get("bombs_are_bad"))
            if any(cells[c] == game_rules.BOMB for c in fresh):
                self.sound.play("boom" if bad else "bomb")
            else:
                self.sound.play("click")
        self._known = cells
        if self.hint and len(self._known_open()) != self.hint["opened"]:
            self.hint = None               # the board changed: advice is stale

        prev = getattr(self, "_prev_turn", None)
        turn = msg.get("current_turn")
        if (turn == self.my_id and prev != self.my_id
                and msg.get("phase") == game_rules.PHASE_PLAYING):
            self.sound.play("turn")
        self._prev_turn = turn

    def _my_role(self):
        for c in self.clients.get("list", []):
            if c.get("id") == self.my_id:
                return c.get("role")
        return self.role

    # ------------------------------------------------------------------
    # state helpers
    # ------------------------------------------------------------------
    @property
    def phase(self):
        return (self.state or {}).get("phase", game_rules.PHASE_WAITING)

    @property
    def board(self):
        return (self.state or {}).get("board", [])

    @property
    def players(self):
        return (self.state or {}).get("players", [])

    @property
    def my_turn(self):
        return (self.state or {}).get("current_turn") == self.my_id

    @property
    def paused(self):
        return bool((self.state or {}).get("paused"))

    @property
    def bombs_are_bad(self):
        return bool((self.state or {}).get("bombs_are_bad"))

    @property
    def dims(self):
        """Board shape the server is playing: (rows, cols), or (layers, ...)."""
        shape = (self.state or {}).get("dims") or self.welcome_dims
        return tuple(shape) if shape else (config.GRID_SIZE, config.GRID_SIZE)

    @property
    def is_3d(self):
        return len(self.dims) == 3

    @property
    def mode(self):
        return (self.state or {}).get("mode", config.DEFAULT_MODE)

    @property
    def flags(self):
        """Markers, keyed by slot: the value is whoever planted it."""
        return {tuple(f["cell"]): f["by"]
                for f in (self.state or {}).get("flags", [])}

    @property
    def bomb_owners(self):
        return {tuple(f["cell"]): f["by"]
                for f in (self.state or {}).get("bomb_owners", [])}

    @property
    def last_move_cell(self):
        move = (self.state or {}).get("last_move")
        return (tuple(move["cell"]), move["by"]) if move else (None, None)

    def board_cells(self):
        if self.is_3d:
            layers, rows, cols = self.dims
            return [(l, r, c) for l in range(layers)
                    for r in range(rows) for c in range(cols)]
        rows, cols = self.dims
        return [(r, c) for r in range(rows) for c in range(cols)]

    def value_at(self, cell):
        """What the server says is in a slot: None, "bomb", or a number."""
        board = self.board
        try:
            if self.is_3d:
                layer, row, col = cell
                return board[layer][row][col]
            row, col = cell
            return board[row][col]
        except (IndexError, KeyError, TypeError):
            return None

    def _geometry(self):
        """Slot size and origin for whichever board is in play.

        Returns (size, gap, layer_gap, left, top, layer_width).  The cube is
        laid out as its layers side by side so the whole board is clickable
        at once.  Slots shrink rather than let a big board run off the window
        or into the status text beneath it.
        """
        if self.is_3d:
            layers, rows, cols = self.dims
            gap, layer_gap = 6, 18
            room = GAME_W - 48 - (layers - 1) * layer_gap
            size = max(16, min(42, room // (layers * cols) - gap))
            layer_w = cols * (size + gap) - gap
            total = layers * layer_w + (layers - 1) * layer_gap
            return size, gap, layer_gap, (GAME_W - total) // 2, CUBE_TOP, layer_w
        rows, cols = self.dims
        fit_h = (BOARD_LIMIT - BOARD_TOP + GAP) // rows - GAP
        fit_w = (GAME_W - 80 + GAP) // cols - GAP
        size = max(26, min(CELL, fit_h, fit_w))
        total = cols * (size + GAP) - GAP
        return size, GAP, 0, (GAME_W - total) // 2, BOARD_TOP, total

    def cell_rect(self, cell):
        size, gap, layer_gap, ox, oy, layer_w = self._geometry()
        if self.is_3d:
            layer, row, col = cell
            x = ox + layer * (layer_w + layer_gap) + col * (size + gap)
        else:
            row, col = cell
            x = ox + col * (size + gap)
        return pygame.Rect(x, oy + row * (size + gap), size, size)

    def board_bottom(self):
        size, gap, _, _, oy, _ = self._geometry()
        return oy + self.dims[-2] * (size + gap) - gap

    def can_click(self, cell):
        return (self.role == "player"
                and self.phase == game_rules.PHASE_PLAYING
                and self.my_turn
                and not self.paused and not self.reconnecting
                and self.value_at(cell) is None
                and self.flags.get(cell) != self.my_id)

    def cell_at(self, pos):
        for cell in self.board_cells():
            if self.cell_rect(cell).collidepoint(pos):
                return cell
        return None

    def _send_cell(self, msg_type, cell):
        if self.is_3d:
            layer, row, col = cell
            self.net.send(msg_type, layer=layer, row=row, col=col)
        else:
            row, col = cell
            self.net.send(msg_type, row=row, col=col)

    # ------------------------------------------------------------------
    # main loop and input
    # ------------------------------------------------------------------
    def run(self):
        while self.running:
            for event in pygame.event.get():
                self._on_event(event)
            self.pump_network()
            self.draw()
            self.clock.tick(FPS)
        self.net.close()
        pygame.quit()

    def _map_event(self, event):
        """Window coordinates -> canvas coordinates for mouse events."""
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP,
                          pygame.MOUSEMOTION):
            fields = dict(event.dict)
            fields["pos"] = self._to_canvas(event.pos)
            return pygame.event.Event(event.type, fields)
        return event

    def _on_event(self, event):
        if event.type == pygame.QUIT:
            self.running = False
            return
        event = self._map_event(event)

        if self.screen_name == SCREEN_NICKNAME:
            self._on_nickname_event(event)
        elif self.screen_name == SCREEN_GAME:
            if event.type == pygame.KEYDOWN:
                self._on_game_key(event)
            elif event.type == pygame.MOUSEWHEEL:
                self._on_wheel(event)
            else:
                self._on_game_event(event)
        elif self.screen_name == SCREEN_ERROR:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_r:
                self.net.close()
                self.net = NetworkClient()
                self.net.connect_async()
                self.joined = False
                self.reconnect_until = None
                self.screen_name = SCREEN_NICKNAME

    def _on_nickname_event(self, event):
        if event.type != pygame.KEYDOWN:
            return
        if event.key == pygame.K_RETURN:
            if self.nickname.strip() and self.net.status == "connected":
                self.net.send(protocol.JOIN, nickname=self.nickname.strip())
        elif event.key == pygame.K_BACKSPACE:
            self.nickname = self.nickname[:-1]
        elif event.key == pygame.K_ESCAPE:
            self.running = False
        elif event.unicode and event.unicode.isprintable() and len(self.nickname) < 16:
            self.nickname += event.unicode

    def _on_wheel(self, event):
        log, _quick, _input = self._chat_rects()
        if log.collidepoint(self.mouse()):
            self.chat_scroll = max(0, self.chat_scroll + event.y * 2)

    def _toggle_sound(self):
        self.sound.toggle()
        self._remember()
        self.say("Sound off" if self.sound.muted else "Sound on", 1.5)

    def _toggle_ranked(self):
        if self.role != "player":
            self.say("Only players can change match mode", 1.5)
            return
        curr = (self.state or {}).get("ranked", True)
        self.net.send(protocol.SET_RANKED, ranked=not curr)

    def _next_theme(self):
        apply_theme(themes.next_theme(THEME_NAME))
        self._remember()
        self.say("Theme: %s" % THEME_LABEL, 1.5)

    def _ask_coach(self):
        if self.role != "player":
            self.say("Only players can ask the coach")
        elif self.phase != game_rules.PHASE_PLAYING:
            self.say("The coach helps during a match")
        elif not self.my_turn:
            self.say("Ask the coach on your own turn")
        else:
            self.net.send(protocol.HINT)

    def _send_chat(self, text):
        text = text.strip()
        if text:
            self.net.send(protocol.CHAT, text=text)

    def _on_game_key(self, event):
        if self.chat_focus:
            if event.key == pygame.K_RETURN:
                self._send_chat(self.chat_input)
                self.chat_input = ""
                self.chat_focus = False
            elif event.key == pygame.K_ESCAPE:
                self.chat_focus = False
            elif event.key == pygame.K_BACKSPACE:
                self.chat_input = self.chat_input[:-1]
            elif event.unicode and event.unicode.isprintable() and len(self.chat_input) < 120:
                self.chat_input += event.unicode
            return
        if event.key == pygame.K_RETURN:
            self.chat_focus = True
        elif event.key == pygame.K_m:
            self._toggle_sound()
        elif event.key == pygame.K_t:
            self._next_theme()
        elif event.key == pygame.K_h:
            self._ask_coach()

    def _on_game_event(self, event):
        if event.type != pygame.MOUSEBUTTONDOWN or event.button not in (1, 3):
            return
        if (self.chat_focus and event.button == 1
                and not self._chat_rects()[2].collidepoint(event.pos)):
            self.chat_focus = False          # clicking anywhere else stops typing
        if event.button == 1 and self._side_click(event.pos):
            return
        if event.button == 1:
            if self.sound_rect.collidepoint(event.pos):
                self._toggle_sound()
                return
            if self.ranked_rect.collidepoint(event.pos):
                self._toggle_ranked()
                return
            if self.theme_rect.collidepoint(event.pos):
                self._next_theme()
                return
        if self.reconnecting:
            return
        if self.match_end is not None:
            if (event.button == 1 and not self.voted_rematch
                    and self.rematch_rect.collidepoint(event.pos)):
                if self.role == "player":
                    self.net.send(protocol.REMATCH)
                    self.voted_rematch = True
            return
        if self._settings_click(event):
            return

        if event.button == 1:
            for mode, box in self.mode_rects:
                if box.collidepoint(event.pos):
                    if self.role != "player":
                        self.say("Only players can change the mode")
                    elif mode != self.mode:
                        self.net.send(protocol.SET_MODE, mode=mode)
                        self.settings_open = mode == game_rules.MODE_CUSTOM
                    elif mode == game_rules.MODE_CUSTOM:
                        self.settings_open = not self.settings_open
                    return

        hit = self.cell_at(event.pos)
        if hit is None:
            return

        if event.button == 3:
            # Right-click marks a slot.  A marker only blocks the player who
            # planted it, so it is a note to yourself, not a wall.
            if self.role == "player" and self.value_at(hit) is None:
                self._send_cell(protocol.FLAG, hit)
            return

        if self.can_click(hit):
            self._send_cell(protocol.PICK, hit)
        elif self.paused:
            self.say("Paused - waiting for a player to reconnect")
        elif self.flags.get(hit) == self.my_id:
            self.say("Right-click to lift your flag first")
        elif self.role != "player":
            self.say("You are watching this match")
        elif self.phase != game_rules.PHASE_PLAYING:
            self.say("No match in progress")
        elif not self.my_turn:
            self.say("Not your turn")
        else:
            self.say("That slot is already open")

    # ------------------------------------------------------------------
    # the side panel: opponent, coach, hall of fame, chat
    # ------------------------------------------------------------------
    def _side_layout(self):
        x, w = GAME_W + 12, SIDE_W - 24
        return {
            "opponent": pygame.Rect(x, 16, w, 112),
            "coach": pygame.Rect(x, 140, w, 152),
            "board": pygame.Rect(x, 304, w, 150),
            "chat": pygame.Rect(x, 466, w, WIN_H - 16 - 466),
        }

    def _opponent_buttons(self):
        card = self.cards["opponent"]
        bw = (card.w - 24 - 3 * 6) // 4
        return [(level, label,
                 pygame.Rect(card.x + 12 + i * (bw + 6), card.y + 42, bw, 30))
                for i, (level, label) in enumerate(OPPONENTS)]

    def _coach_buttons(self):
        card = self.cards["coach"]
        ask = pygame.Rect(card.x + 12, card.y + 40, 176, 34)
        odds = pygame.Rect(ask.right + 8, card.y + 40, card.w - 24 - 176 - 8, 34)
        return ask, odds

    def _chat_rects(self):
        card = self.cards["chat"]
        log = pygame.Rect(card.x + 12, card.y + 38, card.w - 24, card.h - 38 - 92)
        bw = (card.w - 24 - 3 * 6) // 4
        quick = [pygame.Rect(card.x + 12 + i * (bw + 6), card.bottom - 86, bw, 28)
                 for i in range(4)]
        field = pygame.Rect(card.x + 12, card.bottom - 48, card.w - 24, 36)
        return log, quick, field

    def _side_click(self, pos):
        """Handle a click in the side panel.  True when it landed there."""
        if pos[0] < GAME_W:
            return False
        for level, _label, rect in self._opponent_buttons():
            if rect.collidepoint(pos):
                if self.role != "player":
                    self.say("Only players can pick an opponent")
                else:
                    self.net.send(protocol.SET_BOT, level=level)
                return True
        ask, odds = self._coach_buttons()
        if ask.collidepoint(pos):
            self._ask_coach()
            return True
        if odds.collidepoint(pos):
            self.show_odds = not self.show_odds
            return True
        _log, quick, field = self._chat_rects()
        for text, rect in zip(QUICK_CHAT, quick):
            if rect.collidepoint(pos):
                self._send_chat(text)
                return True
        if field.collidepoint(pos):
            self.chat_focus = True
            return True
        self.chat_focus = False
        return True

    def _my_hints_left(self):
        for p in self.players:
            if p["id"] == self.my_id:
                return p.get("hints_left", 0)
        return 0

    def _draw_side(self):
        self._draw_opponent()
        self._draw_coach()
        self._draw_hall()
        self._draw_chat()

    def _draw_opponent(self):
        card = self.cards["opponent"]
        self._card(card, "OPPONENT")
        level = (self.state or {}).get("bot_level", "off")
        for value, label, rect in self._opponent_buttons():
            self._button(rect, label, active=value == level)
        seated = (self.state or {}).get("bot_seated")
        if seated:
            name = next((p["name"] for p in self.players if p.get("bot")), "Computer")
            line = "Playing against %s" % name
        elif len(self.players) >= 2:
            line = "Playing against another person"
        else:
            line = "Waiting for a friend - or pick a level"
        self.text(self.fit(line, self.f_small, card.w - 28),
                  (card.x + 14, card.y + 84), self.f_small, MUTED)

    def _draw_coach(self):
        card = self.cards["coach"]
        self._card(card, "AI COACH")
        left = self._my_hints_left()
        ask, odds = self._coach_buttons()
        self._button(ask, "Ask the coach (%d left)" % left,
                     enabled=left > 0 and self.my_turn)
        self._button(odds, "Odds: on" if self.show_odds else "Odds: off",
                     active=self.show_odds)
        y = card.y + 88
        if self.hint:
            cell = self.hint["cell"]
            names = ("layer", "row", "col") if self.is_3d else ("row", "col")
            where = ", ".join("%s %d" % (n, v + 1) for n, v in zip(names, cell))
            avoid = self.hint["goal"] == "avoid"
            chance = round(100 * (1 - self.hint["p"] if avoid else self.hint["p"]))
            lines = ["Best: %s" % where,
                     "%s chance: %d%%" % ("Safe" if avoid else "Bomb", chance),
                     "" if self.hint["exact"] else "(an estimate - big board)"]
        else:
            lines = ["Ask on your turn to see the",
                     "best slot and the odds for",
                     "every covered slot."]
        for line in lines:
            if line:
                self.text(self.fit(line, self.f_small, card.w - 28),
                          (card.x + 14, y), self.f_small,
                          TEXT if self.hint else MUTED)
            y += 18

    def _draw_hall(self):
        card = self.cards["board"]
        mode_label = (self.state or {}).get("mode_label")
        mode = (self.state or {}).get("mode", "classic")
        ranked_modes = getattr(config, "ELO_RANKED_MODES", ("classic", "radius2", "sweeper", "cube"))
        if mode in ranked_modes and mode_label:
            title = self.fit(("HALL OF FAME - %s" % mode_label).upper(), self.f_card, card.w - 28)
        else:
            title = "HALL OF FAME"
        self._card(card, title)
        if not self.leaderboard:
            self.text("Win a match to get on the board.",
                      (card.x + 14, card.y + 44), self.f_small, MUTED)
            return
        y = card.y + 40
        for rank, row in enumerate(self.leaderboard[:5], start=1):
            mine = row["name"] == next((p["name"] for p in self.players
                                        if p["id"] == self.my_id), None)
            self.text("%d" % rank, (card.x + 14, y), self.f_small, MUTED)
            elo_val = row.get("elo")
            peak_val = row.get("peak_elo")
            tier_name = row.get("tier", "Bronze")
            tier_col = row.get("tier_color", WARN)
            if elo_val is not None:
                if peak_val is not None:
                    stat_str = "%s %d (Pk %d)  %dW %dL" % (tier_name[:4].upper(), elo_val, peak_val, row["wins"], row["losses"])
                else:
                    stat_str = "%s %d  %dW %dL" % (tier_name[:4].upper(), elo_val, row["wins"], row["losses"])
            else:
                stat_str = "%dW %dL  %d" % (row["wins"], row["losses"], row["points"])
            stat_w = self.f_small.size(stat_str)[0]
            name_max_w = max(40, (card.right - 14) - stat_w - (card.x + 34) - 6)
            self.text(self.fit(row["name"], self.f_small, name_max_w),
                      (card.x + 34, y), self.f_small, GOOD if mine else TEXT)
            self.text(stat_str, (card.right - 14, y), self.f_small, tier_col if elo_val else WARN, right=True)
            y += 22

    def _chat_lines_wrapped(self, width):
        """Every chat message, wrapped, as (prefix, prefix_colour, text) rows."""
        order = {p["id"]: i for i, p in enumerate(self.players)}
        rows = []
        for entry in self.chat_lines:
            if entry.get("system"):
                for line in self.wrap(entry["text"], self.f_small, width):
                    rows.append(("", MUTED, line))
                continue
            index = order.get(entry.get("id"))
            colour = (P1, P2)[index] if index in (0, 1) else ACCENT
            prefix = "%s: " % entry["name"]
            lines = self.wrap(prefix + entry["text"], self.f_small, width)
            first = lines[0]
            if first.startswith(prefix):
                rows.append((prefix, colour, first[len(prefix):]))
            else:
                rows.append(("", colour, first))
            rows.extend(("", colour, line) for line in lines[1:])
        return rows

    def _draw_chat(self):
        card = self.cards["chat"]
        self._card(card, "CHAT")
        hint = "Enter to type" if not self.chat_focus else "Enter sends - Esc cancels"
        self.text(hint, (card.right - 14, card.y + 12), self.f_small, MUTED,
                  right=True)
        log, quick, field = self._chat_rects()
        line_h = 19
        rows = self._chat_lines_wrapped(log.width)
        capacity = log.height // line_h
        end = len(rows) - self.chat_scroll
        end = max(min(end, len(rows)), min(len(rows), capacity))
        window = rows[max(0, end - capacity):end]
        y = log.y
        for prefix, colour, body in window:
            width = 0
            if prefix:
                width = self.text(prefix, (log.x, y), self.f_small, colour).width
            self.text(body, (log.x + width, y), self.f_small,
                      colour if not prefix and colour == MUTED else TEXT)
            y += line_h
        if not rows:
            self.text("No messages yet - say hi!", (log.x, log.y), self.f_small, MUTED)

        for text, rect in zip(QUICK_CHAT, quick):
            self._button(rect, text)

        pygame.draw.rect(self.screen, BG, field, border_radius=8)
        pygame.draw.rect(self.screen, ACCENT if self.chat_focus else LINE, field,
                         width=2 if self.chat_focus else 1, border_radius=8)
        shown = self.chat_input
        caret = "|" if self.chat_focus and (pygame.time.get_ticks() // 500) % 2 == 0 else ""
        if not shown and not self.chat_focus:
            self.text("Click here or press Enter", (field.x + 10, field.y + 9),
                      self.f_small, MUTED)
        else:
            while shown and self.f_small.size(shown + caret)[0] > field.width - 20:
                shown = shown[1:]
            self.text(shown + caret, (field.x + 10, field.y + 9), self.f_small, TEXT)

    # ------------------------------------------------------------------
    # drawing
    # ------------------------------------------------------------------
    def draw(self):
        self.screen.fill(BG)
        if self.screen_name == SCREEN_NICKNAME:
            self._draw_nickname()
        elif self.screen_name == SCREEN_ERROR:
            self._draw_error()
        else:
            self._draw_game()
        self._draw_toast()
        self._present()

    def _present(self):
        """Scale the canvas into the window, keeping its proportions."""
        window = pygame.display.get_surface() or self.window
        self.window = window
        ww, wh = window.get_size()
        scale = min(ww / float(WIN_W), wh / float(WIN_H))
        if abs(scale - 1.0) < 1e-6:
            window.blit(self.screen, (0, 0))
            self._view = (0, 0, 1.0)
        else:
            size = (max(1, int(WIN_W * scale)), max(1, int(WIN_H * scale)))
            scaled = pygame.transform.smoothscale(self.screen, size)
            ox, oy = (ww - size[0]) // 2, (wh - size[1]) // 2
            window.fill(BG)
            window.blit(scaled, (ox, oy))
            self._view = (ox, oy, scale)
        pygame.display.flip()

    def _draw_nickname(self):
        mid = WIN_W // 2
        self.text("FIND MY MINES", (mid, 210), self.f_title, TEXT, center=True)

        box = pygame.Rect(mid - 200, 300, 400, 62)
        pygame.draw.rect(self.screen, PANEL, box, border_radius=10)
        pygame.draw.rect(self.screen, ACCENT, box, width=2, border_radius=10)
        caret = "|" if (pygame.time.get_ticks() // 500) % 2 == 0 else " "
        self.text(self.nickname + caret, box.center, self.f_head, TEXT, center=True)
        self.text("Enter your nickname", (mid, 282), self.f_small, MUTED, center=True)

        if self.net.status == "connected":
            self.text("Press ENTER to join", (mid, 386), self.f_body, GOOD, center=True)
        else:
            self.text("Connecting to the server...", (mid, 386), self.f_body, WARN,
                      center=True)

        # proof for the demo that the address comes from the source, not the user
        self.text("server %s  (set in config.py)" % self.net.address,
                  (mid, 440), self.f_small, MUTED, center=True)

    def _draw_error(self):
        mid = WIN_W // 2
        self.text("CANNOT REACH THE SERVER", (mid, 240), self.f_head, BAD, center=True)
        if self.net.status == "lost" or self.joined:
            detail = "The connection to %s was closed." % self.net.address
        else:
            detail = "Tried %s - %s" % (self.net.address, self.net.error)
        y = 292
        for line in self.wrap(detail, self.f_body, 760)[:3]:
            self.text(line, (mid, y), self.f_body, TEXT, center=True)
            y += 24
        y += 34
        for hint in ("Is server.py running on that computer?",
                     "Is SERVER_HOST in config.py the server's current IP?",
                     "Is Python allowed through the firewall on the server?",
                     "Are both computers on the same Wi-Fi? (a phone hotspot works)"):
            self.text("- " + hint, (mid - 260, y), self.f_small, MUTED)
            y += 30
        self.text("Press R to try again", (mid, y + 24), self.f_body, ACCENT,
                  center=True)

    def _draw_game(self):
        self.text("FIND MY MINES", (CX, 22), self.f_title, TEXT, center=True)
        self._draw_titlebar()
        self._draw_modes()
        self._draw_clock()
        self._draw_scoreboard()
        self._draw_board()
        self._draw_status()
        self._draw_online()
        self._draw_side()
        if self.settings_open and self.mode == game_rules.MODE_CUSTOM:
            self._draw_settings()
        if self.match_end is not None:
            self._draw_end_overlay()
        if self.reconnecting:
            self._draw_reconnect_overlay()

    def _draw_titlebar(self):
        self._button(self.sound_rect, "Sound: off" if self.sound.muted else "Sound: on",
                     active=not self.sound.muted)
        ranked = (self.state or {}).get("ranked", True)
        self._button(self.ranked_rect, "Ranked" if ranked else "Casual",
                     active=ranked)
        self._button(self.theme_rect, "Theme: %s" % THEME_LABEL)

    def _draw_modes(self):
        """The mode bar - this is where the extra games are found."""
        mouse = self.mouse()
        for mode, box in self.mode_rects:
            active = mode == self.mode
            hot = box.collidepoint(mouse) and self.role == "player"
            fill = (BTN_HOT if hot else BTN) if active else (PANEL_2 if hot else PANEL)
            pygame.draw.rect(self.screen, fill, box, border_radius=8)
            pygame.draw.rect(self.screen, ACCENT if active else LINE, box,
                             width=1, border_radius=8)
            self.text(game_rules.MODE_LABELS.get(mode, mode), box.center,
                      self.f_small, BTN_TEXT if active else MUTED, center=True)
        blurb = game_rules.MODE_BLURBS.get(self.mode, "")
        self.text(blurb, (CX, 100), self.f_small, MUTED, center=True)

    # ------------------------------------------------------------------
    # the custom game's settings panel
    # ------------------------------------------------------------------
    @property
    def custom(self):
        return (self.state or {}).get("custom", {})

    def _settings_widgets(self):
        """Panel rectangles, rebuilt each frame so one layout serves both
        drawing and clicking."""
        card = pygame.Rect(0, 0, 470, 430)
        card.center = (CX, 452)
        controls = []                      # (rect, key, value_or_delta, is_step)
        y = card.y + 64
        for _, key, step in CUSTOM_STEPS:
            controls.append((pygame.Rect(card.right - 148, y, 34, 30), key, -step, True))
            controls.append((pygame.Rect(card.right - 58, y, 34, 30), key, step, True))
            y += 46
        for _, key, options in CUSTOM_CHOICES:
            x = card.right - 210
            for value, _text in options:
                controls.append((pygame.Rect(x, y, 92, 30), key, value, False))
                x += 100
            y += 46
        close = pygame.Rect(card.centerx - 60, card.bottom - 46, 120, 34)
        return card, controls, close

    def _settings_click(self, event):
        """Returns True when the panel swallowed the click."""
        if not (self.settings_open and self.mode == game_rules.MODE_CUSTOM):
            return False
        if event.button != 1:
            return False
        card, controls, close = self._settings_widgets()
        if close.collidepoint(event.pos):
            self.settings_open = False
            return True
        for rect, key, value, is_step in controls:
            if not rect.collidepoint(event.pos):
                continue
            if self.role != "player":
                self.say("Only players can change the settings")
                return True
            current = self.custom
            wanted = (current.get(key, 0) + value) if is_step else value
            self.net.send(protocol.SET_CUSTOM, settings={key: wanted})
            return True
        return card.collidepoint(event.pos)   # clicks on the card do nothing

    def _veil(self, alpha=None):
        """Dim the board column (the side panel stays usable)."""
        veil = pygame.Surface((GAME_W, WIN_H), pygame.SRCALPHA)
        veil.fill((*VEIL, VEIL_ALPHA if alpha is None else alpha))
        self.screen.blit(veil, (0, 0))

    def _draw_settings(self):
        card, controls, close = self._settings_widgets()
        self._veil(205)
        pygame.draw.rect(self.screen, PANEL, card, border_radius=14)
        pygame.draw.rect(self.screen, LINE, card, width=1, border_radius=14)

        self.text("CUSTOM GAME", (card.centerx, card.y + 26), self.f_head,
                  TEXT, center=True)
        current = self.custom
        mouse = self.mouse()
        index = 0
        y = card.y + 64

        for label, key, _step in CUSTOM_STEPS:
            self.text(label, (card.x + 24, y + 5), self.f_body, MUTED)
            for _ in range(2):
                rect, _k, delta, _is_step = controls[index]
                hot = rect.collidepoint(mouse)
                pygame.draw.rect(self.screen, PANEL_2 if hot else BG, rect,
                                 border_radius=6)
                pygame.draw.rect(self.screen, LINE, rect, width=1, border_radius=6)
                self.text("+" if delta > 0 else "-", rect.center, self.f_body,
                          TEXT, center=True)
                index += 1
            value = current.get(key, "?")
            if key == "turn_seconds":
                value = "%ss" % value
            self.text(value, (card.right - 96, y + 3), self.f_head, WARN)
            y += 46

        for label, key, options in CUSTOM_CHOICES:
            self.text(label, (card.x + 24, y + 5), self.f_body, MUTED)
            for value, text in options:
                rect, _k, _v, _is_step = controls[index]
                active = current.get(key) == value
                hot = rect.collidepoint(mouse)
                fill = (BTN_HOT if hot else BTN) if active else (PANEL_2 if hot else BG)
                pygame.draw.rect(self.screen, fill, rect, border_radius=6)
                pygame.draw.rect(self.screen, ACCENT if active else LINE, rect,
                                 width=1, border_radius=6)
                self.text(text, rect.center, self.f_small,
                          BTN_TEXT if active else MUTED, center=True)
                index += 1
            y += 46

        limits = (self.state or {}).get("custom_limits", {})
        low, high = limits.get(
            "size_cube" if current.get("shape") == "cube" else "size_flat", (0, 0))
        self.text("size %s-%s, bombs up to %d%% of the board"
                  % (low, high, int(limits.get("max_bomb_share", 0) * 100)),
                  (card.centerx, card.bottom - 66), self.f_small, MUTED, center=True)

        hot = close.collidepoint(mouse)
        pygame.draw.rect(self.screen, BTN_HOT if hot else BTN, close, border_radius=8)
        self.text("PLAY", close.center, self.f_body, BTN_TEXT, center=True)

    def _draw_clock(self):
        running = self.phase == game_rules.PHASE_PLAYING and not self.paused
        left = self.seconds_left if running else 0
        colour = MUTED if not running else (BAD if left <= 3 else ACCENT)
        self.text("00:00:%02d" % left, (CX, 128), self.f_clock, colour, center=True)

    def _draw_scoreboard(self):
        """Both names and scores at the two edges, the active player marked."""
        players = self.players
        current = (self.state or {}).get("current_turn")
        away = {a["id"] for a in (self.state or {}).get("away", [])}
        for index, side in enumerate(("left", "right")):
            if index < len(players):
                p = players[index]
                name = p["name"] + (" (you)" if p["id"] == self.my_id else "")
                if p["id"] in away:
                    name += " (away)"
                elo_val = p.get("elo")
                mode = (self.state or {}).get("mode", "classic")
                ranked_modes = getattr(config, "ELO_RANKED_MODES", ("classic", "radius2", "sweeper", "cube"))
                is_ranked = (self.state or {}).get("ranked", True)
                if not is_ranked and not p.get("bot"):
                    name += " [Casual]"
                elif elo_val is not None and mode in ranked_modes and not p.get("bot"):
                    tier_name = p.get("tier", "Bronze")
                    if p.get("provisional"):
                        matches_done = p.get("mode_matches", 0)
                        name += " [%s · Prov %d/5 · %d]" % (tier_name, matches_done, elo_val)
                    else:
                        name += " [%s · %d]" % (tier_name, elo_val)
                score = p["score"]
                active = p["id"] == current
                colour = GOOD if active else (WARN if p["id"] in away else TEXT)
            else:
                name, score, active, colour = "waiting...", "-", False, TEXT
            name = self.fit(name, self.f_head, 380)
            x = 24 if side == "left" else GAME_W - 24
            swatch = (P1, P2)[index]
            if side == "left":
                rect = self.text(name, (x, 182), self.f_head, colour)
                self.text(score, (x, 210), self.f_clock, WARN)
                bar = pygame.Rect(x, rect.bottom + 4, rect.width, 3)
            else:
                rect = self.text(name, (x, 182), self.f_head, colour, right=True)
                self.text(score, (x, 210), self.f_clock, WARN, right=True)
                bar = pygame.Rect(rect.right - rect.width, rect.bottom + 4,
                                  rect.width, 3)
            # each player's colour, used on the bombs they find
            pygame.draw.rect(self.screen, swatch, bar, border_radius=2)
            if active:
                pygame.draw.rect(self.screen, GOOD, bar.inflate(0, 2), border_radius=2)

    # ------------------------------------------------------------------
    # the board
    # ------------------------------------------------------------------
    def _reveal_progress(self, cell, now):
        started = self.reveal_times.get(cell)
        if started is None:
            return 1.0
        p = (now - started) / REVEAL_SECONDS
        if p >= 1.0:
            del self.reveal_times[cell]
            return 1.0
        return max(0.0, p)

    def _draw_board(self):
        mouse = self.mouse()
        flags = self.flags
        owners = self.bomb_owners
        order = {p["id"]: i for i, p in enumerate(self.players)}
        last_cell, last_by = self.last_move_cell
        now = time.monotonic()
        size = self._geometry()[0]
        radius = 6 if (self.is_3d or size < 40) else 8
        font = self.f_cell if size >= 56 else (self.f_cell_m if size >= 34
                                               else self.f_cell_s)
        hint = self.hint
        heat = hint["heat"] if hint and self.show_odds else None

        if self.is_3d:
            _s, _g, layer_gap, ox, oy, layer_w = self._geometry()
            for layer in range(self.dims[0]):
                x = ox + layer * (layer_w + layer_gap)
                self.text("layer %d" % layer, (x + layer_w // 2, oy - 24),
                          self.f_small, MUTED, center=True)

        for cell in self.board_cells():
            rect = self.cell_rect(cell)
            value = self.value_at(cell)
            progress = self._reveal_progress(cell, now) if value is not None else 1.0
            if value is None:
                clickable = self.can_click(cell) and self.match_end is None
                hot = clickable and rect.collidepoint(mouse)
                pygame.draw.rect(self.screen, COVERED_HOVER if hot else COVERED,
                                 rect, border_radius=radius)
                if clickable:
                    pygame.draw.rect(self.screen, ACCENT if hot else LINE,
                                     rect, width=2, border_radius=radius)
                if heat is not None and cell in heat:
                    self._draw_heat(rect, heat[cell], hint["goal"], size)
                if cell in flags:
                    self._draw_flag(rect, flags[cell] == self.my_id)
                if hint and cell == hint["cell"]:
                    pulse = 3 + int(2 * abs(((now * 2) % 2) - 1))
                    pygame.draw.rect(self.screen, WARN, rect.inflate(2, 2),
                                     width=pulse, border_radius=radius)
                continue

            # opened slots pop in, growing from the middle
            body = rect
            if progress < 1.0:
                shrink = int((1 - (0.55 + 0.45 * progress)) * rect.width)
                body = rect.inflate(-shrink, -shrink)
            if value == game_rules.BOMB:
                pygame.draw.rect(self.screen, BOMB_CELL, body, border_radius=radius)
                self._draw_bomb(body)
                index = order.get(owners.get(cell))
                if index in (0, 1):
                    pygame.draw.rect(self.screen, (P1, P2)[index], body, width=3,
                                     border_radius=radius)
                    self.text(str(index + 1), (body.x + 4, body.y + 2),
                              self.f_tiny, (P1, P2)[index])
            else:
                pygame.draw.rect(self.screen, OPENED, body, border_radius=radius)
                pygame.draw.rect(self.screen, LINE, body, width=1,
                                 border_radius=radius)
                if progress >= 0.5:
                    self.text(value, body.center, font,
                              DIGIT_COLOURS.get(value, TEXT), center=True)
            if progress < 1.0:
                flash = pygame.Surface(body.size, pygame.SRCALPHA)
                flash.fill((*ACCENT, int(150 * (1 - progress))))
                self.screen.blit(flash, body)

        if last_cell is not None and self.value_at(last_cell) is not None:
            index = order.get(last_by)
            colour = (P1, P2)[index] if index in (0, 1) else WARN
            pygame.draw.rect(self.screen, colour,
                             self.cell_rect(last_cell).inflate(4, 4), width=2,
                             border_radius=radius + 2)

    def _draw_heat(self, rect, p, goal, size):
        """Tint a covered slot by the coach's odds for it."""
        colour = BAD if goal == "avoid" else WARN
        tint = pygame.Surface(rect.size, pygame.SRCALPHA)
        tint.fill((*colour, int(35 + 170 * p)))
        self.screen.blit(tint, rect)
        if size >= 34:
            label = "%d%%" % round(p * 100) if size >= 50 else "%d" % round(p * 100)
            self.text(label, rect.center, self.f_tiny, TEXT, center=True)

    def _draw_flag(self, rect, mine):
        """A little pennant.  Yours is bright; your opponent's is muted."""
        colour = WARN if mine else MUTED
        cx, cy = rect.center
        half = max(6, int(rect.width * 0.17))
        pole_x = cx - max(3, half // 2)
        pygame.draw.line(self.screen, colour, (pole_x, cy - half), (pole_x, cy + half), 2)
        pygame.draw.polygon(self.screen, colour, [
            (pole_x + 1, cy - half), (pole_x + 1 + half, cy - half // 2),
            (pole_x + 1, cy)])

    def _draw_bomb(self, rect):
        cx, cy = rect.center
        r = max(5, int(min(rect.width, rect.height) * 0.22))
        pygame.draw.line(self.screen, (255, 214, 214),
                         (cx + r // 2, cy - r * 2 // 3), (cx + r, cy - r * 5 // 4), 3)
        pygame.draw.circle(self.screen, (255, 226, 226),
                           (cx + r + 1, cy - r * 5 // 4 - 1), max(2, r // 5))
        pygame.draw.circle(self.screen, (22, 14, 14), (cx, cy), r)
        pygame.draw.circle(self.screen, (255, 230, 230),
                           (cx - r // 3, cy - r // 3), max(2, r // 4))

    def _draw_status(self):
        y = self.board_bottom() + 28
        away = (self.state or {}).get("away") or []
        if self.paused and away:
            msg, colour = ("Paused - %s lost connection (%ds)"
                           % (away[0]["name"], away[0]["seconds"])), WARN
        elif self.role != "player":
            msg, colour = "You are watching this match", ACCENT
        elif self.phase == game_rules.PHASE_WAITING:
            msg, colour = "Waiting for another player to join...", MUTED
        elif self.phase == game_rules.PHASE_ENDED:
            msg, colour = "Match finished", WARN
        elif self.my_turn:
            if self.bombs_are_bad:
                msg = "Your turn - open safe ground, avoid the bombs!"
            else:
                msg = "Your turn - find a bomb!"
            colour = GOOD
        else:
            others = [p["name"] for p in self.players if p["id"] != self.my_id]
            msg = "Waiting for %s..." % (others[0] if others else "the other player")
            colour = MUTED
        self.text(self.fit(msg, self.f_head, GAME_W - 48), (CX, y), self.f_head,
                  colour, center=True)

        state = self.state or {}
        if self.bombs_are_bad:
            bombs, total = state.get("safe_left"), None
            caption = "safe slots left  %d" % (bombs or 0)
        else:
            bombs, total = state.get("bombs_left"), state.get("bombs_total")
            caption = "bombs left  %s / %s" % (bombs, total)
        if bombs is not None:
            self.text(caption, (CX, y + 32), self.f_small, MUTED, center=True)

    def _online_text(self, max_width):
        """Who is here, shortened to fit with a '+N more' when it must."""
        parts = []
        for c in self.clients.get("list", []):
            tag = ""
            if c.get("away"):
                tag = " (away)"
            elif c.get("role") != "player":
                tag = " (watching)"
            parts.append(c["name"] + tag)
        parts += [b["name"] for b in self.clients.get("bots", [])]
        out = ""
        for i, part in enumerate(parts):
            trial = (out + ", " if out else "") + part
            if self.f_small.size(trial)[0] > max_width and i > 0:
                return "%s  +%d more" % (out, len(parts) - i)
            out = trial
        return self.fit(out, self.f_small, max_width)

    def _draw_online(self):
        """The connected-client list the server pushes to everyone."""
        panel = pygame.Rect(24, WIN_H - 56, GAME_W - 48, 44)
        pygame.draw.rect(self.screen, PANEL, panel, border_radius=10)
        pygame.draw.rect(self.screen, LINE, panel, width=1, border_radius=10)

        self.text("ONLINE  %d" % self.clients.get("count", 0),
                  (panel.x + 16, panel.y + 13), self.f_small, ACCENT)
        self.text(self._online_text(panel.width - 130 - 16) or "-",
                  (panel.x + 114, panel.y + 13), self.f_small, MUTED)

    # ------------------------------------------------------------------
    # overlays
    # ------------------------------------------------------------------
    def _draw_end_overlay(self):
        self._veil()
        card = pygame.Rect(0, 0, 520, 470)
        card.center = (CX, 450)
        pygame.draw.rect(self.screen, PANEL, card, border_radius=16)
        pygame.draw.rect(self.screen, LINE, card, width=1, border_radius=16)

        end = self.match_end
        players = end.get("players", [])
        if end.get("draw"):
            headline, colour = "DRAW", WARN
        elif self.role != "player":
            winner = next((p["name"] for p in players
                           if p["id"] == end.get("winner_id")), "?")
            headline, colour = self.fit("%s WINS" % winner.upper(), self.f_huge,
                                        card.width - 40), ACCENT
        elif end.get("winner_id") == self.my_id:
            headline, colour = "YOU WIN", GOOD
        else:
            headline, colour = "YOU LOST", BAD
        self.text(headline, (CX, card.y + 50), self.f_huge, colour, center=True)

        left, right = card.x + 30, card.right - 30
        y = card.y + 108
        elo_changes = end.get("elo_changes", {})
        is_ranked_match = end.get("ranked", True)
        elapsed = time.time() - getattr(self, "match_end_time", time.time())
        progress = min(1.0, max(0.0, elapsed / 1.0))
        eased = 1.0 - (1.0 - progress) ** 3

        for p in players:
            label = "%s%s" % (p["name"], "  (you)" if p["id"] == self.my_id else "")
            ch = elo_changes.get(p["id"]) or elo_changes.get(str(p["id"])) or elo_changes.get(p["name"])
            if ch and is_ranked_match:
                before = ch.get("before", 1200)
                after = ch.get("after", 1200)
                delta = ch.get("delta", 0)
                cur_display = int(before + (after - before) * eased)
                sign = "+" if delta > 0 else ""
                col = GOOD if delta > 0 else (BAD if delta < 0 else WARN)
                elo_str = "%s%d ELO (%d)" % (sign, delta, cur_display)
                if progress >= 1.0:
                    if ch.get("promoted"):
                        elo_str += " ▲ %s!" % ch["promoted"].upper()
                    elif ch.get("streak_bonus"):
                        elo_str += " (+%d streak!)" % ch["streak_bonus"]
                self.text(self.fit(label, self.f_head, 210), (left, y), self.f_head, TEXT)
                self.text(elo_str, (right - 65, y + 2), self.f_small, col, right=True)
                self.text(p["score"], (right, y), self.f_head, WARN, right=True)
            elif not is_ranked_match and len(players) >= 2:
                self.text(self.fit(label, self.f_head, 240), (left, y), self.f_head, TEXT)
                self.text("Casual", (right - 65, y + 2), self.f_small, MUTED, right=True)
                self.text(p["score"], (right, y), self.f_head, WARN, right=True)
            else:
                self.text(self.fit(label, self.f_head, 360), (left, y), self.f_head, TEXT)
                self.text(p["score"], (right, y), self.f_head, WARN, right=True)
            y += 34

        # a small table of how each player did
        stats = {s["id"]: s for s in end.get("stats", [])}
        if stats:
            y = card.y + 206
            columns = [("Picks", right - 170), ("Chain", right - 90), ("Rate", right)]
            self.text("MATCH STATS", (left, y), self.f_card, MUTED)
            for title, x in columns:
                self.text(title, (x, y), self.f_card, MUTED, right=True)
            y += 28
            for p in players:
                s = stats.get(p["id"])
                if not s:
                    continue
                self.text(self.fit(p["name"], self.f_body, 210), (left, y),
                          self.f_body, TEXT)
                self.text(s["picks"], (columns[0][1], y), self.f_body, TEXT, right=True)
                self.text(s["best_chain"], (columns[1][1], y), self.f_body, TEXT,
                          right=True)
                self.text("%d%%" % s["rate"], (columns[2][1], y), self.f_body, TEXT,
                          right=True)
                y += 30
            self.text("chain = most picks in a row that kept your turn",
                      (left, y + 4), self.f_small, MUTED)

        if self.role != "player":
            self.text("waiting for the players to rematch",
                      self.rematch_rect.center, self.f_body, MUTED, center=True)
            return

        votes = len((self.state or {}).get("rematch_votes", []))
        total = len(self.players)
        if self.voted_rematch:
            pygame.draw.rect(self.screen, PANEL_2, self.rematch_rect, border_radius=10)
            pygame.draw.rect(self.screen, LINE, self.rematch_rect, width=1,
                             border_radius=10)
            self.text("waiting  %d/%d" % (votes, total),
                      self.rematch_rect.center, self.f_body, MUTED, center=True)
        else:
            hot = self.rematch_rect.collidepoint(self.mouse())
            pygame.draw.rect(self.screen, BTN_HOT if hot else BTN,
                             self.rematch_rect, border_radius=10)
            self.text("REMATCH", self.rematch_rect.center, self.f_head, BTN_TEXT,
                      center=True)

    def _draw_reconnect_overlay(self):
        self._veil(215)
        card = pygame.Rect(0, 0, 460, 170)
        card.center = (CX, 430)
        pygame.draw.rect(self.screen, PANEL, card, border_radius=14)
        pygame.draw.rect(self.screen, WARN, card, width=2, border_radius=14)
        left = max(0, int(self.reconnect_until - time.time()))
        self.text("CONNECTION LOST", (CX, card.y + 44), self.f_head, WARN, center=True)
        self.text("Trying to get you back to your seat...", (CX, card.y + 84),
                  self.f_body, TEXT, center=True)
        self.text("Your score and turn are being held  (%ds)" % left,
                  (CX, card.y + 116), self.f_small, MUTED, center=True)

    def _draw_toast(self):
        if not self.toast or pygame.time.get_ticks() > self.toast_until:
            return
        text = self.fit(self.toast, self.f_body, GAME_W - 120)
        width, height = self.f_body.size(text)
        box = pygame.Rect(0, 0, width + 28, height + 16)
        box.center = (CX, WIN_H - 82)
        pygame.draw.rect(self.screen, PANEL_2, box, border_radius=8)
        pygame.draw.rect(self.screen, LINE, box, width=1, border_radius=8)
        self.text(text, box.center, self.f_body, TEXT, center=True)


def main():
    """Start the client.

    The address normally comes from config.py, so nobody has to type one.
    An optional argument overrides it - handy when the server has moved to
    a new address and editing the file would be one more thing to get
    wrong:  python client.py 192.168.1.14
    """
    args = [a for a in sys.argv[1:] if a.strip()]
    if args:
        config.SERVER_HOST = args[0].strip()
    if len(args) > 1 and args[1].strip().isdigit():
        config.SERVER_PORT = int(args[1])
    ClientUI().run()


if __name__ == "__main__":
    main()
