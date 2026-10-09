"""A small hall of fame: who has won what, across matches and restarts.

Results are keyed by nickname.  They are saved to a JSON file next to the
game so the table survives the server being closed and reopened.  Pass
path=None for an in-memory table (the tests do this, so they never touch a
real file).
"""

import json
import os
import queue
import threading
import time

import config

FIELDS = ("matches", "wins", "losses", "draws", "points", "streak", "best_streak")


def default_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.json")


def default_elo_dict():
    """Starting rating for every ranked mode."""
    starting = getattr(config, "ELO_STARTING", 1200)
    modes = getattr(config, "ELO_RANKED_MODES", ("classic", "radius2", "sweeper", "cube"))
    return {m: starting for m in modes}


def get_tier(elo):
    """Return (tier_name, rgb_color) for an Elo rating."""
    tiers = getattr(config, "ELO_TIERS", [
        ("Bronze",   0,    (195, 130, 80)),
        ("Silver",   1100, (185, 195, 205)),
        ("Gold",     1300, (240, 185, 50)),
        ("Platinum", 1500, (65, 215, 195)),
        ("Diamond",  1700, (170, 130, 250)),
        ("Master",   2000, (255, 80, 120)),
    ])
    matched = tiers[0]
    for name, min_val, color in tiers:
        if elo >= min_val:
            matched = (name, color)
    return matched


def calculate_elo(rating_a, rating_b, outcome, k=None, k_b=None, floor=None):
    """Calculate new Elo ratings and deltas for player A and player B.

    outcome: "win" (A won, B lost), "loss" (A lost, B won), or "draw"
    Returns: ((new_a, delta_a), (new_b, delta_b))
    """
    if k is None:
        k = getattr(config, "ELO_K_FACTOR", 32)
    k_a = k
    if k_b is None:
        k_b = k_a
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

    delta_a = int(round(k_a * (score_a - expected_a)))
    delta_b = int(round(k_b * (score_b - expected_b)))

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
        self.load_records(raw)

    def load_records(self, raw):
        """Normalize current and legacy JSON records without reopening a file."""
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

                # peak_elo
                peak_raw = row.get("peak_elo")
                clean_peak = dict(clean_elo)
                if isinstance(peak_raw, dict):
                    for m in clean_peak:
                        if m in peak_raw and isinstance(peak_raw[m], int):
                            clean_peak[m] = max(clean_elo[m], peak_raw[m])
                clean["peak_elo"] = clean_peak

                # mode_matches
                mm_raw = row.get("mode_matches")
                clean_mm = {m: 0 for m in clean_elo}
                if isinstance(mm_raw, dict):
                    for m in clean_mm:
                        if m in mm_raw and isinstance(mm_raw[m], int):
                            clean_mm[m] = max(0, mm_raw[m])
                clean["mode_matches"] = clean_mm

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
        if "peak_elo" not in row or not isinstance(row["peak_elo"], dict):
            row["peak_elo"] = dict(row["elo"])
        if "mode_matches" not in row or not isinstance(row["mode_matches"], dict):
            modes = getattr(config, "ELO_RANKED_MODES", ("classic", "radius2", "sweeper", "cube"))
            row["mode_matches"] = {m: 0 for m in modes}
        return row

    def get_elo(self, name, mode="classic"):
        row = self.data.get(name)
        if not row:
            return getattr(config, "ELO_STARTING", 1200)
        elo_map = row.get("elo")
        if isinstance(elo_map, dict):
            return elo_map.get(mode, getattr(config, "ELO_STARTING", 1200))
        return getattr(config, "ELO_STARTING", 1200)

    def get_peak_elo(self, name, mode="classic"):
        row = self.data.get(name)
        if not row or "peak_elo" not in row:
            return self.get_elo(name, mode)
        return row["peak_elo"].get(mode, self.get_elo(name, mode))

    def get_mode_matches(self, name, mode="classic"):
        row = self.data.get(name)
        if not row or "mode_matches" not in row:
            return 0
        return row["mode_matches"].get(mode, 0)

    def is_provisional(self, name, mode="classic"):
        limit = getattr(config, "ELO_PROVISIONAL_MATCHES", 5)
        return self.get_mode_matches(name, mode) < limit

    def record_match(self, results, mode="classic", ranked=True):
        """results: [{"name", "result": "win"|"loss"|"draw", "points": int}]"""
        elo_changes = {}
        ranked_modes = getattr(config, "ELO_RANKED_MODES", ("classic", "radius2", "sweeper", "cube"))
        is_ranked = ranked and (mode in ranked_modes) and len(results) == 2

        if is_ranked:
            p1, p2 = results[0], results[1]
            name1, name2 = p1["name"], p2["name"]
            r1 = self.get_elo(name1, mode)
            r2 = self.get_elo(name2, mode)
            tier1_before = get_tier(r1)[0]
            tier2_before = get_tier(r2)[0]

            prov_matches = getattr(config, "ELO_PROVISIONAL_MATCHES", 5)
            prov_k = getattr(config, "ELO_PROVISIONAL_K", 64)
            std_k = getattr(config, "ELO_K_FACTOR", 32)
            k1 = prov_k if self.get_mode_matches(name1, mode) < prov_matches else std_k
            k2 = prov_k if self.get_mode_matches(name2, mode) < prov_matches else std_k

            if p1["result"] == "win":
                outcome = "win"
            elif p1["result"] == "loss":
                outcome = "loss"
            else:
                outcome = "draw"

            (new_1, delta_1), (new_2, delta_2) = calculate_elo(r1, r2, outcome, k=k1, k_b=k2)

            streak_bonus_1 = 0
            streak_bonus_2 = 0
            threshold = getattr(config, "ELO_STREAK_THRESHOLD", 3)
            bonus_amt = getattr(config, "ELO_STREAK_BONUS", 6)
            row1 = self._row(name1)
            row2 = self._row(name2)

            if outcome == "win" and (row1["streak"] + 1) >= threshold:
                streak_bonus_1 = bonus_amt
                new_1 += streak_bonus_1
                delta_1 += streak_bonus_1
            elif outcome == "loss" and (row2["streak"] + 1) >= threshold:
                streak_bonus_2 = bonus_amt
                new_2 += streak_bonus_2
                delta_2 += streak_bonus_2

            row1["elo"][mode] = new_1
            row2["elo"][mode] = new_2

            row1["peak_elo"][mode] = max(row1["peak_elo"].get(mode, r1), new_1)
            row2["peak_elo"][mode] = max(row2["peak_elo"].get(mode, r2), new_2)

            row1["mode_matches"][mode] = row1["mode_matches"].get(mode, 0) + 1
            row2["mode_matches"][mode] = row2["mode_matches"].get(mode, 0) + 1

            tier1_after = get_tier(new_1)
            tier2_after = get_tier(new_2)
            promoted_1 = tier1_after[0] if (tier1_after[0] != tier1_before and new_1 > r1) else None
            promoted_2 = tier2_after[0] if (tier2_after[0] != tier2_before and new_2 > r2) else None

            elo_changes[name1] = {
                "before": r1, "after": new_1, "delta": delta_1, "mode": mode,
                "tier": tier1_after[0], "tier_color": tier1_after[1],
                "promoted": promoted_1,
                "peak": row1["peak_elo"][mode],
                "provisional": row1["mode_matches"][mode] <= prov_matches,
                "mode_matches": row1["mode_matches"][mode],
                "streak_bonus": streak_bonus_1,
            }
            elo_changes[name2] = {
                "before": r2, "after": new_2, "delta": delta_2, "mode": mode,
                "tier": tier2_after[0], "tier_color": tier2_after[1],
                "promoted": promoted_2,
                "peak": row2["peak_elo"][mode],
                "provisional": row2["mode_matches"][mode] <= prov_matches,
                "mode_matches": row2["mode_matches"][mode],
                "streak_bonus": streak_bonus_2,
            }

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
            res["peak_elo"] = default_elo_dict()
            res["mode_matches"] = {m: 0 for m in default_elo_dict()}
            return res
        res = dict(row)
        for k in ("elo", "peak_elo", "mode_matches"):
            if k in res and isinstance(res[k], dict):
                res[k] = dict(res[k])
            else:
                res[k] = default_elo_dict() if "elo" in k else {m: 0 for m in default_elo_dict()}
        return res

    def top(self, count=5, mode="classic"):
        """Best first: highest Elo in mode, then most wins, then most points."""
        ranked = sorted(
            self.data.items(),
            key=lambda kv: (-self.get_elo(kv[0], mode), -kv[1]["wins"], -kv[1]["points"], kv[0])
        )
        out = []
        for name, row in ranked[:count]:
            elo_val = self.get_elo(name, mode)
            tier_name, tier_col = get_tier(elo_val)
            peak_val = self.get_peak_elo(name, mode)
            matches_count = self.get_mode_matches(name, mode)
            prov_limit = getattr(config, "ELO_PROVISIONAL_MATCHES", 5)
            out.append({
                "name": name, "elo": elo_val, "peak_elo": peak_val,
                "tier": tier_name, "tier_color": tier_col,
                "provisional": matches_count < prov_limit,
                "mode_matches": matches_count,
                "wins": row["wins"], "losses": row["losses"],
                "draws": row["draws"], "points": row["points"],
                "matches": row["matches"], "best_streak": row["best_streak"],
                "mode": mode,
            })
        return out


