"""Symmetry augmentation: a turned or mirrored board is the same position.

For a square board that is 8 versions (4 turns x mirror); for the cube it
is 48 (6 axis orders x 8 mirrors).  Both are "every axis order, every
combination of flips", so one routine covers every mode.
"""

import itertools

import numpy as np
import torch


class Symmetric:
    def __init__(self, dims):
        assert len(set(dims)) == 1, "symmetry needs a square or cubic board"
        base = np.arange(int(np.prod(dims))).reshape(dims)
        rows = []
        for order in itertools.permutations(range(len(dims))):
            for flips in itertools.product((False, True), repeat=len(dims)):
                grid = base.transpose(order)
                for axis, flip in enumerate(flips):
                    if flip:
                        grid = np.flip(grid, axis)
                rows.append(grid.flatten())
        self.table = torch.tensor(np.stack(rows))     # (versions, cells)
        self.reverse_table = self.table.argsort(dim=-1)
        self.versions = len(rows)

    def reorder(self, batch, ids):                    # (batch, channel, *dims)
        flat = torch.flatten(batch, start_dim=2)
        index = self.table[ids].unsqueeze(1).expand_as(flat)
        return torch.gather(flat, dim=2, index=index).reshape_as(batch)

    def reorder_mask(self, mask, ids):
        return torch.gather(mask, dim=1, index=self.table[ids])

    def map_action(self, actions, ids):
        return self.reverse_table[ids, actions]

    def augment(self, batch, ids=None):
        obs, action, reward, next_obs, next_mask, done = batch
        if ids is None:
            ids = torch.randint(0, self.versions, (obs.shape[0],))
        return (self.reorder(obs, ids), self.map_action(action, ids), reward,
                self.reorder(next_obs, ids),
                self.reorder_mask(next_mask, ids), done)
