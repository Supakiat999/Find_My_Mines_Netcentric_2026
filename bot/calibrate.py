"""Pick each difficulty level's temperature, per mode.

    python -m bot.calibrate --mode all        writes bot/weights/levels.json

Q-values have a different scale in every mode (sweeper rewards are +-, the
cube plays longer), so one temperature cannot give the same strength
everywhere.  Instead each level is defined by strength: hard plays the best
slot, medium takes TARGETS["medium"] times as many turns as that, easy
TARGETS["easy"] times.  The ratios are the classic repo's (10.42 / 12.56 /
17.37 turns).  Each temperature is found by bisection, on boards that stay
fixed, so the search is not chasing noise in the boards.
"""

import argparse
import json
import math
import os
import random

import torch

from .agent import WEIGHTS_DIR, load_network
from .evaluate import evaluate
from .modes import get_spec, preset_specs

TARGETS = {"medium": 1.2, "easy": 1.67}
LOW, HIGH = 1e-3, 10.0     # temperature search range, bisected in log space
STEPS = 9


def find_temperature(network, spec, device, goal, games, seed):
    """Smallest-effort bisection: turns grow with temperature."""
    low, high = math.log(LOW), math.log(HIGH)
    for _ in range(STEPS):
        mid = (low + high) / 2
        random.seed(seed)
        torch.manual_seed(seed)
        if evaluate(network, spec, device, games, seed,
                    temperature=math.exp(mid)) < goal:
            low = mid
        else:
            high = mid
    return math.exp((low + high) / 2)


def calibrate(spec, device, games=200, log=print):
    network = load_network(spec).to(device)
    seed = spec.hyper.eval_seed
    best = evaluate(network, spec, device, games, seed)
    result = {"hard": 0.0, "best_turns": round(best, 2)}
    for level, ratio in TARGETS.items():
        result[level] = round(find_temperature(network, spec, device,
                                               best * ratio, games, seed), 4)
        random.seed(seed)
        torch.manual_seed(seed)
        got = evaluate(network, spec, device, games, seed,
                       temperature=result[level])
        result[level + "_turns"] = round(got, 2)
    log("%-8s hard %.2f  medium T=%.3f -> %.2f  easy T=%.3f -> %.2f" % (
        spec.name, best, result["medium"], result["medium_turns"],
        result["easy"], result["easy_turns"]))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", default="all", help="a mode id, or all (the four presets)")
    p.add_argument("--custom", default=None, help="custom settings as JSON")
    p.add_argument("--games", type=int, default=200)
    a = p.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    specs = (list(preset_specs().values()) if a.mode == "all" else
             [get_spec(a.mode, json.loads(a.custom) if a.custom else None)])
    path = os.path.join(WEIGHTS_DIR, "levels.json")
    levels = json.load(open(path)) if os.path.exists(path) else {}
    for spec in specs:
        levels[spec.name] = calibrate(spec, device, a.games)
        with open(path, "w") as f:      # saved per mode: a long run can be stopped
            json.dump(levels, f, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
