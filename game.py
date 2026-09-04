"""Pure game rules for Find My Mines.

No sockets, no pygame, no threads live in here - just the board and the
turn order.  That keeps the rules testable on their own and means the
server is the only place that has to worry about concurrency.

Four modes share one board engine.  A slot is a coordinate tuple: (row, col)
on a flat board, (layer, row, col) on the cube.  Everything that touches
geometry goes through _neighbours(), so growing a third dimension did not
mean rewriting the rules.
"""

import itertools
import random

import config

# Phases of a room.
PHASE_WAITING = "waiting"   # not enough players seated yet
PHASE_PLAYING = "playing"   # a match is running
PHASE_ENDED = "ended"       # finished, waiting on rematch votes

BOMB = "bomb"               # a revealed bomb, in a board view
HIDDEN_BOMB = "hidden_bomb"  # only ever sent to the server's own admin view

# Modes.
MODE_CLASSIC = "classic"    # the graded game: find bombs, one point each
MODE_RADIUS2 = "radius2"    # same, but a bomb's hint reaches two rings out
MODE_SWEEPER = "sweeper"    # inverted: bombs are bad, open safe ground
MODE_CUBE = "cube"          # the classic game on a 4x4x4 cube
MODE_CUSTOM = "custom"      # players set the board and the rules themselves

MODES = [MODE_CLASSIC, MODE_RADIUS2, MODE_SWEEPER, MODE_CUBE, MODE_CUSTOM]

MODE_LABELS = {
    MODE_CLASSIC: "Classic",
    MODE_RADIUS2: "Radius 2",
    MODE_SWEEPER: "Minesweeper",
    MODE_CUBE: "3D Cube",
    MODE_CUSTOM: "Custom",
}

MODE_BLURBS = {
    MODE_CLASSIC: "Find bombs, one point each.",
    MODE_RADIUS2: "Hints reach two rings: 2 then 1.",
    MODE_SWEEPER: "Bombs are bad. Clear safe ground.",
    MODE_CUBE: "4x4x4 cube - 26 neighbours.",
    MODE_CUSTOM: "Your board, your rules.",
}


def clamp_custom(settings):
    """Force a settings dict into something playable.

    Everything here arrives from a client, so nothing is trusted: sizes
    are clipped to the limits in config and the bomb count can never
    swallow the whole board.
    """
    limits = config.CUSTOM_LIMITS
    out = dict(config.DEFAULT_CUSTOM)
    out.update({k: v for k, v in (settings or {}).items() if k in out})

    out["shape"] = "cube" if out.get("shape") == "cube" else "flat"
    out["hints"] = "radius2" if out.get("hints") == "radius2" else "simple"
    out["goal"] = "avoid" if out.get("goal") == "avoid" else "collect"

    low, high = limits["size_cube" if out["shape"] == "cube" else "size_flat"]
    out["size"] = max(low, min(high, int(out.get("size", low))))

    low, high = limits["turn_seconds"]
    out["turn_seconds"] = max(low, min(high, int(out.get("turn_seconds", low))))

    cells = out["size"] ** (3 if out["shape"] == "cube" else 2)
    most = max(1, int(cells * limits["max_bomb_share"]))
    out["bombs"] = max(1, min(most, int(out.get("bombs", 1))))
    return out


