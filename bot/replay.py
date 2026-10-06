"""Replay buffer of past moves, optionally rotated/mirrored on the way out."""

import random
from collections import deque, namedtuple

import numpy as np
import torch

Transition = namedtuple(
    "Transition",
    ("observe", "action", "reward", "next_observe", "next_legal_mask", "is_done"))


class ReplayBuffer:
    def __init__(self, capacity, symmetric=None):
        self.memory = deque([], maxlen=capacity)
        self.symmetric = symmetric

    def push(self, transition):
        self.memory.append(transition)

    def sample(self, batch_size):
        obs, action, reward, next_obs, next_mask, done = zip(
            *random.sample(self.memory, batch_size))
        batch = (torch.tensor(np.array(obs), dtype=torch.float32),
                 torch.tensor(action, dtype=torch.long),
                 torch.tensor(reward, dtype=torch.float32),
                 torch.tensor(np.array(next_obs), dtype=torch.float32),
                 torch.tensor(np.array(next_mask), dtype=torch.bool),
                 torch.tensor(done, dtype=torch.float32))
        return self.symmetric.augment(batch) if self.symmetric else batch

    def __len__(self):
        return len(self.memory)
