"""Score a network on fixed boards: mean turns per board (lower is better).

    python -m bot.evaluate --mode classic            saved weights vs random
"""

import argparse
import random

import numpy as np
import torch

from .env import SoloEnv
from .learner import sample_action, select_action


def evaluate(network, spec, device, games=None, seed=None, epsilon=0.0,
             temperature=0.0):
    """Mean turns over `games` boards.  The boards depend only on `seed`, so
    two networks (or two checkpoints) are compared on identical boards.
    Only `temperature` > 0 makes the play itself random."""
    hyper = spec.hyper
    games = hyper.eval_games if games is None else games
    seed = hyper.eval_seed if seed is None else seed
    env = SoloEnv(spec)
    turns = []
    for n in range(games):
        obs, mask = env.reset(seed + n)
        done = False
        while not done:
            if temperature > 0:
                action = sample_action(network, obs, mask, temperature, device)
            else:
                action = select_action(network, obs, mask, epsilon, device)
            obs, mask, _, done = env.step(action)
        turns.append(env.turns)
    return float(np.mean(turns))


def random_baseline(spec, games=None, seed=None):
    """Mean turns for a player who opens covered slots at random."""
    return evaluate(None, spec, "cpu", games, seed, epsilon=1.0)


def main():
    from .agent import weights_path, load_network
    from .modes import get_spec
    import json

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", default="classic")
    p.add_argument("--custom", default=None, help="settings as JSON")
    p.add_argument("--weights", default=None)
    p.add_argument("--games", type=int, default=500)
    a = p.parse_args()

    spec = get_spec(a.mode, json.loads(a.custom) if a.custom else None)
    network = load_network(spec, a.weights or weights_path(spec))
    seed = spec.hyper.eval_seed
    print("%s: %d games" % (spec.name, a.games))
    print("  random  %.2f turns" % random_baseline(spec, a.games, seed))
    print("  trained %.2f turns" % evaluate(network, spec, "cpu", a.games, seed))


if __name__ == "__main__":
    main()
