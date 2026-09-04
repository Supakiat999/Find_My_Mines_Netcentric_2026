"""Pure game rules for Find My Mines.

No sockets, no pygame, no threads live in here - just the board and the
turn order.  That keeps the rules testable on their own and means the
server is the only place that has to worry about concurrency.
"""

import random

import config

# Phases of a room.
PHASE_WAITING = "waiting"   # not enough players seated yet
PHASE_PLAYING = "playing"   # a match is running
PHASE_ENDED = "ended"       # all bombs found, waiting on rematch votes

BOMB = "bomb"               # a revealed bomb, in a board view
HIDDEN_BOMB = "hidden_bomb"  # only ever sent to the server's own admin view


class Game:
    def __init__(self, grid_size=None, bomb_count=None, rng=None):
        self.grid_size = grid_size or config.GRID_SIZE
        self.bomb_count = bomb_count or config.BOMB_COUNT
        self.rng = rng or random.Random()

        self.players = []        # ordered player ids; index 0 and 1 play
        self.scores = {}         # player id -> score, kept across rematches
        self.phase = PHASE_WAITING
        self.current_turn = None
        self.last_winner = None  # winner of the previous match, starts next
        self._clear_board()

    # -- board ----------------------------------------------------------
    def _clear_board(self):
        n = self.grid_size
        self.bombs = set()
        self.revealed = {}                      # (row, col) -> BOMB or int
        self.adjacent = [[0] * n for _ in range(n)]
        self.bombs_found = 0

    def _place_bombs(self):
        """Scatter bomb_count bombs at random, then cache neighbour counts."""
        n = self.grid_size
        cells = [(r, c) for r in range(n) for c in range(n)]
        self.bombs = set(self.rng.sample(cells, self.bomb_count))
        for r in range(n):
            for c in range(n):
                self.adjacent[r][c] = sum(
                    1 for nr, nc in self._neighbours(r, c) if (nr, nc) in self.bombs
                )

    def _neighbours(self, row, col):
        """The eight surrounding cells, diagonals included, clipped to grid."""
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                r, c = row + dr, col + dc
                if 0 <= r < self.grid_size and 0 <= c < self.grid_size:
                    yield r, c

    def board_view(self, reveal_all=False):
        """Board as nested lists: None hidden, "bomb", or a 0-8 count.

        reveal_all is for the server's own screen only - it marks bombs the
        players have not found yet.  Clients never receive it.
        """
        view = []
        for r in range(self.grid_size):
            row = []
            for c in range(self.grid_size):
                if (r, c) in self.revealed:
                    row.append(self.revealed[(r, c)])
                elif reveal_all and (r, c) in self.bombs:
                    row.append(HIDDEN_BOMB)
                else:
                    row.append(None)
            view.append(row)
        return view

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
        if first_player not in self.players:
            first_player = self.rng.choice(self.players)
        self.current_turn = first_player
        self.phase = PHASE_PLAYING
        return True

    def pick(self, player_id, row, col):
        """Reveal one cell.  Returns a result dict describing what happened.

        The caller (the server) uses turn_changed to decide whether to
        restart the countdown.
        """
        if self.phase != PHASE_PLAYING:
            return {"ok": False, "reason": "no match in progress"}
        if player_id != self.current_turn:
            return {"ok": False, "reason": "not your turn"}
        if not (0 <= row < self.grid_size and 0 <= col < self.grid_size):
            return {"ok": False, "reason": "off the board"}
        if (row, col) in self.revealed:
            return {"ok": False, "reason": "slot already taken"}

        is_bomb = (row, col) in self.bombs
        if is_bomb:
            self.revealed[(row, col)] = BOMB
            self.bombs_found += 1
            self.scores[player_id] = self.scores.get(player_id, 0) + 1
        else:
            self.revealed[(row, col)] = self.adjacent[row][col]

        match_over = self.bombs_found >= self.bomb_count
        if match_over:
            self._finish_match()
        elif not is_bomb:
            # An empty slot ends your turn; a bomb lets you keep going.
            self.pass_turn()

        return {
            "ok": True,
            "is_bomb": is_bomb,
            "value": self.revealed[(row, col)],
            "turn_changed": (not is_bomb) and not match_over,
            "match_over": match_over,
        }

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
        ranked = sorted(self.players, key=lambda p: self.scores.get(p, 0), reverse=True)
        if len(ranked) > 1 and self.scores.get(ranked[0], 0) == self.scores.get(ranked[1], 0):
            return None
        return ranked[0]

    @property
    def bombs_left(self):
        return self.bomb_count - self.bombs_found

    def full_reset(self):
        """Server Reset button: wipe the board, the scores and the history."""
        self._clear_board()
        self.reset_scores()
        self.phase = PHASE_WAITING
        self.current_turn = None
        self.last_winner = None
