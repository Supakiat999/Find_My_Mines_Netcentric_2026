"""Turn what a player can see into the network's input.

Works from the public board only (game.Game.board_view), so training and
play share one code path and the bot can never see a hidden bomb.  The
seven channels are the classic repo's, written for any number of
dimensions and for the two-ring hint style:

    0 hidden        1 if the slot is still covered
    1 found         1 if it is an opened bomb
    2 clue          the number shown on an opened safe slot
    3 remaining     clue minus the opened bombs it already explains
    4 hidden reach  covered slots a clue there would be counting
    5 ratio         remaining / hidden reach: a clue's local bomb odds
    6 density       bombs left / covered slots, the same everywhere

Channels 2-4 are divided by the largest clue the mode can show.
"""

import itertools
from functools import lru_cache

import numpy as np
from scipy.ndimage import correlate

from game import BOMB

CHANNELS = 7


@lru_cache(maxsize=None)
def hint_kernel(ndim, weighted):
    """How much a bomb at each offset adds to a clue: 1 for the touching
    ring, or 2 for the touching ring plus 1 for the ring beyond it."""
    reach = 2 if weighted else 1
    side = 2 * reach + 1
    offset = np.indices((side,) * ndim) - reach
    ring = np.abs(offset).max(axis=0)
    kernel = np.where(ring == 1, 2 if weighted else 1, np.where(ring == 2, 1, 0))
    return kernel.astype(np.float32)


def _read(view, dims):
    """Nested lists -> (hidden, found, clue) arrays shaped like the board."""
    hidden = np.zeros(dims, np.float32)
    found = np.zeros(dims, np.float32)
    clue = np.zeros(dims, np.float32)
    for cell in itertools.product(*(range(n) for n in dims)):
        value = view
        for step in cell:
            value = value[step]
        if value is None:
            hidden[cell] = 1
        elif value == BOMB:
            found[cell] = 1
        else:
            clue[cell] = value
    return hidden, found, clue


def observe_view(view, dims, weighted, bombs_left):
    """Returns (observation, legal_mask).

    observation is float32, shape (CHANNELS, *dims); legal_mask is a flat
    bool array over the covered slots, in the same row-major order as the
    network's output.
    """
    dims = tuple(dims)
    kernel = hint_kernel(len(dims), weighted)
    kmax = float(kernel.sum())
    hidden, found, clue = _read(view, dims)

    safe_shown = (1 - hidden) * (1 - found)
    explained = correlate(found, kernel, mode="constant")
    remaining = np.where(safe_shown == 1, np.maximum(0, clue - explained), 0)
    reach = correlate(hidden, kernel, mode="constant")
    ratio = np.where((safe_shown == 1) & (remaining > 0),
                     np.minimum(1, remaining / np.maximum(reach, 1)), 0)
    density = np.full(dims, bombs_left / max(hidden.sum(), 1))

    obs = np.stack([hidden, found, clue / kmax, remaining / kmax,
                    reach / kmax, ratio, density]).astype(np.float32)
    return obs, hidden.astype(bool).ravel()
