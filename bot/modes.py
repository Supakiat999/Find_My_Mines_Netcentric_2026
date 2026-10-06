"""What a bot has to learn, per mode: the board, the reward, the network size.

The board facts (size, bombs, hint style, goal) are read off a real
game.Game, never copied, so they cannot drift from the rules.  Everything
tunable lives in Hyper.  Hyper is built from a base plus one override per
board feature, so the four presets and every Custom board are tuned by the
same rule:

    radius2 = weighted hints      sweeper = bombs are bad      cube = 3D

Change a number in FEATURES, not in the training loop.
"""

from dataclasses import dataclass, replace

import game

# Reward and training settings.  The base is the classic recipe from
# T6523/dqn-minesweeper-solver, untouched.
BASE = dict(
    bomb_reward=1.0,      # classic: finding a bomb is the prize
    safe_reward=0.0,      # reward per safe slot opened (sweeper)
    width=64, depth=4,    # network: depth 3x3 convolutions of `width` maps
    gamma=0.3,
    lr=1e-3,
    batch=64,
    buffer=20_000,
    learn_starts=1_000,
    target_every=500,
    eps_start=1.0, eps_end=0.05, eps_decay=10_000,
    steps=30_000,
    clip=10.0,
    updates_per_step=1,   # gradient updates per game step
    eval_every=5_000, eval_games=300, eval_seed=12345,
)

# Starting points, NOT tuned - nothing has been trained yet.  Each is a
# reasoned guess and says what it is a guess about.
FEATURES = {
    # A clue sums 24 neighbours with weights 2/1, so it is harder to read
    # and the net needs longer to learn the weighting.
    "weighted": dict(steps=60_000, eps_decay=20_000, width=96),
    # Bombs are a hazard: -1 per bomb hit and nothing else, so the reward
    # is exactly the score (turns lost).  The first version also paid 0.05
    # per slot cleared, which made a risky pick that opens a big region look
    # worth it; the engine just takes the safest slot.
    "bad": dict(bomb_reward=-1.0, safe_reward=0.0, gamma=0.5, steps=60_000,
                eps_decay=20_000, updates_per_step=4),
    # 3D: 26 neighbours and 48 symmetries instead of 8, more states.
    "cube": dict(steps=60_000, eps_decay=20_000, buffer=40_000),
}


@dataclass(frozen=True)
class Hyper:
    bomb_reward: float
    safe_reward: float
    width: int
    depth: int
    gamma: float
    lr: float
    batch: int
    buffer: int
    learn_starts: int
    target_every: int
    eps_start: float
    eps_end: float
    eps_decay: int
    steps: int
    clip: float
    updates_per_step: int
    eval_every: int
    eval_games: int
    eval_seed: int


@dataclass(frozen=True)
class ModeSpec:
    name: str              # file name of the weights
    mode: str              # a game.MODES id
    custom: tuple          # sorted (key, value) pairs; empty unless custom
    dims: tuple
    bomb_count: int
    weighted: bool         # two-ring 2/1 hints
    bombs_are_bad: bool
    hyper: Hyper

    @property
    def ndim(self):
        return len(self.dims)

    def new_game(self, rng=None):
        """A game.Game set up exactly like this mode."""
        g = game.Game(mode=self.mode, rng=rng)
        if self.mode == game.MODE_CUSTOM:
            g.set_custom(dict(self.custom))
        return g


def _hyper(ndim, weighted, bad):
    values = dict(BASE)
    for on, key in ((weighted, "weighted"), (bad, "bad"), (ndim == 3, "cube")):
        if on:
            values.update(FEATURES[key])
    return Hyper(**values)


def get_spec(mode, custom=None, **hyper_overrides):
    """The spec for a mode id.  `custom` is a settings dict (see
    config.DEFAULT_CUSTOM) and is only read for the custom mode."""
    if mode not in game.MODES:
        raise ValueError("unknown mode %r, pick one of %s" % (mode, game.MODES))
    pairs = ()
    if mode == game.MODE_CUSTOM:
        # the game clamps the settings, so the name matches what is played
        pairs = tuple(sorted(game.clamp_custom(custom).items()))
    probe = game.Game(mode=mode)
    if pairs:
        probe.set_custom(dict(pairs))
    name = mode
    if pairs:
        c = dict(pairs)
        name = "custom_%s%d_b%d_%s_%s" % (c["shape"], c["size"], c["bombs"],
                                         c["hints"], c["goal"])
    hyper = _hyper(len(probe.dims), probe.hints_weighted, probe.bombs_are_bad)
    return ModeSpec(name, mode, pairs, tuple(probe.dims), probe.bomb_count,
                    probe.hints_weighted, probe.bombs_are_bad,
                    replace(hyper, **hyper_overrides))


def preset_specs():
    """The four fixed modes, by name."""
    return {m: get_spec(m) for m in game.MODES if m != game.MODE_CUSTOM}
