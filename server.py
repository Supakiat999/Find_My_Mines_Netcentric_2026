"""Find My Mines - game server.

Runs the authoritative game and a pygame admin console that shows how many
clients are online, who they are, and a Reset button.

Threading model
    accept thread   : waits for new TCP connections
    one per client  : blocking recv, pushes decoded messages onto a queue
    main thread     : pygame loop - drains that queue, runs the turn clock,
                      drives the computer opponent, mutates the game,
                      broadcasts, and draws the console

Only the main thread ever touches the Game or sends on a socket, so the
rules never need locking; the lock guards the client table alone.

Run:  python server.py
"""

import math
import queue
import secrets
import socket
import sys
import threading
import time
from collections import deque

import pygame

import ai
import botbrain
import config
import game as game_rules
import protocol
import stats as stats_mod
from room import Room, BOT_ID, BOT_LABELS

WIN_W, WIN_H = 940, 720
FPS = 30

# palette
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
HIDDEN_CELL = (44, 55, 74)

class ClientRecord:
    """One connected socket and what we know about it."""

    def __init__(self, client_id, sock, addr):
        self.id = client_id
        self.sock = sock
        self.addr = addr
        self.name = None          # set on JOIN
        self.role = "lobby"       # lobby | player | spectator
        self.room_id = None
        self.watch = False
        self.connected_at = time.time()
        self.joined_at = None     # set on JOIN - seats follow this, not the
        self.alive = True         # connection order, so a client sitting on
                                  # the nickname screen cannot take a seat
                                  # from someone already playing
        self.token = secrets.token_hex(8)   # proves who you are on reconnect
        self.away_since = None    # set while we hold the seat of a dropped player
        self.last_chat = 0.0

    @property
    def label(self):
        return self.name or "(connecting)"


