"""Unit and integration tests for the Elo rating system and advanced features."""

import json
import os
import tempfile

from helpers import ok
import config
import stats as stats_mod


# =====================================================================
# 1. Elo math calculations
# =====================================================================
# Equal ratings, A wins with standard K=32
(new_a, delta_a), (new_b, delta_b) = stats_mod.calculate_elo(1200, 1200, "win", k=32)
assert (new_a, delta_a) == (1216, 16)
assert (new_b, delta_b) == (1184, -16)
ok(1, "equal ratings: winner gets +16, loser loses 16 with K=32")

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
# 2. Rank Tiers categorization
# =====================================================================
assert stats_mod.get_tier(800)[0] == "Bronze"
assert stats_mod.get_tier(1100)[0] == "Silver"
assert stats_mod.get_tier(1299)[0] == "Silver"
assert stats_mod.get_tier(1300)[0] == "Gold"
assert stats_mod.get_tier(1550)[0] == "Platinum"
assert stats_mod.get_tier(1750)[0] == "Diamond"
assert stats_mod.get_tier(2000)[0] == "Master"
assert stats_mod.get_tier(2250)[0] == "Master"
ok(6, "rank tiers: correct classification from Bronze through Master at 2000+")


# =====================================================================
# 3. Provisional calibration, streak bonus, and peak tracking
# =====================================================================
board = stats_mod.Leaderboard(path=None)
assert board.get_elo("Alice", "classic") == 1200
assert board.is_provisional("Alice", "classic") is True

# Match 1: Provisional match uses accelerated K=64 -> delta = 32
changes = board.record_match([
    {"name": "Alice", "result": "win", "points": 8},
    {"name": "Bob", "result": "loss", "points": 3}
], mode="classic", ranked=True)

assert changes["Alice"]["delta"] == 32
assert changes["Alice"]["after"] == 1232
assert changes["Alice"]["provisional"] is True
assert changes["Alice"]["peak"] == 1232
assert changes["Bob"]["delta"] == -32
assert changes["Bob"]["after"] == 1168
ok(7, "provisional calibration: initial matches use K=64 (+32 / -32) and track peak Elo")

# Play more matches so Alice gets a streak >= 3
# Match 2
board.record_match([
    {"name": "Alice", "result": "win", "points": 7},
    {"name": "Bob", "result": "loss", "points": 4}
], mode="classic")
# Match 3: Alice reaches streak of 3 -> earns +6 streak bonus
changes3 = board.record_match([
    {"name": "Alice", "result": "win", "points": 9},
    {"name": "Bob", "result": "loss", "points": 2}
], mode="classic")

assert changes3["Alice"]["streak_bonus"] == 6
assert board.data["Alice"]["streak"] == 3
ok(8, "streak bonus: 3rd consecutive win awards flat +6 bonus Elo")

# Peak tracking survives a loss
# Alice loses Match 4
before_peak = board.get_peak_elo("Alice", "classic")
changes4 = board.record_match([
    {"name": "Alice", "result": "loss", "points": 3},
    {"name": "Bob", "result": "win", "points": 8}
], mode="classic")
assert changes4["Alice"]["after"] < before_peak
assert board.get_peak_elo("Alice", "classic") == before_peak
ok(9, "peak tracking: peak Elo is preserved even after a rating loss")


# =====================================================================
# 4. Casual vs Ranked match toggle
# =====================================================================
elo_before = board.get_elo("Alice", "classic")
casual_changes = board.record_match([
    {"name": "Alice", "result": "win", "points": 10},
    {"name": "Bob", "result": "loss", "points": 1}
], mode="classic", ranked=False)

assert casual_changes == {}
assert board.get_elo("Alice", "classic") == elo_before
# But match counts/wins still recorded
assert board.data["Alice"]["matches"] == 5
ok(10, "casual mode: ranked=False leaves Elo untouched while updating stats")


# =====================================================================
# 5. Mode isolation and top() leaderboard
# =====================================================================
assert board.get_elo("Alice", "cube") == 1200
board.record_match([
    {"name": "Bob", "result": "win", "points": 10},
    {"name": "Alice", "result": "loss", "points": 5}
], mode="cube")
assert board.get_elo("Bob", "cube") == 1232
assert board.get_elo("Alice", "cube") == 1168

top_cube = board.top(count=5, mode="cube")
assert top_cube[0]["name"] == "Bob" and top_cube[0]["elo"] == 1232
assert "tier" in top_cube[0] and "tier_color" in top_cube[0]
ok(11, "mode isolation & rich leaderboard: top(mode) returns tiers, peak, and ranks correctly")


# =====================================================================
# 6. Schema migration and persistence
# =====================================================================
legacy_json = json.dumps({
    "Charlie": {
        "matches": 5, "wins": 3, "losses": 2, "draws": 0, "points": 25,
        "streak": 1, "best_streak": 2
        # no "elo", "peak_elo", or "mode_matches" keys
    },
    "Dave": {
        "matches": 2, "wins": 2, "losses": 0, "draws": 0, "points": 15,
        "streak": 2, "best_streak": 2,
        "elo": 1350  # legacy single-integer elo
    }
})

path = os.path.join(tempfile.gettempdir(), "test_find_my_mines_advanced_elo.json")
with open(path, "w", encoding="utf-8") as f:
    f.write(legacy_json)

loaded_board = stats_mod.Leaderboard(path=path)
assert loaded_board.get_elo("Charlie", "classic") == 1200
assert loaded_board.get_peak_elo("Charlie", "classic") == 1200
assert loaded_board.get_elo("Dave", "classic") == 1350
assert loaded_board.get_peak_elo("Dave", "classic") == 1350
assert loaded_board.get_mode_matches("Dave", "classic") == 0
ok(12, "schema migration: legacy records seamlessly upgrade with peak_elo and mode_matches")

# Save and reload
loaded_board.record_match([
    {"name": "Charlie", "result": "win", "points": 6},
    {"name": "Dave", "result": "loss", "points": 4}
], mode="sweeper")
reloaded_board = stats_mod.Leaderboard(path=path)
assert reloaded_board.get_elo("Charlie", "sweeper") > 1200
assert reloaded_board.get_peak_elo("Charlie", "sweeper") > 1200
ok(13, "persistence: advanced Elo fields persist and reload across disk restarts")

print("\nALL ADVANCED ELO CHECKS PASSED")
