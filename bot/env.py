"""One agent alone on a real game.Game board - the training ground.

The opponent's turns are skipped: after a pick that would pass the turn, the
turn is handed straight back.  So an episode is one board played start to
finish, and `turns` counts the picks that ended a turn - the number of times
an opponent would have got to move.  Fewer is better, in every mode (empty
slots in the hunting modes, bombs hit in Minesweeper).
"""

import random

import game as rules
from .observation import observe_view

ME, OPPONENT = "agent", "opponent"


class SoloEnv:
    def __init__(self, spec, seed=None):
        self.spec = spec
        self.game = spec.new_game(random.Random(seed))
        self.game.seat_players([ME, OPPONENT])
        self.turns = 0

    def observe(self):
        g = self.game
        return observe_view(g.board_view(), g.dims, g.hints_weighted,
                            g.bombs_left)

    def reset(self, seed=None):
        if seed is not None:
            self.game.rng = random.Random(seed)
        self.game.start_match(first_player=ME)
        self.turns = 0
        return self.observe()

    def step(self, action):
        """Open slot number `action`.  Returns (obs, mask, reward, done)."""
        g, hyper = self.game, self.spec.hyper
        cell = tuple(int(i) for i in _unravel(action, g.dims))
        result = g.pick(ME, cell)
        if not result["ok"]:
            raise ValueError("invalid move %s: %s" % (cell, result["reason"]))
        if result["turn_changed"]:
            self.turns += 1
            g.current_turn = ME            # solo: the opponent's turn is skipped
        if result["is_bomb"]:
            reward = hyper.bomb_reward
        else:
            reward = hyper.safe_reward * result["opened"]
        obs, mask = self.observe()
        return obs, mask, reward, g.phase == rules.PHASE_ENDED


def _unravel(index, dims):
    out = []
    for size in reversed(dims):
        index, rest = divmod(index, size)
        out.append(rest)
    return reversed(out)
