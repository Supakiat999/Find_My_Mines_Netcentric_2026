"""The rules engine on its own: every mode, scoring, and the new bookkeeping."""

import itertools
import random

from helpers import ok
import config
import game as g


def ring_count(cell, bombs, dims, distance):
    """Bombs exactly `distance` away, worked out the long way."""
    span = range(-distance, distance + 1)
    total = 0
    for off in itertools.product(span, repeat=len(cell)):
        if max(abs(o) for o in off) != distance:
            continue
        nb = tuple(v + o for v, o in zip(cell, off))
        if all(0 <= v < d for v, d in zip(nb, dims)) and nb in bombs:
            total += 1
    return total


def new(mode=g.MODE_CLASSIC, seed=7, custom=None):
    game = g.Game(rng=random.Random(seed))
    if custom:
        game.set_custom(custom)
    game.set_mode(mode)
    game.seat_players(["a", "b"])
    game.start_match("a")
    return game


def play_out(game):
    while game.phase == g.PHASE_PLAYING:
        for cell in list(game.cells()):
            if game.phase == g.PHASE_PLAYING and cell not in game.revealed:
                game.pick(game.current_turn, cell)


# --- 1. classic ---------------------------------------------------------
game = new()
assert game.dims == (6, 6) and len(game.bombs) == 11 and game.current_turn == "a"
for cell in game.cells():
    assert game.hints[cell] == ring_count(cell, game.bombs, game.dims, 1), cell
bomb = next(iter(game.bombs))
empty = next(c for c in game.cells() if c not in game.bombs)
res = game.pick("a", bomb)
assert res["is_bomb"] and not res["turn_changed"] and game.current_turn == "a"
assert game.pick("b", empty)["ok"] is False            # not b's turn
res = game.pick("a", empty)
assert not res["is_bomb"] and res["turn_changed"] and game.current_turn == "b"
assert game.pick("b", empty)["ok"] is False            # already open
play_out(game)
assert game.phase == g.PHASE_ENDED and game.bombs_found == 11
assert sum(game.scores.values()) == 11 and game.current_turn is None
ok(1, "classic: 11 bombs, hints = touching count, ends on the 11th bomb")

# --- 2. scores reset every match ---------------------------------------
assert sum(game.scores.values()) == 11
game.start_match(game.last_winner)
assert sum(game.scores.values()) == 0 and game.bombs_found == 0
ok(2, "a new match starts both players level")

# --- 3. two-ring hints --------------------------------------------------
game = new(g.MODE_RADIUS2, seed=3)
for cell in game.cells():
    want = (2 * ring_count(cell, game.bombs, game.dims, 1)
            + ring_count(cell, game.bombs, game.dims, 2))
    assert game.hints[cell] == want, cell
assert game.hints_weighted
assert game.pick("a", next(iter(game.bombs)))["is_bomb"] and game.scores["a"] == 1
ok(3, "radius 2: hints are 2 x ring1 + ring2, clipped at edges, 1 point a bomb")

# --- 4. the cube --------------------------------------------------------
game = new(g.MODE_CUBE, seed=11)
assert game.dims == (4, 4, 4) and game.cell_count == 64 and len(game.bombs) == 19
assert len(list(game._neighbours((1, 1, 1), 1))) == 26
assert len(list(game._neighbours((0, 0, 0), 1))) == 7
for cell in game.cells():
    assert game.hints[cell] == ring_count(cell, game.bombs, game.dims, 1), cell
view = game.board_view()
assert len(view) == 4 and len(view[0]) == 4 and len(view[0][0]) == 4
assert game.pick("a", (0, 0))["ok"] is False           # wrong number of coords
ok(4, "cube: 4x4x4, 19 bombs, 26 neighbours inside, 7 at a corner")

# --- 5. minesweeper -----------------------------------------------------
game = new(g.MODE_SWEEPER, seed=5)
safe_total = game.cell_count - game.bomb_count
bomb = next(iter(game.bombs))
res = game.pick("a", bomb)
assert res["is_bomb"] and res["turn_changed"] and game.current_turn == "b"
assert game.scores["a"] == 0
zero = next((c for c in game.cells()
             if c not in game.bombs and game.hints[c] == 0), None)
if zero is not None:
    res = game.pick("b", zero)
    assert res["opened"] > 1 and not res["turn_changed"]
    assert game.scores["b"] == res["opened"]
play_out(game)
assert game.phase == g.PHASE_ENDED and game.safe_left == 0
assert sum(game.scores.values()) == safe_total
ok(5, "minesweeper: a bomb ends the turn, zeros cascade, ends on the last safe slot")

# --- 6. flags -----------------------------------------------------------
game = new(seed=2)
spot = next(c for c in game.cells() if c not in game.bombs)
assert game.toggle_flag("a", spot) and game.pick("a", spot)["ok"] is False
game.current_turn = "b"
assert game.pick("b", spot)["ok"] and spot not in game.flags
ok(6, "a flag blocks only the player who planted it")

