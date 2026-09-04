"""Find My Mines - game server.

Runs the authoritative game and a pygame admin console that shows how many
clients are online, who they are, and a Reset button.

Threading model
    accept thread   : waits for new TCP connections
    one per client  : blocking recv, pushes decoded messages onto a queue
    main thread     : pygame loop - drains that queue, runs the turn clock,
                      mutates the game, broadcasts, and draws the console

Only the main thread ever touches the Game or sends on a socket, so the
rules never need locking; the lock guards the client table alone.

Run:  python server.py
"""

import queue
import socket
import sys
import threading
import time
from collections import deque

import pygame

import config
import game as game_rules
import protocol

WIN_W, WIN_H = 940, 660
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
        self.role = "connecting"  # connecting | player | spectator
        self.connected_at = time.time()
        self.joined_at = None     # set on JOIN - seats follow this, not the
        self.alive = True         # connection order, so a client sitting on
                                  # the nickname screen cannot take a seat
                                  # from someone already playing

    @property
    def label(self):
        return self.name or "(connecting)"


class Server:
    def __init__(self):
        self.game = game_rules.Game()
        self.clients = {}                 # client_id -> ClientRecord
        self.clients_lock = threading.Lock()
        self.events = queue.Queue()       # (client_id, message dict)
        self.log = deque(maxlen=9)
        self.running = True
        self._next_id = 1

        self.turn_deadline = None         # wall clock when the turn expires
        self.last_tick_sent = None
        self.rematch_votes = set()
        self.first_match_done = False

        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((config.BIND_HOST, config.SERVER_PORT))
        self.listener.listen(8)
        self.listener.settimeout(0.5)     # so the accept loop can be stopped
        self.lan_ip = protocol.local_ip()
        self._ip_checked_at = 0.0

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
        reader = protocol.MessageReader(sock)
        for msg in reader.messages():
            self.events.put((client_id, msg))
        self.events.put((client_id, {"type": "__disconnected__"}))

    def _drop_client(self, client_id):
        with self.clients_lock:
            rec = self.clients.pop(client_id, None)
        if rec is None:
            return None
        rec.alive = False
        try:
            rec.sock.close()
        except OSError:
            pass
        return rec

    def _ordered_clients(self):
        with self.clients_lock:
            return sorted(self.clients.values(), key=lambda c: c.connected_at)

    def _joined_clients(self):
        """Everyone who has sent a nickname, in the order they sent it."""
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
    # snapshots pushed to clients
    # ------------------------------------------------------------------
    def _clients_payload(self):
        joined = self._joined_clients()
        return {
            "count": len(joined),
            "list": [{"id": c.id, "name": c.name, "role": c.role} for c in joined],
        }

    def _seconds_left(self):
        if self.turn_deadline is None:
            return 0
        return max(0, int(round(self.turn_deadline - time.time())))

    def _state_payload(self):
        g = self.game
        by_id = {c.id: c for c in self._joined_clients()}
        players = [{"id": pid,
                    "name": by_id[pid].name if pid in by_id else "?",
                    "score": g.scores.get(pid, 0)}
                   for pid in g.players]
        return {
            "phase": g.phase,
            "grid_size": g.grid_size,
            "board": g.board_view(),          # never reveals unfound bombs
            "players": players,
            "current_turn": g.current_turn,
            "bombs_left": g.bombs_left,
            "bombs_total": g.bomb_count,
            "seconds_left": self._seconds_left(),
            "turn_seconds": config.TURN_SECONDS,
            "rematch_votes": sorted(self.rematch_votes),
            "spectators": [c.name for c in self._joined_clients()
                           if c.role == "spectator"],
        }

    def push_clients(self):
        self._broadcast(protocol.CLIENTS, **self._clients_payload())

    def push_state(self):
        self._broadcast(protocol.STATE, **self._state_payload())

    # ------------------------------------------------------------------
    # seating and match flow
    # ------------------------------------------------------------------
    def _reseat(self):
        """First MAX_PLAYERS joiners play; anyone later watches."""
        joined = self._joined_clients()
        seats = [c.id for c in joined[:config.MAX_PLAYERS]]
        for c in joined:
            c.role = "player" if c.id in seats else "spectator"
        if seats != self.game.players:
            self.game.seat_players(seats)

    def _begin_match(self, first_player=None):
        if not self.game.start_match(first_player):
            return
        self.rematch_votes.clear()
        self._start_turn_clock()
        self.first_match_done = True
        names = {c.id: c.name for c in self._joined_clients()}
        self.say("Match started - %s goes first"
                 % names.get(self.game.current_turn, "?"))

    def _start_turn_clock(self):
        self.turn_deadline = time.time() + config.TURN_SECONDS
        self.last_tick_sent = None

    def _stop_turn_clock(self):
        self.turn_deadline = None
        self.last_tick_sent = None

    def _maybe_autostart(self):
        """Kick off a match as soon as two players are seated."""
        if self.game.phase == game_rules.PHASE_WAITING and self.game.can_start():
            # First match of the session: the server picks the starter at
            # random.  Later ones follow the previous winner.
            first = None if not self.first_match_done else self.game.last_winner
            self._begin_match(first)

    def _end_match(self):
        self._stop_turn_clock()
        g = self.game
        by_id = {c.id: c for c in self._joined_clients()}
        self._broadcast(
            protocol.MATCH_END,
            winner_id=g.last_winner,
            draw=g.last_winner is None,
            players=[{"id": pid,
                      "name": by_id[pid].name if pid in by_id else "?",
                      "score": g.scores.get(pid, 0)} for pid in g.players],
        )
        if g.last_winner is None:
            self.say("Match over - draw")
        elif g.last_winner in by_id:
            self.say("Match over - %s wins" % by_id[g.last_winner].name)
        else:
            self.say("Match over")

    def reset_all(self):
        """The Reset button: clear the board and both scores, then re-deal."""
        self.game.full_reset()
        self.rematch_votes.clear()
        self._stop_turn_clock()
        self.first_match_done = False
        self._reseat()
        self.say("Server reset - board and scores cleared")
        self._broadcast(protocol.SERVER_RESET)
        self.push_clients()
        self._maybe_autostart()
        self.push_state()

    # ------------------------------------------------------------------
    # message handling (main thread only)
    # ------------------------------------------------------------------
    def handle_events(self):
        while True:
            try:
                client_id, msg = self.events.get_nowait()
            except queue.Empty:
                return
            self._handle(client_id, msg)

    def _handle(self, client_id, msg):
        kind = msg.get("type")
        if kind == "__connected__":
            rec = self._client(client_id)
            if rec:
                self.say("Connection from %s:%d" % rec.addr)
            return
        if kind == "__disconnected__":
            self._on_disconnect(client_id)
            return

        rec = self._client(client_id)
        if rec is None:
            return
        if kind == protocol.JOIN:
            self._on_join(rec, msg)
        elif kind == protocol.PICK:
            self._on_pick(rec, msg)
        elif kind == protocol.REMATCH:
            self._on_rematch(rec)

    def _unique_name(self, wanted):
        taken = {c.name for c in self._joined_clients() if c.name}
        name = wanted
        n = 2
        while name in taken:
            name = "%s (%d)" % (wanted, n)
            n += 1
        return name

    def _on_join(self, rec, msg):
        if rec.name:
            return  # already joined
        wanted = str(msg.get("nickname", "")).strip()[:16] or "Player"
        rec.name = self._unique_name(wanted)
        rec.joined_at = time.time()
        self._reseat()
        self._send(rec, protocol.WELCOME,
                   client_id=rec.id, role=rec.role,
                   message="Welcome, %s." % rec.name,
                   grid_size=self.game.grid_size,
                   bombs_total=self.game.bomb_count,
                   turn_seconds=config.TURN_SECONDS)
        self.say("%s joined as %s (%d online)"
                 % (rec.name, rec.role, len(self._joined_clients())))
        self.push_clients()
        self._maybe_autostart()
        self.push_state()

    def _on_pick(self, rec, msg):
        result = self.game.pick(rec.id, msg.get("row", -1), msg.get("col", -1))
        if not result.get("ok"):
            self._send(rec, protocol.ERROR, message=result.get("reason", "invalid"))
            return
        where = "(%s,%s)" % (msg.get("row"), msg.get("col"))
        if result["is_bomb"]:
            self.say("%s found a BOMB at %s - keeps the turn" % (rec.name, where))
            if config.RESET_TIMER_ON_BOMB and not result["match_over"]:
                self._start_turn_clock()
        else:
            self.say("%s opened %s - %d nearby" % (rec.name, where, result["value"]))
        if result["match_over"]:
            self._end_match()
        elif result["turn_changed"]:
            self._start_turn_clock()
        self.push_state()

    def _on_rematch(self, rec):
        if self.game.phase != game_rules.PHASE_ENDED or rec.role != "player":
            return
        self.rematch_votes.add(rec.id)
        self.say("%s wants a rematch (%d/%d)"
                 % (rec.name, len(self.rematch_votes), len(self.game.players)))
        if self.rematch_votes >= set(self.game.players):
            # The winner of the last match starts the next one.
            self._begin_match(self.game.last_winner)
        self.push_state()

    def _on_disconnect(self, client_id):
        rec = self._drop_client(client_id)
        if rec is None:
            return
        self.say("%s disconnected" % rec.label)
        self.rematch_votes.discard(client_id)
        was_player = client_id in self.game.players
        self._reseat()
        if was_player and self.game.phase == game_rules.PHASE_PLAYING:
            self.game.phase = game_rules.PHASE_WAITING
            self.game.current_turn = None
            self._stop_turn_clock()
            self.say("Match halted - waiting for a second player")
        self.push_clients()
        self._maybe_autostart()
        self.push_state()

    # ------------------------------------------------------------------
    # turn clock
    # ------------------------------------------------------------------
    def update_clock(self):
        if self.game.phase != game_rules.PHASE_PLAYING or self.turn_deadline is None:
            return
        left = self._seconds_left()
        if left != self.last_tick_sent:
            self.last_tick_sent = left
            self._broadcast(protocol.TICK, seconds_left=left,
                            current_turn=self.game.current_turn)
        if time.time() >= self.turn_deadline:
            names = {c.id: c.name for c in self._joined_clients()}
            self.say("%s ran out of time" % names.get(self.game.current_turn, "?"))
            self.game.pass_turn()
            self._start_turn_clock()
            self.push_state()

    def shutdown(self):
        self.running = False
        for rec in self._ordered_clients():
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

    @staticmethod
    def _font(size, bold=False):
        for name in ("Segoe UI", "Arial", "DejaVu Sans"):
            try:
                return pygame.font.SysFont(name, size, bold=bold)
            except Exception:
                continue
        return pygame.font.Font(None, size)

    # -- small drawing helpers -----------------------------------------
    def text(self, s, pos, font=None, color=TEXT):
        surf = (font or self.f_body).render(str(s), True, color)
        self.screen.blit(surf, pos)
        return surf.get_width()

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

        self._draw_clients(pygame.Rect(32, 92, 400, 300))
        self._draw_match(pygame.Rect(452, 92, 456, 300))
        self._draw_board(pygame.Rect(452, 408, 456, 220))
        self._draw_log(pygame.Rect(32, 408, 400, 220))

        pygame.display.flip()

    def _draw_reset_button(self):
        colour = (185, 60, 60) if self.reset_hover else (150, 48, 48)
        pygame.draw.rect(self.screen, colour, self.reset_rect, border_radius=8)
        pygame.draw.rect(self.screen, (220, 120, 120), self.reset_rect,
                         width=1, border_radius=8)
        label = self.f_head.render("RESET GAME", True, (255, 235, 235))
        self.screen.blit(label, label.get_rect(center=self.reset_rect.center))

    def _draw_clients(self, rect):
        srv = self.server
        joined = srv._joined_clients()
        self.panel(rect, "CONNECTED CLIENTS")
        self.text("online", (rect.right - 66, rect.y + 20), self.f_small, MUTED)
        self.text(str(len(joined)), (rect.right - 34, rect.y + 6),
                  self.f_title, ACCENT)

        y = rect.y + 52
        if not joined:
            self.text("waiting for players to connect...",
                      (rect.x + 16, y + 8), self.f_body, MUTED)
            return
        for c in joined:
            row = pygame.Rect(rect.x + 10, y, rect.width - 20, 40)
            pygame.draw.rect(self.screen, PANEL_2, row, border_radius=8)
            is_turn = (c.id == srv.game.current_turn)
            if is_turn:
                pygame.draw.rect(self.screen, GOOD, row, width=2, border_radius=8)
            self.text(c.name, (row.x + 12, row.y + 9), self.f_body,
                      GOOD if is_turn else TEXT)
            badge = "PLAYER" if c.role == "player" else "SPECTATOR"
            self.text(badge, (row.x + 150, row.y + 12), self.f_small,
                      ACCENT if c.role == "player" else MUTED)
            self.text("%s:%d" % c.addr, (row.x + 240, row.y + 12),
                      self.f_small, MUTED)
            if c.role == "player":
                self.text(srv.game.scores.get(c.id, 0),
                          (row.right - 26, row.y + 9), self.f_body, WARN)
            y += 46

    def _draw_match(self, rect):
        srv = self.server
        g = srv.game
        self.panel(rect, "MATCH")

        phase_text = {game_rules.PHASE_WAITING: "waiting for players",
                      game_rules.PHASE_PLAYING: "in progress",
                      game_rules.PHASE_ENDED: "finished"}[g.phase]
        phase_colour = {game_rules.PHASE_WAITING: MUTED,
                        game_rules.PHASE_PLAYING: GOOD,
                        game_rules.PHASE_ENDED: WARN}[g.phase]
        self.text(phase_text, (rect.x + 16, rect.y + 44), self.f_body, phase_colour)

        names = {c.id: c.name for c in srv._joined_clients()}
        self.text("turn", (rect.x + 16, rect.y + 84), self.f_small, MUTED)
        self.text(names.get(g.current_turn, "-"),
                  (rect.x + 16, rect.y + 102), self.f_head, TEXT)

        left = srv._seconds_left()
        colour = BAD if left <= 3 and g.phase == game_rules.PHASE_PLAYING else ACCENT
        self.text("countdown", (rect.x + 200, rect.y + 84), self.f_small, MUTED)
        self.text("00:00:%02d" % left, (rect.x + 200, rect.y + 98),
                  self.f_title, colour)

        self.text("bombs left", (rect.x + 16, rect.y + 150), self.f_small, MUTED)
        self.text("%d / %d" % (g.bombs_left, g.bomb_count),
                  (rect.x + 16, rect.y + 168), self.f_head, TEXT)

        if g.phase == game_rules.PHASE_ENDED:
            self.text("rematch votes", (rect.x + 200, rect.y + 150),
                      self.f_small, MUTED)
            self.text("%d / %d" % (len(srv.rematch_votes), len(g.players)),
                      (rect.x + 200, rect.y + 168), self.f_head, WARN)

        y = rect.y + 216
        for pid in g.players:
            self.text("%-14s %d" % (names.get(pid, "?"), g.scores.get(pid, 0)),
                      (rect.x + 16, y), self.f_body,
                      GOOD if pid == g.last_winner else TEXT)
            y += 26

    def _draw_board(self, rect):
        """Admin view of the board - the only place unfound bombs are shown."""
        g = self.server.game
        self.panel(rect, "BOARD  (server view)")
        view = g.board_view(reveal_all=True)
        size, gap = 28, 4
        n = g.grid_size
        ox, oy = rect.x + 16, rect.y + 44
        for r in range(n):
            for c in range(n):
                cell = pygame.Rect(ox + c * (size + gap), oy + r * (size + gap),
                                   size, size)
                value = view[r][c]
                if value is None:
                    pygame.draw.rect(self.screen, HIDDEN_CELL, cell, border_radius=4)
                elif value == game_rules.HIDDEN_BOMB:
                    pygame.draw.rect(self.screen, HIDDEN_CELL, cell, border_radius=4)
                    pygame.draw.circle(self.screen, (120, 70, 70), cell.center, 5)
                elif value == game_rules.BOMB:
                    pygame.draw.rect(self.screen, (150, 48, 48), cell, border_radius=4)
                    pygame.draw.circle(self.screen, (255, 220, 220), cell.center, 6)
                else:
                    pygame.draw.rect(self.screen, PANEL_2, cell, border_radius=4)
                    label = self.f_cell.render(str(value), True,
                                               MUTED if value == 0 else TEXT)
                    self.screen.blit(label, label.get_rect(center=cell.center))

        legend_x = ox + n * (size + gap) + 20
        self.text("found bomb", (legend_x, oy + 4), self.f_small, BAD)
        self.text("hidden bomb", (legend_x, oy + 28), self.f_small, MUTED)
        self.text("opened slot", (legend_x, oy + 52), self.f_small, TEXT)

    def _draw_log(self, rect):
        self.panel(rect, "ACTIVITY")
        y = rect.y + 44
        for line in list(self.server.log):
            self.text(line, (rect.x + 16, y), self.f_small, MUTED)
            y += 19


def main():
    try:
        server = Server()
    except OSError as exc:
        print("Could not bind port %d: %s" % (config.SERVER_PORT, exc))
        sys.exit(1)
    server.start_network()
    ServerUI(server).run()


if __name__ == "__main__":
    main()
