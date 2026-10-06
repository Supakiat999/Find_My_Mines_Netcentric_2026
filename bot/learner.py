"""Double DQN: the update rule, the exploration schedule, the action rule."""

import random

import torch
import torch.nn.functional as F

NEGATIVE_INF = -1e9


class LearningStep:
    def __init__(self, main_network, target_network, optimizer, hyper, device):
        self.main_network = main_network.to(device)
        self.target_network = target_network.to(device)
        self.optimizer = optimizer
        self.hyper = hyper
        self.device = device

    def predict(self, batch):
        obs, action = batch[0].to(self.device), batch[1].to(self.device)
        return self.main_network(obs).gather(1, action.unsqueeze(-1)).squeeze(1)

    def compute_target(self, batch):
        reward, next_obs, next_mask, done = (t.to(self.device) for t in batch[2:])
        with torch.no_grad():
            # the main network picks the best covered slot, the target scores it
            masked = torch.where(next_mask, self.main_network(next_obs),
                                 torch.tensor(NEGATIVE_INF, device=self.device))
            best = masked.argmax(dim=1, keepdim=True)
            chosen = self.target_network(next_obs).gather(1, best).squeeze(1)
            return reward + self.hyper.gamma * chosen * (1 - done)

    def update(self, batch):
        loss = F.smooth_l1_loss(self.predict(batch), self.compute_target(batch))
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.main_network.parameters(),
                                       self.hyper.clip)
        self.optimizer.step()
        return loss.item()


def epsilon_schedule(step, hyper):
    span = hyper.eps_start - hyper.eps_end
    return max(hyper.eps_end, hyper.eps_start - span * step / hyper.eps_decay)


def select_action(network, observation, legal_mask, epsilon, device):
    """Random covered slot with chance epsilon, else the best-valued one."""
    if random.random() < epsilon:
        return random.choice([i for i, ok in enumerate(legal_mask) if ok])
    batch = torch.tensor(observation).unsqueeze(0).to(device)
    mask = torch.tensor(legal_mask).to(device)
    with torch.no_grad():
        q = network(batch)
        q = torch.where(mask, q, torch.tensor(NEGATIVE_INF, device=device))
    return q.argmax().item()


def sample_action(network, observation, legal_mask, temperature, device):
    """Softmax over the covered slots' Q-values: temperature 0 is the best
    slot, higher is looser.  This is how weaker difficulty levels play."""
    if temperature <= 0:
        return select_action(network, observation, legal_mask, 0.0, device)
    batch = torch.tensor(observation).unsqueeze(0).to(device)
    mask = torch.tensor(legal_mask).to(device)
    with torch.no_grad():
        q = network(batch)[0] / temperature
        q = torch.where(mask, q, torch.tensor(NEGATIVE_INF, device=device))
        return torch.multinomial(torch.softmax(q, dim=0), 1).item()
