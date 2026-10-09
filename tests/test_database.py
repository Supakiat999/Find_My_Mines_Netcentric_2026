"""PostgreSQL checks use a temporary schema, never the live game tables."""

import copy
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from helpers import ROOT, ok
import stats


def snapshot(mode="classic", ranked=True, names=("Alice", "Bob"), draw=False, bot=False):
    parts = []
    for seat, name in enumerate(names, 1):
        parts.append({"seat": seat, "name": name, "bot_level": "medium" if bot and seat == 2 else None,
                      "result": "draw" if draw else "win" if seat == 1 else "loss",
                      "score": 2 if draw else 3 if seat == 1 else 1,
                      "picks": 4, "hits": 2, "best_chain": 2})
    return {"id": str(uuid.uuid4()), "mode": mode, "settings": {"dims": [6, 6]}, "ranked": ranked,
            "started_at": "2026-01-01T00:00:00+00:00", "ended_at": "2026-01-01T00:01:00+00:00",
            "participants": parts,
            "results": [{"name": p["name"], "result": p["result"], "points": p["score"]}
                        for p in parts if p["bot_level"] is None]}


def main():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        print("SKIP PostgreSQL checks: set TEST_DATABASE_URL to a test database owner URL")
        return

    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from database import Store

    schema = "test_database_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            test_url = make_conninfo(url, options="-c search_path=" + schema)
            with psycopg.connect(test_url) as conn:
                conn.execute(Path(ROOT, "db/001_initial.sql").read_text())
            store = Store(test_url)
            seed = stats.Leaderboard(None)
            seed.record_match([{"name": "ImportedA", "result": "win", "points": 6},
                               {"name": "ImportedB", "result": "loss", "points": 5}])
            assert store.import_data(seed.data)
            assert store.load() == seed.data
            assert not store.import_data(seed.data)
            ok(1, "JSON-shaped records preserve totals, Elo, peaks and placements; import retry is safe")

            expected = stats.Leaderboard(None)
            expected.data = copy.deepcopy(seed.data)
            games = [snapshot() for _ in range(6)]
            games += [snapshot(mode=m) for m in ("radius2", "sweeper", "cube")]
            games += [snapshot(ranked=False), snapshot(mode="custom"), snapshot(draw=True),
                      snapshot(mode="sweeper", names=("Alice", "Computer (Medium)"), bot=True)]
            for game in games:
                expected_changes = expected.record_match(game["results"], game["mode"], game["ranked"])
                data, changes = store.record_match(game)
                assert data == expected.data
                assert json.loads(json.dumps(changes)) == json.loads(json.dumps(expected_changes))
                retry_data, retry_changes = store.record_match(game)
                assert retry_data == data
                assert json.loads(json.dumps(retry_changes)) == json.loads(json.dumps(changes))
            assert "Computer (Medium)" not in store.load()
            ok(2, "rating parity across modes, placements, streaks, casual and bots; retries preserve saved Elo payload")

            before = store.load()
            conflict = copy.deepcopy(games[0])
            conflict["settings"]["bombs"] = 12
            try:
                store.record_match(conflict)
            except ValueError:
                pass
            else:
                raise AssertionError("conflicting match UUID accepted")
            broken = snapshot(names=("RollbackA", "RollbackB"))
            broken["participants"][0]["hits"] = 99
            try:
                store.record_match(broken)
            except psycopg.errors.CheckViolation:
                pass
            else:
                raise AssertionError("invalid participant accepted")
            assert store.load() == before
            with psycopg.connect(test_url) as conn:
                assert conn.execute("SELECT count(*) FROM matches WHERE id=%s", (broken["id"],)).fetchone()[0] == 0
            try:
                store.import_data(before)
            except ValueError:
                pass
            else:
                raise AssertionError("import overwrote played records")
            ok(3, "invalid writes roll back every table; conflicting retries and destructive imports are rejected")

            games = [snapshot(names=("ConcurrentA", "ConcurrentB"), draw=True) for _ in range(2)]
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(store.record_match, games))
            data = store.load()
            for name in ("ConcurrentA", "ConcurrentB"):
                assert data[name]["matches"] == 2
                assert data[name]["draws"] == 2
                assert data[name]["mode_matches"]["classic"] == 2
            ok(4, "concurrent transactions do not lose player or rating updates")

            with psycopg.connect(test_url) as conn:
                try:
                    with conn.transaction():
                        conn.execute("INSERT INTO players (nickname, points) VALUES ('Invalid', -1)")
                except psycopg.errors.CheckViolation:
                    pass
                else:
                    raise AssertionError("negative totals accepted")
                rows = conn.execute("SELECT match_id, count(*) FROM match_participants GROUP BY match_id").fetchall()
                assert rows and all(count == 2 for _, count in rows)
            ok(5, "schema rejects negative totals; every recorded match has two seats")

            from seed.seed import snapshots
            mock_matches = list(snapshots())
            assert mock_matches == list(snapshots())
            assert {s["mode"] for s in mock_matches} == {"classic", "radius2", "sweeper", "cube", "custom"}
            for match in mock_matches:
                store.record_match(match)
            seeded = store.load()
            for match in snapshots():
                store.record_match(match)
            assert store.load() == seeded
            assert all(seeded[name] == row for name, row in data.items())
            assert len([name for name in seeded if name.startswith("Mock_")]) == 4
            with psycopg.connect(test_url) as conn:
                count = conn.execute("SELECT count(*) FROM matches WHERE id = ANY(%s::uuid[])",
                                     ([s["id"] for s in mock_matches],)).fetchone()[0]
                assert count == len(mock_matches)
            ok(6, "seed covers all modes, preserves existing players and is deterministic and safe to rerun")
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    main()
