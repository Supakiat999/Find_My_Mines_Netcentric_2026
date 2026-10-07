"""Add deterministic mock matches through the same storage path as the server."""

import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import game
from database import Store


def snapshots():
    names = ("Mock_Ada", "Mock_Ben", "Mock_Cleo", "Mock_Dan")
    modes = ("classic", "radius2", "sweeper", "cube") * 2
    scenarios = [(mode, names[i % 4], names[(i + 1) % 4], None, True)
                 for i, mode in enumerate(modes)]
    scenarios += [("classic", names[0], names[1], None, False),
                  ("custom", names[2], names[3], None, False),
                  ("sweeper", names[0], "Computer (Medium)", "medium", False)]
    for index, (mode, first, second, bot_level, ranked) in enumerate(scenarios):
        rng = random.Random(2026 + index)
        match = game.Game(mode, rng=rng)
        match.seat_players([1, 2])
        assert match.start_match(first_player=1)
        while match.phase == game.PHASE_PLAYING:
            covered = [cell for cell in match.cells() if cell not in match.revealed]
            assert match.pick(match.current_turn, rng.choice(covered))["ok"]

        participants = []
        for seat, name in enumerate((first, second), 1):
            stat = match.stats[seat]
            participants.append({
                "seat": seat, "name": name,
                "bot_level": bot_level if seat == 2 else None,
                "result": ("draw" if match.last_winner is None else
                           "win" if seat == match.last_winner else "loss"),
                "score": match.scores[seat], "picks": stat["picks"],
                "hits": stat["kept"], "best_chain": stat["best_chain"],
            })
        started = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index * 30)
        yield {
            "id": str(uuid5(NAMESPACE_URL, "find-my-mines/mock/v1/" + str(index))),
            "mode": mode, "ranked": ranked,
            "started_at": started.isoformat(),
            "ended_at": (started + timedelta(minutes=2)).isoformat(),
            "settings": {"dims": list(match.dims), "bombs": match.bomb_count,
                         "turn_seconds": match.turn_seconds, "weighted": match.hints_weighted,
                         "bombs_are_bad": match.bombs_are_bad},
            "participants": participants,
            "results": [{"name": p["name"], "result": p["result"], "points": p["score"]}
                        for p in participants if p["bot_level"] is None],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        parser.error("set DATABASE_URL before seeding")
    store = Store(url)
    count = 0
    for snapshot in snapshots():
        store.record_match(snapshot)
        count += 1
    print("Applied %d mock matches for 4 Mock_ players; reruns do not duplicate results." % count)


if __name__ == "__main__":
    main()
