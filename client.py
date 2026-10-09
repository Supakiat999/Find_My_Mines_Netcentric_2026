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

Layout
    left column  : title bar, mode bar, both players and the clock, the board
    right column : three tabs - Play (opponent, coach, tips), Chat, Ranks

Look and feel
    * tiles have depth: covered tiles are raised and lift when hovered
    * every opened slot pops in, and a big opening ripples outwards
    * bombs glow, burn a fuse and explode (sparks, shock ring, screen shake)
    * the 3D cube is drawn as isometric slabs you can click directly
    * seven colour themes, picked from a menu (T cycles them)

Run:  python client.py
"""

import json
import math
import os
import queue
import random
import socket
import sys
import threading
import time

import pygame
from pygame import gfxdraw

import config
import game as game_rules
import protocol
import themes
from sound import SoundBank

GAME_W = 860                      # the board column
SIDE_W = 340                      # tabs: play, chat, ranks
WIN_W, WIN_H = GAME_W + SIDE_W, 880
CX = GAME_W // 2                  # centre of the board column
FPS = 60

SCREEN_NICKNAME = "nickname"
SCREEN_LOBBY = "lobby"
SCREEN_CREATE_ROOM = "create_room"
SCREEN_GAME = "game"
SCREEN_ERROR = "error"

CELL = 66
GAP = 8
BOARD_TOP = 216                   # where the board area starts
BOARD_LIMIT = 716                 # ...and the lowest it may reach
ISO_INSET = 0.90                  # how much of its diamond a 3D tile fills

RECONNECT_WINDOW = config.RECONNECT_GRACE + 8      # how long we keep trying
REVEAL_SECONDS = 0.34             # an opened slot's pop-in
RIPPLE_STEP = 0.05                # delay per ring when a region opens up
END_DELAY = 0.9                   # let the final reveal play before the result
END_FADE = 0.35

TABS = [("play", "Play"), ("chat", "Chat"), ("ranks", "Ranks")]

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

# "off" is a second person (2 players); the rest are the computer (1 player).
OPPONENTS = [("off", "Friend"), ("easy", "Easy"), ("medium", "Medium"),
             ("hard", "Hard")]
QUICK_CHAT = ["GG", "Nice!", "Oops", "Again?"]


# ----------------------------------------------------------------------
# small maths and colour helpers
# ----------------------------------------------------------------------
def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def mix(a, b, t):
    """Blend colour a towards colour b by t (0..1)."""
    t = clamp(t)
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t))


def lighten(c, t):
    return mix(c, (255, 255, 255), t)


def darken(c, t):
    return mix(c, (0, 0, 0), t)


def ease_out_cubic(t):
    t = clamp(t)
    return 1 - (1 - t) ** 3


def ease_out_back(t):
    t = clamp(t)
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


class Gfx:
    """Drawing primitives that take the game's fixed logical coordinates and
    draw them at the window's real resolution.

    The whole UI is laid out on a 1200x880 grid.  Instead of drawing that grid
    onto a bitmap and shrinking the bitmap (which blurs text and edges), every
    shape is multiplied by `s` and drawn straight onto a surface that already
    has the window's pixel size, so lines and text stay sharp at any size.
    """

    def __init__(self):
        self.s = 1.0

    def _w(self, width):
        return 0 if not width else max(1, int(round(width * self.s)))

    def _r(self, rect):
        r = pygame.Rect(rect)
        s = self.s
        x0, y0 = int(round(r.x * s)), int(round(r.y * s))
        x1, y1 = int(round(r.right * s)), int(round(r.bottom * s))
        return pygame.Rect(x0, y0, max(1, x1 - x0), max(1, y1 - y0))

    def _pt(self, p):
        return (int(round(p[0] * self.s)), int(round(p[1] * self.s)))

    def rect(self, surf, col, rect, width=0, border_radius=0):
        pygame.draw.rect(surf, col, self._r(rect), self._w(width),
                         border_radius=int(round(border_radius * self.s)))

    def circle(self, surf, col, centre, radius, width=0):
        pygame.draw.circle(surf, col, self._pt(centre),
                           max(1, int(round(radius * self.s))), self._w(width))

    def line(self, surf, col, a, b, width=1):
        pygame.draw.line(surf, col, self._pt(a), self._pt(b), self._w(width) or 1)

    def lines(self, surf, col, closed, points, width=1):
        pygame.draw.lines(surf, col, closed, [self._pt(p) for p in points],
                          self._w(width) or 1)

    def polygon(self, surf, col, points, width=0):
        pygame.draw.polygon(surf, col, [self._pt(p) for p in points],
                            self._w(width))

    def poly(self, surf, points, col):
        """A smooth-edged filled polygon."""
        pts = [self._pt(p) for p in points]
        gfxdraw.filled_polygon(surf, pts, col)
        gfxdraw.aapolygon(surf, pts, col)


# ----------------------------------------------------------------------
# themes
# ----------------------------------------------------------------------
THEME_VERSION = 0


def apply_theme(name):
    """Point the colour names at a palette.  Drawing code reads these names
    each frame, so switching takes effect on the next draw."""
    theme = themes.THEMES.get(name) or themes.DARK
    g = globals()
    g["THEME_VERSION"] = g.get("THEME_VERSION", 0) + 1
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
    # bomb look - older palettes without these keys still work
    g["BOMB_BODY"] = theme.get("bomb_body", (22, 14, 14))
    g["BOMB_GLOW"] = theme.get("bomb_glow", theme["bad"])
    g["SPARK"] = theme.get("spark", theme["warn"])
    g["IS_LIGHT"] = themes.luminance(theme["bg"]) > 0.4
    g["SHADOW"] = darken(theme["bg"], 0.30 if g["IS_LIGHT"] else 0.55)


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
        self.gx = Gfx()
        self.S = self._quantise(scale)
        self.gx.s = self.S
        self.screen = pygame.Surface(self._canvas_size())
        self._view = (0, 0, self.S)          # canvas offset x, y and scale
        self.clock = pygame.time.Clock()
        self._font_cache = {}

        self._build_fonts()

        self.sound = SoundBank(muted=bool(self.prefs.get("muted", False)))
        self.fx_on = bool(self.prefs.get("animations", True))

        self.net = NetworkClient()
        self.net.connect_async()

        self.screen_name = SCREEN_NICKNAME
        self.nickname = ""
        self.vs = "off"                      # chosen on the start screen
        self.room_id = None
        self.rooms = []
        self.room_scroll = 0
        self.room_name = "Room"
        self.create_mode = config.DEFAULT_MODE
        self.create_custom = dict(config.DEFAULT_CUSTOM)
        self.create_bot = "off"
        self.create_ranked = True
        self.room_error = ""
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
        self.unread = 0
        self.tab = "play"
        self.leaderboard = []
        self.hint = None                     # the coach's last answer
        self.show_odds = True
        self._known = {}                     # slot -> value at the last state

        # animation state
        self.now = time.monotonic()
        self.dt = 1.0 / FPS
        self._last_frame = self.now
        self.reveal_times = {}               # slot -> when its pop-in starts
        self.flag_times = {}                 # slot -> when its flag dropped
        self._known_flags = {}
        self.hover = {}                      # slot -> hover lift, 0..1
        self.score_bump = {}                 # player id -> when it scored
        self._known_scores = {}
        self.turn_at = 0.0                   # when the turn last changed
        self.timer_fill = 1.0                # smoothed turn-timer bar
        self.particles = []
        self.rings = []
        self.shake = 0.0
        self.fx_layer = pygame.Surface(self._canvas_size(), pygame.SRCALPHA)
        self._bg_surface = None
        self._bg_game = None
        self._bg_version = -1
        self._layout_cache = None
        self._cursor = None
        self.match_end_time = 0.0

        self.mode_rects = self._mode_rects()
        self.settings_open = False
        self.theme_open = False
        self.rules_open = False
        self.rules_at = 0.0
        self.cards = self._side_layout()
        self.rematch_rect = pygame.Rect(CX - 100, 601, 200, 52)
        self.theme_rect = pygame.Rect(GAME_W - 24 - 150, 14, 150, 32)
        self.ranked_rect = pygame.Rect(self.theme_rect.x - 8 - 96, 14, 96, 32)
        self.sound_rect = pygame.Rect(self.ranked_rect.x - 8 - 96, 14, 96, 32)
        self.rules_rect = pygame.Rect(self.sound_rect.x - 8 - 84, 14, 84, 32)
        self.rules_close = pygame.Rect(0, 0, 160, 44)
        self.leave_rect = pygame.Rect(24, 56, 90, 32)

    # ------------------------------------------------------------------
    # small helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _load_font(size, bold=False):
        for name in ("SF Pro Text", "Helvetica Neue", "Avenir Next", "Segoe UI",
                     "Arial", "DejaVu Sans"):
            try:
                path = pygame.font.match_font(name, bold=bold)
                if path:
                    return pygame.font.Font(path, size)
            except Exception:
                continue
        try:
            return pygame.font.SysFont("arial", size, bold=bold)
        except Exception:
            return pygame.font.Font(None, size)

    def _font(self, size, bold=False):
        """A font of `size` logical pixels, made at the real pixel size so
        text stays sharp when the window is not exactly 1200x880."""
        key = (int(size), bool(bold))
        if key not in self._font_cache:
            real = max(6, int(round(size * self.S)))
            self._font_cache[key] = self._load_font(real, bold)
        return self._font_cache[key]

    def _build_fonts(self):
        self._font_cache = {}
        self.f_title = self._font(26, bold=True)
        self.f_splash = self._font(58, bold=True)
        self.f_clock = self._font(38, bold=True)
        self.f_score = self._font(40, bold=True)
        self.f_head = self._font(20, bold=True)
        self.f_body = self._font(17)
        self.f_small = self._font(14)
        self.f_card = self._font(13, bold=True)
        self.f_tiny = self._font(12, bold=True)
        self.f_cell = self._font(30, bold=True)
        self.f_cell_m = self._font(22, bold=True)
        self.f_cell_s = self._font(16, bold=True)
        self.f_huge = self._font(52, bold=True)

    @staticmethod
    def _quantise(scale):
        """Round the window scale down to a 2% step, so the canvas never
        spills past the window and a drag-resize does not rebuild every frame."""
        return max(0.4, math.floor(scale * 50.0) / 50.0)

    def _canvas_size(self):
        return (int(round(WIN_W * self.S)), int(round(WIN_H * self.S)))

    def _apply_scale(self, scale):
        """Re-make everything that depends on the pixel size."""
        self.S = scale
        self.gx.s = scale
        self.screen = pygame.Surface(self._canvas_size())
        self.fx_layer = pygame.Surface(self._canvas_size(), pygame.SRCALPHA)
        self._build_fonts()
        self._bg_version = -1

    @staticmethod
    def _mode_rects():
        """A button per mode, centred in the mode bar."""
        width, gap = 116, 8
        total = len(game_rules.MODES) * width + (len(game_rules.MODES) - 1) * gap
        x = (GAME_W - total) // 2
        rects = []
        for mode in game_rules.MODES:
            rects.append((mode, pygame.Rect(x, 58, width, 30)))
            x += width + gap
        return rects

    def text(self, s, pos, font=None, color=None, center=False, right=False):
        S = self.S
        surf = (font or self.f_body).render(str(s), True, TEXT if color is None
                                            else color)
        rect = surf.get_rect()
        p = (int(round(pos[0] * S)), int(round(pos[1] * S)))
        if center:
            rect.center = p
        elif right:
            rect.topright = p
        else:
            rect.topleft = p
        self.screen.blit(surf, rect)
        # hand back the rectangle in logical units, like every other layout value
        return pygame.Rect(int(rect.x / S), int(rect.y / S),
                           int(rect.w / S), int(rect.h / S))

    def text_scaled(self, s, center, font, color, scale):
        """Text drawn at a zoom - used for the score bump."""
        surf = font.render(str(s), True, color)
        if abs(scale - 1.0) > 0.01:
            surf = pygame.transform.rotozoom(surf, 0, scale)
        centre = (int(round(center[0] * self.S)), int(round(center[1] * self.S)))
        self.screen.blit(surf, surf.get_rect(center=centre))

    def tsize(self, font, s):
        """Text size in logical units (fonts are made at the real pixel size)."""
        w, h = font.size(str(s))
        return w / self.S, h / self.S

    def _tw(self, font, s):
        return font.size(str(s))[0] / self.S

    def fit(self, s, font, max_width):
        """Shorten with '...' so a line never leaves its box."""
        s = str(s)
        if self._tw(font, s) <= max_width + 2:
            return s
        while s and self._tw(font, s + "...") > max_width:
            s = s[:-1]
        return s + "..."

    def wrap(self, s, font, width):
        """Break text into lines no wider than `width`."""
        lines, current = [], ""
        for word in str(s).split(" "):
            trial = (current + " " + word).strip()
            if self._tw(font, trial) <= width:
                current = trial
                continue
            if current:
                lines.append(current)
            while self._tw(font, word) > width and len(word) > 1:
                cut = len(word)
                while cut > 1 and self._tw(font, word[:cut]) > width:
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
        self.toast_at = self.now

    def mouse(self):
        """The pointer, in canvas coordinates."""
        return self._to_canvas(pygame.mouse.get_pos())

    def _to_canvas(self, pos):
        ox, oy, scale = self._view
        return (int((pos[0] - ox) / scale), int((pos[1] - oy) / scale))

    def _poly(self, surface, points, colour):
        """A smooth-edged filled polygon."""
        self.gx.poly(surface, points, colour)

    def _panel(self, rect, radius=14, fill=None, border=None, shadow=True):
        if shadow:
            self.gx.rect(self.screen, SHADOW, rect.move(0, 3),
                             border_radius=radius)
        self.gx.rect(self.screen, fill or PANEL, rect, border_radius=radius)
        self.gx.rect(self.screen, border or LINE, rect, width=1,
                         border_radius=radius)

    def _button(self, rect, label, active=False, enabled=True, small=True):
        """A themed button; hit-testing is done separately."""
        hot = enabled and rect.collidepoint(self.mouse())
        if active:
            fill = BTN_HOT if hot else BTN
        else:
            fill = PANEL_2 if hot else PANEL
        self.gx.rect(self.screen, fill, rect, border_radius=9)
        self.gx.rect(self.screen, ACCENT if (active or hot) else LINE, rect,
                         width=1, border_radius=9)
        colour = BTN_TEXT if active else (TEXT if enabled else MUTED)
        font = self.f_small if small else self.f_body
        self.text(self.fit(label, font, rect.width - 10), rect.center, font,
                  colour, center=True)

    def _card(self, rect, title):
        self._panel(rect)
        self.text(title, (rect.x + 16, rect.y + 13), self.f_card, MUTED)

    def _remember(self):
        self.prefs["theme"] = THEME_NAME
        self.prefs["muted"] = self.sound.muted
        self.prefs["animations"] = self.fx_on
        save_prefs(self.prefs)

    # ------------------------------------------------------------------
    # effects: particles, shock rings, screen shake
    # ------------------------------------------------------------------
    def _spawn(self, x, y, n, colours, speed=(60, 260), life=(0.4, 0.9),
               size=(2.0, 4.5), gravity=380.0, kind="spark",
               angle=(0.0, 2 * math.pi)):
        if not self.fx_on:
            return
        for _ in range(n):
            a = random.uniform(*angle)
            s = random.uniform(*speed)
            self.particles.append({
                "x": x, "y": y, "vx": math.cos(a) * s, "vy": math.sin(a) * s,
                "life": 0.0, "max": random.uniform(*life),
                "col": random.choice(colours), "size": random.uniform(*size),
                "g": gravity, "kind": kind, "seed": random.uniform(0, 6.28)})
        del self.particles[:-450]

    def _ring(self, x, y, radius, colour, dur=0.55, width=3):
        if not self.fx_on:
            return
        self.rings.append({"x": x, "y": y, "r1": radius, "col": colour,
                           "life": 0.0, "dur": dur, "w": width})

    def _update_fx(self, dt):
        if self.particles:
            alive = []
            for p in self.particles:
                p["life"] += dt
                if p["life"] >= p["max"]:
                    continue
                drag = 0.12 ** dt if p["kind"] != "confetti" else 0.5 ** dt
                p["vx"] *= drag
                p["vy"] = p["vy"] * drag + p["g"] * dt
                if p["kind"] == "confetti":
                    p["x"] += math.sin(p["life"] * 7 + p["seed"]) * 40 * dt
                p["x"] += p["vx"] * dt
                p["y"] += p["vy"] * dt
                alive.append(p)
            self.particles = alive
        if self.rings:
            for r in self.rings:
                r["life"] += dt
            self.rings = [r for r in self.rings if r["life"] < r["dur"]]
        if self.shake > 0:
            self.shake *= 0.0004 ** dt
            if self.shake < 0.4:
                self.shake = 0.0

    def _draw_fx(self):
        if not self.particles and not self.rings:
            return
        layer = self.fx_layer
        layer.fill((0, 0, 0, 0))
        for r in self.rings:
            p = r["life"] / r["dur"]
            radius = int(3 + r["r1"] * ease_out_cubic(p))
            alpha = int(230 * (1 - p) ** 1.5)
            width = max(1, int(r["w"] * (1 - p) + 1))
            if radius > width:
                self.gx.circle(layer, (*r["col"], alpha),
                                   (int(r["x"]), int(r["y"])), radius, width)
        for p in self.particles:
            f = p["life"] / p["max"]
            alpha = int(255 * (1 - f * f))
            if p["kind"] == "confetti":
                w = max(2, int(p["size"] * 2.2))
                h = max(2, int(p["size"] * (0.5 + 0.5 * abs(math.sin(
                    p["life"] * 9 + p["seed"])))))
                self.gx.rect(layer, (*p["col"], alpha),
                                 (int(p["x"]), int(p["y"]), w, h))
            else:
                size = max(1, int(p["size"] * (1 - 0.6 * f)))
                x, y = int(p["x"]), int(p["y"])
                self.gx.line(layer, (*p["col"], alpha // 2), (x, y),
                                 (int(x - p["vx"] * 0.04),
                                  int(y - p["vy"] * 0.04)), max(1, size - 1))
                self.gx.circle(layer, (*p["col"], alpha), (x, y), size)
        self.screen.blit(layer, (0, 0))

    def _explode(self, centre, size, bad):
        """A bomb going off: flash ring, shock ring, sparks, a jolt."""
        x, y = centre
        cols = [BOMB_GLOW, SPARK, lighten(BOMB_GLOW, 0.5), TEXT]
        self._ring(x, y, size * 1.5, BOMB_GLOW, 0.55, 5)
        self._ring(x, y, size * 2.6, SPARK, 0.75, 2)
        self._spawn(x, y, 34 if bad else 22, cols, speed=(90, 340),
                    life=(0.45, 1.0), size=(2.0, 5.0))
        if self.fx_on:
            self.shake = max(self.shake, 9.0 if bad else 4.0)

    # ------------------------------------------------------------------
    # connection upkeep
    # ------------------------------------------------------------------
    def _maintain_connection(self):
        """If the link drops mid-game, keep trying to get back to the seat."""
        now = time.time()
        if self.screen_name not in (SCREEN_GAME, SCREEN_LOBBY, SCREEN_CREATE_ROOM) or not self.joined:
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
        if kind == protocol.ROOMS:
            self.rooms = msg.get("rooms", [])
            self.room_scroll = min(self.room_scroll, max(0, len(self.rooms) - 1))
            return
        if kind == protocol.ROOM_LEFT:
            if msg.get("room_id") == self.room_id:
                self._reset_room_view()
                self.room_id = None
                self.screen_name = SCREEN_LOBBY
            return
        if (kind not in (protocol.WELCOME, protocol.ERROR, protocol.ROOMS,
                         protocol.ROOM_LEFT) and msg.get("room_id") != self.room_id):
            return
        if kind == protocol.WELCOME:
            keep_draft = (self.screen_name == SCREEN_CREATE_ROOM and msg.get("reconnected")
                          and msg.get("room_id") is None)
            old_room = self.room_id
            self.room_id = msg.get("room_id")
            self.my_id = msg.get("client_id")
            self.role = msg.get("role")
            self.token = msg.get("token") or self.token
            self.welcome = msg.get("message", "")
            self.room_name_active = msg.get("room_name", "")
            self.welcome_dims = msg.get("dims")
            self.joined = True
            self.reconnect_until = None
            self.rejoin_pending = False
            self.screen_name = SCREEN_GAME if self.room_id is not None else SCREEN_LOBBY
            if keep_draft:
                self.screen_name = SCREEN_CREATE_ROOM
            if old_room != self.room_id:
                self._reset_room_view()
            self.leaderboard = msg.get("leaderboard", self.leaderboard)
            self.chat_lines = list(msg.get("chat", []))[-60:]
            self.chat_scroll = 0
            self.say(self.welcome, 4)
            if self.screen_name == SCREEN_LOBBY:
                self.net.send(protocol.LIST_ROOMS)
            elif self.screen_name == SCREEN_GAME and not self.prefs.get("rules_seen"):
                self._open_rules()
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
            self.match_end_time = self.now
            self.leaderboard = msg.get("leaderboard", self.leaderboard)
            if self.role != "player" or msg.get("draw"):
                self.sound.play("draw")
            elif msg.get("winner_id") == self.my_id:
                self.sound.play("win")
                self._confetti()
            else:
                self.sound.play("lose")
        elif kind == protocol.SERVER_RESET:
            self.match_end = None
            self.voted_rematch = False
            self.hint = None
            self.reveal_times.clear()
            self.flag_times.clear()
            self._known = {}
            self.say("The server reset the game", 3)
        elif kind == protocol.CHAT_MSG:
            self.chat_lines.append(msg)
            del self.chat_lines[:-60]
            self.chat_scroll = 0
            if not msg.get("system") and msg.get("id") != self.my_id:
                self.sound.play("chat")
                if self.tab != "chat":
                    self.unread += 1
                    self.say("%s: %s" % (msg.get("name", "?"), msg.get("text", "")), 2.2)
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
            self.room_error = msg.get("message", "not allowed")
            self.sound.play("error")

    def _reset_room_view(self):
        self.state = None
        self.welcome_dims = None
        self.clients = {"count": 0, "list": [], "bots": []}
        self.chat_lines, self.chat_input = [], ""
        self.chat_focus = False
        self.chat_scroll = 0
        self.leaderboard = []
        self.hint = self.match_end = None
        self.voted_rematch = False
        self.reveal_times.clear()
        self._known = {}
        self.settings_open = False
        self._prev_turn = None
        self.room_error = ""
        self.rules_open = False
        self.flag_times.clear()
        self._known_flags.clear()
        self.hover.clear()
        self.score_bump.clear()
        self._known_scores.clear()
        self.particles.clear()
        self.rings.clear()
        self.shake = 0.0

    def _send_game(self, msg_type, **payload):
        if self.room_id is not None:
            self.net.send(msg_type, room_id=self.room_id, **payload)

    def _confetti(self):
        colours = [P1, P2, WARN, GOOD, ACCENT, TEXT]
        for _ in range(3):
            self._spawn(random.uniform(40, GAME_W - 40), random.uniform(-460, -20),
                        30, colours, speed=(0, 40), life=(2.2, 3.4),
                        size=(3.0, 5.5), gravity=210.0, kind="confetti",
                        angle=(0.0, 6.28))

    def _known_open(self):
        return [c for c, v in self._known.items() if v is not None]

    def _on_state(self, msg):
        now = self.now
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
        if len(old_cells) != len(cells):
            self.reveal_times.clear()      # a different board altogether
            self.flag_times.clear()
            fresh = [] if not old_cells else fresh
        if not any(v is not None for v in cells.values()):
            self.reveal_times.clear()      # a brand-new board
            self.flag_times.clear()
            self.hover.clear()
        self._known = cells

        # Reveal animation: the slot you opened pops first, and anything that
        # opened up with it ripples outwards from there.
        move = msg.get("last_move") or {}
        origin = tuple(move["cell"]) if move.get("cell") else None
        bad = bool(msg.get("bombs_are_bad"))
        made_bomb = False
        for c in fresh:
            delay = 0.0
            if origin and len(origin) == len(c) and cells[c] != game_rules.BOMB:
                delay = min(0.9, RIPPLE_STEP * max(abs(a - b)
                                                   for a, b in zip(c, origin)))
            self.reveal_times[c] = now + (delay if self.fx_on else 0.0) - (
                0.0 if self.fx_on else REVEAL_SECONDS)
            if cells[c] == game_rules.BOMB:
                made_bomb = True
                self._explode(self._cell_center(c), self._ref_size(), bad)
        if fresh:
            if made_bomb:
                self.sound.play("boom" if bad else "bomb")
            else:
                self.sound.play("click")
            if origin and not made_bomb and len(fresh) > 1:
                cx, cy = self._cell_center(origin)
                self._ring(cx, cy, self._ref_size() * 2.2, ACCENT, 0.7, 2)

        # flags dropping in
        new_flags = {tuple(f["cell"]): f["by"] for f in msg.get("flags", [])}
        for c in new_flags:
            if c not in self._known_flags:
                self.flag_times[c] = now
        for c in list(self.flag_times):
            if c not in new_flags:
                del self.flag_times[c]
        self._known_flags = new_flags

        # scores bumping
        for p in msg.get("players", []):
            old = self._known_scores.get(p["id"])
            if old is not None and p.get("score", 0) > old:
                self.score_bump[p["id"]] = now
            self._known_scores[p["id"]] = p.get("score", 0)

        if self.hint and len(self._known_open()) != self.hint["opened"]:
            self.hint = None               # the board changed: advice is stale

        prev = getattr(self, "_prev_turn", None)
        turn = msg.get("current_turn")
        if turn != prev:
            self.turn_at = now
            self.timer_fill = 1.0
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

    # ------------------------------------------------------------------
    # board geometry - flat grid, or isometric slabs for the cube
    # ------------------------------------------------------------------
    def _layout(self):
        key = self.dims
        if self._layout_cache and self._layout_cache[0] == key:
            return self._layout_cache[1]
        lay = self._iso_layout(key) if len(key) == 3 else self._flat_layout(key)
        self._layout_cache = (key, lay)
        return lay

    @staticmethod
    def _flat_layout(dims):
        rows, cols = dims
        avail_h = BOARD_LIMIT - BOARD_TOP
        fit_h = (avail_h + GAP) // rows - GAP
        fit_w = (GAME_W - 80 + GAP) // cols - GAP
        size = max(26, min(CELL, fit_h, fit_w))
        total_w = cols * (size + GAP) - GAP
        total_h = rows * (size + GAP) - GAP
        oy = BOARD_TOP + min(48, max(0, (avail_h - total_h) // 2))
        return {"kind": "flat", "size": size, "ox": (GAME_W - total_w) // 2,
                "oy": oy, "rows": rows, "cols": cols, "bottom": oy + total_h}

    @staticmethod
    def _iso_layout(dims):
        """Each layer is an isometric slab; the slabs sit in a grid so the
        whole cube is visible and every tile can be clicked."""
        layers, rows, cols = dims
        m = (rows + cols) / 2.0
        x0, x1 = 24, GAME_W - 24
        y0, y1 = BOARD_TOP - 6, BOARD_LIMIT
        aw, ah = x1 - x0, y1 - y0
        gx, gy, label = 22, 8, 22
        best = None
        for k in range(1, layers + 1):
            nrows = -(-layers // k)
            tw_w = (aw - (k - 1) * gx) / (k * m)
            tw_h = (ah - nrows * label - (nrows - 1) * gy) / (
                nrows * (m * 0.5 + 0.16))
            tw = min(tw_w, tw_h, 112.0)
            if best is None or tw > best[0]:
                best = (tw, k, nrows)
        tw, k, nrows = best
        tw = max(20.0, tw)
        th = tw * 0.5
        depth = max(4.0, tw * 0.16)
        slab_w = m * tw
        slab_h = label + m * th + depth
        total_w = k * slab_w + (k - 1) * gx
        total_h = nrows * slab_h + (nrows - 1) * gy
        left0 = x0 + (aw - total_w) / 2.0
        top0 = y0 + min(24.0, max(0.0, (ah - total_h) / 2.0))
        slabs = []
        for l in range(layers):
            gr, gc = divmod(l, k)
            # centre a short last row
            in_row = min(k, layers - gr * k)
            shift = (k - in_row) * (slab_w + gx) / 2.0
            left = left0 + shift + gc * (slab_w + gx)
            top = top0 + gr * (slab_h + gy)
            slabs.append({"cx": left + rows * tw / 2.0, "dt": top + label,
                          "left": left, "top": top})
        return {"kind": "iso", "tw": tw, "th": th, "depth": depth,
                "layers": layers, "rows": rows, "cols": cols, "slabs": slabs,
                "bottom": int(top0 + total_h), "label": label}

    def _ref_size(self):
        lay = self._layout()
        return lay["size"] if lay["kind"] == "flat" else lay["tw"] * 0.55

    @staticmethod
    def _iso_v(lay, layer, r, c):
        s = lay["slabs"][layer]
        return (s["cx"] + (c - r) * lay["tw"] / 2.0,
                s["dt"] + (r + c) * lay["th"] / 2.0)

    def _cell_center(self, cell):
        lay = self._layout()
        if lay["kind"] == "flat":
            r, c = cell
            size = lay["size"]
            return (lay["ox"] + c * (size + GAP) + size / 2.0,
                    lay["oy"] + r * (size + GAP) + size / 2.0)
        layer, r, c = cell
        return self._iso_v(lay, layer, r + 0.5, c + 0.5)

    def cell_rect(self, cell):
        """Flat boards only: the slot's rectangle."""
        lay = self._layout()
        size = lay["size"]
        r, c = cell
        return pygame.Rect(lay["ox"] + c * (size + GAP),
                           lay["oy"] + r * (size + GAP), size, size)

    def board_bottom(self):
        return self._layout()["bottom"]

    def can_click(self, cell):
        return (self.role == "player"
                and self.phase == game_rules.PHASE_PLAYING
                and self.my_turn
                and not self.paused and not self.reconnecting
                and self.value_at(cell) is None
                and self.flags.get(cell) != self.my_id)

    def cell_at(self, pos):
        lay = self._layout()
        if lay["kind"] == "flat":
            for cell in self.board_cells():
                if self.cell_rect(cell).collidepoint(pos):
                    return cell
            return None
        hw, hh = lay["tw"] / 2.0 * 0.98, lay["th"] / 2.0 * 0.98
        for cell in self.board_cells():
            cx, cy = self._cell_center(cell)
            if abs(pos[0] - cx) / hw + abs(pos[1] - cy) / hh <= 1.0:
                return cell
        return None

    def _send_cell(self, msg_type, cell):
        if self.is_3d:
            layer, row, col = cell
            self._send_game(msg_type, layer=layer, row=row, col=col)
        else:
            row, col = cell
            self._send_game(msg_type, row=row, col=col)

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
        elif self.screen_name == SCREEN_LOBBY:
            self._on_lobby_event(event)
        elif self.screen_name == SCREEN_CREATE_ROOM:
            self._on_create_event(event)
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
                self.nickname = self.nickname.strip()
                self.net.send(protocol.JOIN, nickname=self.nickname)
        elif event.key == pygame.K_BACKSPACE:
            self.nickname = self.nickname[:-1]
        elif event.key == pygame.K_ESCAPE:
            self.running = False
        elif event.unicode and event.unicode.isprintable() and len(self.nickname) < 16:
            self.nickname += event.unicode

    def _on_wheel(self, event):
        if self.screen_name == SCREEN_LOBBY:
            self.room_scroll = max(0, min(max(0, len(self.rooms) - 1),
                                         self.room_scroll - event.y))
            return
        if self.tab != "chat":
            return
        log, _quick, _input = self._chat_rects()
        if log.collidepoint(self.mouse()):
            self.chat_scroll = max(0, self.chat_scroll + event.y * 2)

    def _set_tab(self, name):
        self.tab = name
        if name == "chat":
            self.unread = 0
        else:
            self.chat_focus = False

    def _open_rules(self):
        self.rules_open = True
        self.rules_at = self.now
        self.theme_open = False
        self.chat_focus = False

    def _close_rules(self):
        self.rules_open = False
        if not self.prefs.get("rules_seen"):
            self.prefs["rules_seen"] = True
            self._remember()

    def _toggle_sound(self):
        self.sound.toggle()
        self._remember()
        self.say("Sound off" if self.sound.muted else "Sound on", 1.5)

    def _toggle_fx(self):
        self.fx_on = not self.fx_on
        if not self.fx_on:
            self.particles.clear()
            self.rings.clear()
            self.shake = 0.0
        self._remember()
        self.say("Animations on" if self.fx_on else "Animations off", 1.5)

    def _toggle_ranked(self):
        self.say("Room settings cannot change during a match", 1.5)

    def _set_theme(self, name):
        apply_theme(name)
        self._remember()
        self.say("Theme: %s" % THEME_LABEL, 1.5)

    def _next_theme(self):
        self._set_theme(themes.next_theme(THEME_NAME))

    def _ask_coach(self):
        if self.role != "player":
            self.say("Only players can ask the coach")
        elif self.phase != game_rules.PHASE_PLAYING:
            self.say("The coach helps during a match")
        elif not self.my_turn:
            self.say("Wait for your turn, then ask the coach")
        else:
            self._send_game(protocol.HINT)

    def _send_chat(self, text):
        text = text.strip()
        if text:
            self._send_game(protocol.CHAT, text=text)

    def _on_game_key(self, event):
        if self.rules_open:
            if event.key in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_F1,
                             pygame.K_SPACE) or event.unicode == "?":
                self._close_rules()
            return
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
            self._set_tab("chat")
            self.chat_focus = True
        elif event.key == pygame.K_TAB:
            names = [n for n, _l in TABS]
            self._set_tab(names[(names.index(self.tab) + 1) % len(names)])
        elif event.key == pygame.K_m:
            self._toggle_sound()
        elif event.key == pygame.K_t:
            self._next_theme()
        elif event.key == pygame.K_a:
            self._toggle_fx()
        elif event.key == pygame.K_h:
            self._ask_coach()
        elif event.key == pygame.K_F1 or event.unicode == "?":
            self._open_rules()
        elif event.key == pygame.K_ESCAPE:
            self.theme_open = False
            self.settings_open = False

    def _theme_items(self):
        """The theme menu: one row per palette, under the Theme button."""
        n = len(themes.ORDER)
        box = pygame.Rect(self.theme_rect.right - 230, self.theme_rect.bottom + 8,
                          230, n * 34 + 12)
        return box, [(name, pygame.Rect(box.x + 6, box.y + 6 + i * 34,
                                        box.w - 12, 32))
                     for i, name in enumerate(themes.ORDER)]

    def _on_game_event(self, event):
        if event.type != pygame.MOUSEBUTTONDOWN or event.button not in (1, 3):
            return
        if self.rules_open:
            # the rules card swallows every click; "Got it" or a click outside closes it
            card = self._rules_card()
            if (self.rules_close.collidepoint(event.pos)
                    or not card.collidepoint(event.pos)):
                self._close_rules()
            return
        if self.theme_open:
            # the menu swallows the click: pick a theme, or just close
            self.theme_open = False
            if event.button == 1:
                for name, rect in self._theme_items()[1]:
                    if rect.collidepoint(event.pos):
                        self._set_theme(name)
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
            if self.theme_rect.collidepoint(event.pos):
                self.theme_open = True
                return
            if self.rules_rect.collidepoint(event.pos):
                self._open_rules()
                return
            if self.leave_rect.collidepoint(event.pos):
                self._send_game(protocol.LEAVE_ROOM)
                return
        if self.reconnecting:
            return
        if self.match_end is not None:
            if (event.button == 1 and not self.voted_rematch
                    and self._end_progress() > 0.5
                    and self.rematch_rect.collidepoint(event.pos)):
                if self.role == "player":
                    self._send_game(protocol.REMATCH)
                    self.voted_rematch = True
            return
        if self._settings_click(event):
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
            away = (self.state or {}).get("away") or [{}]
            self.say("Paused - waiting for %s to reconnect"
                     % away[0].get("name", "a player"))
        elif self.flags.get(hit) == self.my_id:
            self.say("Right-click to lift your flag first")
        elif self.role != "player":
            self.say("You're spectating this match")
        elif self.phase != game_rules.PHASE_PLAYING:
            self.say("No match is running yet")
        elif not self.my_turn:
            self.say("It's not your turn yet")
        else:
            self.say("That slot is already open")

    # ------------------------------------------------------------------
    # the side panel: three tabs
    # ------------------------------------------------------------------
    def _side_layout(self):
        x, w = GAME_W + 12, SIDE_W - 24
        top, bottom = 64, WIN_H - 16
        return {
            "opponent": pygame.Rect(x, top, w, 118),
            "coach": pygame.Rect(x, top + 130, w, 206),
            "tips": pygame.Rect(x, top + 348, w, bottom - (top + 348)),
            "chat": pygame.Rect(x, top, w, bottom - top),
            "ranks": pygame.Rect(x, top, w, 316),
            "online": pygame.Rect(x, top + 328, w, bottom - (top + 328)),
        }

    def _tab_rects(self):
        x, w = GAME_W + 12, SIDE_W - 24
        gap = 6
        tw = (w - gap * (len(TABS) - 1)) // len(TABS)
        return [(name, label, pygame.Rect(x + i * (tw + gap), 16, tw, 36))
                for i, (name, label) in enumerate(TABS)]

    def _opponent_buttons(self):
        card = self.cards["opponent"]
        first = 84                                # "Friend" needs the room
        bw = (card.w - 28 - first - 3 * 6) // 3
        x, rects = card.x + 14, []
        for i, (level, label) in enumerate(OPPONENTS):
            width = first if i == 0 else bw
            rects.append((level, label, pygame.Rect(x, card.y + 42, width, 32)))
            x += width + 6
        return rects

    def _coach_buttons(self):
        card = self.cards["coach"]
        ask = pygame.Rect(card.x + 14, card.y + 42, 176, 34)
        odds = pygame.Rect(ask.right + 8, card.y + 42, card.w - 28 - 176 - 8, 34)
        return ask, odds

    def _chat_rects(self):
        card = self.cards["chat"]
        log = pygame.Rect(card.x + 14, card.y + 42, card.w - 28, card.h - 42 - 100)
        bw = (card.w - 28 - 3 * 6) // 4
        quick = [pygame.Rect(card.x + 14 + i * (bw + 6), card.bottom - 92, bw, 30)
                 for i in range(4)]
        field = pygame.Rect(card.x + 14, card.bottom - 54, card.w - 28, 40)
        return log, quick, field

    def _side_click(self, pos):
        """Handle a click in the side panel.  True when it landed there."""
        if pos[0] < GAME_W:
            return False
        for name, _label, rect in self._tab_rects():
            if rect.collidepoint(pos):
                self._set_tab(name)
                return True
        if self.tab == "play":
            for level, _label, rect in self._opponent_buttons():
                if rect.collidepoint(pos):
                    self.say("Opponent is fixed when room is created")
                    return True
            ask, odds = self._coach_buttons()
            if ask.collidepoint(pos):
                self._ask_coach()
                return True
            if odds.collidepoint(pos):
                self.show_odds = not self.show_odds
                return True
        elif self.tab == "chat":
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
        self._draw_tabs()
        if self.tab == "play":
            self._draw_opponent()
            self._draw_coach()
            self._draw_tips()
        elif self.tab == "chat":
            self._draw_chat()
        else:
            self._draw_hall()
            self._draw_online()

    def _draw_tabs(self):
        for name, label, rect in self._tab_rects():
            active = name == self.tab
            self._button(rect, label, active=active, small=False)
            if name == "chat" and self.unread and not active:
                badge = pygame.Rect(0, 0, 22, 18)
                badge.center = (rect.right - 10, rect.y + 4)
                self.gx.rect(self.screen, BAD, badge, border_radius=9)
                self.text(min(self.unread, 9), badge.center, self.f_tiny,
                          BG, center=True)

    def _draw_opponent(self):
        card = self.cards["opponent"]
        self._card(card, "OPPONENT")
        level = (self.state or {}).get("bot_level", "off")
        for value, label, rect in self._opponent_buttons():
            self._button(rect, label, active=value == level, enabled=False)
        seated = (self.state or {}).get("bot_seated")
        if seated:
            name = next((p["name"] for p in self.players if p.get("bot")), "Computer")
            line = "You vs %s" % name
            if (self.state or {}).get("bot_learned"):
                line += " (trained)"
        elif len(self.players) >= 2:
            line = "Two players, head to head"
        else:
            line = "Waiting for another player"
        self.text(self.fit(line, self.f_small, card.w - 28),
                  (card.x + 14, card.y + 84), self.f_small, MUTED)

    def _draw_coach(self):
        card = self.cards["coach"]
        self._card(card, "AI COACH")
        left = self._my_hints_left()
        ask, odds = self._coach_buttons()
        self._button(ask, "Ask the coach  (%d left)" % left,
                     enabled=left > 0 and self.my_turn)
        self._button(odds, "Odds on" if self.show_odds else "Odds off",
                     active=self.show_odds)
        y = card.y + 92
        if self.hint:
            cell = self.hint["cell"]
            names = ("layer", "row", "col") if self.is_3d else ("row", "col")
            where = ", ".join("%s %d" % (n, v + 1) for n, v in zip(names, cell))
            avoid = self.hint["goal"] == "avoid"
            chance = round(100 * (1 - self.hint["p"] if avoid else self.hint["p"]))
            self.text("Try  %s" % where, (card.x + 16, y), self.f_head, TEXT)
            self.text("%s chance: %d%%" % ("Safe" if avoid else "Bomb", chance),
                      (card.x + 16, y + 30), self.f_body, GOOD if chance >= 50 else WARN)
            if not self.hint["exact"]:
                self.text("an estimate - big board", (card.x + 16, y + 58),
                          self.f_small, MUTED)
            else:
                self.text("Shaded slots show the odds", (card.x + 16, y + 58),
                          self.f_small, MUTED)
        else:
            for line in self.wrap("On your turn, ask for the best slot and the "
                                  "odds for every covered slot.", self.f_small,
                                  card.w - 32):
                self.text(line, (card.x + 16, y), self.f_small, MUTED)
                y += 20

    def _draw_tips(self):
        card = self.cards["tips"]
        self._card(card, "QUICK GUIDE")
        rows = [("Click", "open a slot"), ("Right-click", "plant or lift a flag"),
                ("H", "ask the coach"), ("Tab", "switch panels"),
                ("T", "change theme"), ("M", "sound on / off"),
                ("A", "animations on / off"), ("Enter", "type in chat")]
        y = card.y + 42
        for key, what in rows:
            if y + 24 > card.bottom - 8:
                break
            chip = pygame.Rect(card.x + 16, y, 92, 24)
            self.gx.rect(self.screen, PANEL_2, chip, border_radius=6)
            self.gx.rect(self.screen, LINE, chip, width=1, border_radius=6)
            self.text(key, chip.center, self.f_small, TEXT, center=True)
            self.text(what, (chip.right + 12, y + 3), self.f_small, MUTED)
            y += 31

    def _draw_hall(self):
        card = self.cards["ranks"]
        mode_label = (self.state or {}).get("mode_label")
        mode = (self.state or {}).get("mode", "classic")
        ranked_modes = getattr(config, "ELO_RANKED_MODES",
                               ("classic", "radius2", "sweeper", "cube"))
        if mode in ranked_modes and mode_label:
            title = self.fit(("HALL OF FAME - %s" % mode_label).upper(),
                             self.f_card, card.w - 32)
        else:
            title = "HALL OF FAME"
        self._card(card, title)
        if not self.leaderboard:
            self.text("Win a match to get on the board.",
                      (card.x + 16, card.y + 46), self.f_small, MUTED)
            return
        my_name = next((p["name"] for p in self.players
                        if p["id"] == self.my_id), None)
        y = card.y + 42
        medals = [WARN, (190, 198, 210), (205, 127, 50)]
        for rank, row in enumerate(self.leaderboard[:10], start=1):
            if y + 26 > card.bottom - 6:
                break
            mine = row["name"] == my_name
            if mine:
                self.gx.rect(self.screen, PANEL_2,
                                 pygame.Rect(card.x + 8, y - 4, card.w - 16, 28),
                                 border_radius=8)
            badge = pygame.Rect(card.x + 16, y, 22, 20)
            if rank <= 3:
                self.gx.circle(self.screen, medals[rank - 1], badge.center, 10)
                self.text(rank, badge.center, self.f_tiny, (24, 24, 30), center=True)
            else:
                self.text(rank, badge.center, self.f_small, MUTED, center=True)
            elo_val = row.get("elo")
            tier_name = row.get("tier", "Bronze")
            tier_col = row.get("tier_color", WARN)
            if elo_val is not None:
                stat = "%s %d  %dW %dL" % (tier_name[:4].upper(), elo_val,
                                           row["wins"], row["losses"])
            else:
                stat = "%dW %dL  %d" % (row["wins"], row["losses"], row["points"])
            stat_w = self._tw(self.f_small, stat)
            name_w = max(40, card.w - 32 - 30 - stat_w - 8)
            self.text(self.fit(row["name"], self.f_small, name_w),
                      (card.x + 46, y + 1), self.f_small, GOOD if mine else TEXT)
            self.text(stat, (card.right - 16, y + 1), self.f_small,
                      tier_col if elo_val is not None else WARN, right=True)
            y += 30

    def _draw_online(self):
        card = self.cards["online"]
        self._card(card, "ONLINE  %d" % self.clients.get("count", 0))
        y = card.y + 42
        entries = []
        for c in self.clients.get("list", []):
            tag = ""
            if c.get("away"):
                tag = "away"
            elif c.get("role") != "player":
                tag = "watching"
            entries.append((c["name"], tag, c.get("id") == self.my_id))
        entries += [(b["name"], "bot", False) for b in self.clients.get("bots", [])]
        for name, tag, me in entries:
            if y + 22 > card.bottom - 6:
                self.text("...and more", (card.x + 16, y), self.f_small, MUTED)
                break
            dot = WARN if tag == "away" else (MUTED if tag in ("watching", "bot") else GOOD)
            self.gx.circle(self.screen, dot, (card.x + 22, y + 10), 5)
            self.text(self.fit(name + ("  (you)" if me else ""), self.f_small,
                               card.w - 120), (card.x + 38, y + 2), self.f_small,
                      TEXT)
            if tag:
                self.text(tag, (card.right - 16, y + 2), self.f_small, MUTED,
                          right=True)
            y += 26
        if not entries:
            self.text("Nobody yet", (card.x + 16, y), self.f_small, MUTED)

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
        self.text(hint, (card.right - 16, card.y + 13), self.f_small, MUTED,
                  right=True)
        log, quick, field = self._chat_rects()
        line_h = 20
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

        self.gx.rect(self.screen, BG, field, border_radius=10)
        self.gx.rect(self.screen, ACCENT if self.chat_focus else LINE, field,
                         width=2 if self.chat_focus else 1, border_radius=10)
        shown = self.chat_input
        caret = "|" if self.chat_focus and (pygame.time.get_ticks() // 500) % 2 == 0 else ""
        if not shown and not self.chat_focus:
            self.text("Click here or press Enter", (field.x + 12, field.y + 11),
                      self.f_small, MUTED)
        else:
            while shown and self._tw(self.f_small, shown + caret) > field.width - 24:
                shown = shown[1:]
            self.text(shown + caret, (field.x + 12, field.y + 11), self.f_small, TEXT)

    # ------------------------------------------------------------------
    # drawing
    # ------------------------------------------------------------------
    def draw(self):
        now = time.monotonic()
        self.dt = min(0.1, max(0.0, now - self._last_frame))
        self._last_frame = now
        self.now = now
        self._fit_window()
        self._update_fx(self.dt)
        self._blit_bg()
        if self.screen_name == SCREEN_NICKNAME:
            self._draw_nickname()
        elif self.screen_name == SCREEN_LOBBY:
            self._draw_lobby()
        elif self.screen_name == SCREEN_CREATE_ROOM:
            self._draw_create_room()
        elif self.screen_name == SCREEN_ERROR:
            self._draw_error()
        else:
            self._draw_game()
        self._draw_fx()
        self._draw_toast()
        self._present()

    def _blit_bg(self):
        """A soft vertical gradient; the game screen adds a shaded strip
        behind the tabs.  Built at the real pixel size, once per theme."""
        if self._bg_version != THEME_VERSION or self._bg_surface is None:
            cw, ch = self.screen.get_size()
            surf = pygame.Surface((cw, ch))
            top = mix(BG, ACCENT, 0.06)
            for y in range(ch):
                pygame.draw.line(surf, mix(top, BG, y / (ch * 0.55)),
                                 (0, y), (cw, y))
            game = surf.copy()
            sx = int(round(GAME_W * self.S))
            shade = pygame.Surface((cw - sx, ch), pygame.SRCALPHA)
            shade.fill((0, 0, 0, 14 if IS_LIGHT else 56))
            game.blit(shade, (sx, 0))
            pygame.draw.line(game, LINE, (sx, 0), (sx, ch))
            self._bg_surface = surf
            self._bg_game = game
            self._bg_version = THEME_VERSION
        self.screen.blit(self._bg_game if self.screen_name == SCREEN_GAME
                         else self._bg_surface, (0, 0))

    def _fit_window(self):
        """If the window changed size, redraw at the new size instead of
        stretching the old picture - that stretching is what blurred text."""
        window = pygame.display.get_surface() or self.window
        ww, wh = window.get_size()
        want = self._quantise(min(ww / float(WIN_W), wh / float(WIN_H)))
        if abs(want - self.S) > 1e-6:
            self._apply_scale(want)

    def _present(self):
        """Centre the canvas in the window.  It is already the right size,
        so nothing is resampled."""
        window = pygame.display.get_surface() or self.window
        self.window = window
        ww, wh = window.get_size()
        cw, ch = self.screen.get_size()
        ox, oy = (ww - cw) // 2, (wh - ch) // 2
        jolt = (0, 0)
        if self.shake > 0.4:
            jolt = (int(random.uniform(-1, 1) * self.shake * self.S),
                    int(random.uniform(-1, 1) * self.shake * self.S))
        if ox or oy or jolt != (0, 0):
            window.fill(BG)
        window.blit(self.screen, (ox + jolt[0], oy + jolt[1]))
        self._view = (ox, oy, cw / float(WIN_W))
        pygame.display.flip()

    @staticmethod
    def _start_buttons():
        """Who to play, on the start screen: a person, or the computer."""
        mid, gap = WIN_W // 2, 8
        width = (440 - 3 * gap) // 4
        return [(level, label,
                 pygame.Rect(mid - 220 + i * (width + gap), 470, width, 40))
                for i, (level, label) in enumerate(OPPONENTS)]

    def _draw_nickname(self):
        mid = WIN_W // 2
        now = self.now
        # a big bomb, bobbing gently, with a live fuse
        bob = math.sin(now * 1.6) * 6
        self._draw_bomb((mid, 150 + bob), 40, now, glow=1.0)
        self.text("FIND MY MINES", (mid, 270), self.f_splash, TEXT, center=True)
        self.text("A two-player bomb hunt", (mid, 318), self.f_body, MUTED,
                  center=True)

        box = pygame.Rect(mid - 220, 378, 440, 60)
        self._panel(box, radius=12)
        self.gx.rect(self.screen, ACCENT, box, width=2, border_radius=12)
        caret = "|" if (pygame.time.get_ticks() // 500) % 2 == 0 else " "
        if self.nickname:
            self.text(self.nickname + caret, box.center, self.f_head, TEXT,
                      center=True)
        else:
            self.text("Type a nickname" + caret, box.center, self.f_head, MUTED,
                      center=True)
        self.text("YOUR NICKNAME", (box.x + 4, box.y - 22), self.f_card, MUTED)

        self.text("Create or join a room after choosing your nickname",
                  (mid, 490), self.f_small, MUTED, center=True)

        if self.net.status == "connected":
            ready = bool(self.nickname.strip())
            self.text("Press ENTER to continue" if ready
                      else "Pick a nickname to start",
                      (mid, 576), self.f_body, GOOD if ready else MUTED,
                      center=True)
        else:
            self.text("Connecting to the server...", (mid, 576), self.f_body,
                      WARN, center=True)

        # proof for the demo that the address comes from the source, not the user
        self.text("server %s  (set in config.py)" % self.net.address,
                  (mid, 640), self.f_small, MUTED, center=True)

    def _room_rects(self, index):
        y = 148 + index * 82
        return pygame.Rect(50, y, WIN_W - 100, 72), pygame.Rect(WIN_W - 300, y + 18, 100, 36), pygame.Rect(WIN_W - 188, y + 18, 100, 36)

    def _draw_lobby(self):
        self.text("ROOM LOBBY", (WIN_W // 2, 48), self.f_title, TEXT, center=True)
        self.text("Playing as %s" % self.nickname, (52, 102), self.f_body, MUTED)
        create = pygame.Rect(WIN_W - 300, 88, 120, 38)
        refresh = pygame.Rect(WIN_W - 168, 88, 116, 38)
        self._button(create, "Create room", active=True)
        self._button(refresh, "Refresh")
        visible = max(1, (WIN_H - 180) // 82)
        for offset, room in enumerate(self.rooms[self.room_scroll:self.room_scroll + visible]):
            card, join, watch = self._room_rects(offset)
            self.gx.rect(self.screen, PANEL, card, border_radius=10)
            self.gx.rect(self.screen, LINE, card, width=1, border_radius=10)
            phase = room.get("phase", "waiting")
            mode = room.get("mode_label", room.get("mode", ""))
            ranked = "Ranked" if room.get("ranked") and room.get("rated") else "Unrated"
            bot = room.get("bot_level", "off")
            info = "%s · %s · %s · %s · %d/2 players · %d spectators" % (
                mode, ranked, ("bot " + bot) if bot != "off" else "2 players",
                phase, room.get("players", 0), room.get("spectators", 0))
            self.text(self.fit(room.get("name", "Room"), self.f_head, 400),
                      (card.x + 16, card.y + 10), self.f_head, TEXT)
            self.text(self.fit(info, self.f_small, card.width - 340),
                      (card.x + 16, card.y + 44), self.f_small, MUTED)
            self._button(join, "Join", active=True, enabled=room.get("joinable", False))
            self._button(watch, "Watch")
        if not self.rooms:
            self.text("No rooms yet. Create one to start playing.", (WIN_W // 2, 180), self.f_body, MUTED, center=True)
        self.text("Scroll to browse rooms", (WIN_W // 2, WIN_H - 34), self.f_small, MUTED, center=True)

    def _on_lobby_event(self, event):
        if event.type == pygame.MOUSEWHEEL:
            self._on_wheel(event)
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        if pygame.Rect(WIN_W - 300, 88, 120, 38).collidepoint(event.pos):
            self.create_mode = config.DEFAULT_MODE
            self.create_bot = "off"
            self.create_ranked = True
            self.room_error = ""
            self.screen_name = SCREEN_CREATE_ROOM
        elif pygame.Rect(WIN_W - 168, 88, 116, 38).collidepoint(event.pos):
            self.net.send(protocol.LIST_ROOMS)
        else:
            visible = max(1, (WIN_H - 180) // 82)
            for i, room in enumerate(self.rooms[self.room_scroll:self.room_scroll + visible]):
                _card, join, watch = self._room_rects(i)
                if join.collidepoint(event.pos) and room.get("joinable"):
                    self.net.send(protocol.JOIN_ROOM, room_id=room["id"], watch=False)
                    return
                if watch.collidepoint(event.pos):
                    self.net.send(protocol.JOIN_ROOM, room_id=room["id"], watch=True)
                    return

    def _create_mode_rects(self):
        width, gap = 116, 8
        total = len(game_rules.MODES) * width + (len(game_rules.MODES) - 1) * gap
        x = (WIN_W - total) // 2
        return [(mode, pygame.Rect(x + i * (width + gap), 190, width, 34))
                for i, mode in enumerate(game_rules.MODES)]

    def _draw_create_room(self):
        self.text("CREATE ROOM", (WIN_W // 2, 42), self.f_title, TEXT, center=True)
        self.text("Room name", (170, 100), self.f_body, MUTED)
        namebox = pygame.Rect(330, 92, 520, 42)
        self.gx.rect(self.screen, PANEL, namebox, border_radius=8)
        self.gx.rect(self.screen, ACCENT, namebox, width=1, border_radius=8)
        self.text(self.room_name, (namebox.x + 12, namebox.y + 10), self.f_body, TEXT)
        self.text("Mode", (170, 158), self.f_body, MUTED)
        for mode, rect in self._create_mode_rects():
            self._button(rect, game_rules.MODE_LABELS.get(mode, mode), active=mode == self.create_mode)
        self.text(game_rules.MODE_BLURBS.get(self.create_mode, ""), (WIN_W // 2, 236), self.f_small, MUTED, center=True)
        if self.create_mode == game_rules.MODE_CUSTOM:
            y = 278
            for label, key, step in CUSTOM_STEPS:
                self.text("%s: %s" % (label, self.create_custom[key]), (220, y + 4), self.f_small, TEXT)
                self._button(pygame.Rect(520, y, 40, 30), "-")
                self._button(pygame.Rect(570, y, 40, 30), "+")
                y += 38
            for label, key, choices in CUSTOM_CHOICES:
                self.text(label, (220, y + 4), self.f_small, TEXT)
                x = 520
                for value, title in choices:
                    rect = pygame.Rect(x, y, 102, 30)
                    self._button(rect, title, active=self.create_custom[key] == value)
                    x += 108
                y += 38
            y += 4
        else:
            y = 286
        self.text("Opponent", (220, y + 4), self.f_body, MUTED)
        for i, (level, label) in enumerate(OPPONENTS):
            rect = pygame.Rect(520 + i * 108, y, 102, 34)
            self._button(rect, label, active=self.create_bot == level)
        y += 54
        ranked = pygame.Rect(520, y, 180, 36)
        eligible = self.create_mode != game_rules.MODE_CUSTOM and self.create_bot == "off"
        self._button(ranked, "Ranked: " + ("on" if self.create_ranked and eligible else "off"),
                     active=self.create_ranked and eligible, enabled=eligible)
        if self.create_mode == game_rules.MODE_CUSTOM or self.create_bot != "off":
            self.text("Custom and bot rooms are always unrated.", (720, y + 8), self.f_small, WARN)
        self._button(pygame.Rect(430, WIN_H - 100, 150, 42), "Create", active=True)
        self._button(pygame.Rect(600, WIN_H - 100, 130, 42), "Back")
        if self.room_error:
            self.text(self.fit(self.room_error, self.f_small, 700), (WIN_W // 2, WIN_H - 42), self.f_small, BAD, center=True)

    def _on_create_event(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_BACKSPACE:
            self.room_name = self.room_name[:-1]
            return
        if event.type == pygame.KEYDOWN and event.unicode and event.unicode.isprintable() and len(self.room_name) < 32:
            self.room_name += event.unicode
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        for mode, rect in self._create_mode_rects():
            if rect.collidepoint(event.pos):
                self.create_mode = mode
                return
        y = 278
        if self.create_mode == game_rules.MODE_CUSTOM:
            for _label, key, step in CUSTOM_STEPS:
                if pygame.Rect(520, y, 40, 30).collidepoint(event.pos):
                    self.create_custom[key] -= step
                    self.create_custom = game_rules.clamp_custom(self.create_custom)
                    return
                if pygame.Rect(570, y, 40, 30).collidepoint(event.pos):
                    self.create_custom[key] += step
                    self.create_custom = game_rules.clamp_custom(self.create_custom)
                    return
                y += 38
            for _label, key, choices in CUSTOM_CHOICES:
                for i, (value, _label) in enumerate(choices):
                    if pygame.Rect(520 + i * 108, y, 102, 30).collidepoint(event.pos):
                        self.create_custom[key] = value
                        self.create_custom = game_rules.clamp_custom(self.create_custom)
                        return
                y += 38
            y += 4
        else:
            y = 286
        for i, (level, _label) in enumerate(OPPONENTS):
            if pygame.Rect(520 + i * 108, y, 102, 34).collidepoint(event.pos):
                self.create_bot = level
                return
        y += 54
        if pygame.Rect(520, y, 180, 36).collidepoint(event.pos):
            if self.create_mode != game_rules.MODE_CUSTOM and self.create_bot == "off":
                self.create_ranked = not self.create_ranked
        elif pygame.Rect(430, WIN_H - 100, 150, 42).collidepoint(event.pos):
            self.net.send(protocol.CREATE_ROOM, name=self.room_name.strip() or "Room",
                          mode=self.create_mode, custom=self.create_custom,
                          bot_level=self.create_bot,
                          ranked=self.create_ranked and self.create_mode != game_rules.MODE_CUSTOM and self.create_bot == "off")
        elif pygame.Rect(600, WIN_H - 100, 130, 42).collidepoint(event.pos):
            self.room_error = ""
            self.screen_name = SCREEN_LOBBY

    def _draw_error(self):
        mid = WIN_W // 2
        self._draw_bomb((mid, 190), 30, self.now, glow=0.6)
        self.text("CAN'T REACH THE SERVER", (mid, 260), self.f_head, BAD, center=True)
        if self.net.status == "lost" or self.joined:
            detail = "The connection to %s was closed." % self.net.address
        else:
            detail = "Tried %s - %s" % (self.net.address, self.net.error)
        y = 306
        for line in self.wrap(detail, self.f_body, 760)[:3]:
            self.text(line, (mid, y), self.f_body, TEXT, center=True)
            y += 24
        y += 30
        for hint in ("Is server.py running on that computer?",
                     "Is SERVER_HOST in config.py the server's current IP?",
                     "Is Python allowed through the firewall on the server?",
                     "Are both computers on the same Wi-Fi? (a phone hotspot works)"):
            self.text("- " + hint, (mid - 260, y), self.f_small, MUTED)
            y += 30
        self.text("Press R to try again", (mid, y + 24), self.f_body, ACCENT,
                  center=True)

    def _draw_game(self):
        self._draw_titlebar()
        self._draw_modes()
        self._draw_players()
        self._draw_board()
        self._draw_status()
        self._draw_side()
        if self.settings_open and self.mode == game_rules.MODE_CUSTOM:
            self._draw_settings()
        if self._end_progress() > 0:
            self._draw_end_overlay()
        if self.reconnecting:
            self._draw_reconnect_overlay()
        if self.rules_open:
            self._draw_rules()
        if self.theme_open:
            self._draw_theme_menu()

    def _draw_titlebar(self):
        self._draw_bomb((38, 30), 9, self.now, glow=0.0, fuse=True)
        room_name = getattr(self, "room_name_active", "") or "FIND MY MINES"
        self.text(self.fit(room_name, self.f_title, 210), (60, 14), self.f_title, TEXT)
        self._button(self.sound_rect, "Sound off" if self.sound.muted else "Sound on",
                     active=not self.sound.muted)
        ranked = (self.state or {}).get("rated", (self.state or {}).get("ranked", True))
        self.text("Ranked room" if ranked else "Unrated room", self.ranked_rect.center,
                  self.f_small, ACCENT if ranked else MUTED, center=True)
        self._button(self.theme_rect, "Theme: %s" % THEME_LABEL)
        self._button(self.leave_rect, "Leave room")
        self._button(self.rules_rect, "? Rules")
        # a little chevron on the theme button
        cx, cy = self.theme_rect.right - 14, self.theme_rect.centery
        self.gx.polygon(self.screen, MUTED,
                            [(cx - 4, cy - 2), (cx + 4, cy - 2), (cx, cy + 3)])
        count = self.clients.get("count", 0)
        self.gx.circle(self.screen, GOOD, (self.rules_rect.x - 18, 30), 4)
        self.text("%d online" % count, (self.rules_rect.x - 28, 21), self.f_small,
                  MUTED, right=True)

    def _draw_modes(self):
        """The mode bar - this is where the extra games are found."""
        first = self.mode_rects[0][1]
        last = self.mode_rects[-1][1]
        bar = pygame.Rect(first.x - 5, first.y - 5, last.right - first.x + 10,
                          first.h + 10)
        self._panel(bar, radius=13, shadow=False)
        for mode, box in self.mode_rects:
            active = mode == self.mode
            if active:
                self.gx.rect(self.screen, BTN, box,
                                 border_radius=9)
            self.text(game_rules.MODE_LABELS.get(mode, mode), box.center,
                      self.f_small, BTN_TEXT if active else MUTED, center=True)
        blurb = game_rules.MODE_BLURBS.get(self.mode, "")
        self.text(blurb, (CX, 102), self.f_small, MUTED, center=True)

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
            self.say("Room settings are fixed")
            return True
        return card.collidepoint(event.pos)   # clicks on the card do nothing

    def _veil(self, alpha=None):
        """Dim the board column (the side panel stays usable)."""
        veil = pygame.Surface((int(round(GAME_W * self.S)), self.screen.get_height()),
                              pygame.SRCALPHA)
        veil.fill((*VEIL, VEIL_ALPHA if alpha is None else int(alpha)))
        self.screen.blit(veil, (0, 0))

    # ------------------------------------------------------------------
    # the rules screen
    # ------------------------------------------------------------------
    def _rules_card(self):
        card = pygame.Rect(0, 0, 680, 644)
        card.center = (CX, 450)
        return card

    def _rules_content(self):
        """The rules for the game being played right now, in plain words."""
        st = self.state or {}
        bad = self.bombs_are_bad
        cube = self.is_3d
        weighted = (self.mode == game_rules.MODE_RADIUS2
                    or (self.mode == game_rules.MODE_CUSTOM
                        and self.custom.get("hints") == "radius2"))
        secs = st.get("turn_seconds") or getattr(config, "TURN_SECONDS", 10)
        total = st.get("bombs_total")
        around = ("the 26 slots around it, including the layers above and below"
                  if cube else "the 8 slots around it, diagonals too")
        if weighted:
            number = ("The number adds up the bombs near that slot: a bomb "
                      "touching it counts 2, a bomb one ring further out counts 1.")
        else:
            number = "The number is how many bombs touch that slot - that is %s." % around
        if bad:
            goal = "Clear the safe ground and stay away from the bombs. Most points wins."
            steps = [
                "Take turns opening covered slots. You get %d seconds per turn." % secs,
                "A safe slot scores a point for every slot it opens, and you keep "
                "your turn. A 0 spreads open on its own.",
                number + " Use it to work out where the bombs are.",
                "Open a bomb and you lose your turn. The match ends when all the "
                "safe ground is open.",
            ]
        else:
            goal = "Find more bombs than your rival. Every bomb you find is 1 point."
            steps = [
                "Take turns opening covered slots. You get %d seconds per turn." % secs,
                "Opened a bomb? You score 1 point and go again.",
                "Opened an empty slot? It shows a number and your turn passes. "
                + number,
                "The match ends when all %s bombs are found. Most points wins."
                % (total if total else "the"),
            ]
        if cube:
            steps.append("The board is a stack of layers. Neighbours can be in the "
                         "layers above and below, so check every layer.")
        return goal, steps, weighted, cube

    def _draw_rules_example(self, box, weighted):
        """A tiny picture of what a number means."""
        now = self.now
        if weighted:
            n, size, gap, bombs, centre_val = 5, 24, 3, {(1, 2), (0, 4)}, 3
            caption = "3 = one bomb touching (2) plus one a ring away (1)"
        else:
            n, size, gap, bombs, centre_val = 3, 38, 4, {(0, 1), (2, 2)}, 2
            caption = "2 = two bombs touch this slot"
        total = n * (size + gap) - gap
        x0 = box.centerx - total // 2
        y0 = box.y + 14
        mid = n // 2
        for r in range(n):
            for c in range(n):
                rect = pygame.Rect(x0 + c * (size + gap), y0 + r * (size + gap),
                                   size, size)
                if (r, c) == (mid, mid):
                    self.gx.rect(self.screen, OPENED, rect, border_radius=6)
                    self.gx.rect(self.screen, ACCENT, rect, width=2, border_radius=6)
                    self.text(centre_val, rect.center,
                              self.f_cell_m if size >= 30 else self.f_head,
                              DIGIT_COLOURS.get(centre_val, TEXT), center=True)
                else:
                    near = max(abs(r - mid), abs(c - mid)) == 1
                    self.gx.rect(self.screen, COVERED if near or weighted else
                                 mix(COVERED, BG, 0.4), rect, border_radius=5)
                    if (r, c) in bombs:
                        self.gx.rect(self.screen, BOMB_CELL, rect, border_radius=5)
                        self._draw_bomb(rect.center, size * 0.24, now, glow=0.0,
                                        fuse=False)
        y = y0 + total + 12
        for line in self.wrap(caption, self.f_small, box.width - 8):
            self.text(line, (box.centerx, y), self.f_small, MUTED, center=True)
            y += 18

    def _draw_rules(self):
        p = ease_out_cubic((self.now - self.rules_at) / 0.28) if self.fx_on else 1.0
        self._veil(215 * p)
        card = self._rules_card().move(0, int((1 - p) * 26))
        self._panel(card, radius=18)

        goal, steps, weighted, cube = self._rules_content()
        mode_label = (self.state or {}).get("mode_label") or game_rules.MODE_LABELS.get(
            self.mode, "")
        self.text("HOW TO PLAY", (card.x + 32, card.y + 26), self.f_head, TEXT)
        self.text(mode_label.upper(), (card.right - 32, card.y + 29), self.f_card,
                  ACCENT, right=True)
        self.gx.line(self.screen, LINE, (card.x + 32, card.y + 62),
                     (card.right - 32, card.y + 62))

        # the goal, in one line anyone can remember
        goal_y = card.y + 78
        for line in self.wrap(goal, self.f_body, card.w - 64):
            self.text(line, (card.x + 32, goal_y), self.f_body, GOOD)
            goal_y += 23

        # numbered steps on the left, a worked example on the right
        text_w = 380
        y = goal_y + 14
        for i, step in enumerate(steps, start=1):
            badge = (card.x + 44, y + 11)
            self.gx.circle(self.screen, ACCENT if not self.fx_on else ACCENT,
                           badge, 12)
            self.text(i, badge, self.f_small, BTN_TEXT if not IS_LIGHT else BG,
                      center=True)
            lines = self.wrap(step, self.f_small, text_w)
            ly = y + 2
            for line in lines:
                self.text(line, (card.x + 66, ly), self.f_small, TEXT)
                ly += 19
            y += max(30, len(lines) * 19 + 14)
        steps_bottom = y

        ex = pygame.Rect(card.right - 32 - 200, goal_y + 14, 200, 210)
        self.gx.rect(self.screen, PANEL_2, ex, border_radius=12)
        self.gx.rect(self.screen, LINE, ex, width=1, border_radius=12)
        self._draw_rules_example(ex, weighted)

        # controls
        cy = max(steps_bottom + 8, card.bottom - 212)
        self.gx.line(self.screen, LINE, (card.x + 32, cy), (card.right - 32, cy))
        self.text("CONTROLS", (card.x + 32, cy + 12), self.f_card, MUTED)
        controls = [("Click", "open a covered slot"),
                    ("Right-click", "flag a slot you suspect"),
                    ("H", "ask the AI coach for odds"),
                    ("Tab", "switch Play / Chat / Ranks")]
        for i, (key, what) in enumerate(controls):
            col, row = i % 2, i // 2
            x = card.x + 32 + col * 310
            yy = cy + 38 + row * 34
            chip = pygame.Rect(x, yy, 92, 26)
            self.gx.rect(self.screen, PANEL_2, chip, border_radius=7)
            self.gx.rect(self.screen, LINE, chip, width=1, border_radius=7)
            self.text(key, chip.center, self.f_small, TEXT, center=True)
            self.text(what, (chip.right + 10, yy + 4), self.f_small, MUTED)
        self.text("A flag is only a note to yourself - it never blocks your rival.",
                  (card.x + 32, cy + 110), self.f_small, MUTED)

        self.rules_close = pygame.Rect(0, 0, 180, 44)
        self.rules_close.center = (card.centerx, card.bottom - 56)
        hot = self.rules_close.collidepoint(self.mouse())
        self.gx.rect(self.screen, BTN_HOT if hot else BTN, self.rules_close,
                     border_radius=12)
        self.text("GOT IT", self.rules_close.center, self.f_head, BTN_TEXT,
                  center=True)
        self.text("Open this any time with ? or the Rules button",
                  (card.centerx, card.bottom - 18), self.f_tiny, MUTED, center=True)

    def _draw_settings(self):
        card, controls, close = self._settings_widgets()
        self._veil(205)
        self._panel(card, radius=16)

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
                self.gx.rect(self.screen, PANEL_2 if hot else BG, rect,
                                 border_radius=6)
                self.gx.rect(self.screen, LINE, rect, width=1, border_radius=6)
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
                self.gx.rect(self.screen, fill, rect, border_radius=6)
                self.gx.rect(self.screen, ACCENT if active else LINE, rect,
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
        self.gx.rect(self.screen, BTN_HOT if hot else BTN, close, border_radius=9)
        self.text("PLAY", close.center, self.f_body, BTN_TEXT, center=True)

    def _draw_theme_menu(self):
        box, items = self._theme_items()
        self._panel(box, radius=14)
        mouse = self.mouse()
        for name, rect in items:
            theme = themes.THEMES[name]
            active = name == THEME_NAME
            if active:
                self.gx.rect(self.screen, PANEL_2, rect, border_radius=8)
                self.gx.rect(self.screen, ACCENT, rect, width=1, border_radius=8)
            elif rect.collidepoint(mouse):
                self.gx.rect(self.screen, PANEL_2, rect, border_radius=8)
            x = rect.x + 10
            for key in ("bg", "covered", "accent", "p2", "good"):
                chip = pygame.Rect(x, rect.centery - 8, 16, 16)
                self.gx.rect(self.screen, theme[key], chip, border_radius=4)
                self.gx.rect(self.screen, theme["line"], chip, width=1,
                                 border_radius=4)
                x += 19
            self.text(theme["label"], (x + 10, rect.y + 7), self.f_small,
                      TEXT if active else MUTED)

    # ------------------------------------------------------------------
    # the two players and the clock
    # ------------------------------------------------------------------
    def _rank_tag(self, p):
        state = self.state or {}
        if p.get("bot"):
            return "Computer"
        if not state.get("rated", state.get("ranked", True)):
            return "Casual"
        mode = state.get("mode", "classic")
        ranked_modes = getattr(config, "ELO_RANKED_MODES",
                               ("classic", "radius2", "sweeper", "cube"))
        elo_val = p.get("elo")
        if elo_val is None or mode not in ranked_modes:
            return ""
        tier = p.get("tier", "Bronze")
        if p.get("provisional"):
            return "%s - trial %d/5 - %d" % (tier, p.get("mode_matches", 0), elo_val)
        return "%s - %d" % (tier, elo_val)

    def _draw_players(self):
        players = self.players
        state = self.state or {}
        current = state.get("current_turn")
        away = {a["id"] for a in state.get("away", [])}
        playing = self.phase == game_rules.PHASE_PLAYING
        left_card = pygame.Rect(24, 122, 304, 78)
        right_card = pygame.Rect(GAME_W - 24 - 304, 122, 304, 78)
        for index, rect in enumerate((left_card, right_card)):
            p = players[index] if index < len(players) else None
            self._draw_player_card(rect, index, p, p is not None
                                   and p["id"] == current and playing,
                                   p is not None and p["id"] in away)
        self._draw_clock()

    def _draw_player_card(self, rect, index, p, active, away):
        now = self.now
        colour = (P1, P2)[index]
        self._panel(rect, radius=14)
        stripe = pygame.Rect(rect.x if index == 0 else rect.right - 6,
                             rect.y + 12, 6, rect.h - 24)
        self.gx.rect(self.screen, colour, stripe, border_radius=3)
        if active:
            pulse = 0.5 + 0.5 * math.sin(now * 5)
            glow = mix(LINE, GOOD, 0.55 + 0.45 * pulse)
            self.gx.rect(self.screen, glow, rect.inflate(2, 2), width=2,
                             border_radius=15)
        if p is None:
            name, score, name_col = "Waiting for a player...", "-", MUTED
            tag = ""
        else:
            name = p["name"] + ("  (you)" if p["id"] == self.my_id else "")
            if away:
                name += "  (away)"
            name_col = GOOD if active else (WARN if away else TEXT)
            score = p.get("score", 0)
            tag = self._rank_tag(p)
        left = index == 0
        pad = 20
        name_w = rect.w - pad - 84
        dot_shift = 0
        if active:
            dot_shift = 16
        name_x = rect.x + pad + dot_shift if left else rect.right - pad
        font = self.f_head
        shown = self.fit(name, font, name_w - dot_shift)
        if left:
            r = self.text(shown, (name_x, rect.y + 12), font, name_col)
            dot_x = rect.x + pad + 4
        else:
            r = self.text(shown, (name_x, rect.y + 12), font, name_col, right=True)
            dot_x = r.x - 14
        if active:
            pulse = 0.5 + 0.5 * math.sin(now * 5)
            self.gx.circle(self.screen, GOOD, (dot_x, rect.y + 23),
                               int(4 + 2 * pulse))
        if tag:
            ref = rect.x + pad + dot_shift if left else rect.right - pad
            self.text(self.fit(tag, self.f_small, name_w), (ref, rect.y + 44),
                      self.f_small, MUTED, right=not left)
        elif active and p is not None:
            msg = "Your move" if p["id"] == self.my_id else "Thinking..."
            ref = rect.x + pad + dot_shift if left else rect.right - pad
            self.text(msg, (ref, rect.y + 44), self.f_small, GOOD, right=not left)
        # the score sits on the side facing the clock, and bumps when it grows
        sx = rect.right - 18 - 24 if left else rect.x + 18 + 24
        bump = 1.0
        if p is not None and p["id"] in self.score_bump:
            age = (now - self.score_bump[p["id"]]) / 0.5
            if age < 1.0:
                bump = 1.0 + 0.55 * math.sin(age * math.pi)
        self.text_scaled(score, (sx, rect.centery), self.f_score,
                         WARN if p is not None else MUTED, bump)

    def _draw_clock(self):
        state = self.state or {}
        running = self.phase == game_rules.PHASE_PLAYING and not self.paused
        left = self.seconds_left if running else 0
        total = float(self.custom.get("turn_seconds", 0)
                      if self.mode == game_rules.MODE_CUSTOM
                      else getattr(config, "TURN_SECONDS", 20)) or 20.0
        target = clamp(left / total) if running else 0.0
        self.timer_fill += (target - self.timer_fill) * min(1.0, self.dt * 6)
        colour = MUTED if not running else (BAD if left <= 3 else
                                            (WARN if left <= 6 else ACCENT))
        scale = 1.0
        if running and left <= 3 and self.my_turn:
            scale = 1.0 + 0.12 * abs(math.sin(self.now * 6))
        self.text_scaled("0:%02d" % left, (CX, 142), self.f_clock, colour, scale)
        bar = pygame.Rect(CX - 78, 172, 156, 8)
        self.gx.rect(self.screen, PANEL_2, bar, border_radius=4)
        if self.timer_fill > 0.01:
            fill = pygame.Rect(bar.x, bar.y, max(8, int(bar.w * self.timer_fill)),
                               bar.h)
            self.gx.rect(self.screen, colour, fill, border_radius=4)
        self.gx.rect(self.screen, LINE, bar, width=1, border_radius=4)
        label = "turn timer" if running else (
            "paused" if self.paused else "")
        if label:
            self.text(label, (CX, 190), self.f_tiny, MUTED, center=True)

    # ------------------------------------------------------------------
    # bombs
    # ------------------------------------------------------------------
    def _draw_bomb(self, centre, r, now, glow=1.0, appear=1.0, fuse=True):
        """A glowing round bomb with a burning fuse."""
        cx, cy = centre
        r = max(4.0, r * appear)
        pulse = 0.5 + 0.5 * math.sin(now * 4.0 + cx * 0.05)
        if glow > 0 and self.fx_on:
            S = self.S
            gr = int(r * (2.3 + 0.35 * pulse) * S)
            if gr > 2:
                surf = pygame.Surface((gr * 2, gr * 2), pygame.SRCALPHA)
                for i, a in ((3, 20), (2, 34), (1, 52)):
                    pygame.draw.circle(
                        surf, (*BOMB_GLOW, int(a * glow * (0.7 + 0.3 * pulse))),
                        (gr, gr), max(1, int(gr * i / 3.0)))
                self.screen.blit(surf, (int(cx * S - gr), int(cy * S - gr)))
        body = BOMB_BODY
        self.gx.circle(self.screen, body, (int(cx), int(cy)), int(r))
        self.gx.circle(self.screen, lighten(body, 0.28), (int(cx), int(cy)),
                           int(r), max(1, int(r * 0.1)))
        # shine
        hl = (int(cx - r * 0.36), int(cy - r * 0.38))
        self.gx.circle(self.screen, lighten(body, 0.55), hl,
                           max(2, int(r * 0.26)))
        self.gx.circle(self.screen, lighten(body, 0.85),
                           (hl[0] - 1, hl[1] - 1), max(1, int(r * 0.12)))
        # the neck cap
        cap = pygame.Rect(0, 0, max(4, int(r * 0.62)), max(3, int(r * 0.34)))
        cap.center = (int(cx + r * 0.52), int(cy - r * 0.86))
        self.gx.rect(self.screen, lighten(body, 0.22), cap, border_radius=2)
        if not fuse:
            return
        # the fuse curls up and out; the spark flickers at its tip
        p0 = (cap.centerx, cap.y)
        p2 = (cx + r * 1.22, cy - r * 1.78)
        p1 = (cx + r * 0.72, cy - r * 1.72)
        pts = []
        for i in range(9):
            t = i / 8.0
            x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0]
            y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]
            pts.append((x, y))
        self.gx.lines(self.screen, (214, 188, 140), False, pts,
                          max(2, int(r * 0.13)))
        flick = 0.5 + 0.5 * math.sin(now * 26 + cx)
        sr = max(2.0, r * (0.26 + 0.14 * flick))
        sx, sy = int(p2[0]), int(p2[1])
        for k in range(6):
            a = now * 3 + k * math.pi / 3
            ln = sr * (1.6 + 0.8 * flick)
            self.gx.line(self.screen, SPARK, (sx, sy),
                             (int(sx + math.cos(a) * ln), int(sy + math.sin(a) * ln)), 1)
        self.gx.circle(self.screen, SPARK, (sx, sy), int(sr))
        self.gx.circle(self.screen, (255, 255, 255), (sx, sy), max(1, int(sr * 0.5)))

    def _draw_flag(self, centre, ref, mine, age):
        """A little pennant that drops in.  Yours is bright; theirs is muted."""
        colour = WARN if mine else MUTED
        drop = (1 - ease_out_back(age / 0.35)) if age < 0.35 else 0.0
        cx, cy = centre
        cy += drop * -ref * 0.5
        half = max(6, int(ref * 0.17))
        pole_x = int(cx - max(3, half // 2))
        self.gx.line(self.screen, colour, (pole_x, cy - half), (pole_x, cy + half), 2)
        self.gx.polygon(self.screen, colour, [
            (pole_x + 1, cy - half), (pole_x + 1 + half, cy - half // 2),
            (pole_x + 1, cy)])
        self.gx.line(self.screen, darken(colour, 0.4),
                         (pole_x - 3, cy + half), (pole_x + 4, cy + half), 2)

    # ------------------------------------------------------------------
    # the board
    # ------------------------------------------------------------------
    def _update_hover(self, hot):
        k = 1.0 if not self.fx_on else min(1.0, self.dt * 16)
        if hot is not None:
            v = self.hover.get(hot, 0.0)
            self.hover[hot] = v + (1.0 - v) * k
        for c in list(self.hover):
            if c != hot:
                v = self.hover[c] * (1 - k)
                if v < 0.02:
                    del self.hover[c]
                else:
                    self.hover[c] = v

    def _draw_board(self):
        lay = self._layout()
        mouse = self.mouse()
        now = self.now
        overlay_up = ((self.settings_open and self.mode == game_rules.MODE_CUSTOM)
                      or self.theme_open or self.rules_open
                      or self.match_end is not None)
        hot = None
        if not overlay_up and mouse[0] < GAME_W:
            cand = self.cell_at(mouse)
            if cand is not None and self.can_click(cand):
                hot = cand
        self._update_hover(hot)
        self._set_cursor(hot is not None)

        ctx = {
            "lay": lay, "now": now, "flags": self.flags,
            "owners": self.bomb_owners,
            "order": {p["id"]: i for i, p in enumerate(self.players)},
            "hint": self.hint,
            "heat": self.hint["heat"] if self.hint and self.show_odds else None,
        }
        if lay["kind"] == "iso":
            for layer in range(lay["layers"]):
                self._draw_slab(lay, layer)
            order = sorted(self.board_cells(),
                           key=lambda c: (c[0], c[1] + c[2]))
        else:
            order = self.board_cells()
        for cell in order:
            self._draw_cell(cell, ctx)

        last_cell, last_by = self.last_move_cell
        if last_cell is not None and self.value_at(last_cell) is not None \
                and self.reveal_times.get(last_cell, 0) <= now:
            index = ctx["order"].get(last_by)
            colour = (P1, P2)[index] if index in (0, 1) else WARN
            width = 2 + int(abs(math.sin(now * 3)) * 1.5)
            self._outline_cell(last_cell, colour, width, grow=3)

    def _set_cursor(self, hand):
        if hand == self._cursor:
            return
        self._cursor = hand
        try:
            pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_HAND if hand
                                    else pygame.SYSTEM_CURSOR_ARROW)
        except Exception:
            pass

    def _outline_cell(self, cell, colour, width, grow=0):
        lay = self._layout()
        if lay["kind"] == "flat":
            rect = self.cell_rect(cell).inflate(grow * 2, grow * 2)
            self.gx.rect(self.screen, colour, rect, width=width,
                             border_radius=11)
            return
        cx, cy = self._cell_center(cell)
        hw = lay["tw"] / 2.0 * ISO_INSET + grow
        hh = lay["th"] / 2.0 * ISO_INSET + grow * 0.5
        self.gx.polygon(self.screen, colour,
                            [(cx, cy - hh), (cx + hw, cy), (cx, cy + hh),
                             (cx - hw, cy)], width)

    def _draw_slab(self, lay, layer):
        """The base plate one 3D layer sits on, with its label."""
        rows, cols = lay["rows"], lay["cols"]
        t = self._iso_v(lay, layer, 0, 0)
        r = self._iso_v(lay, layer, 0, cols)
        b = self._iso_v(lay, layer, rows, cols)
        l = self._iso_v(lay, layer, rows, 0)
        mx = (t[0] + b[0]) / 2.0
        my = (t[1] + b[1]) / 2.0
        pad = 1.045
        pts = [(mx + (x - mx) * pad, my + (y - my) * pad) for x, y in (t, r, b, l)]
        d = lay["depth"] * 0.85
        tint = layer / float(max(1, lay["layers"] - 1))
        top = mix(mix(PANEL, BG, 0.25), ACCENT, 0.07 * tint)
        sh = self.gx.polygon
        # a soft shadow under the whole slab
        self._poly(self.screen, [(x + d * 0.7, y + d * 1.4) for x, y in pts],
                   SHADOW)
        self._poly(self.screen, [pts[3], pts[2], (pts[2][0], pts[2][1] + d),
                                 (pts[3][0], pts[3][1] + d)], darken(top, 0.35))
        self._poly(self.screen, [pts[2], pts[1], (pts[1][0], pts[1][1] + d),
                                 (pts[2][0], pts[2][1] + d)], darken(top, 0.5))
        self._poly(self.screen, pts, top)
        sh(self.screen, LINE, pts, 1)
        s = lay["slabs"][layer]
        self.text("LAYER %d" % (layer + 1), (s["left"], s["top"]), self.f_card,
                  mix(MUTED, ACCENT, 0.4 * tint))

    def _draw_cell(self, cell, ctx):
        lay, now = ctx["lay"], ctx["now"]
        iso = lay["kind"] == "iso"
        value = self.value_at(cell)
        start = self.reveal_times.get(cell)
        if value is not None and start is not None and start > now:
            value = None                       # waiting for its ripple turn
        progress = 1.0
        if value is not None and start is not None:
            progress = (now - start) / REVEAL_SECONDS
            if progress >= 1.0:
                del self.reveal_times[cell]
                progress = 1.0
        e = ease_out_cubic(progress)

        centre = self._cell_center(cell)
        ref = lay["tw"] if iso else lay["size"]
        base_lift = lay["depth"] * 0.62 if iso else max(3.0, lay["size"] * 0.08)
        covered = COVERED
        if iso and lay["layers"] > 1:
            covered = mix(COVERED, ACCENT, 0.12 * cell[0] / (lay["layers"] - 1))
        hov = self.hover.get(cell, 0.0)
        flags, owners, order = ctx["flags"], ctx["owners"], ctx["order"]
        radius = 6 if (ref < 40 or iso) else 9

        if value is None:
            colour = mix(covered, COVERED_HOVER, hov)
            heat = ctx["heat"]
            if heat is not None and cell in heat:
                tint = BAD if ctx["hint"]["goal"] == "avoid" else WARN
                colour = mix(colour, tint, 0.18 + 0.55 * heat[cell])
            rise = hov * (base_lift * 0.7 + 2.5)
            if iso:
                self._tile_iso(centre, lay, colour, base_lift + rise,
                               outline=ACCENT if hov > 0.4 else None)
            else:
                self._tile_flat(self.cell_rect(cell), colour, base_lift, rise,
                                radius, outline=ACCENT if hov > 0.4 else None)
            top = (centre[0], centre[1] - (base_lift + rise if iso else rise))
            if heat is not None and cell in heat and ref >= (34 if not iso else 60):
                pct = round(heat[cell] * 100)
                self.text("%d%%" % pct if ref >= 50 else "%d" % pct, top,
                          self.f_tiny, TEXT, center=True)
            if cell in flags:
                self._draw_flag(top, ref * (0.5 if iso else 1.0),
                                flags[cell] == self.my_id,
                                now - self.flag_times.get(cell, -9.0))
            if ctx["hint"] and cell == ctx["hint"]["cell"]:
                pulse = 2 + int(2 * abs(math.sin(now * 4)))
                self._outline_cell(cell, WARN, pulse, grow=2)
            return

        # opened: the tile sinks and changes colour as it pops in
        is_bomb = value == game_rules.BOMB
        end_col = BOMB_CELL if is_bomb else OPENED
        colour = mix(covered, end_col, e)
        lift = base_lift * (1 - e)
        if is_bomb:
            colour = mix(colour, BOMB_GLOW, 0.45 * (1 - e) +
                         0.10 * (0.5 + 0.5 * math.sin(now * 4)))
        if iso:
            self._tile_iso(centre, lay, colour, lift, opened_style=lift < 0.6)
        else:
            rect = self.cell_rect(cell)
            pop = int(rect.w * 0.14 * math.sin(math.pi * progress))
            self._tile_flat(rect.inflate(pop, pop), colour, lift, 0, radius,
                            opened_style=lift < 0.6)
        mid = (centre[0], centre[1] - lift * (1 if iso else 0))
        if is_bomb:
            r = (lay["tw"] * 0.13) if iso else max(6.0, lay["size"] * 0.22)
            self._draw_bomb(mid, r, now, glow=1.0, appear=ease_out_back(progress))
            index = order.get(owners.get(cell))
            if index in (0, 1):
                self._outline_cell(cell, (P1, P2)[index], 3)
                self.text(str(index + 1), (mid[0] - ref * 0.40, mid[1] - ref * 0.20)
                          if iso else (mid[0] - ref * 0.40, mid[1] - ref * 0.44),
                          self.f_tiny, (P1, P2)[index])
        elif progress >= 0.4:
            font = (self._font(max(12, int(lay["tw"] * 0.34)), True) if iso else
                    (self.f_cell if ref >= 56 else
                     (self.f_cell_m if ref >= 34 else self.f_cell_s)))
            self.text(value, mid, font, DIGIT_COLOURS.get(value, TEXT), center=True)
        if progress < 0.6:
            f = 1 - progress / 0.6
            if iso:
                self._tile_iso(centre, lay, mix(colour, lighten(ACCENT, 0.4), 0.0),
                               0, opened_style=True, flash=f)
            else:
                dev = self.gx._r(self.cell_rect(cell))
                flash = pygame.Surface(dev.size, pygame.SRCALPHA)
                flash.fill((*lighten(ACCENT, 0.3), int(150 * f)))
                self.screen.blit(flash, dev.topleft)

    def _tile_flat(self, rect, colour, lift, rise, radius, outline=None,
                   opened_style=False):
        lift, rise = int(round(lift)), int(round(rise))
        if lift > 0:
            self.gx.rect(self.screen, SHADOW, rect.move(0, lift + 2),
                             border_radius=radius)
            self.gx.rect(self.screen, darken(colour, 0.42), rect.move(0, lift),
                             border_radius=radius)
        top = rect.move(0, -rise)
        self.gx.rect(self.screen, colour, top, border_radius=radius)
        if opened_style:
            self.gx.rect(self.screen, LINE, top, width=1, border_radius=radius)
            self.gx.line(self.screen, darken(colour, 0.35),
                             (top.x + radius, top.y + 1),
                             (top.right - radius - 1, top.y + 1), 2)
        else:
            self.gx.line(self.screen, lighten(colour, 0.16),
                             (top.x + radius, top.y + 1),
                             (top.right - radius - 1, top.y + 1))
            if outline:
                self.gx.rect(self.screen, outline, top, width=2,
                                 border_radius=radius)

    def _tile_iso(self, centre, lay, colour, h, outline=None, opened_style=False,
                  flash=0.0):
        """One 3D tile: a diamond top with two shaded side faces."""
        cx, cy = centre
        hw = lay["tw"] / 2.0 * ISO_INSET
        hh = lay["th"] / 2.0 * ISO_INSET
        ground = [(cx, cy - hh), (cx + hw, cy), (cx, cy + hh), (cx - hw, cy)]
        top = [(x, y - h) for x, y in ground]
        if h > 0.6:
            self._poly(self.screen, [top[3], top[2], ground[2], ground[3]],
                       darken(colour, 0.34))
            self._poly(self.screen, [top[2], top[1], ground[1], ground[2]],
                       darken(colour, 0.5))
        self._poly(self.screen, top, colour)
        if flash > 0:
            self._poly(self.screen, top, mix(colour, lighten(ACCENT, 0.5), flash * 0.8))
        edge = LINE if opened_style else (outline or lighten(colour, 0.2))
        self.gx.polygon(self.screen, edge, top, 2 if outline else 1)

    def _draw_status(self):
        now = self.now
        y = self.board_bottom() + 22
        away = (self.state or {}).get("away") or []
        if self.paused and away:
            msg, colour = ("Paused - %s lost connection (%ds)"
                           % (away[0]["name"], away[0]["seconds"])), WARN
        elif self.role != "player":
            msg, colour = "You're spectating this match", ACCENT
        elif self.phase == game_rules.PHASE_WAITING:
            msg, colour = "Waiting for an opponent to join...", MUTED
        elif self.phase == game_rules.PHASE_ENDED:
            msg, colour = "Match over", WARN
        elif self.my_turn:
            if self.bombs_are_bad:
                msg = "Your move - open safe ground, dodge the bombs"
            else:
                msg = "Your move - hunt for a bomb"
            colour = GOOD
        else:
            others = [p["name"] for p in self.players if p["id"] != self.my_id]
            msg = "%s is thinking..." % (others[0] if others else "Your rival")
            colour = MUTED
        text = self.fit(msg, self.f_head, GAME_W - 120)
        w = self._tw(self.f_head, text) + 44
        slide = (1 - ease_out_cubic((now - self.turn_at) / 0.35)) * 12 \
            if self.fx_on else 0
        pill = pygame.Rect(0, 0, w, 40)
        pill.center = (CX, int(y + 20 - slide))
        self.gx.rect(self.screen, mix(PANEL, colour, 0.14), pill, border_radius=20)
        self.gx.rect(self.screen, colour if colour != MUTED else LINE, pill,
                         width=2 if colour in (GOOD, WARN, ACCENT) else 1,
                         border_radius=20)
        self.text(text, pill.center, self.f_head, colour, center=True)

        state = self.state or {}
        if self.bombs_are_bad:
            caption = "%d safe slots left" % (state.get("safe_left") or 0)
            show = state.get("safe_left") is not None
        else:
            bombs, total = state.get("bombs_left"), state.get("bombs_total")
            caption = "%s of %s bombs left" % (bombs, total)
            show = bombs is not None
        if show:
            width = self._tw(self.f_small, caption)
            x0 = CX - (width + 24) // 2
            self._draw_bomb((x0 + 7, y + 66), 7, now, glow=0.0, fuse=False)
            self.text(caption, (x0 + 24, y + 58), self.f_small, MUTED)

    # ------------------------------------------------------------------
    # overlays
    # ------------------------------------------------------------------
    def _end_progress(self):
        if self.match_end is None:
            return 0.0
        return clamp((self.now - self.match_end_time - END_DELAY) / END_FADE)

    def _draw_end_overlay(self):
        op = ease_out_cubic(self._end_progress())
        self._veil(VEIL_ALPHA * op)
        card = pygame.Rect(0, 0, 520, 470)
        card.center = (CX, 450 + int((1 - op) * 36))
        self._panel(card, radius=18)

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
            headline, colour = "YOU WIN!", GOOD
        else:
            headline, colour = "YOU LOST", BAD
        self.text_scaled(headline, (CX, card.y + 50), self.f_huge, colour,
                         0.8 + 0.2 * op)

        left, right = card.x + 30, card.right - 30
        y = card.y + 108
        elo_changes = end.get("elo_changes", {})
        is_ranked_match = end.get("rated", end.get("ranked", True))
        elapsed = self.now - self.match_end_time - END_DELAY - 0.35
        progress = clamp(elapsed / 1.0)
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
                        elo_str += "  Promoted to %s!" % ch["promoted"].upper()
                    elif ch.get("streak_bonus"):
                        elo_str += "  (+%d streak!)" % ch["streak_bonus"]
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
            columns = [("Picks", right - 170), ("Streak", right - 90), ("Rate", right)]
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
            self.text("streak = most picks in a row that kept your turn",
                      (left, y + 4), self.f_small, MUTED)

        self.rematch_rect = pygame.Rect(card.centerx - 100, card.bottom - 84, 200, 52)
        if self.role != "player":
            self.text("Waiting for the players to decide on a rematch",
                      self.rematch_rect.center, self.f_small, MUTED, center=True)
            return

        votes = len((self.state or {}).get("rematch_votes", []))
        total = len(self.players)
        if self.voted_rematch:
            self.gx.rect(self.screen, PANEL_2, self.rematch_rect, border_radius=12)
            self.gx.rect(self.screen, LINE, self.rematch_rect, width=1,
                             border_radius=12)
            self.text("Waiting for rival  %d/%d" % (votes, total),
                      self.rematch_rect.center, self.f_body, MUTED, center=True)
        else:
            hot = self.rematch_rect.collidepoint(self.mouse())
            self.gx.rect(self.screen, BTN_HOT if hot else BTN,
                             self.rematch_rect, border_radius=12)
            self.text("PLAY AGAIN", self.rematch_rect.center, self.f_head, BTN_TEXT,
                      center=True)

    def _draw_reconnect_overlay(self):
        self._veil(215)
        card = pygame.Rect(0, 0, 460, 170)
        card.center = (CX, 430)
        self._panel(card, radius=16, border=WARN)
        self.gx.rect(self.screen, WARN, card, width=2, border_radius=16)
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
        width, height = self.tsize(self.f_body, text)
        box = pygame.Rect(0, 0, width + 32, height + 18)
        age = self.now - getattr(self, "toast_at", 0.0)
        rise = (1 - ease_out_cubic(age / 0.22)) * 14 if self.fx_on else 0
        box.center = (CX, int(WIN_H - 70 + rise))
        self._panel(box, radius=12, fill=PANEL_2)
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
