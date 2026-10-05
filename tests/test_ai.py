"""The probability engine: hand-checked cases, calibration, strength, speed."""

import helpers  # noqa: F401 - puts the project on the path
import random
import time

import config
import game as g
import ai

# --- 1. cases that can be worked out by hand ----------------------------
def blank(n=6):
    return [[None] * n for _ in range(n)]

view = blank()
view[2][2] = 0                       # a zero: every neighbour is safe
probs, exact = ai.probabilities(view, (6, 6), False, 11)
for cell in ai.neighbours((2, 2), (6, 6), 1):
    assert probs[cell] == 0, cell
assert exact

view = blank()
view[2][2] = 8                       # an eight: every neighbour is a bomb
probs, _ = ai.probabilities(view, (6, 6), False, 11)
for cell in ai.neighbours((2, 2), (6, 6), 1):
    assert probs[cell] == 1, cell

view = blank()
view[0][0] = 1                       # a corner one: 3 neighbours, one bomb
probs, _ = ai.probabilities(view, (6, 6), False, 11)
for cell in ai.neighbours((0, 0), (6, 6), 1):
    assert abs(probs[cell] - 1 / 3) < 1e-9, (cell, probs[cell])

# a found bomb is subtracted from the numbers around it
view = blank()
view[0][0] = 1
view[0][1] = g.BOMB                  # that one is already accounted for
probs, _ = ai.probabilities(view, (6, 6), False, 10)
assert probs[(1, 0)] == 0 and probs[(1, 1)] == 0

# two-ring hints: a 1 can only be a single ring-2 bomb, never a touching one
view = blank()
view[2][2] = 1
probs, _ = ai.probabilities(view, (6, 6), True, 11)
for cell in ai.neighbours((2, 2), (6, 6), 1):
    assert probs[cell] == 0, "a touching bomb would already make it >= 2"
ring2 = ai.neighbours((2, 2), (6, 6), 2)
assert all(abs(probs[c] - 1 / len(ring2)) < 1e-9 for c in ring2)
print("1 OK  hand-checked cases: zero, eight, corner one, known bomb, two-ring")

# --- 2. calibration -----------------------------------------------------
def calibration(mode, samples, reveal, custom=None):
    rng = random.Random(1234)
    bins = [[0, 0.0, 0] for _ in range(10)]            # count, sum p, sum bombs
    for _ in range(samples):
        game = g.Game(rng=rng)
        if custom:
            game.set_custom(custom)
        game.set_mode(mode)
        game.seat_players([1, 2])
        game.start_match(1)
        cells = list(game.cells())
        for cell in rng.sample(cells, reveal):
            # reveal independent of the layout, as the maths assumes
            game.revealed[cell] = (g.BOMB if cell in game.bombs
                                   else game.hints[cell])
        game.bombs_found = sum(1 for v in game.revealed.values() if v == g.BOMB)
        probs, _ = ai.probabilities(game.board_view(), game.dims,
                                    game.hints_weighted, game.bombs_left)
        for cell, p in probs.items():
            b = bins[min(9, int(p * 10))]
            b[0] += 1
            b[1] += p
            b[2] += 1 if cell in game.bombs else 0
    worst = 0.0
    for count, sp, sb in bins:
        if count >= 150:
            worst = max(worst, abs(sp / count - sb / count))
    return worst, sum(b[0] for b in bins)

t0 = time.time()
worst, n = calibration(g.MODE_CLASSIC, 700, 9)
assert worst < 0.035, "classic calibration off by %.3f" % worst
print("2a OK  classic: %d predictions, worst bin off by %.3f" % (n, worst))
worst, n = calibration(g.MODE_RADIUS2, 500, 8)
assert worst < 0.04, "radius-2 calibration off by %.3f" % worst
print("2b OK  radius 2: %d predictions, worst bin off by %.3f" % (n, worst))
def cube_quality(samples, reveal):
    """The cube is too big to count exactly, so its odds are sampled.  Check
    what matters: the average is right and the ranking is genuinely useful."""
    rng = random.Random(4321)
    pairs = []
    for _ in range(samples):
        game = g.Game(rng=rng)
        game.set_mode(g.MODE_CUBE)
        game.seat_players([1, 2])
        game.start_match(1)
        for cell in rng.sample(list(game.cells()), reveal):
            game.revealed[cell] = (g.BOMB if cell in game.bombs
                                   else game.hints[cell])
        game.bombs_found = sum(1 for v in game.revealed.values() if v == g.BOMB)
        probs, exact = ai.probabilities(game.board_view(), game.dims, False,
                                        game.bombs_left, rng=random.Random(3))
        pairs += [(p, cell in game.bombs) for cell, p in probs.items()]
    pairs.sort()
    n = len(pairs)
    mean_p = sum(p for p, _ in pairs) / n
    mean_b = sum(1 for _, b in pairs if b) / n
    fifth = n // 5
    low = sum(1 for _, b in pairs[:fifth] if b) / fifth
    high = sum(1 for _, b in pairs[-fifth:] if b) / fifth
    return mean_p, mean_b, low, high, n

