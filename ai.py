"""Probability engine behind the computer opponent and the AI coach.

Both features ask the same question: given what is visible on the board,
how likely is each covered slot to hold a bomb?

The engine is handed only what a player sitting at the table can see - which
slots are open, what number each shows, and how many bombs are still out
there.  It is never given the bomb layout, so neither the computer nor the
coach can cheat.

How it works
    Every opened number is a constraint.  A "2" says exactly two of its
    covered neighbours are bombs.  In the two-ring hint style a touching bomb
    counts 2 and a bomb one ring further out counts 1, so the constraint is a
    weighted sum instead.

    The covered slots that appear in any constraint (the "frontier") are split
    into independent groups.  Each group is solved exactly by backtracking,
    counting every layout that satisfies its constraints.  The groups are then
    combined, weighting each total by the number of ways the remaining bombs
    can be scattered over the slots no number talks about.  That gives a true
    probability for every covered slot, not a guess.

    If a group is too large to enumerate within a work budget, the engine
    falls back to a cheaper local estimate for that group and says so.
"""

import itertools
import math
import random
import time
from collections import defaultdict
from functools import lru_cache

from game import BOMB

LEVELS = ("easy", "medium", "hard")

# Nodes the exact search may visit per call.  Keeps a move fast on big boards.
WORK_BUDGET = 20000


class _OutOfBudget(Exception):
    pass


# ----------------------------------------------------------------------
# geometry, on public information only
# ----------------------------------------------------------------------
@lru_cache(maxsize=None)
def neighbours(cell, dims, distance):
    """Slots exactly `distance` steps from `cell`, clipped to the board."""
    span = range(-distance, distance + 1)
    found = []
    for offset in itertools.product(span, repeat=len(cell)):
        if max(abs(step) for step in offset) != distance:
            continue
        nb = tuple(v + step for v, step in zip(cell, offset))
        if all(0 <= v < size for v, size in zip(nb, dims)):
            found.append(nb)
    return tuple(found)


def flatten(view, dims):
    """Nested board lists -> {slot: None | "bomb" | number}."""
    cells = {}
    if len(dims) == 3:
        for l, layer in enumerate(view):
            for r, row in enumerate(layer):
                for c, value in enumerate(row):
                    cells[(l, r, c)] = value
    else:
        for r, row in enumerate(view):
            for c, value in enumerate(row):
                cells[(r, c)] = value
    return cells


def _is_number(value):
    return isinstance(value, int) and not isinstance(value, bool)


# ----------------------------------------------------------------------
# constraints
# ----------------------------------------------------------------------
def build_constraints(cells, dims, weighted):
    """One (variables, total) pair per opened number.

    `variables` maps each covered neighbour to its weight; `total` is what
    the neighbours must add up to once bombs already found are subtracted.
    """
    touching = 2 if weighted else 1
    constraints = []
    for cell, value in cells.items():
        if not _is_number(value):
            continue
        total = value
        variables = {}
        for nb in neighbours(cell, dims, 1):
            state = cells[nb]
            if state is None:
                variables[nb] = touching
            elif state == BOMB:
                total -= touching
        if weighted:
            for nb in neighbours(cell, dims, 2):
                state = cells[nb]
                if state is None:
                    variables[nb] = 1
                elif state == BOMB:
                    total -= 1
        if variables:
            constraints.append((variables, total))
    return constraints


def _components(constraints):
    """Group constraints that share covered slots."""
    parent = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for variables, _ in constraints:
        cells = list(variables)
        for c in cells:
            parent.setdefault(c, c)
        for c in cells[1:]:
            parent[find(c)] = find(cells[0])

    groups = defaultdict(lambda: ([], []))
    for cell in parent:
        groups[find(cell)][0].append(cell)
    for variables, total in constraints:
        root = find(next(iter(variables)))
        groups[root][1].append((variables, total))
    return [g for g in groups.values()]


