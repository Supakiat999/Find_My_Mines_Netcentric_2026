"""A failed room save must not stall unrelated room saves."""

from unittest.mock import patch

from helpers import ok
import psycopg
import stats


class Store:
    def __init__(self, url):
        self.attempts = {}

    def load(self):
        return {}

    def record_match(self, snapshot):
        key = snapshot["id"]
        self.attempts[key] = self.attempts.get(key, 0) + 1
        if key == "retry" and self.attempts[key] == 1:
            raise psycopg.OperationalError("temporary outage")
        if key == "invalid":
            raise ValueError("invalid result")
        return {key: "saved"}, {key: "rated"}


with patch("database.Store", Store):
    board = stats.DatabaseLeaderboard("unused")
    try:
        board.submit({"id": "retry"}, "room-a")
        board.submit({"id": "healthy"}, "room-b")
        context, _, _, error = board.completed.get(timeout=1)
        assert context == "room-a" and error == "OperationalError"
        context, data, _, error = board.completed.get(timeout=1)
        assert context == "room-b" and data == {"healthy": "saved"} and error is None
        context, _, _, error = board.completed.get(timeout=4)
        assert context == "room-a" and error is None
        board.submit({"id": "invalid"}, "room-a")
        board.submit({"id": "another"}, "room-b")
        assert board.completed.get(timeout=1)[3] == "ValueError"
        context, _, _, error = board.completed.get(timeout=1)
        assert context == "room-b" and error is None
        assert board.data == {}
        board.submit({"id": "shutdown"}, "room-b")
        ok(1, "room save errors do not block other rooms, retries retain context and cache stays main-thread owned")
    finally:
        board.close()
    assert not board.worker.is_alive()
    assert board.store.attempts["shutdown"] == 1
