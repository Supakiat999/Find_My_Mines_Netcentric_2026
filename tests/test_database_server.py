"""Exercise server persistence without touching the configured game database."""

import os
import threading
import uuid
from pathlib import Path

from helpers import ServerRunner, Wire, config, ok, protocol, socket, wait


def main():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        print("SKIP PostgreSQL server checks: set TEST_DATABASE_URL to a test database owner URL")
        return

    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    schema = "test_server_" + uuid.uuid4().hex
    runner = None
    clients = []
    release = threading.Event()
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            test_url = make_conninfo(url, options="-c search_path=" + schema)
            with psycopg.connect(test_url) as conn:
                conn.execute(Path(__file__).resolve().parents[1].joinpath(
                    "db/001_initial.sql").read_text())
                if conn.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='runtime')").fetchone()[0]:
                    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO runtime").format(sql.Identifier(schema)))
                    conn.execute("GRANT SELECT, INSERT, UPDATE ON players, player_ratings TO runtime")
                    conn.execute("GRANT SELECT, INSERT ON matches, match_participants TO runtime")
                    conn.execute(sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA {} TO runtime").format(
                        sql.Identifier(schema)))
                    test_url = make_conninfo(url, options="-c search_path=" + schema + " -c role=runtime")
            config.DATABASE_URL = test_url
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            runner = ServerRunner(port)
            alice, bob = Wire(port, "Alice"), Wire(port, "Bob")
            clients = [alice, bob]
            wait(lambda: runner.game.phase == "playing", "match start")

            original = runner.srv.board.store.record_match
            attempts = []

            def delayed(snapshot):
                attempts.append(snapshot["id"])
                if len(attempts) == 1:
                    raise psycopg.OperationalError("simulated connection loss")
                assert release.wait(10), "test did not release the database worker"
                return original(snapshot)

            runner.srv.board.store.record_match = delayed

            def finish():
                game = runner.game
                for cell in list(game.bombs):
                    runner.srv._apply_pick(game.current_turn, cell)

            runner.call(finish)
            wait(lambda: runner.srv._save_error, "database failure notice")
            assert runner.srv.pending_match is not None
            assert not runner.srv.board.data
            runner.call(runner.srv.reset_all)
            runner.call(runner.srv._begin_match)
            assert runner.game.phase == "ended"
            alice.send(protocol.CHAT, text="Still connected")
            wait(lambda: any(m.get("text") == "Still connected"
                             for m in bob.all(protocol.CHAT_MSG)), "chat during database outage")
            ok(1, "database outage pauses new matches, preserves cache and keeps networking responsive")

            welcome = wait(lambda: bob.get(protocol.WELCOME), "Bob's reconnect token")
            bob2 = Wire(port, "Bob", token=welcome["token"])
            clients.append(bob2)
            recovered = wait(lambda: bob2.get(protocol.WELCOME), "reconnect while saving")
            assert recovered["reconnected"]
            watcher = Wire(port, "Watcher")
            clients.append(watcher)
            assert wait(lambda: watcher.get(protocol.WELCOME), "spectator during save")["role"] == "spectator"
            assert runner.game.phase == "ended"

            release.set()
            wait(lambda: runner.srv.pending_match is None, "transaction retry", 10)
            assert attempts[0] == attempts[1]
            assert sum(row["matches"] for row in runner.srv.board.data.values()) == 2
            announcement = wait(lambda: alice.get(protocol.MATCH_END), "committed match announcement")
            assert next(p["id"] for p in announcement["players"] if p["name"] == "Bob") == recovered["client_id"]
            with psycopg.connect(test_url) as conn:
                assert conn.execute("SELECT count(*) FROM matches").fetchone()[0] == 1
                assert conn.execute("SELECT count(*) FROM match_participants").fetchone()[0] == 2
            ok(2, "server retries one match ID; pending reconnects preserve result IDs and joins cannot change seats")

            for client in clients:
                client.close()
            clients = []
            runner.stop()
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            runner = ServerRunner(port)
            assert sum(row["matches"] for row in runner.srv.board.data.values()) == 2
            ok(3, "server restart reloads PostgreSQL statistics")
        finally:
            release.set()
            for client in clients:
                client.close()
            if runner is not None:
                runner.stop()
            config.DATABASE_URL = None
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    main()
