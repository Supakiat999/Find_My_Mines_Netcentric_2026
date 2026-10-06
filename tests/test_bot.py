"""The RL bots: observation, symmetry, the solo env, a tiny training run.

Nothing here trains for real - the run at the end is 60 steps.  Skipped
(and still passing) when torch is not installed.
"""

import helpers  # noqa: F401 - puts the project on the path
import random
import tempfile

try:
    import numpy as np
    import torch
except ImportError:
    print("SKIPPED - torch/numpy/scipy not installed (pip install -r bot/requirements.txt)")
    raise SystemExit(0)

import game as g
from bot.agent import RLAgent, new_network
from bot.env import SoloEnv
from bot.modes import get_spec, preset_specs
from bot.observation import observe_view
from bot.symmetry import Symmetric
from bot.train import train

CUSTOM = {"size": 4, "bombs": 5, "shape": "cube", "hints": "radius2", "goal": "avoid"}
specs = list(preset_specs().values()) + [get_spec(g.MODE_CUSTOM, CUSTOM)]


def play_a_bit(spec, picks=6):
    env = SoloEnv(spec, 1)
    env.reset(2)
    rng = random.Random(0)
    for _ in range(picks):
        legal = np.flatnonzero(env.observe()[1])
        if env.step(int(rng.choice(list(legal))))[3]:
            break
    return env


# --- 1. the classic network is the classic repo's: 114,945 weights ------
assert sum(p.numel() for p in new_network(preset_specs()["classic"]).parameters()) == 114945
print("1 OK  classic network has 114,945 weights")

# --- 2. a turned or mirrored board gives the turned observation ----------
for spec in specs:
    game = play_a_bit(spec).game
    cells = list(game.cells())
    view = np.empty(len(cells), dtype=object)
    for i, cell in enumerate(cells):
        v = game.board_view()
        for step in cell:
            v = v[step]
        view[i] = v
    args = (game.dims, game.hints_weighted, game.bombs_left)
    obs, mask = observe_view(view.reshape(game.dims).tolist(), *args)
    sym = Symmetric(spec.dims)
    for i in range(sym.versions):
        moved = view[sym.table[i].numpy()].reshape(game.dims).tolist()
        o2, m2 = observe_view(moved, *args)
        ids = torch.tensor([i])
        assert np.allclose(sym.reorder(torch.tensor(obs)[None], ids)[0].numpy(), o2, atol=1e-5), (spec.name, i)
        assert (sym.reorder_mask(torch.tensor(mask)[None], ids)[0].numpy() == m2).all()
    print("2 OK  %-30s %2d symmetries" % (spec.name, sym.versions))

# --- 3. an agent only ever picks a covered slot --------------------------
for spec in specs:
    game = play_a_bit(spec).game
    agent = RLAgent(spec, new_network(spec))
    for temperature in (0, 1.0):
        cell = agent.choose_cell(game.public_info(), temperature, random.Random(1))
        assert cell not in game.revealed and len(cell) == len(game.dims)
print("3 OK  agents pick covered slots")

# --- 4. solo play ends, and counts the turns it would have passed --------
for spec in specs:
    env = SoloEnv(spec, 3)
    obs, mask = env.reset(4)
    done, rng = False, random.Random(5)
    while not done:
        _, mask, _, done = env.step(int(rng.choice(list(np.flatnonzero(mask)))))
    assert env.game.phase == g.PHASE_ENDED and env.turns > 0
print("4 OK  every mode plays to the end")

# --- 5. a 60-step training run saves weights ------------------------------
tiny = get_spec(g.MODE_CLASSIC, learn_starts=20, batch=8, steps=60,
                eval_every=50, eval_games=2)
with tempfile.TemporaryDirectory() as out:
    train(tiny, torch.device("cpu"), out_dir=out, log=lambda *a: None)
    import os
    assert sorted(os.listdir(out)) == ["classic.pt", "classic_curve.csv", "classic_final.pt"], os.listdir(out)
print("5 OK  tiny training run")

print("\nALL BOT CHECKS PASSED")