# --- 7. custom settings are clamped and honoured ------------------------
c = g.clamp_custom({"size": 99, "bombs": 9999, "turn_seconds": 1,
                    "shape": "cube", "hints": "radius2", "goal": "avoid"})
assert c["size"] == config.CUSTOM_LIMITS["size_cube"][1]
assert c["turn_seconds"] == config.CUSTOM_LIMITS["turn_seconds"][0]
assert 1 <= c["bombs"] <= c["size"] ** 3 * config.CUSTOM_LIMITS["max_bomb_share"]
c = g.clamp_custom({"size": 1, "bombs": -5, "shape": "junk", "hints": "junk",
                    "goal": "junk"})
assert c["size"] == config.CUSTOM_LIMITS["size_flat"][0] and c["bombs"] == 1
assert (c["shape"], c["hints"], c["goal"]) == ("flat", "simple", "collect")
game = new(g.MODE_CUSTOM, custom={"size": 8, "bombs": 20, "hints": "radius2",
                                  "turn_seconds": 25})
assert game.dims == (8, 8) and len(game.bombs) == 20 and game.turn_seconds == 25
for cell in game.cells():
    want = (2 * ring_count(cell, game.bombs, game.dims, 1)
            + ring_count(cell, game.bombs, game.dims, 2))
    assert game.hints[cell] == want
game = new(g.MODE_CUSTOM, custom={"shape": "cube", "size": 4, "goal": "avoid"})
assert game.is_3d and game.bombs_are_bad
res = game.pick("a", next(iter(game.bombs)))
assert res["is_bomb"] and res["turn_changed"]
ok(7, "custom: settings clamped, board reshapes in 2D and 3D, either goal")

# --- 8. per-match statistics and ownership ------------------------------
game = new(seed=21)
bombs = sorted(game.bombs)
game.pick("a", bombs[0])
game.pick("a", bombs[1])
game.pick("a", bombs[2])                     # three bombs in one turn
empty = next(c for c in game.cells() if c not in game.bombs)
game.pick("a", empty)                        # ends the chain, hands the turn over
stats = {row["id"]: row for row in game.match_stats()}
assert stats["a"]["picks"] == 4 and stats["a"]["best_chain"] == 3
assert stats["a"]["rate"] == 75 and stats["b"]["picks"] == 0
assert game.bomb_owners == {bombs[0]: "a", bombs[1]: "a", bombs[2]: "a"}
assert game.last_move == {"cell": empty, "by": "a"}
assert {o["by"] for o in game.owner_view()} == {"a"}
game.start_match("a")
assert game.match_stats()[0]["picks"] == 0 and game.bomb_owners == {}
ok(8, "match stats: picks, best chain, hit rate; who found each bomb; last move")

# --- 9. coach allowance -------------------------------------------------
game = new()
assert game.hints_left == {"a": config.HINTS_PER_MATCH, "b": config.HINTS_PER_MATCH}
for _ in range(config.HINTS_PER_MATCH):
    assert game.use_hint("a")
assert not game.use_hint("a") and game.use_hint("b")
game.start_match("a")
assert game.hints_left["a"] == config.HINTS_PER_MATCH
ok(9, "the coach allowance is per player and refills each match")

# --- 10. a returning player keeps everything ----------------------------
game = new(seed=9)
bomb = next(iter(game.bombs))
game.pick("a", bomb)
game.toggle_flag("a", next(c for c in game.cells() if c not in game.revealed))
game.rename_player("a", "a2")
assert game.players == ["a2", "b"] and game.current_turn == "a2"
assert game.scores["a2"] == 1 and "a" not in game.scores
assert game.bomb_owners[bomb] == "a2" and set(game.flags.values()) == {"a2"}
assert game.last_move["by"] == "a2" and game.hints_left["a2"] == config.HINTS_PER_MATCH
assert game.pick("a2", next(c for c in game.cells() if c not in game.revealed
                            and c not in game.flags))["ok"]
ok(10, "rename_player carries score, turn, flags, stats and hints to a new id")

# --- 11. the public view leaks nothing ----------------------------------
game = new(seed=13)
info = game.public_info()
flat = repr(info["view"])
assert "hidden_bomb" not in flat
assert sum(1 for cell in game.cells()
           if game.board_view()[cell[0]][cell[1]] is not None) == 0
assert set(info) == {"view", "dims", "weighted", "bombs_left", "bombs_are_bad"}
ok(11, "public_info carries the visible board and nothing hidden")

# --- 12. draws ----------------------------------------------------------
game = new(g.MODE_CUSTOM, custom={"size": 4, "bombs": 4})
bombs = sorted(game.bombs)
game.pick("a", bombs[0]); game.pick("a", bombs[1])
game.current_turn = "b"
game.pick("b", bombs[2]); game.pick("b", bombs[3])
assert game.phase == g.PHASE_ENDED and game.winner() is None
ok(12, "an even split is a draw")

print("\nALL RULES CHECKS PASSED")