# ----------------------------------------------------------------------
# exact solving of one group
# ----------------------------------------------------------------------
def _solve_group(cells, constraints, budget):
    """Count every bomb layout that satisfies a group's constraints.

    Returns (counts, per_cell, used):
        counts[k]      layouts using k bombs
        per_cell[i][k] of those, how many have bomb i set
    """
    index = {c: i for i, c in enumerate(cells)}
    n = len(cells)
    cons = [([(index[c], w) for c, w in variables.items()], total)
            for variables, total in constraints]
    var_cons = [[] for _ in range(n)]
    for ci, (members, _) in enumerate(cons):
        for vi, w in members:
            var_cons[vi].append((ci, w))

    # search order: walk outwards through shared constraints so pruning bites
    order, seen = [], set()
    start = max(range(n), key=lambda i: len(var_cons[i]))
    queue = [start]
    seen.add(start)
    while queue:
        v = queue.pop(0)
        order.append(v)
        for ci, _ in var_cons[v]:
            for vj, _w in cons[ci][0]:
                if vj not in seen:
                    seen.add(vj)
                    queue.append(vj)
    for i in range(n):
        if i not in seen:
            order.append(i)

    partial = [0] * len(cons)
    remaining = [sum(w for _, w in members) for members, _ in cons]
    counts = defaultdict(int)
    per_cell = [defaultdict(int) for _ in range(n)]
    chosen = []
    nodes = [0]

    def feasible(touched):
        for ci, _ in touched:
            total = cons[ci][1]
            if partial[ci] > total or partial[ci] + remaining[ci] < total:
                return False
        return True

    def search(pos, k):
        nodes[0] += 1
        if nodes[0] > budget:
            raise _OutOfBudget
        if pos == n:
            counts[k] += 1
            for v in chosen:
                per_cell[v][k] += 1
            return
        v = order[pos]
        touched = var_cons[v]
        for ci, w in touched:
            remaining[ci] -= w
        for value in (0, 1):
            if value:
                for ci, w in touched:
                    partial[ci] += w
                chosen.append(v)
            if feasible(touched):
                search(pos + 1, k + value)
            if value:
                chosen.pop()
                for ci, w in touched:
                    partial[ci] -= w
        for ci, w in touched:
            remaining[ci] += w

    search(0, 0)
    return counts, per_cell, nodes[0]


def _local_estimate(cells, constraints):
    """Cheap fallback: how full does each nearby number say its slots are."""
    sums = defaultdict(float)
    hits = defaultdict(int)
    for variables, total in constraints:
        share = max(0.0, min(1.0, total / float(sum(variables.values()))))
        for cell in variables:
            sums[cell] += share
            hits[cell] += 1
    # slots no number talks about have no local evidence; callers use a prior
    return {cell: sums[cell] / hits[cell] for cell in cells if hits[cell]}


def _convolve(a, b):
    out = defaultdict(int)
    for ka, va in a.items():
        for kb, vb in b.items():
            out[ka + kb] += va * vb
    return out


# ----------------------------------------------------------------------
# sampling, for positions too big to enumerate
# ----------------------------------------------------------------------
def _random_layout(n, members, totals, weights_of, bombs_left, rest, rng,
                   cap=6000, attempts=10, deadline=None):
    """Find ONE bomb layout consistent with every number, by randomised
    backtracking.  Finding one is far cheaper than counting them all."""
    frontier = [i for i in range(n) if members[i]]
    if not frontier:
        return [0] * n if bombs_left == 0 else None
    m = len(totals)
    for attempt in range(attempts):
        if deadline is not None and time.perf_counter() > deadline:
            return None
        bias = (0.15, 0.30, 0.45)[attempt % 3]
        # walk outwards through shared numbers so bad choices fail early
        order, seen = [], set()
        queue = [rng.choice(frontier)]
        seen.add(queue[0])
        while queue:
            v = queue.pop(0)
            order.append(v)
            for ci, _ in members[v]:
                for vj in weights_of[ci]:
                    if vj not in seen:
                        seen.add(vj)
                        queue.append(vj)
        partial = [0] * m
        remaining = [sum(weights_of[ci].values()) for ci in range(m)]
        assign = {}
        nodes = [0]

        def feasible(touched):
            for ci, _w in touched:
                if partial[ci] > totals[ci] or partial[ci] + remaining[ci] < totals[ci]:
                    return False
            return True

        def search(pos):
            nodes[0] += 1
            if nodes[0] > cap:
                raise _OutOfBudget
            if (deadline is not None and nodes[0] % 200 == 0
                    and time.perf_counter() > deadline):
                raise _OutOfBudget
            if pos == len(order):
                return True
            v = order[pos]
            touched = members[v]
            for ci, w in touched:
                remaining[ci] -= w
            for value in ((1, 0) if rng.random() < bias else (0, 1)):
                if value:
                    for ci, w in touched:
                        partial[ci] += w
                if feasible(touched) and search(pos + 1):
                    assign[v] = value
                    return True
                if value:
                    for ci, w in touched:
                        partial[ci] -= w
            for ci, w in touched:
                remaining[ci] += w
            return False

        try:
            if not search(0):
                continue
        except _OutOfBudget:
            continue
        used = sum(assign.values())
        spare = bombs_left - used
        if spare < 0 or spare > rest:
            continue
        layout = [0] * n
        for v, value in assign.items():
            layout[v] = value
        loose = [i for i in range(n) if not members[i]]
        for i in rng.sample(loose, spare):
            layout[i] = 1
        return layout
    return None


