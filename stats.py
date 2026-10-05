"""A small hall of fame: who has won what, across matches and restarts.

Results are keyed by nickname.  They are saved to a JSON file next to the
game so the table survives the server being closed and reopened.  Pass
path=None for an in-memory table (the tests do this, so they never touch a
real file).
"""

import json
import os

import config

FIELDS = ("matches", "wins", "losses", "draws", "points", "streak", "best_streak")


def default_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.json")


def default_elo_dict():
    """Starting rating for every ranked mode."""
    starting = getattr(config, "ELO_STARTING", 1200)
    modes = getattr(config, "ELO_RANKED_MODES", ("classic", "radius2", "sweeper", "cube"))
    return {m: starting for m in modes}


def calculate_elo(rating_a, rating_b, outcome, k=None, floor=None):
    """Calculate new Elo ratings and deltas for player A and player B.

    outcome: "win" (A won, B lost), "loss" (A lost, B won), or "draw"
    Returns: ((new_a, delta_a), (new_b, delta_b))
    """
    if k is None:
        k = getattr(config, "ELO_K_FACTOR", 32)
    if floor is None:
        floor = getattr(config, "ELO_MINIMUM", 100)

    expected_a = 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))
    expected_b = 1.0 - expected_a

    if outcome == "win":
        score_a, score_b = 1.0, 0.0
    elif outcome == "loss":
        score_a, score_b = 0.0, 1.0
    else:  # "draw"
        score_a, score_b = 0.5, 0.5

    delta_a = int(round(k * (score_a - expected_a)))
    delta_b = int(round(k * (score_b - expected_b)))

    new_a = max(floor, rating_a + delta_a)
    new_b = max(floor, rating_b + delta_b)
    effective_delta_a = new_a - rating_a
    effective_delta_b = new_b - rating_b

    return (new_a, effective_delta_a), (new_b, effective_delta_b)


class Leaderboard:
    def __init__(self, path=None):
        self.path = path
        self.data = {}
        self.load()

    # -- persistence ----------------------------------------------------
    def load(self):
        """Read the saved table.  A missing or damaged file is not an error:
        the hall of fame simply starts empty."""
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        for name, row in raw.items():
            if isinstance(name, str) and isinstance(row, dict):
                clean = {f: int(row.get(f, 0)) if isinstance(row.get(f, 0), int)
                         else 0 for f in FIELDS}
                elo_raw = row.get("elo")
                clean_elo = default_elo_dict()
                if isinstance(elo_raw, dict):
                    for m in clean_elo:
                        if m in elo_raw and isinstance(elo_raw[m], int):
                            clean_elo[m] = max(getattr(config, "ELO_MINIMUM", 100), elo_raw[m])
                elif isinstance(elo_raw, int):
                    for m in clean_elo:
                        clean_elo[m] = max(getattr(config, "ELO_MINIMUM", 100), elo_raw)
                clean["elo"] = clean_elo
                self.data[name[:16]] = clean

    def save(self):
        if not self.path:
            return False
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=1, sort_keys=True)
            return True
        except OSError:
            return False

    # -- recording ------------------------------------------------------
    def _row(self, name):
        row = self.data.setdefault(name, {f: 0 for f in FIELDS})
        if "elo" not in row or not isinstance(row["elo"], dict):
            row["elo"] = default_elo_dict()
        return row

    def get_elo(self, name, mode="classic"):
        row = self.data.get(name)
        if not row:
            return getattr(config, "ELO_STARTING", 1200)
        elo_map = row.get("elo")
        if isinstance(elo_map, dict):
            return elo_map.get(mode, getattr(config, "ELO_STARTING", 1200))
        return getattr(config, "ELO_STARTING", 1200)

    def record_match(self, results, mode="classic"):
        """results: [{"name", "result": "win"|"loss"|"draw", "points": int}]"""
        elo_changes = {}
        ranked_modes = getattr(config, "ELO_RANKED_MODES", ("classic", "radius2", "sweeper", "cube"))
        is_ranked = mode in ranked_modes and len(results) == 2

        if is_ranked:
            p1, p2 = results[0], results[1]
            name1, name2 = p1["name"], p2["name"]
            r1 = self.get_elo(name1, mode)
            r2 = self.get_elo(name2, mode)

            if p1["result"] == "win":
                outcome = "win"
            elif p1["result"] == "loss":
                outcome = "loss"
            else:
                outcome = "draw"

            (new_1, delta_1), (new_2, delta_2) = calculate_elo(r1, r2, outcome)

            row1 = self._row(name1)
            row2 = self._row(name2)
            row1["elo"][mode] = new_1
            row2["elo"][mode] = new_2

            elo_changes[name1] = {"before": r1, "after": new_1, "delta": delta_1, "mode": mode}
            elo_changes[name2] = {"before": r2, "after": new_2, "delta": delta_2, "mode": mode}

        for r in results:
            row = self._row(r["name"])
            row["matches"] += 1
            row["points"] += int(r.get("points", 0))
            if r["result"] == "win":
                row["wins"] += 1
                row["streak"] += 1
                row["best_streak"] = max(row["best_streak"], row["streak"])
            elif r["result"] == "loss":
                row["losses"] += 1
                row["streak"] = 0
            else:
                row["draws"] += 1
                row["streak"] = 0
        self.save()
        return elo_changes

    # -- reading --------------------------------------------------------
    def record_of(self, name):
        row = self.data.get(name)
        if not row:
            res = {f: 0 for f in FIELDS}
            res["elo"] = default_elo_dict()
            return res
        res = dict(row)
        if "elo" in res and isinstance(res["elo"], dict):
            res["elo"] = dict(res["elo"])
        else:
            res["elo"] = default_elo_dict()
        return res

    def top(self, count=5, mode="classic"):
        """Best first: highest Elo in mode, then most wins, then most points."""
        ranked = sorted(
            self.data.items(),
            key=lambda kv: (-self.get_elo(kv[0], mode), -kv[1]["wins"], -kv[1]["points"], kv[0])
        )
        return [{"name": name, "elo": self.get_elo(name, mode),
                 "wins": row["wins"], "losses": row["losses"],
                 "draws": row["draws"], "points": row["points"],
                 "matches": row["matches"], "best_streak": row["best_streak"],
                 "mode": mode}
                for name, row in ranked[:count]]
