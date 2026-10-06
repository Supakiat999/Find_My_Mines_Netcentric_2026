"""The Q-network: fully convolutional, so one rule scores every slot.

The 3x3 (or 3x3x3) convolutions look at a slot and its neighbours with the
same weights everywhere; `depth` of them in a row see the whole board.  The
output is one Q-value per slot: the expected discounted reward of opening it.
"""

import torch.nn as nn

from .observation import CHANNELS


class DQN(nn.Module):
    def __init__(self, ndim, width=64, depth=4):
        super().__init__()
        conv = {2: nn.Conv2d, 3: nn.Conv3d}[ndim]
        layers, channels = [], CHANNELS
        for _ in range(depth):
            layers += [conv(channels, width, kernel_size=3, padding=1), nn.ReLU()]
            channels = width
        layers += [conv(width, 1, kernel_size=1), nn.Flatten()]
        self.network = nn.Sequential(*layers)

    def forward(self, x):
        return self.network(x)
