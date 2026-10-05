"""Unit and integration tests for the Elo rating system."""

import json
import os
import tempfile

from helpers import ok
import config
import stats as stats_mod


# =====================================================================
# 1. Elo math calculations
# =====================================================================
# Equal ratings, A wins
(new_a, delta_a), (new_b, delta_b) = stats_mod.calculate_elo(1200, 1200, "win", k=32)
assert (new_a, delta_a) == (1216, 16)
assert (new_b, delta_b) == (1184, -16)
ok(1, "equal ratings: winner gets +16, loser loses 16")

# Equal ratings, draw
(new_a, delta_a), (new_b, delta_b) = stats_mod.calculate_elo(1200, 1200, "draw", k=32)
assert (new_a, delta_a) == (1200, 0)
assert (new_b, delta_b) == (1200, 0)
ok(2, "equal ratings: draw yields 0 delta for both players")

# Upset win: 1100 beats 1300
(new_a, delta_a), (new_b, delta_b) = stats_mod.calculate_elo(1100, 1300, "win", k=32)
assert delta_a == 24 and new_a == 1124
assert delta_b == -24 and new_b == 1276
ok(3, "upset win: lower-rated player gains 24, higher-rated player loses 24")

# Draw between unequal ratings: 1100 draws with 1300
(new_a, delta_a), (new_b, delta_b) = stats_mod.calculate_elo(1100, 1300, "draw", k=32)
assert delta_a == 8 and new_a == 1108
assert delta_b == -8 and new_b == 1292
ok(4, "draw between unequal ratings: underdog gains 8, favorite loses 8")

# Floor clamping: rating cannot drop below ELO_MINIMUM (100)
(new_a, delta_a), (new_b, delta_b) = stats_mod.calculate_elo(120, 110, "win", k=32, floor=100)
assert new_b == 100
assert delta_b == -10
ok(5, "rating floor: cannot drop below 100; delta clamped to actual change")


# =====================================================================
# 2. Leaderboard per-mode storage and isolation
# =====================================================================
board = stats_mod.Leaderboard(path=None)
assert board.get_elo("NewPlayer", "classic") == 1200
assert board.get_elo("NewPlayer", "cube") == 1200
ok(6, "new player defaults to starting rating (1200) across all modes")

# Play a match in classic mode
changes = board.record_match([
    {"name": "Alice", "result": "win", "points": 8},
    {"name": "Bob", "result": "loss", "points": 3}
], mode="classic")

assert changes["Alice"]["delta"] == 16 and changes["Alice"]["after"] == 1216
assert changes["Bob"]["delta"] == -16 and changes["Bob"]["after"] == 1184
assert board.get_elo("Alice", "classic") == 1216
assert board.get_elo("Bob", "classic") == 1184

# Verify isolation: other modes are untouched
assert board.get_elo("Alice", "sweeper") == 1200
assert board.get_elo("Alice", "cube") == 1200
assert board.get_elo("Bob", "sweeper") == 1200
ok(7, "mode isolation: classic match updates classic Elo only; sweeper/cube remain 1200")

# Unranked custom mode: no Elo change
custom_changes = board.record_match([
    {"name": "Alice", "result": "win", "points": 10},
    {"name": "Bob", "result": "loss", "points": 2}
], mode="custom")
assert custom_changes == {}
assert board.get_elo("Alice", "classic") == 1216
assert board.get_elo("Bob", "classic") == 1184
ok(8, "unranked mode: custom mode match leaves all Elo ratings unchanged")

# Unranked solo bot match: only 1 human result passed
bot_changes = board.record_match([
    {"name": "Alice", "result": "win", "points": 7}
], mode="classic")
assert bot_changes == {}
assert board.get_elo("Alice", "classic") == 1216
ok(9, "bot match: solo human vs bot match leaves Elo rating unchanged")

# Leaderboard per-mode ranking
# Alice is 1216 in classic. Now let Bob win in cube mode against Alice:
board.record_match([
    {"name": "Bob", "result": "win", "points": 10},
    {"name": "Alice", "result": "loss", "points": 5}
], mode="cube")
assert board.get_elo("Bob", "cube") == 1216
assert board.get_elo("Alice", "cube") == 1184

top_classic = board.top(count=5, mode="classic")
assert top_classic[0]["name"] == "Alice" and top_classic[0]["elo"] == 1216
assert top_classic[1]["name"] == "Bob" and top_classic[1]["elo"] == 1184

top_cube = board.top(count=5, mode="cube")
assert top_cube[0]["name"] == "Bob" and top_cube[0]["elo"] == 1216
assert top_cube[1]["name"] == "Alice" and top_cube[1]["elo"] == 1184
ok(10, "mode leaderboard: top(mode) sorts by mode-specific Elo")


# =====================================================================
# 3. Schema migration and persistence
# =====================================================================
legacy_json = json.dumps({
    "Charlie": {
        "matches": 5, "wins": 3, "losses": 2, "draws": 0, "points": 25,
        "streak": 1, "best_streak": 2
        # no "elo" key
    },
    "Dave": {
        "matches": 2, "wins": 2, "losses": 0, "draws": 0, "points": 15,
        "streak": 2, "best_streak": 2,
        "elo": 1350  # legacy single-integer elo
    }
})

path = os.path.join(tempfile.gettempdir(), "test_find_my_mines_elo_stats.json")
with open(path, "w", encoding="utf-8") as f:
    f.write(legacy_json)

loaded_board = stats_mod.Leaderboard(path=path)
assert loaded_board.get_elo("Charlie", "classic") == 1200
assert loaded_board.get_elo("Charlie", "sweeper") == 1200
assert loaded_board.get_elo("Dave", "classic") == 1350
assert loaded_board.get_elo("Dave", "cube") == 1350
ok(11, "schema migration: legacy missing and single-int elo migrate to per-mode dict")

# Save and reload
loaded_board.record_match([
    {"name": "Charlie", "result": "win", "points": 6},
    {"name": "Dave", "result": "loss", "points": 4}
], mode="sweeper")
reloaded_board = stats_mod.Leaderboard(path=path)
assert reloaded_board.get_elo("Charlie", "sweeper") > 1200
assert reloaded_board.get_elo("Dave", "sweeper") < 1350
ok(12, "persistence: saved and reloaded per-mode Elo survives cleanly")

print("\nALL ELO CHECKS PASSED")