class Game:
    def __init__(self, mode=None, rng=None):
        self.rng = rng or random.Random()
        self.custom = clamp_custom(config.DEFAULT_CUSTOM)

        self.players = []        # ordered player ids; index 0 and 1 play
        self.scores = {}         # player id -> score for the current match
        self.phase = PHASE_WAITING
        self.current_turn = None
        self.last_winner = None  # winner of the previous match, starts next
        self.set_mode(mode or config.DEFAULT_MODE)

    # -- mode and shape -------------------------------------------------
    def set_mode(self, mode):
        """Switch mode and clear the board.  Scores are the caller's call."""
        self.mode = mode if mode in MODES else MODE_CLASSIC
        if self.mode == MODE_CUBE:
            self.dims = tuple(config.GRID_3D)
            self.bomb_count = config.BOMB_COUNT_3D
        elif self.mode == MODE_CUSTOM:
            size = self.custom["size"]
            self.dims = ((size, size, size) if self.custom["shape"] == "cube"
                         else (size, size))
            self.bomb_count = self.custom["bombs"]
        else:
            self.dims = (config.GRID_SIZE, config.GRID_SIZE)
            self.bomb_count = config.BOMB_COUNT
        self.phase = PHASE_WAITING
        self.current_turn = None
        self._clear_board()

    def set_custom(self, settings):
        """Apply new custom settings, and re-shape the board if we are in
        that mode.  Returns the settings actually used, after clamping."""
        self.custom = clamp_custom(settings)
        if self.mode == MODE_CUSTOM:
            self.set_mode(MODE_CUSTOM)
        return self.custom

    @property
    def turn_seconds(self):
        """Seconds per turn - the custom game may have its own."""
        if self.mode == MODE_CUSTOM:
            return self.custom["turn_seconds"]
        return config.TURN_SECONDS

    @property
    def bombs_are_bad(self):
        """True when opening a bomb is a mistake rather than the point."""
        return (self.mode == MODE_SWEEPER
                or (self.mode == MODE_CUSTOM and self.custom["goal"] == "avoid"))

    @property
    def is_3d(self):
        return len(self.dims) == 3

    @property
    def grid_size(self):
        """Width of the board - every mode is square in its rows and columns."""
        return self.dims[-1]

    @property
    def cell_count(self):
        total = 1
        for size in self.dims:
            total *= size
        return total

    def cells(self):
        """Every slot on the board, as coordinate tuples."""
        return itertools.product(*(range(size) for size in self.dims))

    # -- board ----------------------------------------------------------
    def _clear_board(self):
        self.bombs = set()
        self.revealed = {}       # slot -> BOMB or a hint number
        self.flags = {}          # slot -> the player who planted the flag
        self.hints = {}          # slot -> what it will show when opened
        self.bombs_found = 0

    def _place_bombs(self):
        """Scatter bombs at random, then work out every slot's hint."""
        self.bombs = set(self.rng.sample(list(self.cells()), self.bomb_count))
        self._compute_hints()

    def _compute_hints(self):
        for cell in self.cells():
            weighted = (self.mode == MODE_RADIUS2
                        or (self.mode == MODE_CUSTOM
                            and self.custom["hints"] == "radius2"))
            if weighted:
                # A bomb touching the slot counts 2, one a ring further out
                # counts 1 - so every bomb influences 24 slots, not 8.
                self.hints[cell] = (2 * self._bombs_at(cell, 1)
                                    + self._bombs_at(cell, 2))
            else:
                self.hints[cell] = self._bombs_at(cell, 1)

    def _bombs_at(self, cell, distance):
        return sum(1 for nb in self._neighbours(cell, distance)
                   if nb in self.bombs)

    def _neighbours(self, cell, distance=1):
        """Every slot exactly `distance` steps away, diagonals included.

        Distance 1 gives the 8 slots around a flat cell and the 26 around a
        cube cell; distance 2 gives the ring beyond that.  Slots outside the
        board are skipped, which is what clips the rings at the edges.
        """
        span = range(-distance, distance + 1)
        for offset in itertools.product(span, repeat=len(cell)):
            if max(abs(step) for step in offset) != distance:
                continue                      # inside the ring, not on it
            nb = tuple(v + step for v, step in zip(cell, offset))
            if all(0 <= v < size for v, size in zip(nb, self.dims)):
                yield nb

    def board_view(self, reveal_all=False):
        """The board as nested lists: None hidden, "bomb", or a hint number.

        Flat modes give rows of slots; the cube gives layers of rows.
        reveal_all is for the server's own screen only - it marks bombs the
        players have not found yet, and clients never receive it.
        """
        def value_at(cell):
            if cell in self.revealed:
                return self.revealed[cell]
            if reveal_all and cell in self.bombs:
                return HIDDEN_BOMB
            return None

        if self.is_3d:
            layers, rows, cols = self.dims
            return [[[value_at((l, r, c)) for c in range(cols)]
                     for r in range(rows)] for l in range(layers)]
        rows, cols = self.dims
        return [[value_at((r, c)) for c in range(cols)] for r in range(rows)]

    def flag_view(self):
        """Flags as JSON-friendly pairs of slot and owner."""
        return [{"cell": list(cell), "by": owner}
                for cell, owner in self.flags.items()]

    # -- players --------------------------------------------------------
    def seat_players(self, player_ids):
        """Set who is playing.  New players start on a score of 0."""
        self.players = list(player_ids)
        for pid in self.players:
            self.scores.setdefault(pid, 0)
        if self.current_turn not in self.players:
            self.current_turn = None

    def reset_scores(self):
        for pid in self.scores:
            self.scores[pid] = 0

    def opponent_of(self, player_id):
        others = [p for p in self.players if p != player_id]
        return others[0] if others else None

    # -- match flow -----------------------------------------------------
    def can_start(self):
        return len(self.players) >= config.MAX_PLAYERS

    def start_match(self, first_player=None):
        """Deal a fresh board.  first_player=None picks at random."""
        if not self.can_start():
            self.phase = PHASE_WAITING
            return False
        self._clear_board()
        self._place_bombs()
        # Every match starts level: scores belong to the match, not the
        # session, so a rematch is a fresh contest.
        self.reset_scores()
        if first_player not in self.players:
            first_player = self.rng.choice(self.players)
        self.current_turn = first_player
        self.phase = PHASE_PLAYING
        return True

    def pick(self, player_id, cell):
        """Open one slot.  Returns a result dict describing what happened.

        The caller (the server) uses turn_changed to decide whether to
        restart the countdown.
        """
        cell = tuple(cell)
        if self.phase != PHASE_PLAYING:
            return {"ok": False, "reason": "no match in progress"}
        if player_id != self.current_turn:
            return {"ok": False, "reason": "not your turn"}
        if len(cell) != len(self.dims) or not all(
                0 <= v < size for v, size in zip(cell, self.dims)):
            return {"ok": False, "reason": "off the board"}
        if cell in self.revealed:
            return {"ok": False, "reason": "slot already taken"}
        if self.flags.get(cell) == player_id:
            return {"ok": False, "reason": "remove your flag first"}

        if self.bombs_are_bad:
            return self._pick_sweeper(player_id, cell)
        return self._pick_hunt(player_id, cell)

    def _pick_hunt(self, player_id, cell):
        """Classic, radius-2 and cube: bombs are the prize."""
        self.flags.pop(cell, None)
        is_bomb = cell in self.bombs
        if is_bomb:
            self.revealed[cell] = BOMB
            self.bombs_found += 1
            self.scores[player_id] = self.scores.get(player_id, 0) + 1
        else:
            self.revealed[cell] = self.hints[cell]

        match_over = self.bombs_found >= self.bomb_count
        if match_over:
            self._finish_match()
        elif not is_bomb:
            # An empty slot ends your turn; a bomb lets you keep going.
            self.pass_turn()

        return {
            "ok": True,
            "is_bomb": is_bomb,
            "value": self.revealed[cell],
            "opened": 1,
            "turn_changed": (not is_bomb) and not match_over,
            "match_over": match_over,
        }

    def _pick_sweeper(self, player_id, cell):
        """Minesweeper mode: bombs are the hazard, safe ground is the prize."""
        self.flags.pop(cell, None)
        if cell in self.bombs:
            self.revealed[cell] = BOMB
            self.bombs_found += 1
            self.pass_turn()
            return {"ok": True, "is_bomb": True, "value": BOMB, "opened": 0,
                    "turn_changed": True, "match_over": False}

        opened = self._open_region(cell)
        self.scores[player_id] = self.scores.get(player_id, 0) + len(opened)
        match_over = self.safe_left == 0
        if match_over:
            self._finish_match()
        return {
            "ok": True,
            "is_bomb": False,
            "value": self.revealed[cell],
            "opened": len(opened),
            "turn_changed": False,     # safe ground keeps your turn
            "match_over": match_over,
        }

    def _open_region(self, cell):
        """Open a slot, and spill outwards while the hints read zero."""
        opened = []
        stack = [cell]
        while stack:
            current = stack.pop()
            if current in self.revealed or current in self.bombs:
                continue
            self.revealed[current] = self.hints[current]
            self.flags.pop(current, None)
            opened.append(current)
            if self.hints[current] == 0:
                stack.extend(self._neighbours(current, 1))
        return opened

    def toggle_flag(self, player_id, cell):
        """Plant or lift a marker.  A flag only blocks the player who set it,
        so nobody can wall the board off from their opponent."""
        cell = tuple(cell)
        if self.phase != PHASE_PLAYING or cell in self.revealed:
            return False
        if self.flags.get(cell) == player_id:
            del self.flags[cell]
        else:
            self.flags[cell] = player_id
        return True

    def pass_turn(self):
        """Hand the turn to the other player (empty slots and timeouts)."""
        if self.phase != PHASE_PLAYING:
            return
        nxt = self.opponent_of(self.current_turn)
        if nxt is not None:
            self.current_turn = nxt

    def _finish_match(self):
        self.phase = PHASE_ENDED
        self.current_turn = None
        self.last_winner = self.winner()

    def winner(self):
        """Player id with the highest score, or None when it is a draw."""
        if not self.players:
            return None
        ranked = sorted(self.players, key=lambda p: self.scores.get(p, 0),
                        reverse=True)
        if len(ranked) > 1 and self.scores.get(ranked[0], 0) == self.scores.get(
                ranked[1], 0):
            return None
        return ranked[0]

    @property
    def bombs_left(self):
        return self.bomb_count - self.bombs_found

    @property
    def safe_left(self):
        """Safe slots still covered - the sweeper mode's finish line."""
        opened_safe = sum(1 for c in self.revealed if c not in self.bombs)
        return (self.cell_count - self.bomb_count) - opened_safe

    def full_reset(self):
        """Server Reset button: wipe the board, the scores and the history."""
        self._clear_board()
        self.reset_scores()
        self.phase = PHASE_WAITING
        self.current_turn = None
        self.last_winner = None
