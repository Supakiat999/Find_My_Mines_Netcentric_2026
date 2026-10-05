"""A small hall of fame: who has won what, across matches and restarts.

Results are keyed by nickname.  They are saved to a JSON file next to the
game so the table survives the server being closed and reopened.  Pass
path=None for an in-memory table (the tests do this, so they never touch a
real file).
"""

import json
import os

FIELDS = ("matches", "wins", "losses", "draws", "points", "streak", "best_streak")


def default_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.json")


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
        return self.data.setdefault(name, {f: 0 for f in FIELDS})

    def record_match(self, results):
        """results: [{"name", "result": "win"|"loss"|"draw", "points": int}]"""
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

    # -- reading --------------------------------------------------------
    def record_of(self, name):
        return dict(self.data.get(name, {f: 0 for f in FIELDS}))

    def top(self, count=5):
        """Best first: most wins, then most bombs found."""
        ranked = sorted(self.data.items(),
                        key=lambda kv: (-kv[1]["wins"], -kv[1]["points"], kv[0]))
        return [{"name": name, "wins": row["wins"], "losses": row["losses"],
                 "draws": row["draws"], "points": row["points"],
                 "matches": row["matches"], "best_streak": row["best_streak"]}
                for name, row in ranked[:count]]
