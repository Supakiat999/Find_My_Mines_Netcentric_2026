"""Pit each mode's trained bot against the greedy probability engine (ai.py).

    python -m bot.benchmark --mode all --games 100

Two tests per mode, on boards that depend only on the seed:
  solo     mean turns alone on a board - the training metric (lower is better)
  versus   full two-player matches, seats and first move alternating:
           win rate and mean score margin for the bot
The engine is `ai.choose_cell(..., "hard")`: it computes every covered slot's
bomb probability and takes the best one, with no look-ahead.
"""

import argparse
import random

import numpy as np

import ai
import game as rules
from .agent import load_agent
from .env import ME, SoloEnv
from .modes import get_spec, preset_specs


def rl_policy(agent):
    return lambda g, rng: agent.choose_cell(g.public_info())


def solver_policy(g, rng):
    info = g.public_info()
    return ai.choose_cell(info["view"], info["dims"], info["weighted"],
                          info["bombs_left"], info["bombs_are_bad"], "hard", rng)


def random_policy(g, rng):
    return rng.choice([c for c in g.cells() if c not in g.revealed])


def solo_turns(spec, policy, games, seed):
    env = SoloEnv(spec)
    rng = random.Random(seed)
    turns = []
    for n in range(games):
        env.reset(seed + n)
        while env.game.phase == rules.PHASE_PLAYING:
            cell = policy(env.game, rng)
            env.step(int(np.ravel_multi_index(cell, env.game.dims)))
        turns.append(env.turns)
    return float(np.mean(turns))


def versus(spec, first, second, games, seed):
    """`first` against `second`.  Returns (wins, draws, losses, mean margin)
    from `first`'s side."""
    rng = random.Random(seed)
    wins = draws = losses = 0
    margins = []
    for n in range(games):
        g = spec.new_game(random.Random(seed + n))
        g.seat_players(["a", "b"])
        g.start_match(first_player="a" if n % 2 == 0 else "b")   # who starts
        seats = {"a": first, "b": second} if n % 4 < 2 else {"a": second, "b": first}
        mine = "a" if n % 4 < 2 else "b"                          # who `first` is
        while g.phase == rules.PHASE_PLAYING:
            player = g.current_turn
            result = g.pick(player, seats[player](g, rng))
            assert result["ok"], result
        margin = g.scores[mine] - g.scores["b" if mine == "a" else "a"]
        margins.append(margin)
        wins, draws, losses = (wins + (margin > 0), draws + (margin == 0),
                               losses + (margin < 0))
    return wins, draws, losses, float(np.mean(margins))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", default="all")
    p.add_argument("--games", type=int, default=100)
    p.add_argument("--seed", type=int, default=777)
    a = p.parse_args()
    specs = (list(preset_specs().values()) if a.mode == "all"
             else [get_spec(a.mode)])
    for spec in specs:
        bot = rl_policy(load_agent(spec.mode))
        print("%s (%d boards)" % (spec.name, a.games), flush=True)
        for label, policy in (("bot", bot), ("engine", solver_policy),
                              ("random", random_policy)):
            print("  solo %-7s %.2f turns" % (label, solo_turns(
                spec, policy, a.games, a.seed)), flush=True)
        w, d, l, margin = versus(spec, bot, solver_policy, a.games, a.seed)
        print("  bot vs engine  %d-%d-%d (W-D-L)  win rate %.0f%%  mean margin %+.2f"
              % (w, d, l, 100.0 * w / a.games, margin), flush=True)


if __name__ == "__main__":
    main()
