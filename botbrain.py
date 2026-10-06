"""Who plays the computer's moves: the trained model, or the solver.

Each mode has one trained model (see bot/).  Easy, Medium and Hard are that
same model played looser or tighter - the temperatures bot/calibrate.py
chose.  If a mode has no model (no torch, no weights, or a custom board),
the probability solver in ai.py plays instead, so there is always an
opponent.  The solver is also what the coach uses, either way.
"""

import json
import os
import threading

import ai
import game as game_rules

_lock = threading.Lock()
_agents = {}        # mode id -> (agent, {level: temperature}), or None


def _load(mode):
    try:
        from bot.agent import RLAgent, WEIGHTS_DIR, load_network
        from bot.modes import get_spec
        spec = get_spec(mode)
        with open(os.path.join(WEIGHTS_DIR, "levels.json")) as f:
            temperatures = json.load(f)[spec.name]
        return RLAgent(spec, load_network(spec)), temperatures
    except Exception:       # no torch, no weights, not calibrated: use the solver
        return None


def _get(mode):
    with _lock:
        if mode not in _agents:
            _agents[mode] = _load(mode)
        return _agents[mode]


def preload():
    """Load every mode's model.  Run in a background thread at start-up, so
    importing torch never stalls a match."""
    for mode in game_rules.MODES:
        if mode != game_rules.MODE_CUSTOM:
            _get(mode)


def ready(g):
    """True once a trained model is loaded for the mode being played."""
    return g.mode != game_rules.MODE_CUSTOM and _agents.get(g.mode) is not None


def choose_cell(g, level, rng):
    """The computer's move for the game `g`, or None if nothing is left."""
    info = g.public_info()
    entry = None if g.mode == game_rules.MODE_CUSTOM else _get(g.mode)
    if entry:
        agent, temperatures = entry
        return agent.choose_cell(info, temperatures[level], rng)
    return ai.choose_cell(info["view"], info["dims"], info["weighted"],
                          info["bombs_left"], info["bombs_are_bad"], level, rng)
