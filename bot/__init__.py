"""Reinforcement-learning opponents for Find My Mines, one per game mode.

    python -m bot.train --mode classic      train (nothing is trained yet)
    python -m bot.evaluate --mode classic   score saved weights
    from bot.agent import load_agent        play with them

The rules are never re-implemented here: training runs on game.Game itself,
so a bot cannot learn a rule the real game does not have.
"""
