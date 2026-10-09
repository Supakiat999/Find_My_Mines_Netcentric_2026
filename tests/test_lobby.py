"""Headless client checks for room selection and fixed room settings."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
from helpers import ok
import client
import protocol


class Net:
    status = "connected"
    inbox = None
    def __init__(self): self.sent = []
    def send(self, kind, **payload): self.sent.append((kind, payload))
    def close(self): pass


def event(pos):
    return pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos)


ui = client.ClientUI()
ui.net.close()
ui.net = Net()
ui.nickname = "Ada"
ui._handle({"type": protocol.WELCOME, "client_id": 1, "token": "t",
            "role": "lobby", "room_id": None, "message": "Welcome"})
assert ui.screen_name == client.SCREEN_LOBBY
assert not ui.rules_open
assert ui.net.sent[-1][0] == protocol.LIST_ROOMS
ui.rooms = [{"id": "r1", "name": "Test", "mode": "classic",
             "mode_label": "Classic", "ranked": True, "rated": True,
             "bot_level": "off", "phase": "waiting", "players": 1,
             "spectators": 0, "capacity": 2, "joinable": True}]
ui.draw()
_, join, watch = ui._room_rects(0)
ui._on_lobby_event(event(join.center))
assert ui.net.sent[-1] == (protocol.JOIN_ROOM, {"room_id": "r1", "watch": False})
ui.screen_name = client.SCREEN_CREATE_ROOM
ui.room_name = "Draft"
ui.create_mode = "custom"
ui.create_custom["size"] += 1
ui._on_create_event(event(pygame.Rect(430, client.WIN_H - 100, 150, 42).center))
kind, payload = ui.net.sent[-1]
assert kind == protocol.CREATE_ROOM and payload["name"] == "Draft"
assert payload["custom"]["size"] == ui.create_custom["size"]
assert payload["ranked"] is False
ui._handle({"type": protocol.ERROR, "message": "Name unavailable"})
ui.draw()
assert ui.screen_name == client.SCREEN_CREATE_ROOM and ui.room_name == "Draft"
assert ui.room_error == "Name unavailable"

ui.chat_lines = [{"text": "prior room"}]
ui.hint = {"cell": (0, 0)}
ui._handle({"type": protocol.WELCOME, "client_id": 1, "role": "player",
            "room_id": "r1", "dims": [6, 6], "message": "Joined"})
assert ui.screen_name == client.SCREEN_GAME and ui.room_id == "r1"
assert ui.rules_open
ui._close_rules()
ui._handle({"type": protocol.CHAT_MSG, "room_id": "old", "text": "stale"})
assert ui.chat_lines == []
ui.state = {"phase": "playing", "ranked": True, "board": [[None]], "dims": [1, 1]}
ui._on_game_event(event(ui.mode_rects[0][1].center))
assert all(kind not in (protocol.SET_MODE, protocol.SET_CUSTOM, protocol.SET_RANKED,
                        protocol.SET_BOT) for kind, _ in ui.net.sent)
ui._on_game_event(event(ui.leave_rect.center))
assert ui.net.sent[-1] == (protocol.LEAVE_ROOM, {"room_id": "r1"})
ui._handle({"type": protocol.ROOM_LEFT, "room_id": "r1"})
assert ui.screen_name == client.SCREEN_LOBBY and ui.room_id is None
assert not ui.rules_open and not ui.particles and not ui.flag_times
assert not ui.chat_lines and ui.hint is None and ui.state is None
ui.net.close()
pygame.quit()
ok(1, "lobby joins, creation drafts, immutable settings, stale events and leave reset work")
