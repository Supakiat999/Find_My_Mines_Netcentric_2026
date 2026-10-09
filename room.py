"""Room-owned gameplay and public directory representation."""

import random
import socket
import time
import uuid
from collections import deque
from datetime import datetime, timezone

import ai
import game as game_rules
import config
import protocol
import botbrain
import stats as stats_mod

BOT_ID = -1
BOT_LABELS = {"easy": "Computer (Easy)", "medium": "Computer (Medium)",
              "hard": "Computer (Hard)"}
CHAT_LIMIT = 120
CHAT_HISTORY = 40
CHAT_GAP = 0.6


class Room:
    def __init__(self, server, name, mode, custom, bot_level, ranked):
        if (not isinstance(name, str) or not name.strip() or len(name.strip()) > 32
                or not name.strip().isprintable()):
            raise ValueError("room name must be 1-32 characters")
        if mode not in game_rules.MODES:
            raise ValueError("invalid mode")
        if not isinstance(custom, dict):
            raise ValueError("custom settings must be an object")
        if bot_level not in ("off", *ai.LEVELS):
            raise ValueError("invalid bot level")
        if type(ranked) is not bool:
            raise ValueError("ranked must be boolean")
        self.id = uuid.uuid4().hex
        self.name = name.strip()
        self.mode = mode
        self.custom = game_rules.clamp_custom(custom)
        self.bot_level = bot_level
        self.ranked = ranked
        self.game = game_rules.Game(mode)
        self.game.set_custom(self.custom)
        self.server = server
        self.chat = deque(maxlen=CHAT_HISTORY)
        self.rematch_votes = set()
        self.pending_match = None
        self.save_error = None
        self.match_id = None
        self.match_started_at = None
        self.turn_deadline = None
        self.last_tick_sent = None
        self.bot_ready_at = None
        self.paused_left = None
        self.first_match_done = False
        self.rng = random.Random()

    @property
    def board(self):
        return self.server.board

    @property
    def clients(self):
        return self.server.clients

    @property
    def clients_lock(self):
        return self.server.clients_lock

    def _client(self, client_id):
        return self.server._client(client_id)

    def _drop_client(self, client_id):
        return self.server._drop_client(client_id)

    def _joined_clients(self):
        with self.clients_lock:
            members = [c for c in self.clients.values()
                       if c.room_id == self.id and c.name]
        return sorted(members, key=lambda c: c.joined_at)

    def _send(self, rec, msg_type, **payload):
        payload["room_id"] = self.id
        self.server._send(rec, msg_type, **payload)

    def _broadcast(self, msg_type, **payload):
        payload["room_id"] = self.id
        line = protocol.encode(msg_type, **payload)
        for rec in self._joined_clients():
            if rec.alive:
                try:
                    rec.sock.sendall(line)
                except OSError:
                    rec.alive = False

    def say(self, text):
        self.server.say("[%s] %s" % (self.name, text))

    def summary(self, members):
        roster = [c for c in members if c.room_id == self.id]
        players = sum(c.role == "player" for c in roster) + int(BOT_ID in self.game.players)
        spectators = sum(c.role == "spectator" for c in roster)
        return {"id": self.id, "name": self.name, "mode": self.mode,
                "mode_label": game_rules.MODE_LABELS.get(self.mode, self.mode),
                "ranked": self.ranked,
                "rated": self.ranked and self.mode in config.ELO_RANKED_MODES and self.bot_level == "off",
                "bot_level": self.bot_level,
                "phase": "saving" if self.pending_match is not None else self.game.phase,
                "players": players, "spectators": spectators, "capacity": 2,
                "joinable": self.pending_match is None and
                            sum(c.role == "player" for c in roster) < (1 if self.bot_level != "off" else 2),
                "settings": dict(self.custom)}


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
                elo_val = self.board.get_elo(name, g.mode)
                entry["elo"] = elo_val
                tier_info = stats_mod.get_tier(elo_val)
                entry["tier"] = tier_info[0]
                entry["tier_color"] = tier_info[1]
                entry["peak_elo"] = self.board.get_peak_elo(name, g.mode)
                entry["provisional"] = self.board.is_provisional(name, g.mode)
                entry["mode_matches"] = self.board.get_mode_matches(name, g.mode)
            players.append(entry)
        away = self._away_payload()
        return {
            "room_name": self.name,
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
            "bot_learned": botbrain.ready(g),    # trained model, not the solver
            "away": away,
            "paused": bool(away) and g.phase == game_rules.PHASE_PLAYING,
            "ranked": self.ranked,
            "rated": self.ranked and g.mode in config.ELO_RANKED_MODES and self.bot_level == "off",
            "leaderboard": self.board.top(5, mode=g.mode),
        }

    def push_clients(self):
        self._broadcast(protocol.CLIENTS, **self._clients_payload())

    def push_state(self):
        self._broadcast(protocol.STATE, **self._state_payload())
        self.server.push_rooms()

    def _send_welcome(self, rec, reconnected=False):
        greeting = ("Welcome back, %s." if reconnected else "Welcome, %s.") % rec.name
        self._send(rec, protocol.WELCOME,
                   client_id=rec.id, role=rec.role, message=greeting, room_name=self.name,
                   token=rec.token, reconnected=reconnected,
                   grid_size=self.game.grid_size,
                   dims=list(self.game.dims),
                   mode=self.game.mode,
                   bombs_total=self.game.bomb_count,
                   turn_seconds=self.game.turn_seconds,
                   bot_level=self.bot_level,
                   chat=list(self.chat),
                   leaderboard=self.board.top(5, mode=self.game.mode))

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
        if self.pending_match is not None:
            for c in joined:
                if c.id not in before:
                    c.role = "spectator"
            return
        eligible = [c for c in joined if not c.watch]
        capacity = 1 if self.bot_level != "off" else config.MAX_PLAYERS
        seats = [c.id for c in eligible[:capacity]]
        for c in joined:
            c.role = "player" if c.id in seats else "spectator"
        if self._bot_wanted(len(seats)):
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
        if self.pending_match is not None:
            return
        if not self.game.start_match(first_player):
            return
        self.match_id = str(uuid.uuid4())
        self.match_started_at = datetime.now(timezone.utc).isoformat()
        self.rematch_votes.clear()
        self.bot_ready_at = None
        self.paused_left = None
        self._start_turn_clock()
        self.first_match_done = True
        names = self._names()
        self.say("Match started - %s goes first"
                 % names.get(self.game.current_turn, "?"))
        self.server.push_rooms()

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
        if self.pending_match is not None:
            return
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
        context = {"players": players, "detail": detail, "results": results,
                   "mode": g.mode, "ranked": self.ranked,
                   "winner": g.last_winner, "names": names,
                   "bot": self._bot_seated()}
        if isinstance(self.board, stats_mod.DatabaseLeaderboard):
            participants = []
            for seat, p in enumerate(players, 1):
                pid = p["id"]
                stat = g.stats.get(pid, {})
                participants.append({
                    "seat": seat, "name": p["name"],
                    "bot_level": self.bot_level if pid == BOT_ID else None,
                    "result": ("draw" if g.last_winner is None else
                               "win" if pid == g.last_winner else "loss"),
                    "score": p["score"], "picks": stat.get("picks", 0),
                    "hits": stat.get("kept", 0),
                    "best_chain": stat.get("best_chain", 0),
                })
            self.pending_match = context
            self.board.submit({
                "id": self.match_id, "mode": g.mode, "ranked": self.ranked,
                "started_at": self.match_started_at,
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "settings": {"room_id": self.id, "room_name": self.name,
                             "dims": list(g.dims), "bombs": g.bomb_count,
                             "turn_seconds": g.turn_seconds,
                             "weighted": g.hints_weighted,
                             "bombs_are_bad": g.bombs_are_bad},
                "results": results, "participants": participants,
            }, context=(self.id, self.match_id))
            self._announce("Match finished - saving result.", system=True)
            self.server.push_rooms()
            return
        changes = self.board.record_match(results, mode=g.mode, ranked=self.ranked)
        self._publish_match(context, changes)

    def accept_saved(self, changes, error):
        if error:
            if error != self.save_error:
                self.save_error = error
                self.say("Result saving pending: %s" % error)
                self._announce("Result saving pending - database error. "
                               "New matches in this room are paused.", system=True)
            return
        context = self.pending_match
        self.pending_match = None
        self.save_error = None
        if context is not None:
            self._publish_match(context, changes)
            self._reseat()
            self._maybe_autostart()
            self.push_state()

    def _publish_match(self, context, elo_changes):
        players, detail = context["players"], context["detail"]
        results, names = context["results"], context["names"]
        winner, mode, ranked = context["winner"], context["mode"], context["ranked"]

        elo_payload = {}
        for p in players:
            name = p["name"]
            if name in elo_changes:
                elo_payload[p["id"]] = elo_changes[name]
                elo_payload[name] = elo_changes[name]

        if context["bot"] and self._bot_seated():
            self.rematch_votes.add(BOT_ID)      # the computer always says yes
        self._broadcast(
            protocol.MATCH_END,
            winner_id=winner,
            draw=winner is None,
            players=players,
            stats=detail,
            mode=mode,
            ranked=ranked,
            rated=bool(elo_changes),
            elo_changes=elo_payload,
            leaderboard=self.board.top(5, mode=mode),
        )
        if elo_changes:
            parts = []
            for name, ch in elo_changes.items():
                sign = "+" if ch["delta"] > 0 else ""
                extra = []
                if ch.get("streak_bonus"):
                    extra.append("+%d streak" % ch["streak_bonus"])
                if ch.get("promoted"):
                    extra.append("PROMOTED TO %s!" % ch["promoted"].upper())
                extra_str = (" [%s]" % ", ".join(extra)) if extra else ""
                parts.append("%s (%s%d -> %d)%s" % (name, sign, ch["delta"], ch["after"], extra_str))
            self._announce("Elo: " + " | ".join(parts), system=True)
            self.say("Elo: %s" % " | ".join(parts))
        elif not ranked and len(results) == 2:
            self._announce("Casual match completed - no rating changes.", system=True)
        if winner is None:
            self.say("Match over - draw")
        elif winner in names:
            self.say("Match over - %s wins" % names[winner])
        else:
            self.say("Match over")

    def reset_all(self):
        """The Reset button: clear the board and both scores, then re-deal."""
        if self.pending_match is not None:
            self.say("Reset blocked - result saving pending")
            return
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
        cell = botbrain.choose_cell(g, self.bot_level, self.rng)
        if cell is None:
            g.pass_turn()
            self.push_state()
            return
        self._apply_pick(BOT_ID, cell)

    def _reattach(self, new, old):
        """Give a returning player their seat, score and turn back."""
        new.name = old.name
        new.joined_at = old.joined_at
        new.role = old.role
        new.token = old.token
        new.away_since = None
        self.game.rename_player(old.id, new.id)
        if self.pending_match is not None:
            context = self.pending_match
            for row in context["players"] + context["detail"]:
                if row["id"] == old.id:
                    row["id"] = new.id
            if context["winner"] == old.id:
                context["winner"] = new.id
            context["names"][new.id] = context["names"].pop(old.id, old.name)
        if old.id in self.rematch_votes:
            self.rematch_votes.discard(old.id)
            self.rematch_votes.add(new.id)
        with self.clients_lock:
            self.clients.pop(old.id, None)
        old.alive = False
        try:                        # a half-dead connection may still be open
            old.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
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
                rec.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
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