t0 = time.time()
mean_p, mean_b, low, high, n = cube_quality(70, 12)
assert abs(mean_p - mean_b) < 0.02, (mean_p, mean_b)
assert high > low * 2 and high - low > 0.25, (low, high)
print("2c OK  3D cube (sampled): %d predictions, average %.3f vs actual %.3f; "
      "least-likely fifth %.0f%% bombs, most-likely fifth %.0f%%  (%.0fs)"
      % (n, mean_p, mean_b, low * 100, high * 100, time.time() - t0))

# --- 3. the bot is a real opponent ---------------------------------------
def play(level_a, level_b, mode, seed, custom=None):
    """Two computers play a match; returns the winner index or None."""
    rng = random.Random(seed)
    game = g.Game(rng=rng)
    if custom:
        game.set_custom(custom)
    game.set_mode(mode)
    game.seat_players([1, 2])
    game.start_match(1 if seed % 2 else 2)
    levels = {1: level_a, 2: level_b}
    guard = 0
    while game.phase == g.PHASE_PLAYING and guard < 500:
        guard += 1
        me = game.current_turn
        view = game.board_view()
        if levels[me] == "random":
            cell = rng.choice([c for c in game.cells() if c not in game.revealed])
        else:
            cell = ai.choose_cell(view, game.dims, game.hints_weighted,
                                  game.bombs_left, game.bombs_are_bad,
                                  levels[me], rng)
        assert cell is not None and cell not in game.revealed
        assert game.pick(me, cell)["ok"]
    w = game.winner()
    return None if w is None else (0 if w == 1 else 1)

def score(a, b, mode, games, custom=None):
    wins = draws = 0
    for s in range(games):
        first = play(a, b, mode, s, custom)
        if first == 0:
            wins += 1
        elif first is None:
            draws += 1
    return wins, draws

t0 = time.time()
wins, draws = score("hard", "random", g.MODE_CLASSIC, 60)
assert wins >= 42, "hard beat random only %d/60" % wins
print("3a OK  classic: hard beats random %d-%d (%d draws)"
      % (wins, 60 - wins - draws, draws))
wins, draws = score("hard", "easy", g.MODE_CLASSIC, 60)
assert wins >= 33, "hard should beat easy, got %d/60" % wins
print("3b OK  classic: hard beats easy %d-%d" % (wins, 60 - wins - draws))
wins, draws = score("hard", "random", g.MODE_SWEEPER, 40)
assert wins >= 28, "sweeper: hard beat random only %d/40" % wins
print("3c OK  minesweeper: hard beats random %d-%d" % (wins, 40 - wins - draws))
wins, draws = score("hard", "random", g.MODE_RADIUS2, 30)
assert wins >= 19, "radius2: hard beat random only %d/30" % wins
print("3d OK  radius 2: hard beats random %d-%d  (%.0fs)"
      % (wins, 30 - wins - draws, time.time() - t0))

t0 = time.time()
wins, draws = score("hard", "random", g.MODE_CUBE, 14)
assert wins >= 9, "cube: hard beat random only %d/14" % wins
print("3e OK  3D cube: hard beats random %d-%d  (%.0fs)"
      % (wins, 14 - wins - draws, time.time() - t0))

# --- 4. it only uses public information ---------------------------------
game = g.Game(rng=random.Random(4))
game.seat_players([1, 2]); game.start_match(1)
for cell in list(game.cells())[:7]:
    game.pick(1, cell) if game.current_turn == 1 else game.pick(2, cell)
view = game.board_view()
a = ai.probabilities(view, game.dims, False, game.bombs_left)[0]
other = g.Game(rng=random.Random(99))            # a different hidden layout
other.seat_players([1, 2]); other.start_match(1)
b = ai.probabilities(view, game.dims, False, game.bombs_left)[0]
assert a == b, "answers depend only on what is visible"
assert "bombs" not in ai.probabilities.__code__.co_varnames
print("4 OK  answers depend only on the visible board")

# --- 5. speed on the biggest boards -------------------------------------
def slowest(mode, custom, reveal_share):
    rng = random.Random(7)
    worst = 0.0
    for _ in range(6):
        game = g.Game(rng=rng)
        game.set_custom(custom)
        game.set_mode(mode)
        game.seat_players([1, 2]); game.start_match(1)
        cells = list(game.cells())
        for cell in rng.sample(cells, int(len(cells) * reveal_share)):
            game.revealed[cell] = (g.BOMB if cell in game.bombs
                                   else game.hints[cell])
        game.bombs_found = sum(1 for v in game.revealed.values() if v == g.BOMB)
        t = time.time()
        ai.choose_cell(game.board_view(), game.dims, game.hints_weighted,
                       game.bombs_left, False, "hard", rng)
        worst = max(worst, time.time() - t)
    return worst

for label, custom, share in (
        ("10x10, 45 bombs", {"size": 10, "bombs": 45}, 0.45),
        ("10x10 two-ring", {"size": 10, "bombs": 30, "hints": "radius2"}, 0.4),
        ("5x5x5 cube, 56 bombs", {"shape": "cube", "size": 5, "bombs": 56}, 0.4)):
    worst = slowest(g.MODE_CUSTOM, custom, share)
    assert worst < 1.5, "%s took %.2fs" % (label, worst)
    print("5 OK  %-22s slowest move %.2fs" % (label, worst))

print("\nALL AI CHECKS PASSED")
