"""Find My Mines - game client.

Connects to the server named in config.py (the player never types an address),
asks for a nickname, then draws the board the server sends.

The client is deliberately dumb: it sends "I clicked here" and draws whatever
state comes back.  It never decides what is a bomb, whose turn it is, or how
much time is left - the server owns all of that.

Threading model
    reader thread : blocking recv, pushes decoded messages onto a queue
    main thread   : pygame loop - drains the queue, draws, sends clicks

Run:  python client.py
"""

import queue
import socket
import sys
import threading

import pygame

import config
import game as game_rules
import protocol

WIN_W, WIN_H = 860, 860
FPS = 30

# palette - matches the server console
BG = (17, 22, 32)
PANEL = (26, 33, 46)
PANEL_2 = (32, 41, 57)
LINE = (49, 61, 82)
TEXT = (226, 232, 240)
MUTED = (138, 152, 175)
ACCENT = (96, 165, 250)
GOOD = (52, 211, 153)
WARN = (251, 191, 36)
BAD = (248, 113, 113)

COVERED = (55, 68, 92)
COVERED_HOVER = (74, 91, 122)
OPENED = (36, 45, 62)
BOMB_CELL = (150, 48, 48)

# classic minesweeper digit colours, brightened for a dark background
DIGIT_COLOURS = {
    0: (110, 124, 148), 1: (110, 168, 255), 2: (82, 209, 143),
    3: (248, 113, 113), 4: (167, 139, 250), 5: (251, 146, 60),
    6: (45, 212, 191), 7: (226, 232, 240), 8: (148, 163, 184),
}

SCREEN_NICKNAME = "nickname"
SCREEN_GAME = "game"
SCREEN_ERROR = "error"

