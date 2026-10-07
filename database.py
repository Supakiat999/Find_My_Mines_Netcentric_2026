"""Transactional PostgreSQL storage; game rules remain in stats.py."""

import json
from datetime import datetime
from uuid import UUID

import stats


class Store:
    def __init__(self, url):
        self.url = url

    def _connect(self):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        options = conninfo_to_dict(self.url).get("options", "")
        return psycopg.connect(self.url, connect_timeout=5,
                               options=options + " -c statement_timeout=5000 -c lock_timeout=5000")

    def load(self):
        with self._connect() as conn, conn.cursor() as cur:
            return self._load_tx(cur)

    @staticmethod
    def _load_tx(cur):
        cur.execute("""
            SELECT p.nickname, p.matches, p.wins, p.losses, p.draws,
                   p.points, p.streak, p.best_streak,
                   r.mode, r.elo, r.peak_elo, r.ranked_matches
            FROM players p LEFT JOIN player_ratings r ON r.player_id = p.id
            ORDER BY p.nickname, r.mode
        """)
        board = stats.Leaderboard(None)
        for row in cur.fetchall():
            name = row[0]
            record = board._row(name)
            record.update(zip(stats.FIELDS, row[1:8]))
            mode, elo, peak, count = row[8:]
            if mode is not None:
                record["elo"][mode] = elo
                record["peak_elo"][mode] = peak
                record["mode_matches"][mode] = count
        return board.data

    @staticmethod
    def _validate(payload):
        parts, results = payload["participants"], payload["results"]
        if len(parts) != 2 or {p["seat"] for p in parts} != {1, 2}:
            raise ValueError("a completed match requires seats 1 and 2")
        outcomes = sorted(p["result"] for p in parts)
        if outcomes not in (["loss", "win"], ["draw", "draw"]):
            raise ValueError("inconsistent match outcomes")
        if outcomes == ["draw", "draw"]:
            if parts[0]["score"] != parts[1]["score"]:
                raise ValueError("a draw requires equal scores")
        elif next(p["score"] for p in parts if p["result"] == "win") <= next(
                p["score"] for p in parts if p["result"] == "loss"):
            raise ValueError("winner must have the higher score")
        humans = [{"name": p["name"], "result": p["result"], "points": p["score"]}
                  for p in parts if p["bot_level"] is None]
        if not humans or len({p["name"] for p in humans}) != len(humans):
            raise ValueError("a match requires distinct human players")
        if sorted(humans, key=lambda p: p["name"]) != sorted(results, key=lambda p: p["name"]):
            raise ValueError("human results do not match participants")

    def record_match(self, snapshot):
        from psycopg.types.json import Jsonb
        payload = json.loads(json.dumps(snapshot))
        match_id = UUID(payload["id"])
        self._validate(payload)
        with self._connect() as conn, conn.cursor() as cur:
            # A retry must wait for the original transaction before checking its UUID.
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(match_id),))
            cur.execute("SELECT input_snapshot, rating_changes FROM matches WHERE id = %s", (match_id,))
            existing = cur.fetchone()
            if existing:
                if existing[0] != payload:
                    raise ValueError("match id already recorded with different input")
                return self._load_tx(cur), existing[1]

            ids = {}
            for name in sorted(r["name"] for r in payload["results"]):
                cur.execute("INSERT INTO players (nickname) VALUES (%s) ON CONFLICT DO NOTHING", (name,))
                cur.execute("SELECT id FROM players WHERE nickname = %s FOR UPDATE", (name,))
                ids[name] = cur.fetchone()[0]
            board = stats.Leaderboard(None)
            board.data = self._load_tx(cur)
            changes = board.record_match(payload["results"], payload["mode"], payload["ranked"])
            cur.execute("""
                INSERT INTO matches (id, mode, settings, ranked_requested, rated,
                                     started_at, ended_at, input_snapshot, rating_changes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (match_id, payload["mode"], Jsonb(payload["settings"]), payload["ranked"],
                  bool(changes), datetime.fromisoformat(payload["started_at"]),
                  datetime.fromisoformat(payload["ended_at"]), Jsonb(payload), Jsonb(changes)))
            for name, player_id in ids.items():
                row = board.data[name]
                cur.execute("""
                    UPDATE players SET matches=%s, wins=%s, losses=%s, draws=%s,
                                       points=%s, streak=%s, best_streak=%s WHERE id=%s
                """, (*(row[f] for f in stats.FIELDS), player_id))
                self._write_ratings(cur, player_id, row)
            for part in payload["participants"]:
                change = changes.get(part["name"], {}) if part["bot_level"] is None else {}
                cur.execute("""
                    INSERT INTO match_participants
                        (match_id, seat, player_id, nickname, bot_level, result,
                         score, picks, hits, best_chain, elo_before, elo_after, streak_bonus)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (match_id, part["seat"], ids.get(part["name"]) if part["bot_level"] is None else None,
                      part["name"], part["bot_level"], part["result"], part["score"], part["picks"],
                      part["hits"], part["best_chain"], change.get("before"), change.get("after"),
                      change.get("streak_bonus", 0)))
            return board.data, changes

    @staticmethod
    def _write_ratings(cur, player_id, row):
        for mode in row["elo"]:
            cur.execute("""
                INSERT INTO player_ratings (player_id, mode, elo, peak_elo, ranked_matches)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (player_id, mode) DO UPDATE SET
                    elo=excluded.elo, peak_elo=excluded.peak_elo, ranked_matches=excluded.ranked_matches
            """, (player_id, mode, row["elo"][mode], row["peak_elo"][mode], row["mode_matches"][mode]))

    def import_data(self, data):
        """Import normalized Leaderboard data; never overwrite newer records."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("LOCK TABLE players, matches IN EXCLUSIVE MODE")
            existing = self._load_tx(cur)
            cur.execute("SELECT EXISTS (SELECT 1 FROM matches)")
            if cur.fetchone()[0]:
                raise ValueError("cannot import after matches have been recorded")
            if existing:
                if existing == data:
                    return False
                raise ValueError("database already contains different player records")
            for name, row in data.items():
                cur.execute("""
                    INSERT INTO players (nickname, matches, wins, losses, draws, points, streak, best_streak)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                """, (name, *(row[f] for f in stats.FIELDS)))
                self._write_ratings(cur, cur.fetchone()[0], row)
            return True
