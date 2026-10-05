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
import random
import secrets
import socket
import sys
import threading
import time
from collections import deque

import pygame

import ai
import config
import game as game_rules
import protocol
import stats as stats_mod

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

# The computer opponent sits in a seat like a player but is not a socket.
BOT_ID = -1
BOT_LABELS = {"easy": "Computer (Easy)", "medium": "Computer (Medium)",
              "hard": "Computer (Hard)"}

CHAT_LIMIT = 120        # characters in one message
CHAT_HISTORY = 40       # messages kept for people who join late
CHAT_GAP = 0.6          # seconds between one person's messages


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
        self.token = secrets.token_hex(8)   # proves who you are on reconnect
        self.away_since = None    # set while we hold the seat of a dropped player
        self.last_chat = 0.0

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
        self.paused_left = None           # seconds left when the clock was paused

        self.bot_level = "off"            # off | easy | medium | hard
        self.bot_ready_at = None          # when the computer will play its move
        self.rng = random.Random()

        self.chat = deque(maxlen=CHAT_HISTORY)
        path = (stats_mod.default_path() if config.STATS_FILE == "auto"
                else config.STATS_FILE)
        self.board = stats_mod.Leaderboard(path)

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
    def _bot_seated(self):
        return BOT_ID in self.game.players

    def _bot_name(self):
        return BOT_LABELS.get(self.bot_level, "Computer")

    def _names(self):
        """Player id -> display name, for humans and the computer alike."""
        names = {c.id: c.name for c in self._joined_clients()}
        if self._bot_seated():
            names[BOT_ID] = self._bot_name()
        return names

    def _anyone_away(self):
        return any(c.away_since is not None for c in self._joined_clients())

    def _unique_name(self, wanted):
        taken = {c.name for c in self._joined_clients() if c.name}
        taken.update(BOT_LABELS.values())
        name = wanted
        n = 2
        while name in taken:
            name = "%s (%d)" % (wanted, n)
            n += 1
        return name

    # ------------------------------------------------------------------
    # snapshots pushed to clients
    # ------------------------------------------------------------------
    def _clients_payload(self):
        humans = self._joined_clients()
        return {
            "count": len([c for c in humans if c.alive]),
            "list": [{"id": c.id, "name": c.name, "role": c.role,
                      "away": not c.alive} for c in humans],
            "bots": ([{"id": BOT_ID, "name": self._bot_name()}]
                     if self._bot_seated() else []),
        }

    def _seconds_left(self):
        if self.turn_deadline is None:
            return 0
        return max(0, int(round(self.turn_deadline - time.time())))

    def _away_payload(self):
        now = time.time()
        return [{"id": c.id, "name": c.name,
                 "seconds": max(0, int(config.RECONNECT_GRACE
                                       - (now - c.away_since)))}
                for c in self._joined_clients() if c.away_since is not None]

    def _state_payload(self):
        g = self.game
        names = self._names()
        players = []
        for pid in g.players:
            name = names.get(pid, "?")
            entry = {"id": pid, "name": name, "score": g.scores.get(pid, 0),
                     "bot": pid == BOT_ID,
                     "hints_left": g.hints_left.get(pid, 0)}
            if pid != BOT_ID:
                record = self.board.record_of(name)
                entry["record"] = {k: record[k] for k in
                                   ("wins", "losses", "draws", "points")}
            players.append(entry)
        away = self._away_payload()
        return {
            "phase": g.phase,
            "mode": g.mode,
            "mode_label": game_rules.MODE_LABELS.get(g.mode, g.mode),
            "dims": list(g.dims),             # (rows, cols) or (layers, rows, cols)
            "grid_size": g.grid_size,
            "board": g.board_view(),          # never reveals unfound bombs
            "flags": g.flag_view(),
            "bomb_owners": g.owner_view(),
            "last_move": g.last_move_view(),
            "safe_left": g.safe_left,
            "bombs_are_bad": g.bombs_are_bad,
            "players": players,
            "current_turn": g.current_turn,
            "bombs_left": g.bombs_left,
            "bombs_total": g.bomb_count,
            "seconds_left": self._seconds_left(),
            "turn_seconds": self.game.turn_seconds,
            "custom": dict(self.game.custom),
            "custom_limits": dict(config.CUSTOM_LIMITS),
            "rematch_votes": sorted(self.rematch_votes),
            "spectators": [c.name for c in self._joined_clients()
                           if c.role == "spectator"],
            "bot_level": self.bot_level,
            "bot_seated": self._bot_seated(),
            "away": away,
            "paused": bool(away) and g.phase == game_rules.PHASE_PLAYING,
            "leaderboard": self.board.top(5),
        }

    def push_clients(self):
        self._broadcast(protocol.CLIENTS, **self._clients_payload())

    def push_state(self):
        self._broadcast(protocol.STATE, **self._state_payload())

    def _send_welcome(self, rec, reconnected=False):
        greeting = ("Welcome back, %s." if reconnected else "Welcome, %s.") % rec.name
        self._send(rec, protocol.WELCOME,
                   client_id=rec.id, role=rec.role, message=greeting,
                   token=rec.token, reconnected=reconnected,
                   grid_size=self.game.grid_size,
                   dims=list(self.game.dims),
                   mode=self.game.mode,
                   bombs_total=self.game.bomb_count,
                   turn_seconds=self.game.turn_seconds,
                   bot_level=self.bot_level,
                   chat=list(self.chat),
                   leaderboard=self.board.top(5))

    # ------------------------------------------------------------------
    # chat
    # ------------------------------------------------------------------
    def _announce(self, text, player_id=0, name="", system=False):
        entry = {"id": player_id, "name": name, "text": text,
                 "system": system, "t": int(time.time())}
        self.chat.append(entry)
        self._broadcast(protocol.CHAT_MSG, **entry)

    def _note(self, text):
        """A line of table talk from the server itself."""
        self._announce(text, system=True)

    def _on_chat(self, rec, msg):
        if not rec.name:
            return
        text = " ".join(str(msg.get("text", "")).split())[:CHAT_LIMIT]
        if not text:
            return
        now = time.time()
        if now - rec.last_chat < CHAT_GAP:
            self._send(rec, protocol.ERROR, message="slow down a little")
            return
        rec.last_chat = now
        self._announce(text, rec.id, rec.name)

    # ------------------------------------------------------------------
    # seating and match flow
    # ------------------------------------------------------------------
    def _bot_wanted(self, humans):
        """The computer sits down only opposite exactly one human."""
        return self.bot_level in ai.LEVELS and humans == 1

    def _reseat(self):
        """First MAX_PLAYERS joiners play; anyone later watches.  With one
        human and the computer switched on, the computer takes the other seat.

        Changing who is seated ends the match in progress - a match with
        different players is a different match.
        """
        before = list(self.game.players)
        joined = self._joined_clients()
        seats = [c.id for c in joined[:config.MAX_PLAYERS]]
        for c in joined:
            c.role = "player" if c.id in seats else "spectator"
        if self._bot_wanted(len(joined)):
            seats = seats[:1] + [BOT_ID]
        if seats != before:
            self.game.seat_players(seats)
            if (set(seats) != set(before)
                    and self.game.phase != game_rules.PHASE_WAITING):
                self._halt("Match halted - the seating changed")

    def _halt(self, reason):
        """Stop the current match and wait for a fresh start."""
        self.game.phase = game_rules.PHASE_WAITING
        self.game.current_turn = None
        self._stop_turn_clock()
        self.rematch_votes.clear()
        self.bot_ready_at = None
        self.paused_left = None
        self.say(reason)

    def _begin_match(self, first_player=None):
        if not self.game.start_match(first_player):
            return
        self.rematch_votes.clear()
        self.bot_ready_at = None
        self.paused_left = None
        self._start_turn_clock()
        self.first_match_done = True
        names = self._names()
        self.say("Match started - %s goes first"
                 % names.get(self.game.current_turn, "?"))

    def _start_turn_clock(self):
        self.turn_deadline = time.time() + self.game.turn_seconds
        self.last_tick_sent = None

    def _stop_turn_clock(self):
        self.turn_deadline = None
        self.last_tick_sent = None

    def _pause_clock(self):
        """Freeze the turn while a player is away."""
        if self.turn_deadline is not None:
            self.paused_left = max(0.5, self.turn_deadline - time.time())
        self._stop_turn_clock()

    def _resume_clock(self):
        if (self.paused_left is not None and not self._anyone_away()
                and self.game.phase == game_rules.PHASE_PLAYING):
            self.turn_deadline = time.time() + self.paused_left
            self.last_tick_sent = None
            self.paused_left = None

    def _maybe_autostart(self):
        """Kick off a match as soon as two players are seated - and nobody
        has dropped out, since a match should not start without them."""
        if self._anyone_away():
            return
        if self.game.phase == game_rules.PHASE_WAITING and self.game.can_start():
            # First match of the session: the server picks the starter at
            # random.  Later ones follow the previous winner.
            first = None if not self.first_match_done else self.game.last_winner
            self._begin_match(first)

    def _end_match(self):
        self._stop_turn_clock()
        g = self.game
        names = self._names()
        players = [{"id": pid, "name": names.get(pid, "?"),
                    "score": g.scores.get(pid, 0)} for pid in g.players]
        detail = []
        for row in g.match_stats():
            detail.append({**row, "name": names.get(row["id"], "?"),
                           "score": g.scores.get(row["id"], 0)})

        # humans go on the hall of fame; the computer does not
        results = []
        for p in players:
            if p["id"] == BOT_ID:
                continue
            if g.last_winner is None:
                outcome = "draw"
            else:
                outcome = "win" if p["id"] == g.last_winner else "loss"
            results.append({"name": p["name"], "result": outcome,
                            "points": p["score"]})
        self.board.record_match(results)

        if self._bot_seated():
            self.rematch_votes.add(BOT_ID)      # the computer always says yes
        self._broadcast(
            protocol.MATCH_END,
            winner_id=g.last_winner,
            draw=g.last_winner is None,
            players=players,
            stats=detail,
            leaderboard=self.board.top(5),
        )
        if g.last_winner is None:
            self.say("Match over - draw")
        elif g.last_winner in names:
            self.say("Match over - %s wins" % names[g.last_winner])
        else:
            self.say("Match over")

    def set_mode(self, mode):
        """Mode buttons on the console.  The server owns which game is played,
        so a change deals a fresh board for everyone at once."""
        if mode == self.game.mode:
            return
        self.game.set_mode(mode)
        self.game.reset_scores()
        self.rematch_votes.clear()
        self._stop_turn_clock()
        self.bot_ready_at = None
        self.paused_left = None
        self.first_match_done = False
        self._reseat()
        self.say("Mode: %s" % game_rules.MODE_LABELS.get(mode, mode))
        self._note("Mode is now %s" % game_rules.MODE_LABELS.get(mode, mode))
        self._broadcast(protocol.SERVER_RESET)
        self.push_clients()
        self._maybe_autostart()
        self.push_state()

    def reset_all(self):
        """The Reset button: clear the board and both scores, then re-deal."""
        self.game.full_reset()
        self.rematch_votes.clear()
        self._stop_turn_clock()
        self.bot_ready_at = None
        self.paused_left = None
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
        elif kind == protocol.FLAG:
            self._on_flag(rec, msg)
        elif kind == protocol.REMATCH:
            self._on_rematch(rec)
        elif kind == protocol.SET_MODE:
            self._on_set_mode(rec, msg)
        elif kind == protocol.SET_CUSTOM:
            self._on_set_custom(rec, msg)
        elif kind == protocol.CHAT:
            self._on_chat(rec, msg)
        elif kind == protocol.HINT:
            self._on_hint(rec)
        elif kind == protocol.SET_BOT:
            self._on_set_bot(rec, msg)

    def _on_join(self, rec, msg):
        if rec.name:
            return  # already joined

        # Someone coming back on a new socket proves who they are with the
        # token they were given, and takes their old seat back.
        token = msg.get("token")
        if token:
            for old in self._joined_clients():
                if old is not rec and old.token == token:
                    self._reattach(rec, old)
                    return

        wanted = str(msg.get("nickname", "")).strip()[:16] or "Player"
        rec.name = self._unique_name(wanted)
        rec.joined_at = time.time()
        self._reseat()
        self._send_welcome(rec)
        self.say("%s joined as %s (%d online)"
                 % (rec.name, rec.role, len([c for c in self._joined_clients()
                                             if c.alive])))
        self._note("%s joined" % rec.name)
        self.push_clients()
        self._maybe_autostart()
        self.push_state()

    def _reattach(self, new, old):
        """Give a returning player their seat, score and turn back."""
        new.name = old.name
        new.joined_at = old.joined_at
        new.role = old.role
        new.token = old.token
        new.away_since = None
        self.game.rename_player(old.id, new.id)
        if old.id in self.rematch_votes:
            self.rematch_votes.discard(old.id)
            self.rematch_votes.add(new.id)
        with self.clients_lock:
            self.clients.pop(old.id, None)
        old.alive = False
        try:                        # a half-dead connection may still be open
            old.sock.close()
        except OSError:
            pass
        self._send_welcome(new, reconnected=True)
        self.say("%s reconnected" % new.name)
        self._note("%s is back" % new.name)
        self._resume_clock()
        self.push_clients()
        self._maybe_autostart()
        self.push_state()

    def _cell_from(self, msg):
        """Coordinates off the wire, shaped for the mode in play."""
        row, col = msg.get("row", -1), msg.get("col", -1)
        if self.game.is_3d:
            return (msg.get("layer", 0), row, col)
        return (row, col)

    def _on_pick(self, rec, msg):
        if self._anyone_away() and self.game.phase == game_rules.PHASE_PLAYING:
            self._send(rec, protocol.ERROR,
                       message="paused - waiting for a player to reconnect")
            return
        self._apply_pick(rec.id, self._cell_from(msg), rec)

    def _apply_pick(self, player_id, cell, rec=None):
        """One pick, from a human or the computer alike."""
        result = self.game.pick(player_id, cell)
        if not result.get("ok"):
            if rec is not None:
                self._send(rec, protocol.ERROR,
                           message=result.get("reason", "invalid"))
            return result
        who = self._names().get(player_id, "?")
        where = "(%s)" % ",".join(str(v) for v in cell)
        if result["is_bomb"]:
            if self.game.bombs_are_bad:
                self.say("%s hit a BOMB at %s - turn lost" % (who, where))
            else:
                self.say("%s found a BOMB at %s - keeps the turn" % (who, where))
                if config.RESET_TIMER_ON_BOMB and not result["match_over"]:
                    self._start_turn_clock()
        elif result["opened"] > 1:
            self.say("%s cleared %d slots from %s" % (who, result["opened"], where))
        else:
            self.say("%s opened %s - %s nearby" % (who, where, result["value"]))
        if result["match_over"]:
            self._end_match()
        elif result["turn_changed"]:
            self._start_turn_clock()
        self.push_state()
        return result

    def _on_flag(self, rec, msg):
        """Markers are advisory - they only block the player who set them."""
        if rec.role == "player" and self.game.toggle_flag(rec.id,
                                                          self._cell_from(msg)):
            self.push_state()

    def _on_set_mode(self, rec, msg):
        """Players may switch the game from their own window.

        The server still owns the decision - it validates the mode and
        deals the new board - but players should not have to walk over to
        the server console to find the other games.
        """
        mode = msg.get("mode")
        if rec.role != "player":
            self._send(rec, protocol.ERROR, message="only players can change mode")
            return
        if mode not in game_rules.MODES:
            return
        self.say("%s switched the mode" % rec.name)
        self.set_mode(mode)

    def _on_set_custom(self, rec, msg):
        """Board size, bomb count and rules for the custom game.

        Whatever a client sends is clamped by game.clamp_custom before it
        is used, so a hand-crafted message cannot ask for a 500x500 board
        or more bombs than slots.
        """
        if rec.role != "player":
            self._send(rec, protocol.ERROR,
                       message="only players can change the settings")
            return
        settings = msg.get("settings")
        if not isinstance(settings, dict):
            return
        applied = self.game.set_custom({**self.game.custom, **settings})
        self.say("%s set %dx%d%s, %d bombs, %ds"
                 % (rec.name, applied["size"], applied["size"],
                    "x%d" % applied["size"] if applied["shape"] == "cube" else "",
                    applied["bombs"], applied["turn_seconds"]))
        if self.game.mode == game_rules.MODE_CUSTOM:
            self.rematch_votes.clear()
            self._stop_turn_clock()
            self.bot_ready_at = None
            self.first_match_done = False
            self.game.reset_scores()
            self._broadcast(protocol.SERVER_RESET)
            self._maybe_autostart()
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

    # -- the AI coach ---------------------------------------------------
    def _on_hint(self, rec):
        """Advice on the best slot, from the same engine the computer uses.

        It is given only what any player can see, and each player gets a
        limited number of questions per match.
        """
        g = self.game
        if rec.role != "player":
            self._send(rec, protocol.ERROR, message="only players can ask the coach")
            return
        if g.phase != game_rules.PHASE_PLAYING:
            self._send(rec, protocol.ERROR, message="no match in progress")
            return
        if g.current_turn != rec.id:
            self._send(rec, protocol.ERROR, message="ask the coach on your own turn")
            return
        if not g.use_hint(rec.id):
            self._send(rec, protocol.ERROR, message="you have no hints left")
            return
        info = g.public_info()
        advice = ai.advise(info["view"], info["dims"], info["weighted"],
                           info["bombs_left"], info["bombs_are_bad"])
        if advice is None:
            g.hints_left[rec.id] += 1                 # nothing to advise: refund
            self._send(rec, protocol.ERROR, message="nothing left to advise on")
            return
        heat = [{"cell": list(cell), "p": round(p, 3)}
                for cell, p in advice["probs"].items()]
        self._send(rec, protocol.HINT_RESULT,
                   cell=list(advice["cell"]), p=round(advice["p"], 3),
                   exact=advice["exact"],
                   goal="avoid" if info["bombs_are_bad"] else "collect",
                   heat=heat, left=g.hints_left.get(rec.id, 0))
        self.say("%s asked the coach (%d left)" % (rec.name, g.hints_left[rec.id]))
        self.push_state()

    # -- the computer opponent -----------------------------------------
    def _on_set_bot(self, rec, msg):
        level = msg.get("level")
        if rec.role != "player":
            self._send(rec, protocol.ERROR, message="only players can do that")
            return
        if level != "off" and level not in ai.LEVELS:
            return
        if level != "off" and len(self._joined_clients()) >= 2:
            self._send(rec, protocol.ERROR,
                       message="two players are already seated")
            return
        if level == self.bot_level:
            return
        self.bot_level = level
        if level == "off":
            self.say("%s switched the computer off" % rec.name)
            self._note("Computer opponent off")
        else:
            self.say("%s chose to play the computer (%s)" % (rec.name, level))
            self._note("Playing the computer on %s" % level)
        self._reseat()
        if self.game.phase != game_rules.PHASE_WAITING:
            # same seats but a different opponent: start over fairly
            self._halt("Match restarted against a new opponent")
        self.push_clients()
        self._maybe_autostart()
        self.push_state()

    def _drive_bot(self):
        """Let the computer take its turn after a short, human-feeling pause."""
        g = self.game
        if (not self._bot_seated() or g.phase != game_rules.PHASE_PLAYING
                or g.current_turn != BOT_ID or self._anyone_away()):
            self.bot_ready_at = None
            return
        now = time.time()
        if self.bot_ready_at is None:
            lo, hi = config.BOT_THINK_SECONDS
            self.bot_ready_at = now + self.rng.uniform(lo, hi)
            return
        if now < self.bot_ready_at:
            return
        self.bot_ready_at = None
        info = g.public_info()
        cell = ai.choose_cell(info["view"], info["dims"], info["weighted"],
                              info["bombs_left"], info["bombs_are_bad"],
                              self.bot_level, self.rng)
        if cell is None:
            g.pass_turn()
            self.push_state()
            return
        self._apply_pick(BOT_ID, cell)

    # -- leaving and coming back ---------------------------------------
    def _on_disconnect(self, client_id):
        rec = self._client(client_id)
        if rec is None:
            return                      # already replaced by a reconnect
        if (rec.name and rec.role == "player" and config.RECONNECT_GRACE > 0
                and rec.away_since is None):
            # Wi-Fi drops are common.  Hold the seat, pause the clock, and
            # give them a window to come back before giving the seat up.
            rec.alive = False
            rec.away_since = time.time()
            try:
                rec.sock.close()
            except OSError:
                pass
            if self.game.phase == game_rules.PHASE_PLAYING:
                self._pause_clock()
            self.say("%s lost connection - holding the seat for %ds"
                     % (rec.name, config.RECONNECT_GRACE))
            self._note("%s lost connection" % rec.name)
            self.push_clients()
            self.push_state()
            return
        self._finalize_drop(client_id)

    def _finalize_drop(self, client_id):
        rec = self._drop_client(client_id)
        if rec is None:
            return
        self.say("%s disconnected" % rec.label)
        if rec.name:
            self._note("%s left" % rec.name)
        self.rematch_votes.discard(client_id)
        self._reseat()
        if not self._anyone_away():
            self._resume_clock()
        self.push_clients()
        self._maybe_autostart()
        self.push_state()

    def _expire_away(self):
        now = time.time()
        for rec in self._joined_clients():
            if (rec.away_since is not None
                    and now - rec.away_since > config.RECONNECT_GRACE):
                self.say("%s did not come back" % rec.name)
                self._finalize_drop(rec.id)

    # ------------------------------------------------------------------
    # turn clock
    # ------------------------------------------------------------------
    def update_clock(self):
        self._expire_away()
        self._drive_bot()
        if self.game.phase != game_rules.PHASE_PLAYING or self.turn_deadline is None:
            return
        left = self._seconds_left()
        if left != self.last_tick_sent:
            self.last_tick_sent = left
            self._broadcast(protocol.TICK, seconds_left=left,
                            current_turn=self.game.current_turn)
        if time.time() >= self.turn_deadline:
            names = self._names()
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
        self.mode_rects = self._mode_rects()

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
                        for mode, box in self.mode_rects:
                            if box.collidepoint(event.pos):
                                srv.set_mode(mode)
                                break
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

        self._draw_modes()
        self._draw_clients(pygame.Rect(32, 146, 400, 288))
        self._draw_match(pygame.Rect(452, 146, 456, 288))
        self._draw_board(pygame.Rect(452, 450, 456, 246))
        self._draw_log(pygame.Rect(32, 450, 400, 246))

        pygame.display.flip()

    def _mode_rects(self):
        """One button per mode, laid out left to right under the title."""
        rects = []
        x, width = 32, 116
        for mode in game_rules.MODES:
            rects.append((mode, pygame.Rect(x, 100, width, 34)))
            x += width + 8
        return rects

    def _draw_modes(self):
        mouse = pygame.mouse.get_pos()
        current = self.server.game.mode
        for mode, box in self.mode_rects:
            active = mode == current
            hot = box.collidepoint(mouse)
            fill = (37, 99, 235) if active else (PANEL_2 if hot else PANEL)
            pygame.draw.rect(self.screen, fill, box, border_radius=8)
            pygame.draw.rect(self.screen, ACCENT if active else LINE, box,
                             width=1, border_radius=8)
            label = game_rules.MODE_LABELS.get(mode, mode)
            self.text(label, box.center, self.f_body,
                      (235, 244, 255) if active else MUTED, center=True)
        # the description sits under the Reset button, clear of the buttons
        blurb = game_rules.MODE_BLURBS.get(current, "")
        self.text(blurb, (WIN_W - 32, 74), self.f_small, MUTED, right=True)

    def _draw_reset_button(self):
        colour = (185, 60, 60) if self.reset_hover else (150, 48, 48)
        pygame.draw.rect(self.screen, colour, self.reset_rect, border_radius=8)
        pygame.draw.rect(self.screen, (220, 120, 120), self.reset_rect,
                         width=1, border_radius=8)
        label = self.f_head.render("RESET GAME", True, (255, 235, 235))
        self.screen.blit(label, label.get_rect(center=self.reset_rect.center))

    def _draw_clients(self, rect):
        srv = self.server
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
        srv = self.server
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
        g = self.server.game
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
    ServerUI(server).run()


if __name__ == "__main__":
    main()