class DatabaseLeaderboard(Leaderboard):
    """Cached reads on the game thread; transactional writes on one worker."""

    def __init__(self, url):
        from database import Store
        self.path = None
        self.store = Store(url)
        self.data = self.store.load()
        self.jobs = queue.Queue()
        self.completed = queue.Queue()
        self.stopping = threading.Event()
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def submit(self, snapshot, context=None):
        # TODO: Add a durable result journal if recovery before commit is required.
        self.jobs.put((snapshot, context))

    def _run(self):
        import psycopg
        retryable = (psycopg.OperationalError, psycopg.InterfaceError,
                     psycopg.errors.SerializationFailure,
                     psycopg.errors.DeadlockDetected,
                     psycopg.errors.LockNotAvailable,
                     psycopg.errors.QueryCanceled)
        retries = []
        closing = False
        while not self.stopping.is_set():
            if closing and not retries and self.jobs.empty():
                return
            due = min(retries, key=lambda item: item[0]) if retries else None
            if due is not None and due[0] <= time.monotonic():
                retries.remove(due)
                _, snapshot, context = due
            else:
                timeout = max(0, due[0] - time.monotonic()) if due else None
                try:
                    job = self.jobs.get(timeout=timeout)
                except queue.Empty:
                    continue
                if job is None:
                    closing = True
                    continue
                snapshot, context = job
            try:
                data, changes = self.store.record_match(snapshot)
            except Exception as exc:
                self.completed.put((context, None, None, type(exc).__name__))
                if isinstance(exc, retryable):
                    retries.append((time.monotonic() + 3, snapshot, context))
            else:
                self.completed.put((context, data, changes, None))

    def close(self):
        self.jobs.put(None)
        self.worker.join(timeout=10)
        if self.worker.is_alive():
            self.stopping.set()
            self.jobs.put(None)
            self.worker.join(timeout=6)