CELL = 68
GAP = 8


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
        self.screen = pygame.display.set_mode((WIN_W, WIN_H))
        self.clock = pygame.time.Clock()
        self.f_title = self._font(40, bold=True)
        self.f_clock = self._font(34, bold=True)
        self.f_head = self._font(22, bold=True)
        self.f_body = self._font(18)
        self.f_small = self._font(15)
        self.f_cell = self._font(30, bold=True)
        self.f_huge = self._font(52, bold=True)

        self.net = NetworkClient()
        self.net.connect_async()

        self.screen_name = SCREEN_NICKNAME
        self.nickname = ""
        self.my_id = None
        self.role = None
        self.welcome = ""
        self.clients = {"count": 0, "list": []}
        self.state = None
        self.seconds_left = 0
        self.match_end = None
        self.voted_rematch = False
        self.toast = ""
        self.toast_until = 0
        self.running = True

        # geometry
        self.grid_w = config.GRID_SIZE * CELL + (config.GRID_SIZE - 1) * GAP
        self.grid_x = (WIN_W - self.grid_w) // 2
        self.grid_y = 232
        self.rematch_rect = pygame.Rect(WIN_W // 2 - 100, WIN_H // 2 + 66, 200, 52)

    @staticmethod
    def _font(size, bold=False):
        for name in ("Segoe UI", "Arial", "DejaVu Sans"):
            try:
                return pygame.font.SysFont(name, size, bold=bold)
            except Exception:
                continue
        return pygame.font.Font(None, size)

    # ------------------------------------------------------------------
    # small drawing helpers
    # ------------------------------------------------------------------
    def text(self, s, pos, font=None, color=TEXT, center=False, right=False):
        surf = (font or self.f_body).render(str(s), True, color)
        rect = surf.get_rect()
        if center:
            rect.center = pos
        elif right:
            rect.topright = pos
        else:
            rect.topleft = pos
        self.screen.blit(surf, rect)
        return rect

    def say(self, message, seconds=2.5):
        self.toast = message
        self.toast_until = pygame.time.get_ticks() + int(seconds * 1000)

    # ------------------------------------------------------------------
    # incoming messages
    # ------------------------------------------------------------------
    def pump_network(self):
        if self.net.status in ("failed", "lost") and self.screen_name != SCREEN_ERROR:
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
            self.welcome = msg.get("message", "")
            self.screen_name = SCREEN_GAME
            self.say(self.welcome, 4)
        elif kind == protocol.CLIENTS:
            self.clients = {"count": msg.get("count", 0), "list": msg.get("list", [])}
        elif kind == protocol.STATE:
            self.state = msg
            self.seconds_left = msg.get("seconds_left", 0)
            if msg.get("phase") != game_rules.PHASE_ENDED:
                self.match_end = None
                self.voted_rematch = False
            self.role = self._my_role()
        elif kind == protocol.TICK:
            self.seconds_left = msg.get("seconds_left", 0)
        elif kind == protocol.MATCH_END:
            self.match_end = msg
        elif kind == protocol.SERVER_RESET:
            self.match_end = None
            self.voted_rematch = False
            self.say("The server reset the game", 3)
        elif kind == protocol.ERROR:
            self.say(msg.get("message", "not allowed"))

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

    def can_click(self, row, col):
        return (self.role == "player"
                and self.phase == game_rules.PHASE_PLAYING
                and self.my_turn
                and self.board and self.board[row][col] is None)

    def cell_rect(self, row, col):
        return pygame.Rect(self.grid_x + col * (CELL + GAP),
                           self.grid_y + row * (CELL + GAP), CELL, CELL)

    def cell_at(self, pos):
        for r in range(config.GRID_SIZE):
            for c in range(config.GRID_SIZE):
                if self.cell_rect(r, c).collidepoint(pos):
                    return r, c
        return None

    # ------------------------------------------------------------------
    # main loop
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

    def _on_event(self, event):
        if event.type == pygame.QUIT:
            self.running = False
            return

        if self.screen_name == SCREEN_NICKNAME:
            self._on_nickname_event(event)
        elif self.screen_name == SCREEN_GAME:
            self._on_game_event(event)
        elif self.screen_name == SCREEN_ERROR:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_r:
                self.net = NetworkClient()
                self.net.connect_async()
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

    def _on_game_event(self, event):
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        if self.match_end is not None:
            if self.rematch_rect.collidepoint(event.pos) and not self.voted_rematch:
                if self.role == "player":
                    self.net.send(protocol.REMATCH)
                    self.voted_rematch = True
            return
        hit = self.cell_at(event.pos)
        if hit is None:
            return
        row, col = hit
        if self.can_click(row, col):
            self.net.send(protocol.PICK, row=row, col=col)
        elif self.role != "player":
            self.say("You are watching this match")
        elif self.phase != game_rules.PHASE_PLAYING:
            self.say("No match in progress")
        elif not self.my_turn:
            self.say("Not your turn")
        else:
            self.say("That slot is already open")

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
        pygame.display.flip()

    def _draw_nickname(self):
        self.text("FIND MY MINES", (WIN_W // 2, 210), self.f_title, TEXT, center=True)

        box = pygame.Rect(WIN_W // 2 - 200, 300, 400, 62)
        pygame.draw.rect(self.screen, PANEL, box, border_radius=10)
        pygame.draw.rect(self.screen, ACCENT, box, width=2, border_radius=10)
        caret = "|" if (pygame.time.get_ticks() // 500) % 2 == 0 else " "
        self.text(self.nickname + caret, box.center, self.f_head, TEXT, center=True)
        self.text("Enter your nickname", (WIN_W // 2, 282), self.f_small,
                  MUTED, center=True)

        if self.net.status == "connected":
            self.text("Press ENTER to join", (WIN_W // 2, 386), self.f_body,
                      GOOD, center=True)
        else:
            self.text("Connecting to the server...", (WIN_W // 2, 386),
                      self.f_body, WARN, center=True)

        # proof for the demo that the address comes from the source, not the user
        self.text("server %s  (set in config.py)" % self.net.address,
                  (WIN_W // 2, 440), self.f_small, MUTED, center=True)

    def _draw_error(self):
        self.text("CANNOT REACH THE SERVER", (WIN_W // 2, 240), self.f_head,
                  BAD, center=True)
        if self.net.status == "lost":
            detail = "The connection to %s was closed." % self.net.address
        else:
            detail = "Tried %s - %s" % (self.net.address, self.net.error)
        self.text(detail, (WIN_W // 2, 292), self.f_body, TEXT, center=True)

        hints = [
            "Is server.py running on that computer?",
            "Is SERVER_HOST in config.py the server's current IP?",
            "Is Python allowed through the firewall on the server?",
            "Are both computers on the same Wi-Fi? (a phone hotspot works)",
        ]
        y = 350
        for hint in hints:
            self.text("- " + hint, (WIN_W // 2 - 240, y), self.f_small, MUTED)
            y += 30
        self.text("Press R to try again", (WIN_W // 2, y + 24), self.f_body,
                  ACCENT, center=True)

    def _draw_game(self):
        self.text("FIND MY MINES", (WIN_W // 2, 26), self.f_title, TEXT, center=True)
        self._draw_clock()
        self._draw_scoreboard()
        self._draw_board()
        self._draw_status()
        self._draw_online()
        if self.match_end is not None:
            self._draw_end_overlay()

    def _draw_clock(self):
        running = self.phase == game_rules.PHASE_PLAYING
        left = self.seconds_left if running else 0
        colour = MUTED if not running else (BAD if left <= 3 else ACCENT)
        self.text("00:00:%02d" % left, (WIN_W // 2, 86), self.f_clock,
                  colour, center=True)

    def _draw_scoreboard(self):
        """Both names and scores, the active player highlighted."""
        players = self.players
        slots = [(self.grid_x, "left"), (self.grid_x + self.grid_w, "right")]
        current = (self.state or {}).get("current_turn")
        for index, (x, side) in enumerate(slots):
            if index < len(players):
                p = players[index]
                name = p["name"] + (" (you)" if p["id"] == self.my_id else "")
                score = p["score"]
                active = p["id"] == current
            else:
                name, score, active = "waiting...", "-", False
            colour = GOOD if active else TEXT
            if side == "left":
                rect = self.text(name, (x, 140), self.f_head, colour)
                self.text(score, (x, 174), self.f_clock, WARN)
                if active:
                    pygame.draw.rect(self.screen, GOOD,
                                     (x, rect.bottom + 4, rect.width, 3),
                                     border_radius=2)
            else:
                rect = self.text(name, (x, 140), self.f_head, colour, right=True)
                self.text(score, (x, 174), self.f_clock, WARN, right=True)
                if active:
                    pygame.draw.rect(self.screen, GOOD,
                                     (rect.right - rect.width, rect.bottom + 4,
                                      rect.width, 3), border_radius=2)

    def _draw_board(self):
        board = self.board
        mouse = pygame.mouse.get_pos()
        for r in range(config.GRID_SIZE):
            for c in range(config.GRID_SIZE):
                rect = self.cell_rect(r, c)
                value = board[r][c] if board else None
                if value is None:
                    clickable = self.can_click(r, c) and self.match_end is None
                    hot = clickable and rect.collidepoint(mouse)
                    pygame.draw.rect(self.screen, COVERED_HOVER if hot else COVERED,
                                     rect, border_radius=8)
                    if clickable:
                        pygame.draw.rect(self.screen, ACCENT if hot else LINE,
                                         rect, width=2, border_radius=8)
                elif value == game_rules.BOMB:
                    pygame.draw.rect(self.screen, BOMB_CELL, rect, border_radius=8)
                    self._draw_bomb(rect)
                else:
                    pygame.draw.rect(self.screen, OPENED, rect, border_radius=8)
                    pygame.draw.rect(self.screen, LINE, rect, width=1, border_radius=8)
                    self.text(value, rect.center, self.f_cell,
                              DIGIT_COLOURS.get(value, TEXT), center=True)

    def _draw_bomb(self, rect):
        cx, cy = rect.center
        pygame.draw.line(self.screen, (255, 214, 214),
                         (cx + 8, cy - 10), (cx + 16, cy - 19), 3)
        pygame.draw.circle(self.screen, (255, 226, 226), (cx + 17, cy - 20), 3)
        pygame.draw.circle(self.screen, (22, 14, 14), (cx, cy), 15)
        pygame.draw.circle(self.screen, (255, 230, 230), (cx - 5, cy - 5), 4)

    def _draw_status(self):
        y = self.grid_y + self.grid_w + 24
        if self.role != "player":
            msg, colour = "You are watching this match", ACCENT
        elif self.phase == game_rules.PHASE_WAITING:
            msg, colour = "Waiting for another player to join...", MUTED
        elif self.phase == game_rules.PHASE_ENDED:
            msg, colour = "Match finished", WARN
        elif self.my_turn:
            msg, colour = "Your turn - find a bomb!", GOOD
        else:
            others = [p["name"] for p in self.players if p["id"] != self.my_id]
            msg = "Waiting for %s..." % (others[0] if others else "the other player")
            colour = MUTED
        self.text(msg, (WIN_W // 2, y), self.f_head, colour, center=True)

        bombs = (self.state or {}).get("bombs_left")
        total = (self.state or {}).get("bombs_total")
        if bombs is not None:
            self.text("bombs left  %d / %d" % (bombs, total),
                      (WIN_W // 2, y + 32), self.f_small, MUTED, center=True)

    def _draw_online(self):
        """The connected-client list the server pushes to everyone."""
        y = WIN_H - 44
        panel = pygame.Rect(24, y - 12, WIN_W - 48, 52)
        pygame.draw.rect(self.screen, PANEL, panel, border_radius=10)
        pygame.draw.rect(self.screen, LINE, panel, width=1, border_radius=10)

        self.text("ONLINE  %d" % self.clients.get("count", 0),
                  (panel.x + 16, panel.y + 15), self.f_small, ACCENT)
        names = ", ".join(
            "%s%s" % (c["name"], "" if c["role"] == "player" else " (watching)")
            for c in self.clients.get("list", []))
        self.text(names or "-", (panel.x + 110, panel.y + 15), self.f_small, MUTED)

    def _draw_end_overlay(self):
        veil = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        veil.fill((10, 14, 20, 232))
        self.screen.blit(veil, (0, 0))

        card = pygame.Rect(0, 0, 440, 350)
        card.center = (WIN_W // 2, WIN_H // 2 - 12)
        pygame.draw.rect(self.screen, PANEL, card, border_radius=16)
        pygame.draw.rect(self.screen, LINE, card, width=1, border_radius=16)

        end = self.match_end
        players = end.get("players", [])
        if end.get("draw"):
            headline, colour = "DRAW", WARN
        elif self.role != "player":
            winner = next((p["name"] for p in players
                           if p["id"] == end.get("winner_id")), "?")
            headline, colour = "%s WINS" % winner.upper(), ACCENT
        elif end.get("winner_id") == self.my_id:
            headline, colour = "YOU WIN", GOOD
        else:
            headline, colour = "YOU LOST", BAD

        self.text(headline, (WIN_W // 2, WIN_H // 2 - 130), self.f_huge,
                  colour, center=True)

        y = WIN_H // 2 - 46
        for p in players:
            label = "%s%s" % (p["name"], "  (you)" if p["id"] == self.my_id else "")
            self.text(label, (WIN_W // 2 - 130, y), self.f_head, TEXT)
            self.text(p["score"], (WIN_W // 2 + 130, y), self.f_head, WARN, right=True)
            y += 34

        if self.role != "player":
            self.text("waiting for the players to rematch",
                      (WIN_W // 2, self.rematch_rect.centery), self.f_body,
                      MUTED, center=True)
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
            hot = self.rematch_rect.collidepoint(pygame.mouse.get_pos())
            pygame.draw.rect(self.screen, (37, 99, 235) if hot else (29, 78, 216),
                             self.rematch_rect, border_radius=10)
            self.text("REMATCH", self.rematch_rect.center, self.f_head,
                      (235, 244, 255), center=True)

    def _draw_toast(self):
        if not self.toast or pygame.time.get_ticks() > self.toast_until:
            return
        surf = self.f_body.render(self.toast, True, TEXT)
        box = surf.get_rect(center=(WIN_W // 2, WIN_H - 82))
        box = box.inflate(28, 16)
        pygame.draw.rect(self.screen, PANEL_2, box, border_radius=8)
        pygame.draw.rect(self.screen, LINE, box, width=1, border_radius=8)
        self.screen.blit(surf, surf.get_rect(center=box.center))


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
