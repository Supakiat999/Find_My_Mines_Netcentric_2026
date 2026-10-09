"""Independent rooms over real TCP, including lifecycle and authority checks."""

import copy
import socket
import time

from helpers import ServerRunner, Wire, ok, protocol, wait
import server


def lobby(port, name):
    wire = Wire(port)
    wire.send(protocol.JOIN, nickname=name)
    assert wait(lambda: wire.get(protocol.WELCOME), "lobby identity")["role"] == "lobby"
    return wire


def create(wire, name, mode="classic", **settings):
    wire.forget(protocol.WELCOME)
    wire.send(protocol.CREATE_ROOM, name=name, mode=mode, custom=settings.pop("custom", {}),
              bot_level=settings.pop("bot_level", "off"), ranked=settings.pop("ranked", True))
    welcome = wait(lambda: wire.get(protocol.WELCOME), "room creation")
    assert welcome["role"] == "player"
    return welcome["room_id"]


def join(wire, room_id, watch=False):
    wire.forget(protocol.WELCOME)
    wire.send(protocol.JOIN_ROOM, room_id=room_id, watch=watch)
    return wait(lambda: wire.get(protocol.WELCOME), "room join")


with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
run = ServerRunner(port)
wires = []
try:
    a, b, c, d, watcher = [lobby(port, name) for name in ("A", "B", "C", "D", "Watcher")]
    wires = [a, b, c, d, watcher]
    invalid = Wire(port)
    wires.append(invalid)
    invalid.send(protocol.JOIN, nickname="Injected\nName")
    wait(lambda: invalid.get(protocol.ERROR), "invalid nickname rejection")
    assert not run.srv.rooms
    first = create(a, "First")
    join(b, first)
    second = create(c, "Second", "radius2")
    join(d, second)
    wait(lambda: all(room.game.phase == "playing" for room in run.srv.rooms.values()), "two concurrent games")
    room_a, room_b = run.srv.rooms[first], run.srv.rooms[second]
    assert room_a.game is not room_b.game
    assert room_a.turn_deadline and room_b.turn_deadline
    assert join(watcher, first, watch=True)["role"] == "spectator"
    ok(1, "clients start in lobby; two independent games and explicit spectator membership")

    state_b = copy.deepcopy(room_b.game.board_view())
    picks_before = len(room_a.game.revealed)
    current = room_a.game.current_turn
    player = a if a.get(protocol.WELCOME)["client_id"] == current else b
    cell = next(room_a.game.cells())
    player.send(protocol.PICK, room_id=second, row=cell[0], col=cell[1])
    wait(lambda: player.get(protocol.ERROR), "cross-room pick rejection")
    assert len(room_a.game.revealed) == picks_before and room_b.game.board_view() == state_b
    player.send(protocol.PICK, room_id=first, row=cell[0], col=cell[1])
    wait(lambda: len(room_a.game.revealed) > picks_before, "room-local pick")
    assert room_b.game.board_view() == state_b
    a.send(protocol.CHAT, text="first room only")
    wait(lambda: any(m.get("text") == "first room only" for m in b.all(protocol.CHAT_MSG)), "room chat")
    assert not any(m.get("text") == "first room only" for m in c.all(protocol.CHAT_MSG))
    current = room_a.game.current_turn
    coach_user = a if a.get(protocol.WELCOME)["client_id"] == current else b
    coach_user.send(protocol.HINT)
    wait(lambda: coach_user.get(protocol.HINT_RESULT), "room-local coach")
    assert not c.get(protocol.HINT_RESULT) and not d.get(protocol.HINT_RESULT)
    watcher.send(protocol.PICK, row=0, col=1)
    wait(lambda: watcher.get(protocol.ERROR), "spectator action rejection")
    watcher.forget(protocol.ERROR)
    watcher.send(protocol.LEAVE_ROOM, room_id=second)
    wait(lambda: watcher.get(protocol.ERROR), "stale leave rejection")
    assert run.srv._client(watcher.get(protocol.WELCOME)["client_id"]).room_id == first
    ok(2, "moves and chat stay room-local; cross-room and spectator actions are rejected")

    current = room_b.game.current_turn
    clock_a = room_a.turn_deadline
    run.call(lambda: setattr(room_b, "turn_deadline", time.time() - 1))
    wait(lambda: room_b.game.current_turn != current, "second room timeout")
    assert room_a.turn_deadline == clock_a
    run.srv.selected_room_id = first
    run.call(run.srv.reset_all)
    assert room_b.game.board_view() == state_b and room_b.game.current_turn != current
    a.send(protocol.SET_MODE, mode="cube")
    wait(lambda: a.get(protocol.ERROR), "immutable setting rejection")
    assert room_a.game.mode == "classic"
    ok(3, "clocks and admin reset are independent; room settings cannot change during play")

    def finish_first():
        for bomb in list(room_a.game.bombs):
            room_a._apply_pick(room_a.game.current_turn, bomb)

    run.call(finish_first)
    wait(lambda: a.get(protocol.MATCH_END) and b.get(protocol.MATCH_END), "room-local result")
    assert not c.get(protocol.MATCH_END) and room_b.game.phase == "playing"
    a.send(protocol.REMATCH)
    wait(lambda: a.get(protocol.WELCOME)["client_id"] in room_a.rematch_votes, "first rematch vote")
    assert room_a.game.phase == "ended"
    b.send(protocol.REMATCH)
    wait(lambda: room_a.game.phase == "playing", "room-local rematch")
    assert room_b.game.board_view() == state_b
    ok("3b", "coach replies, results and rematch votes are isolated between rooms")

    token = a.get(protocol.WELCOME)["token"]
    old_id = a.get(protocol.WELCOME)["client_id"]
    replacement = Wire(port)
    wires.append(replacement)
    replacement.send(protocol.JOIN, nickname="A", token=token)
    restored = wait(lambda: replacement.get(protocol.WELCOME), "room reconnect")
    assert restored["room_id"] == first and restored["reconnected"]
    assert restored["client_id"] in room_a.game.players and old_id not in room_a.game.players
    a = replacement
    a.send(protocol.LEAVE_ROOM)
    wait(lambda: a.get(protocol.WELCOME)["role"] == "lobby", "leave to lobby")
    assert room_a.game.phase == "waiting" and room_b.game.phase == "playing"
    b.send(protocol.LEAVE_ROOM)
    watcher.send(protocol.LEAVE_ROOM)
    wait(lambda: first not in run.srv.rooms, "empty room cleanup")
    ok(4, "reconnect restores original room; leaving interrupts only that game and cleans empty rooms")

    a.forget(protocol.ERROR)
    a.send(protocol.CREATE_ROOM, name="", mode="classic", custom={}, bot_level="off", ranked=True)
    wait(lambda: a.get(protocol.ERROR), "invalid room name")
    assert a.get(protocol.WELCOME)["role"] == "lobby"
    a.forget(protocol.ERROR)
    a.send(protocol.CREATE_ROOM, name="Invalid", mode="custom", custom={"size": None},
           bot_level="off", ranked=True)
    wait(lambda: a.get(protocol.ERROR), "invalid custom input")
    custom = create(a, "Custom", "custom", custom={"size": 500, "bombs": 9999})
    assert run.srv.rooms[custom].game.dims == (10, 10)
    summary = next(r for r in a.get(protocol.ROOMS)["rooms"] if r["id"] == custom)
    assert not summary["rated"]
    a.send(protocol.LEAVE_ROOM)
    wait(lambda: a.get(protocol.WELCOME)["role"] == "lobby", "leave custom")
    bot = create(a, "Bot", bot_level="medium")
    wait(lambda: run.srv.rooms[bot].game.phase == "playing", "immediate bot match")
    assert server.BOT_ID in run.srv.rooms[bot].game.players
    assert join(b, bot, watch=True)["role"] == "spectator"
    assert run.srv.rooms[bot].game.players == [a.get(protocol.WELCOME)["client_id"], server.BOT_ID]
    ok(5, "creation validates settings, clamps custom boards and keeps fixed bot rooms unrated")

    ui = server.ServerUI(run.srv)
    run.call(ui.draw)
    assert run.srv.selected_room is not None
    ok(6, "admin console renders a selected room without a global Game")
finally:
    for wire in wires:
        wire.close()
    run.stop()
