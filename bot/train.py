"""Train a Double DQN for one mode (or all four presets).

    python -m bot.train --mode classic
    python -m bot.train --mode all
    python -m bot.train --mode custom --custom '{"size": 8, "bombs": 14}'
    python -m bot.train --mode cube --steps 500       a quick look
    python -m bot.train --mode cube --set width=128 --tag w128   a variant

The loop is the classic repo's, unchanged: act, store, sample a batch,
update, copy to the target network now and then, keep the best checkpoint.
Weights go to bot/weights/<name>.pt (best) and <name>_final.pt (last), with
the eval curve in <name>_curve.csv.
"""

import argparse
import json
import os
import random
from dataclasses import replace

import torch
import torch.optim as optim

import game as rules
from .agent import new_network, weights_path
from .env import SoloEnv
from .evaluate import evaluate
from .learner import LearningStep, epsilon_schedule, select_action
from .modes import get_spec, preset_specs
from .replay import ReplayBuffer, Transition
from .symmetry import Symmetric


def train(spec, device, seed=42, out_dir=None, log=print):
    hyper = spec.hyper
    random.seed(seed)
    torch.manual_seed(seed)

    env = SoloEnv(spec, seed)
    main_network, target_network = new_network(spec), new_network(spec)
    target_network.load_state_dict(main_network.state_dict())
    optimizer = optim.Adam(main_network.parameters(), lr=hyper.lr)
    buffer = ReplayBuffer(hyper.buffer, Symmetric(spec.dims))
    learner = LearningStep(main_network, target_network, optimizer, hyper, device)

    def path(final):
        p = weights_path(spec, final)
        return os.path.join(out_dir, os.path.basename(p)) if out_dir else p

    os.makedirs(os.path.dirname(path(False)), exist_ok=True)
    curve = open(path(False).replace(".pt", "_curve.csv"), "w")
    curve.write("step,turns,epsilon,loss\n")
    obs, mask = env.reset()
    best, loss = float("inf"), 0.0
    for step in range(hyper.steps):
        epsilon = epsilon_schedule(step, hyper)
        action = select_action(main_network, obs, mask, epsilon, device)
        next_obs, next_mask, reward, done = env.step(action)
        buffer.push(Transition(obs, action, reward, next_obs, next_mask, done))
        obs, mask = next_obs, next_mask

        if len(buffer) >= hyper.learn_starts:
            for _ in range(hyper.updates_per_step):
                loss = learner.update(buffer.sample(hyper.batch))
            if step % hyper.target_every == 0:
                target_network.load_state_dict(main_network.state_dict())
        if done:
            obs, mask = env.reset()

        if step > 0 and step % hyper.eval_every == 0:
            score = evaluate(main_network, spec, device)
            if score < best:
                best = score
                torch.save(main_network.state_dict(), path(False))
            log("%s step %d  turns %.2f  eps %.2f  loss %.3f"
                % (spec.name, step, score, epsilon, loss))
            curve.write("%d,%.3f,%.3f,%.4f\n" % (step, score, epsilon, loss))
            curve.flush()

    score = evaluate(main_network, spec, device, hyper.eval_games * 5)
    torch.save(main_network.state_dict(), path(True))
    if not os.path.exists(path(False)):     # run shorter than one eval interval
        torch.save(main_network.state_dict(), path(False))
    curve.close()
    log("%s FINAL  turns %.2f" % (spec.name, score))
    return main_network


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", default="classic",
                   help="%s, or all (the four presets)" % ", ".join(rules.MODES))
    p.add_argument("--custom", default=None, help="custom settings as JSON")
    p.add_argument("--steps", type=int, default=None, help="override run length")
    p.add_argument("--set", default=None, metavar="K=V,K=V",
                   help="override settings, e.g. steps=150000,width=128")
    p.add_argument("--tag", default=None,
                   help="save as <name>_<tag>.pt instead of overwriting")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out-dir", default=None, help="instead of bot/weights/")
    a = p.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("device:", device)
    if a.mode == "all":
        specs = list(preset_specs().values())
    else:
        specs = [get_spec(a.mode, json.loads(a.custom) if a.custom else None)]
    for spec in specs:
        if a.steps:
            spec = replace(spec, hyper=replace(spec.hyper, steps=a.steps))
        if a.set:
            fields = {k: type(getattr(spec.hyper, k))(v) for k, v in
                      (item.split("=") for item in a.set.split(","))}
            spec = replace(spec, hyper=replace(spec.hyper, **fields))
        if a.tag:
            spec = replace(spec, name="%s_%s" % (spec.name, a.tag))
        train(spec, device, a.seed, a.out_dir)


if __name__ == "__main__":
    main()