def _sample_probabilities(covered, constraints, bombs_left, rng,
                          samples=60, seconds=0.35):
    """Estimate every covered slot's bomb chance from sampled layouts.

    Each sample is an independent layout found by randomised backtracking
    that satisfies every number and the number of bombs still out there.
    Averaging them gives each slot's share of bomb layouts.

    With a few dozen samples the extremes are partly luck - a slot that
    scores 95% may really be 75% - so each estimate is pulled a little toward
    the board-wide bomb density.  The pull shrinks as samples accumulate.
    """
    n = len(covered)
    index = {c: i for i, c in enumerate(covered)}
    members = [[] for _ in range(n)]
    weights_of = []
    totals = []
    for ci, (variables, total) in enumerate(constraints):
        totals.append(total)
        weights_of.append({index[c]: w for c, w in variables.items()})
        for c, w in variables.items():
            members[index[c]].append((ci, w))
    rest = sum(1 for i in range(n) if not members[i])

    tally = [0] * n
    got = 0
    deadline = time.perf_counter() + seconds
    for _ in range(samples):
        layout = _random_layout(n, members, totals, weights_of, bombs_left,
                                rest, rng, cap=4000, attempts=3,
                                deadline=deadline)
        if layout is not None:
            got += 1
            for i in range(n):
                tally[i] += layout[i]
        if time.perf_counter() > deadline:
            break
    if not got:
        return None
    prior = bombs_left / float(n)
    pull = 10.0
    return {covered[i]: (tally[i] + pull * prior) / (got + pull)
            for i in range(n)}