class Server:
    def __init__(self):
        self.rooms = {}
        self.selected_room_id = None
        self._last_rooms = None
        self.clients = {}                 # client_id -> ClientRecord
        self.clients_lock = threading.Lock()
        self.events = queue.Queue()       # (client_id, message dict)
        self.log = deque(maxlen=9)
        self.running = True
        self._next_id = 1

        path = (stats_mod.default_path() if config.STATS_FILE == "auto"
                else config.STATS_FILE)
        self.board = (stats_mod.DatabaseLeaderboard(config.DATABASE_URL)
                      if config.DATABASE_URL else stats_mod.Leaderboard(path))
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((config.BIND_HOST, config.SERVER_PORT))
        self.listener.listen(8)
        self.listener.settimeout(0.5)     # so the accept loop can be stopped
        self.lan_ip = protocol.local_ip()
        self._ip_checked_at = 0.0
        self._offline = False

    def current_ip(self):
        """Our LAN address, re-checked as we go.

        DHCP hands out a new address when the network changes - switching
        Wi-Fi, or a hotspot restarting.  Showing the address we had at
        start-up would send players to somewhere that no longer exists.
        """
        now = time.time()
        if now - self._ip_checked_at > 3:
            self._ip_checked_at = now
            fresh = protocol.local_ip()
            if fresh == "127.0.0.1" and self.lan_ip != "127.0.0.1":
                # No route at all - the Wi-Fi dropped.  Keep showing the
                # last good address instead of sending players to loopback.
                if not self._offline:
                    self._offline = True
                    self.say("Network unreachable - still showing %s"
                             % self.lan_ip)
            else:
                self._offline = False
                if fresh != self.lan_ip:
                    self.lan_ip = fresh
                    self.say("Address changed - players must now use %s" % fresh)
        return self.lan_ip

    # ------------------------------------------------------------------
    # logging
    # ------------------------------------------------------------------
    def say(self, text):
        stamp = time.strftime("%H:%M:%S")
        self.log.append("%s  %s" % (stamp, text))
        print("[%s] %s" % (stamp, text), flush=True)

    # ------------------------------------------------------------------
    # networking
    # ------------------------------------------------------------------
    def start_network(self):
        threading.Thread(target=self._accept_loop, daemon=True).start()
        self.say("Listening on %s:%d  (LAN %s)"
                 % (config.BIND_HOST, config.SERVER_PORT, self.lan_ip))

    def _accept_loop(self):
        while self.running:
            try:
                sock, addr = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self.clients_lock:
                client_id = self._next_id
                self._next_id += 1
                self.clients[client_id] = ClientRecord(client_id, sock, addr)
            threading.Thread(target=self._client_loop,
                             args=(client_id, sock), daemon=True).start()
            self.events.put((client_id, {"type": "__connected__"}))

    def _client_loop(self, client_id, sock):
        """Blocking reader for one client; runs on its own thread."""
        if self._answered_browser(client_id, sock):
            return
        reader = protocol.MessageReader(sock)
        for msg in reader.messages():
            self.events.put((client_id, msg))
        self.events.put((client_id, {"type": "__disconnected__"}))

    def _answered_browser(self, client_id, sock):
        """Reply to a browser instead of treating it as a game client.

        Opening http://<server address>/ from a phone or another laptop is
        the quickest way to prove the network path works, before anyone
        edits config.py.  We peek at the first bytes, so a real client's
        JOIN is left untouched in the buffer for the reader below.
        """
        try:
            head = sock.recv(8, socket.MSG_PEEK)
        except OSError:
            return False
        if not (head.startswith(b"GET") or head.startswith(b"HEAD")):
            return False

        rec = self._client(client_id)
        seen_by = rec.addr[0] if rec else "?"
        body = (
            "<!doctype html><meta charset=utf-8>"
            "<title>Find My Mines</title>"
            "<body style=\"font:16px system-ui;background:#111621;color:#e2e8f0;"
            "text-align:center;padding:60px\">"
            "<h1 style=\"color:#34d399\">Connection works</h1>"
            "<p>You reached the Find My Mines server at "
            "<b>%s:%d</b>.</p>"
            "<p>Your address here is <b>%s</b>.</p>"
            "<p style=\"color:#8a98af\">Now set SERVER_HOST in config.py to "
            "%s and run <b>python client.py</b>.</p>"
            % (self.lan_ip, config.SERVER_PORT, seen_by, self.lan_ip)
        ).encode("utf-8")
        crlf = "\r\n"
        headers = (
            "HTTP/1.1 200 OK" + crlf
            + "Content-Type: text/html; charset=utf-8" + crlf
            + "Content-Length: " + str(len(body)) + crlf
            + "Connection: close" + crlf + crlf
        ).encode("ascii")
        try:
            # Consume the request we only peeked at.  Closing a socket that
            # still holds unread data makes Windows send RST, which would
            # throw our reply away before the browser could read it.
            sock.settimeout(0.5)
            sock.recv(65536)
        except OSError:
            pass
        try:
            sock.settimeout(None)
            sock.sendall(headers + body)
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        self.say("Reachability check from %s - answered OK" % seen_by)
        self.events.put((client_id, {"type": "__disconnected__"}))
        return True

    def _drop_client(self, client_id):
        with self.clients_lock:
            rec = self.clients.pop(client_id, None)
        if rec is None:
            return None
        rec.alive = False
        try:
            rec.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            rec.sock.close()
        except OSError:
            pass
        return rec

    def _ordered_clients(self):
        with self.clients_lock:
            return sorted(self.clients.values(), key=lambda c: c.connected_at)

    def _joined_clients(self):
        """Everyone who has sent a nickname, in the order they sent it.
        Includes players whose connection dropped but whose seat is held."""
        with self.clients_lock:
            named = [c for c in self.clients.values() if c.name]
        return sorted(named, key=lambda c: c.joined_at)

    def _client(self, client_id):
        with self.clients_lock:
            return self.clients.get(client_id)

    def _send(self, rec, msg_type, **payload):
        if rec and rec.alive and not protocol.send(rec.sock, msg_type, **payload):
            rec.alive = False

    def _broadcast(self, msg_type, **payload):
        line = protocol.encode(msg_type, **payload)
        for rec in self._ordered_clients():
            if not rec.alive:
                continue
            try:
                rec.sock.sendall(line)
            except OSError:
                rec.alive = False

    # ------------------------------------------------------------------
    # who is who, including the computer
    # ------------------------------------------------------------------
    def _unique_name(self, wanted):
        taken = {c.name for c in self._joined_clients() if c.name}
        taken.update(BOT_LABELS.values())
        name = wanted
        n = 2
        while name in taken:
            suffix = " (%d)" % n
            name = wanted[:16 - len(suffix)] + suffix
            n += 1
        return name

    # ------------------------------------------------------------------
    # snapshots pushed to clients
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # chat
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # seating and match flow
    # ------------------------------------------------------------------
    def _poll_database(self):
        if not isinstance(self.board, stats_mod.DatabaseLeaderboard):
            return
        while True:
            try:
                context, data, changes, error = self.board.completed.get_nowait()
            except queue.Empty:
                return
            if not error:
                self.board.data = data
            if context is not None:
                room_id, match_id = context
                room = self.rooms.get(room_id)
                if room and room.match_id == match_id:
                    room.accept_saved(changes, error)

    @property
    def selected_room(self):
        return self.rooms.get(self.selected_room_id)

    def push_rooms(self, force=False):
        rooms = [room.summary(self._joined_clients()) for room in self.rooms.values()]
        if not force and rooms == self._last_rooms:
            return
        self._last_rooms = rooms
        for rec in self._joined_clients():
            self._send(rec, protocol.ROOMS, rooms=rooms)

    def _welcome_lobby(self, rec, reconnected=False):
        self._send(rec, protocol.WELCOME, client_id=rec.id, token=rec.token,
                   role="lobby", room_id=None, reconnected=reconnected,
                   message="Welcome, %s." % rec.name, leaderboard=self.board.top(5), chat=[])

    def _enter_room(self, rec, room, watch=False):
        if rec.room_id is not None:
            raise ValueError("leave your current room first")
        if not watch and not room.summary(self._joined_clients())["joinable"]:
            raise ValueError("room is full or saving; choose Watch")
        rec.room_id = room.id
        rec.watch = watch
        rec.joined_at = time.time()
        rec.last_chat = 0
        room._reseat()
        room._send_welcome(rec)
        room._note("%s joined" % rec.name)
        room.push_clients()
        room._maybe_autostart()
        room.push_state()
        self.push_rooms()

    def _create_room(self, rec, msg):
        if rec.room_id is not None:
            raise ValueError("leave your current room first")
        room = Room(self, msg.get("name"), msg.get("mode", "classic"),
                    msg.get("custom", {}), msg.get("bot_level", "off"),
                    msg.get("ranked", True))
        self.rooms[room.id] = room
        if self.selected_room_id is None:
            self.selected_room_id = room.id
        self._enter_room(rec, room)

    def _join_room(self, rec, msg):
        room_id, watch = msg.get("room_id"), msg.get("watch", False)
        if not isinstance(room_id, str) or type(watch) is not bool:
            raise ValueError("invalid room membership request")
        room = self.rooms.get(room_id)
        if room is None:
            raise ValueError("room no longer exists")
        self._enter_room(rec, room, watch)

    def _leave_room(self, rec):
        room = self.rooms.get(rec.room_id)
        if room is None:
            return
        rec.room_id = None
        rec.role = "lobby"
        rec.watch = False
        room.rematch_votes.discard(rec.id)
        room._reseat()
        room._resume_clock()
        room.push_clients()
        room._maybe_autostart()
        room.push_state()
        self._send(rec, protocol.ROOM_LEFT, room_id=room.id)
        self._welcome_lobby(rec)
        self._cleanup_rooms()
        self.push_rooms()

    def _cleanup_rooms(self):
        for room_id, room in list(self.rooms.items()):
            if not room._joined_clients() and room.pending_match is None:
                del self.rooms[room_id]
        if self.selected_room_id not in self.rooms:
            self.selected_room_id = next(iter(self.rooms), None)
        self.push_rooms()

    def update_clock(self):
        for room in list(self.rooms.values()):
            room.update_clock()
        self._cleanup_rooms()

    def reset_all(self):
        if self.selected_room is not None:
            self.selected_room.reset_all()

    # ------------------------------------------------------------------
    # message handling (main thread only)
    # ------------------------------------------------------------------
    def handle_events(self):
        self._poll_database()
        while True:
            try:
                client_id, msg = self.events.get_nowait()
            except queue.Empty:
                return
            self._handle(client_id, msg)

    def _handle(self, client_id, msg):
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "__connected__":
            rec = self._client(client_id)
            if rec:
                self.say("Connection from %s:%d" % rec.addr)
            return
        if kind == "__disconnected__":
            rec = self._client(client_id)
            if rec:
                room = self.rooms.get(rec.room_id)
                if room:
                    room._on_disconnect(client_id)
                else:
                    self._drop_client(client_id)
                self._cleanup_rooms()
                self.push_rooms()
            return

        rec = self._client(client_id)
        if rec is None:
            return
        if kind == protocol.JOIN:
            self._on_join(rec, msg)
            return
        if not rec.name:
            self._send(rec, protocol.ERROR, message="join with a nickname first")
            return
        try:
            if kind == protocol.LIST_ROOMS:
                self.push_rooms(force=True)
            elif kind == protocol.CREATE_ROOM:
                self._create_room(rec, msg)
            elif kind == protocol.JOIN_ROOM:
                self._join_room(rec, msg)
            elif kind == protocol.LEAVE_ROOM:
                if "room_id" in msg and msg["room_id"] != rec.room_id:
                    raise ValueError("stale room leave request")
                self._leave_room(rec)
            elif kind in (protocol.SET_MODE, protocol.SET_CUSTOM, protocol.SET_BOT, protocol.SET_RANKED):
                raise ValueError("room settings are fixed; create another room")
            else:
                room = self.rooms.get(rec.room_id)
                if room is None or msg.get("room_id") != room.id:
                    raise ValueError("join a room before sending game actions")
                if room.pending_match is not None and kind != protocol.CHAT:
                    raise ValueError("result saving pending")
                if kind == protocol.PICK:
                    room._on_pick(rec, msg)
                elif kind == protocol.FLAG:
                    room._on_flag(rec, msg)
                elif kind == protocol.REMATCH:
                    room._on_rematch(rec)
                elif kind == protocol.CHAT:
                    room._on_chat(rec, msg)
                elif kind == protocol.HINT:
                    room._on_hint(rec)
        except (ValueError, TypeError, OverflowError) as exc:
            self._send(rec, protocol.ERROR, room_id=rec.room_id, message=str(exc))

    def _on_join(self, rec, msg):
        if rec.name:
            return  # already joined

        # Someone coming back on a new socket proves who they are with the
        # token they were given, and takes their old seat back.
        token = msg.get("token")
        if token:
            for old in self._joined_clients():
                if old is not rec and old.token == token:
                    room = self.rooms.get(old.room_id)
                    rec.room_id = old.room_id
                    rec.watch = old.watch
                    if room:
                        room._reattach(rec, old)
                    else:
                        rec.name, rec.token, rec.joined_at = old.name, old.token, old.joined_at
                        self._drop_client(old.id)
                        self._welcome_lobby(rec, reconnected=True)
                    self.push_rooms(force=True)
                    return

        nickname = msg.get("nickname", "")
        if not isinstance(nickname, str) or not nickname.strip().isprintable():
            self._send(rec, protocol.ERROR, message="nickname must contain printable characters")
            return
        wanted = nickname.strip()[:16]
        rec.name = self._unique_name(wanted)
        rec.joined_at = time.time()
        self._welcome_lobby(rec)
        self.say("%s joined the lobby" % rec.name)
        self.push_rooms(force=True)

    # -- leaving and coming back ---------------------------------------
    # ------------------------------------------------------------------
    # turn clock
    # ------------------------------------------------------------------
    def shutdown(self):
        self.running = False
        if isinstance(self.board, stats_mod.DatabaseLeaderboard):
            if any(room.pending_match is not None for room in self.rooms.values()):
                self.say("WARNING: closing with a pending result; check database "
                         "before restarting. Uncommitted results are not durable.")
            self.board.close()
        for rec in self._ordered_clients():
            try:
                rec.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                rec.sock.close()
            except OSError:
                pass
        try:
            self.listener.close()
        except OSError:
            pass


