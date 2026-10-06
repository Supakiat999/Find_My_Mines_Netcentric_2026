# bot/ - reinforcement-learning opponents

One Double-DQN agent per game mode, trained on the real `game.Game` rules.
**Nothing is trained yet**; `weights/` is empty.

    pip install -r bot/requirements.txt
    python -m bot.train --mode classic          # or radius2 / sweeper / cube / all
    python -m bot.evaluate --mode classic       # trained vs random, mean turns

Run from the project root. A mode's weights land in `bot/weights/<name>.pt`.

| Mode | Board | Learns | Changed from the classic recipe |
|---|---|---|---|
| classic | 6x6, 11 bombs | +1 per bomb | nothing - the T6523/dqn-minesweeper-solver recipe |
| radius2 | 6x6, weighted hints | +1 per bomb | wider net (96), 60k steps |
| sweeper | 6x6, bombs are bad | -1 per bomb | gamma 0.5, 60k steps, 4 updates per step (the saved weights predate this and were trained with +0.05 per slot cleared) |
| cube | 4x4x4, 19 bombs | +1 per bomb | 3D convolutions, 48 symmetries, 60k steps, bigger buffer |
| custom | any (`--custom '{...}'`) | by its settings | composed from the rows above |

Those changes are untuned guesses - they live in `modes.py` (`FEATURES`), so
retuning is one edit. Score is **turns**: picks that end a turn (empty slots
in the hunting modes, bombs hit in sweeper), alone on the board. Lower is better.

Play with it (`public_info()` is only what a player sees):

    from bot.agent import load_agent
    cell = load_agent("cube").choose_cell(game.public_info())

| File | Job |
|---|---|
| `modes.py` | per-mode spec: board, reward, network size, training settings |
| `observation.py` | public board to 7 input channels, any dimension |
| `network.py` | fully convolutional Q-network (2D or 3D) |
| `symmetry.py` | 8 (flat) or 48 (cube) equivalent boards for augmentation |
| `replay.py`, `learner.py` | replay buffer; Double-DQN update, epsilon, action rule |
| `env.py` | one agent alone on a `game.Game` |
| `train.py`, `evaluate.py` | training loop; scoring |
| `agent.py` | load weights and choose a slot |
| `calibrate.py` | pick the easy / medium temperatures, per mode |

The game uses these through `botbrain.py` (server side). Easy / Medium / Hard are
this one model at three temperatures; `python -m bot.calibrate` picks them and
writes `weights/levels.json`. With no weights for a mode, the server falls back
to the `ai.py` solver.