# ----------------------------------------------------------------------
# public API
# ----------------------------------------------------------------------
def probabilities(view, dims, weighted, bombs_left, budget=WORK_BUDGET,
                  rng=None):
    """Chance of a bomb for every covered slot.

    Returns (probs, exact): probs maps slot -> 0..1.  exact is False when the
    position was too big to enumerate; the answer is then a sampled estimate.
    """
    rng = rng or random.Random(0)
    dims = tuple(dims)
    cells = flatten(view, dims)
    covered = [c for c, v in cells.items() if v is None]
    if not covered:
        return {}, True
    bombs_left = max(0, min(bombs_left, len(covered)))

    constraints = build_constraints(cells, dims, weighted)
    frontier = set()
    for variables, _ in constraints:
        frontier.update(variables)
    rest = len(covered) - len(frontier)

    groups = sorted(_components(constraints), key=lambda g: len(g[0]))
    solved = []            # (cells, counts, per_cell)
    exact = True
    for group_cells, group_cons in groups:
        try:
            counts, per_cell, used = _solve_group(group_cells, group_cons, budget)
            budget -= used
            if not counts:
                raise _OutOfBudget          # contradictory - do not trust it
        except _OutOfBudget:
            exact = False
            estimate = _local_estimate(group_cells, group_cons)
            k0 = int(round(sum(estimate.values())))
            counts = {k0: 1}
            per_cell = [{k0: estimate[c]} for c in group_cells]
        solved.append((group_cells, counts, per_cell))
        budget = max(budget, 0)

    if not exact:
        # Too big to count.  Sample layouts instead, which stays accurate.
        sampled = _sample_probabilities(covered, constraints, bombs_left, rng)
        if sampled is not None:
            return sampled, False

    probs = {}
    if solved:
        def others_without(skip):
            dist = {0: 1}
            for j, (_c, counts, _p) in enumerate(solved):
                if j != skip:
                    dist = _convolve(dist, counts)
            return dist

        everything = {0: 1}
        for _c, counts, _p in solved:
            everything = _convolve(everything, counts)

        def ways(total_frontier):
            spare = bombs_left - total_frontier
            if spare < 0 or spare > rest:
                return 0
            return math.comb(rest, spare)

        total_weight = sum(v * ways(k) for k, v in everything.items())
        if total_weight == 0:
            prior = bombs_left / float(len(covered))
            return {c: prior for c in covered}, False

        for i, (group_cells, counts, per_cell) in enumerate(solved):
            others = others_without(i)
            spread = defaultdict(int)          # k for this group -> weight
            for ko, vo in others.items():
                for k in counts:
                    spread[k] += vo * ways(k + ko)
            denominator = sum(counts[k] * spread[k] for k in counts)
            for cell, seen in zip(group_cells, per_cell):
                numerator = sum(seen.get(k, 0) * spread[k] for k in counts)
                probs[cell] = (numerator / denominator) if denominator else 0.0

        expected = sum(k * v * ways(k) for k, v in everything.items()) / total_weight
    else:
        expected = 0.0

    if rest > 0:
        p_rest = max(0.0, min(1.0, (bombs_left - expected) / rest))
        for cell in covered:
            if cell not in frontier:
                probs[cell] = p_rest
    return probs, exact


def _pick_from(probs, bombs_are_bad, tolerance, rng):
    """Best slot for the goal, breaking near-ties at random."""
    if bombs_are_bad:
        best = min(probs.values())
        pool = [c for c, p in probs.items() if p <= best + tolerance]
    else:
        best = max(probs.values())
        pool = [c for c, p in probs.items() if p >= best - tolerance]
    return rng.choice(sorted(pool))


def choose_cell(view, dims, weighted, bombs_left, bombs_are_bad,
                level="hard", rng=None):
    """The computer opponent's move, or None when nothing is left to open."""
    rng = rng or random.Random()
    dims = tuple(dims)
    cells = flatten(view, dims)
    covered = sorted(c for c, v in cells.items() if v is None)
    if not covered:
        return None

    if level == "easy":
        # Reads each number in isolation and sometimes just guesses.
        if rng.random() < 0.30:
            return rng.choice(covered)
        estimate = _local_estimate(
            [c for c in covered],
            [(v, t) for v, t in build_constraints(cells, dims, weighted)])
        prior = bombs_left / float(len(covered))
        probs = {c: estimate.get(c, prior) for c in covered}
        return _pick_from(probs, bombs_are_bad, 0.10, rng)

    probs, _exact = probabilities(view, dims, weighted, bombs_left)
    if level == "medium":
        if rng.random() < 0.12:
            return rng.choice(covered)
        return _pick_from(probs, bombs_are_bad, 0.08, rng)
    return _pick_from(probs, bombs_are_bad, 1e-9, rng)


def advise(view, dims, weighted, bombs_left, bombs_are_bad):
    """The coach's answer: the best slot, how likely it is, and every slot's
    odds so the player can see the reasoning."""
    dims = tuple(dims)
    probs, exact = probabilities(view, dims, weighted, bombs_left)
    if not probs:
        return None
    ordered = sorted(probs)
    if bombs_are_bad:
        best = min(ordered, key=lambda c: probs[c])
    else:
        best = max(ordered, key=lambda c: probs[c])
    return {"cell": best, "p": probs[best], "exact": exact, "probs": probs}
