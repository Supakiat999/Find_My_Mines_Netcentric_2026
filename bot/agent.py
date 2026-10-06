"""Play with trained weights.

    agent = load_agent("classic")
    cell = agent.choose_cell(game.public_info())     # a slot tuple

public_info() is what a player at the table sees, so the agent cannot peek.
`temperature` softens the choice for weaker play: 0 always takes the best
slot, higher samples more freely (the classic repo's easy/medium/hard).
"""

import json
import os
import random

import numpy as np
import torch

from .modes import get_spec
from .network import DQN
from .observation import observe_view

WEIGHTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights")


def weights_path(spec, final=False):
    return os.path.join(WEIGHTS_DIR, spec.name + ("_final" if final else "") + ".pt")


def level_temperature(spec, level):
    """Temperature for "easy" / "medium" / "hard", from bot.calibrate."""
    with open(os.path.join(WEIGHTS_DIR, "levels.json")) as f:
        return json.load(f)[spec.name][level]


def new_network(spec):
    return DQN(spec.ndim, spec.hyper.width, spec.hyper.depth)


def load_network(spec, path=None):
    path = path or weights_path(spec)
    if not os.path.exists(path):
        raise FileNotFoundError(
            "no weights at %s - train first: python -m bot.train --mode %s"
            % (path, spec.mode))
    network = new_network(spec)
    network.load_state_dict(torch.load(path, map_location="cpu"))
    return network.eval()


class RLAgent:
    def __init__(self, spec, network):
        self.spec = spec
        self.network = network.eval()

    def q_values(self, info):
        """Q-value of every covered slot, as {slot: q}."""
        obs, mask = observe_view(info["view"], info["dims"], info["weighted"],
                                 info["bombs_left"])
        with torch.no_grad():
            q = self.network(torch.tensor(obs).unsqueeze(0))[0].numpy()
        dims = tuple(info["dims"])
        return {tuple(int(i) for i in np.unravel_index(n, dims)): float(q[n])
                for n in np.flatnonzero(mask)}

    def choose_cell(self, info, temperature=0.0, rng=None):
        """The agent's move, or None when nothing is left to open."""
        q = self.q_values(info)
        if not q:
            return None
        cells = sorted(q)
        if temperature <= 0:
            return max(cells, key=q.get)
        rng = rng or random
        values = np.array([q[c] for c in cells]) / temperature
        weights = np.exp(values - values.max())
        return cells[rng.choices(range(len(cells)), weights.tolist())[0]]


def load_agent(mode="classic", custom=None, path=None):
    spec = get_spec(mode, custom)
    return RLAgent(spec, load_network(spec, path))
