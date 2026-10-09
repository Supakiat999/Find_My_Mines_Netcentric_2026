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
            first_room = runner.srv.selected_room
            first_match = first_room.match_id

            original = runner.srv.board.store.record_match
            attempts = []

            def delayed(snapshot):
                attempts.append(snapshot["id"])
                if snapshot["id"] == first_match and attempts.count(first_match) == 1:
                    raise psycopg.OperationalError("simulated connection loss")
                if snapshot["id"] == first_match:
                    assert release.wait(10), "test did not release the database worker"
                return original(snapshot)

            runner.srv.board.store.record_match = delayed

            def finish(room):
                game = room.game
                for cell in list(game.bombs):
                    room._apply_pick(game.current_turn, cell)

            runner.call(finish, first_room)
            wait(lambda: first_room.save_error, "database failure notice")
            assert first_room.pending_match is not None
            assert not runner.srv.board.data
            runner.call(runner.srv.reset_all)
            runner.call(first_room._begin_match)
            assert runner.game.phase == "ended"
            alice.send(protocol.CHAT, text="Still connected")
            wait(lambda: any(m.get("text") == "Still connected"
                             for m in bob.all(protocol.CHAT_MSG)), "chat during database outage")
            ok(1, "database outage pauses new matches, preserves cache and keeps networking responsive")

            charlie, dana = Wire(port), Wire(port)
            clients += [charlie, dana]
            for wire, name in ((charlie, "Charlie"), (dana, "Dana")):
                wire.send(protocol.JOIN, nickname=name)
                wait(lambda: wire.get(protocol.WELCOME), "second room lobby identity")
            charlie.forget(protocol.WELCOME)
            charlie.send(protocol.CREATE_ROOM, name="Other room", mode="classic", custom={},
                         bot_level="off", ranked=True)
            second_id = wait(lambda: charlie.get(protocol.WELCOME), "second room creation")["room_id"]
            dana.forget(protocol.WELCOME)
            dana.send(protocol.JOIN_ROOM, room_id=second_id, watch=False)
            wait(lambda: dana.get(protocol.WELCOME), "second room membership")
            second_room = runner.srv.rooms[second_id]
            wait(lambda: second_room.game.phase == "playing", "second room start")
            runner.call(finish, second_room)
            wait(lambda: charlie.get(protocol.MATCH_END), "other room commits before retry", 2)
            assert first_room.pending_match is not None
            assert second_room.pending_match is None
            assert set(runner.srv.board.data) == {"Charlie", "Dana"}
            assert not alice.get(protocol.MATCH_END)
            ok("1b", "a room saves and announces its result while another room awaits a database retry")

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
            wait(lambda: first_room.pending_match is None, "transaction retry", 10)
            assert attempts.count(first_match) == 2
            assert sum(row["matches"] for row in runner.srv.board.data.values()) == 4
            announcement = wait(lambda: alice.get(protocol.MATCH_END), "committed match announcement")
            assert next(p["id"] for p in announcement["players"] if p["name"] == "Bob") == recovered["client_id"]
            with psycopg.connect(test_url) as conn:
                assert conn.execute("SELECT count(*) FROM matches").fetchone()[0] == 2
                assert conn.execute("SELECT count(*) FROM match_participants").fetchone()[0] == 4
            ok(2, "server retries one match ID; pending reconnects preserve result IDs and joins cannot change seats")

            for client in clients:
                client.close()
            clients = []
            runner.stop()
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            runner = ServerRunner(port)
            assert sum(row["matches"] for row in runner.srv.board.data.values()) == 4
            assert not runner.srv.rooms
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