# ----------------------------------------------------------------------
# pygame admin console
# ----------------------------------------------------------------------
class ServerUI:
    def __init__(self, server):
        self.server = server
        pygame.init()
        pygame.display.set_caption("Find My Mines - Server")
        self.screen = pygame.display.set_mode((WIN_W, WIN_H))
        self.clock = pygame.time.Clock()
        self.f_title = self._font(30, bold=True)
        self.f_head = self._font(19, bold=True)
        self.f_body = self._font(17)
        self.f_small = self._font(14)
        self.f_cell = self._font(20, bold=True)
        self.reset_rect = pygame.Rect(WIN_W - 200, 22, 168, 44)
        self.reset_hover = False
        self.previous_room_rect = pygame.Rect(32, 100, 100, 34)
        self.next_room_rect = pygame.Rect(600, 100, 100, 34)

    @staticmethod
    def _font(size, bold=False):
        for name in ("Segoe UI", "Arial", "DejaVu Sans"):
            try:
                return pygame.font.SysFont(name, size, bold=bold)
            except Exception:
                continue
        return pygame.font.Font(None, size)

    # -- small drawing helpers -----------------------------------------
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
        return surf.get_width()

    def fit(self, s, font, max_width):
        """Shorten a line with '...' so it never runs out of its panel."""
        s = str(s)
        if font.size(s)[0] <= max_width:
            return s
        while s and font.size(s + "...")[0] > max_width:
            s = s[:-1]
        return s + "..."

    def panel(self, rect, title=None):
        pygame.draw.rect(self.screen, PANEL, rect, border_radius=10)
        pygame.draw.rect(self.screen, LINE, rect, width=1, border_radius=10)
        if title:
            self.text(title, (rect.x + 16, rect.y + 12), self.f_head, MUTED)

    # -- main loop ------------------------------------------------------
    def run(self):
        srv = self.server
        while srv.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    srv.running = False
                elif event.type == pygame.MOUSEMOTION:
                    self.reset_hover = self.reset_rect.collidepoint(event.pos)
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if self.reset_rect.collidepoint(event.pos):
                        srv.reset_all()
                    else:
                        ids = list(srv.rooms)
                        if ids:
                            index = ids.index(srv.selected_room_id)
                            if self.previous_room_rect.collidepoint(event.pos):
                                srv.selected_room_id = ids[(index - 1) % len(ids)]
                            elif self.next_room_rect.collidepoint(event.pos):
                                srv.selected_room_id = ids[(index + 1) % len(ids)]
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    srv.running = False

            srv.handle_events()
            srv.update_clock()
            self.draw()
            self.clock.tick(FPS)

        srv.shutdown()
        pygame.quit()

    def draw(self):
        srv = self.server
        self.screen.fill(BG)

        self.text("FIND MY MINES", (32, 20), self.f_title)
        width = self.text("players connect to", (34, 60), self.f_small, MUTED)
        self.text("%s:%d" % (srv.current_ip(), config.SERVER_PORT),
                  (34 + width + 10, 54), self.f_head, ACCENT)
        self._draw_reset_button()

        self._draw_rooms()
        self._draw_clients(pygame.Rect(32, 146, 400, 288))
        self._draw_match(pygame.Rect(452, 146, 456, 288))
        self._draw_board(pygame.Rect(452, 450, 456, 246))
        self._draw_log(pygame.Rect(32, 450, 400, 246))

        pygame.display.flip()

    def _draw_rooms(self):
        mouse = pygame.mouse.get_pos()
        for label, box in (("Previous", self.previous_room_rect), ("Next", self.next_room_rect)):
            fill = PANEL_2 if box.collidepoint(mouse) else PANEL
            pygame.draw.rect(self.screen, fill, box, border_radius=8)
            self.text(label, box.center, self.f_body, TEXT, center=True)
        room = self.server.selected_room
        if room:
            label = "%s - %s" % (room.name, game_rules.MODE_LABELS[room.mode])
        else:
            label = "No rooms - players create rooms from the lobby"
        self.text(self.fit(label, self.f_body, 430), (150, 108), self.f_body, ACCENT)

    def _draw_reset_button(self):
        colour = (185, 60, 60) if self.reset_hover else (150, 48, 48)
        pygame.draw.rect(self.screen, colour, self.reset_rect, border_radius=8)
        pygame.draw.rect(self.screen, (220, 120, 120), self.reset_rect,
                         width=1, border_radius=8)
        label = self.f_head.render("RESET GAME", True, (255, 235, 235))
        self.screen.blit(label, label.get_rect(center=self.reset_rect.center))

    def _draw_clients(self, rect):
        srv = self.server.selected_room
        if srv is None:
            self.panel(rect, "LOBBY")
            self.text("%d connected" % len(self.server._joined_clients()),
                      (rect.x + 16, rect.y + 52), self.f_body, MUTED)
            return
        humans = srv._joined_clients()
        online = len([c for c in humans if c.alive])
        self.panel(rect, "CONNECTED CLIENTS")
        self.text("online", (rect.right - 84, rect.y + 20), self.f_small, MUTED)
        self.text(str(online), (rect.right - 34, rect.y + 6), self.f_title, ACCENT)

        rows = [("human", c) for c in humans]
        if srv._bot_seated():
            rows.append(("bot", None))
        y = rect.y + 52
        if not rows:
            self.text("waiting for players to connect...",
                      (rect.x + 16, y + 8), self.f_body, MUTED)
            return

        capacity = 5
        shown = rows if len(rows) <= capacity else rows[:capacity - 1]
        hidden = len(rows) - len(shown)
        now = time.time()
        for kind, c in shown:
            row = pygame.Rect(rect.x + 10, y, rect.width - 20, 40)
            pygame.draw.rect(self.screen, PANEL_2, row, border_radius=8)
            if kind == "bot":
                is_turn = srv.game.current_turn == BOT_ID
                name = srv._bot_name()
                badge, badge_colour = "COMPUTER", WARN
                where = ""
                score = srv.game.scores.get(BOT_ID, 0)
            else:
                is_turn = c.id == srv.game.current_turn
                name = c.name
                if c.away_since is not None:
                    left = max(0, int(config.RECONNECT_GRACE - (now - c.away_since)))
                    badge, badge_colour = "AWAY %ds" % left, WARN
                elif c.role == "player":
                    badge, badge_colour = "PLAYER", ACCENT
                else:
                    badge, badge_colour = "SPECTATOR", MUTED
                where = c.addr[0]
                score = srv.game.scores.get(c.id, 0) if c.role == "player" else None
            if is_turn:
                pygame.draw.rect(self.screen, GOOD, row, width=2, border_radius=8)
            self.text(self.fit(name, self.f_body, 132), (row.x + 12, row.y + 9),
                      self.f_body, GOOD if is_turn else TEXT)
            self.text(badge, (row.x + 152, row.y + 12), self.f_small, badge_colour)
            if where:
                self.text(where, (row.x + 236, row.y + 12), self.f_small, MUTED)
            if score is not None:
                self.text(score, (row.right - 12, row.y + 9), self.f_body, WARN,
                          right=True)
            y += 46
        if hidden > 0:
            self.text("+ %d more connected" % hidden, (rect.x + 20, y + 10),
                      self.f_small, MUTED)

    def _draw_match(self, rect):
        srv = self.server.selected_room
        if srv is None:
            self.panel(rect, "MATCH")
            return
        g = srv.game
        self.panel(rect, "MATCH")

        phase_text = {game_rules.PHASE_WAITING: "waiting for players",
                      game_rules.PHASE_PLAYING: "in progress",
                      game_rules.PHASE_ENDED: "finished"}[g.phase]
        phase_colour = {game_rules.PHASE_WAITING: MUTED,
                        game_rules.PHASE_PLAYING: GOOD,
                        game_rules.PHASE_ENDED: WARN}[g.phase]
        if srv._anyone_away() and g.phase == game_rules.PHASE_PLAYING:
            phase_text, phase_colour = "paused - player away", WARN
        self.text(phase_text, (rect.x + 16, rect.y + 44), self.f_body, phase_colour)
        opponent = ("vs %s" % srv._bot_name() if srv._bot_seated()
                    else "vs another player")
        self.text(opponent, (rect.right - 16, rect.y + 46), self.f_small, MUTED,
                  right=True)

        names = srv._names()
        self.text("turn", (rect.x + 16, rect.y + 84), self.f_small, MUTED)
        self.text(self.fit(names.get(g.current_turn, "-"), self.f_head, 170),
                  (rect.x + 16, rect.y + 102), self.f_head, TEXT)

        left = srv._seconds_left()
        colour = BAD if left <= 3 and g.phase == game_rules.PHASE_PLAYING else ACCENT
        self.text("countdown", (rect.x + 220, rect.y + 84), self.f_small, MUTED)
        self.text("00:00:%02d" % left, (rect.x + 220, rect.y + 98),
                  self.f_title, colour)

        if g.bombs_are_bad:
            # Bombs are the hazard here, so the finish line is safe ground.
            self.text("safe slots left", (rect.x + 16, rect.y + 150),
                      self.f_small, MUTED)
            self.text("%d / %d" % (g.safe_left, g.cell_count - g.bomb_count),
                      (rect.x + 16, rect.y + 168), self.f_head, TEXT)
        else:
            self.text("bombs left", (rect.x + 16, rect.y + 150), self.f_small, MUTED)
            self.text("%d / %d" % (g.bombs_left, g.bomb_count),
                      (rect.x + 16, rect.y + 168), self.f_head, TEXT)

        if g.phase == game_rules.PHASE_ENDED:
            self.text("rematch votes", (rect.x + 220, rect.y + 150),
                      self.f_small, MUTED)
            self.text("%d / %d" % (len(srv.rematch_votes), len(g.players)),
                      (rect.x + 220, rect.y + 168), self.f_head, WARN)

        y = rect.y + 216
        for pid in g.players:
            label = self.fit(names.get(pid, "?"), self.f_body, 250)
            self.text(label, (rect.x + 16, y), self.f_body,
                      GOOD if pid == g.last_winner else TEXT)
            self.text(g.scores.get(pid, 0), (rect.x + 290, y), self.f_body, WARN,
                      right=False)
            y += 26

    # -- the admin board: every slot, bombs included ---------------------
    @staticmethod
    def _fit_layers(layers, rows, cols, box):
        """Biggest slot size, and layers per row, that fit inside `box`."""
        label_h = 16 if layers > 1 else 0
        for size in range(26, 5, -1):
            gap = 2 if size < 14 else 3
            w = cols * (size + gap) - gap
            h = rows * (size + gap) - gap
            for per_row in range(layers, 0, -1):
                lines = math.ceil(layers / per_row)
                need_w = per_row * w + (per_row - 1) * 10
                need_h = lines * (h + label_h) + (lines - 1) * 8
                if need_w <= box.width and need_h <= box.height:
                    return size, gap, per_row
        return 6, 2, layers

    def _draw_board(self, rect):
        """Admin view of the board - the only place unfound bombs are shown."""
        room = self.server.selected_room
        if room is None:
            self.panel(rect, "BOARD")
            return
        g = room.game
        self.panel(rect, "BOARD  (server view)")
        view = g.board_view(reveal_all=True)
        box = pygame.Rect(rect.x + 16, rect.y + 44, rect.width - 32,
                          rect.height - 44 - 30)
        if g.is_3d:
            layers, rows, cols = g.dims
            size, gap, per_row = self._fit_layers(layers, rows, cols, box)
            w = cols * (size + gap) - gap
            h = rows * (size + gap) - gap
            for index, layer in enumerate(view):
                x = box.x + (index % per_row) * (w + 10)
                y = box.y + (index // per_row) * (h + 16 + 8)
                self.text("layer %d" % index, (x, y - 1), self.f_small, MUTED)
                self._draw_slots(layer, x, y + 16, size, gap)
        else:
            rows, cols = g.dims
            size, gap, _ = self._fit_layers(1, rows, cols, box)
            self._draw_slots(view, box.x, box.y, size, gap)
        self._draw_legend(rect.x + 16, rect.bottom - 26)

    def _draw_slots(self, rows, ox, oy, size, gap):
        font = self.f_cell if size >= 26 else self.f_small
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                cell = pygame.Rect(ox + c * (size + gap), oy + r * (size + gap),
                                   size, size)
                if value is None:
                    pygame.draw.rect(self.screen, HIDDEN_CELL, cell, border_radius=3)
                elif value == game_rules.HIDDEN_BOMB:
                    pygame.draw.rect(self.screen, HIDDEN_CELL, cell, border_radius=3)
                    pygame.draw.circle(self.screen, (150, 84, 84), cell.center,
                                       max(2, size // 5))
                elif value == game_rules.BOMB:
                    pygame.draw.rect(self.screen, (150, 48, 48), cell, border_radius=3)
                    pygame.draw.circle(self.screen, (255, 220, 220), cell.center,
                                       max(2, size // 4))
                else:
                    pygame.draw.rect(self.screen, PANEL_2, cell, border_radius=3)
                    if size >= 18:              # too small to read digits below this
                        label = font.render(str(value), True,
                                            MUTED if value == 0 else TEXT)
                        self.screen.blit(label, label.get_rect(center=cell.center))
                    elif value:
                        inner = cell.inflate(-size // 2, -size // 2)
                        pygame.draw.rect(self.screen, (98, 112, 138), inner,
                                         border_radius=2)

    def _draw_legend(self, x, y):
        for label, colour in (("found bomb", BAD), ("hidden bomb", MUTED),
                              ("opened slot", TEXT)):
            self.text(label, (x, y), self.f_small, colour)
            x += 118

    def _draw_log(self, rect):
        self.panel(rect, "ACTIVITY")
        y = rect.y + 44
        for line in list(self.server.log):
            self.text(self.fit(line, self.f_small, rect.width - 32),
                      (rect.x + 16, y), self.f_small, MUTED)
            y += 19


def main():
    try:
        server = Server()
    except OSError as exc:
        print("Could not bind port %d: %s" % (config.SERVER_PORT, exc))
        sys.exit(1)
    server.start_network()
    threading.Thread(target=botbrain.preload, daemon=True).start()
    ServerUI(server).run()


if __name__ == "__main__":
    main()
