"""New server features: chat, AI coach, computer opponent, stats, reconnect."""

import json
import os
import tempfile
import time

from helpers import ServerRunner, Wire, ok, wait
import config

config.TURN_SECONDS = 60
config.RECONNECT_GRACE = 2
config.BOT_THINK_SECONDS = (0.02, 0.06)      # quick, so whole matches finish
import ai
import botbrain
import game as g
import protocol
import stats as stats_mod

botbrain.preload()          # load the models now, not in the middle of a match
PORT = 55602
run = ServerRunner(PORT)
srv, game = run.srv, run.game


def open_slot():
    return next(c for c in game.cells() if c not in game.revealed)


def pick(wire, cell):
    wire.send(protocol.PICK, row=cell[0], col=cell[1])


def table(a="Alice", b="Bob"):
    """A fresh two-person table, returned as ({id: wire}, alice, bob)."""
    x, y = Wire(PORT, a), Wire(PORT, b)
    wait(lambda: game.phase == g.PHASE_PLAYING and len(game.players) == 2,
         "a match at the new table")
    ids = {x.get(protocol.WELCOME)["client_id"]: x,
           y.get(protocol.WELCOME)["client_id"]: y}
    return ids, x, y


def leave(*wires):
    for w in wires:
        w.close()
    wait(lambda: not srv._joined_clients(), "everyone to be gone", 8)
    time.sleep(0.2)


# =====================================================================
# 1. chat
# =====================================================================
ids, alice, bob = table()
spec = Wire(PORT, "Spec")
wait(lambda: spec.get(protocol.WELCOME), "spectator")
alice.send(protocol.CHAT, text="hello there")
for w in (alice, bob, spec):
    m = wait(lambda w=w: [x for x in w.all(protocol.CHAT_MSG)
                          if x["text"] == "hello there"], "chat delivery")[0]
    assert m["name"] == "Alice" and not m["system"]
ok(1, "chat reaches both players and the spectator, tagged with the sender")

time.sleep(config_gap := 0.7)
bob.send(protocol.CHAT, text="x" * 500)
long_msg = wait(lambda: [m for m in alice.all(protocol.CHAT_MSG)
                         if m["name"] == "Bob"], "long message")[-1]
assert len(long_msg["text"]) == 120
time.sleep(0.7)
bob.send(protocol.CHAT, text="   \n\t  ")
time.sleep(0.4)
assert len([m for m in alice.all(protocol.CHAT_MSG) if m["name"] == "Bob"]) == 1
time.sleep(0.7)
alice.send(protocol.CHAT, text="one")
alice.send(protocol.CHAT, text="two")             # straight after: too fast
err = wait(lambda: alice.get(protocol.ERROR), "rate-limit reply")
assert "slow down" in err["message"]
assert not [m for m in bob.all(protocol.CHAT_MSG) if m["text"] == "two"]
ok(2, "chat: long messages trimmed to 120, blanks ignored, spamming refused")

late = Wire(PORT, "Late")
welcome = wait(lambda: late.get(protocol.WELCOME), "late joiner")
texts = [m["text"] for m in welcome["chat"]]
assert "hello there" in texts and any("joined" in t for t in texts)
raw = Wire(PORT)                                  # connected but never joined
raw.send(protocol.CHAT, text="I never joined")
time.sleep(0.4)
assert not [m for m in alice.all(protocol.CHAT_MSG) if "never joined" in m["text"]]
ok(3, "late joiners get the recent history; someone who has not joined cannot talk")
leave(alice, bob, spec, late, raw)

# =====================================================================
# 2. the AI coach
# =====================================================================
ids, alice, bob = table()
who = ids[game.current_turn]
other = next(w for w in ids.values() if w is not who)
other.forget(protocol.ERROR)
other.send(protocol.HINT)
assert "own turn" in wait(lambda: other.get(protocol.ERROR), "wrong-turn reply")["message"]

# open a few slots so there is something to reason about
for _ in range(6):
    if game.phase != g.PHASE_PLAYING:
        break
    turn = ids[game.current_turn]
    before = len(game.revealed)
    pick(turn, open_slot())
    wait(lambda: len(game.revealed) > before, "a pick")
who = ids[game.current_turn]
who.forget(protocol.HINT_RESULT)
who.send(protocol.HINT)
hint = wait(lambda: who.get(protocol.HINT_RESULT), "coach answer")
covered = [c for c in game.cells() if c not in game.revealed]
assert len(hint["heat"]) == len(covered)
assert all(0.0 <= h["p"] <= 1.0 for h in hint["heat"])
assert tuple(hint["cell"]) in covered and hint["left"] == config.HINTS_PER_MATCH - 1
assert hint["goal"] == "collect"
best = max(h["p"] for h in hint["heat"])
assert abs(hint["p"] - best) < 1e-9, "the advice is the likeliest bomb"
info = game.public_info()
again = ai.advise(info["view"], info["dims"], info["weighted"],
                  info["bombs_left"], info["bombs_are_bad"])
assert tuple(hint["cell"]) == again["cell"], "same engine, same public facts"
ok(4, "coach: only on your turn, best slot + odds for every covered slot")

for expected_left in range(config.HINTS_PER_MATCH - 2, -1, -1):
    who.forget(protocol.HINT_RESULT)
    who.send(protocol.HINT)
    assert wait(lambda: who.get(protocol.HINT_RESULT), "another answer")["left"] == expected_left
who.forget(protocol.ERROR)
who.send(protocol.HINT)
assert "no hints left" in wait(lambda: who.get(protocol.ERROR), "empty")["message"]
run.call(srv.reset_all)
wait(lambda: game.phase == g.PHASE_PLAYING, "new match")
assert game.hints_left[game.current_turn] == config.HINTS_PER_MATCH
ok(5, "coach: three questions per match, refused after that, refilled next match")

run.call(srv.set_mode, g.MODE_SWEEPER)
wait(lambda: game.phase == g.PHASE_PLAYING and game.mode == g.MODE_SWEEPER, "sweeper")
who = ids[game.current_turn]
who.forget(protocol.HINT_RESULT)
who.send(protocol.HINT)
hint = wait(lambda: who.get(protocol.HINT_RESULT), "sweeper advice")
assert hint["goal"] == "avoid"
assert abs(hint["p"] - min(h["p"] for h in hint["heat"])) < 1e-9
ok(6, "coach: in Minesweeper mode it recommends the SAFEST slot")
leave(alice, bob)
run.call(srv.set_mode, g.MODE_CLASSIC)

# =====================================================================
# 3. the computer opponent
# =====================================================================
solo = Wire(PORT, "Solo")
solo_id = wait(lambda: solo.get(protocol.WELCOME), "solo")["client_id"]
solo.send(protocol.SET_BOT, level="hard")
wait(lambda: game.players == [solo_id, -1] and game.phase == g.PHASE_PLAYING,
     "the computer to sit down and a match to start")
st = wait(lambda: solo.get(protocol.STATE), "state")
assert st["bot_seated"] and st["bot_level"] == "hard"
assert [p["bot"] for p in st["players"]] == [False, True]
assert st["players"][1]["name"] == "Computer (Hard)"
clients = solo.get(protocol.CLIENTS)
assert clients["count"] == 1 and clients["bots"][0]["name"] == "Computer (Hard)"
ok(7, "one player + 'play the computer' = a seated opponent and an instant match")

moves_by_bot = 0
guard = 0
while game.phase == g.PHASE_PLAYING and guard < 400:
    guard += 1
    if game.current_turn == solo_id:
        before = len(game.revealed)
        pick(solo, open_slot())
        wait(lambda: len(game.revealed) > before, "the human's pick")
    else:
        before = len(game.revealed)
        wait(lambda: game.current_turn == solo_id or len(game.revealed) > before
             or game.phase != g.PHASE_PLAYING, "the computer to move", 6)
        if len(game.revealed) > before:
            moves_by_bot += 1
assert game.phase == g.PHASE_ENDED and moves_by_bot > 0
end = wait(lambda: solo.get(protocol.MATCH_END), "match end")
names = {p["name"] for p in end["players"]}
assert names == {"Solo", "Computer (Hard)"} and sum(p["score"] for p in end["players"]) == 11
assert srv.board.record_of("Solo")["matches"] == 1
assert "Computer (Hard)" not in srv.board.data, "the computer is not on the leaderboard"
ok(8, "the computer plays a whole match by itself; the human is recorded, it is not")

solo.forget(protocol.STATE)
solo.send(protocol.REMATCH)
wait(lambda: game.phase == g.PHASE_PLAYING, "instant rematch (the computer always agrees)")
if end["winner_id"] is not None:
    assert game.current_turn == end["winner_id"]
ok(9, "rematch against the computer needs only the human's click; winner starts")

solo.send(protocol.SET_BOT, level="easy")
wait(lambda: srv.bot_level == "easy" and game.bombs_found == 0
     and game.phase == g.PHASE_PLAYING, "restart at the new level")
assert srv._bot_name() == "Computer (Easy)"
duo = Wire(PORT, "Duo")
duo_id = wait(lambda: duo.get(protocol.WELCOME), "second human")["client_id"]
wait(lambda: game.players == [solo_id, duo_id] and game.phase == g.PHASE_PLAYING,
     "the computer to stand up for a human")
assert not solo.get(protocol.STATE)["bot_seated"]
solo.forget(protocol.ERROR)
solo.send(protocol.SET_BOT, level="hard")
assert "already seated" in wait(lambda: solo.get(protocol.ERROR), "refusal")["message"]
ok(10, "a second human takes the computer's seat; the computer cannot be added then")

duo.close()
wait(lambda: srv.game.players == [solo_id, -1], "the computer to return", 8)
solo.send(protocol.SET_BOT, level="off")
wait(lambda: -1 not in game.players and game.phase == g.PHASE_WAITING, "computer off")
ok(11, "when the opponent leaves the computer comes back; switching it off frees the seat")
leave(solo)

# =====================================================================
# 4. the leaderboard
# =====================================================================
path = os.path.join(tempfile.gettempdir(), "find_my_mines_test_stats.json")
with open(path, "w", encoding="utf-8") as handle:       # start from empty
    handle.write("{}")
board = stats_mod.Leaderboard(path)
board.record_match([{"name": "Ann", "result": "win", "points": 7},
                    {"name": "Ben", "result": "loss", "points": 4}])
board.record_match([{"name": "Ann", "result": "win", "points": 6},
                    {"name": "Ben", "result": "loss", "points": 5}])
board.record_match([{"name": "Ann", "result": "loss", "points": 3},
                    {"name": "Ben", "result": "win", "points": 8}])
board.record_match([{"name": "Ann", "result": "draw", "points": 5},
                    {"name": "Ben", "result": "draw", "points": 5}])
top = board.top()
assert top[0]["name"] == "Ann" and top[0]["wins"] == 2 and top[0]["best_streak"] == 2
assert top[1]["name"] == "Ben" and top[1]["wins"] == 1
reloaded = stats_mod.Leaderboard(path)
assert reloaded.top() == top, "the table survives a restart"
with open(path, "w", encoding="utf-8") as handle:
    handle.write("{ this is not json")
assert stats_mod.Leaderboard(path).top() == [], "a damaged file is not fatal"
assert stats_mod.Leaderboard(None).top() == []
ok(12, "leaderboard: wins, streaks, saved and reloaded; a broken file is survivable")

# =====================================================================
# 5. reconnecting
# =====================================================================
ids, alice, bob = table("Ann", "Ben")
ann_id = alice.get(protocol.WELCOME)["client_id"]
token = alice.get(protocol.WELCOME)["token"]
for _ in range(4):
    turn = ids[game.current_turn]
    before = len(game.revealed)
    pick(turn, open_slot())
    wait(lambda: len(game.revealed) > before, "a pick")
score_before = dict(game.scores)
turn_before = game.current_turn
revealed_before = len(game.revealed)

alice.close()                                       # her Wi-Fi drops
wait(lambda: srv.turn_deadline is None, "the clock to freeze")
st = wait(lambda: bob.get(protocol.STATE) if (bob.get(protocol.STATE) or {}).get("paused")
          else None, "the paused state")
assert st["away"][0]["name"] == "Ann" and st["phase"] == "playing"
assert ann_id in game.players, "her seat is held"
ok(13, "a dropped player's seat is held, the match pauses, everyone is told")

alice2 = Wire(PORT, "Ann", token=token)
welcome = wait(lambda: alice2.get(protocol.WELCOME), "welcome back")
assert welcome["reconnected"] and welcome["message"] == "Welcome back, Ann."
new_id = welcome["client_id"]
wait(lambda: new_id in game.players and ann_id not in game.players, "seat handed over")
assert game.scores[new_id] == score_before[ann_id]
assert len(game.revealed) == revealed_before
assert game.current_turn == (new_id if turn_before == ann_id else turn_before)
wait(lambda: srv.turn_deadline is not None, "the clock to resume")
assert not (wait(lambda: alice2.get(protocol.STATE), "state")["paused"])
ids = {new_id: alice2, [i for i in ids if i != ann_id][0]: bob}
turn = ids[game.current_turn]
before = len(game.revealed)
pick(turn, open_slot())
wait(lambda: len(game.revealed) > before, "a pick after reconnecting")
ok(14, "reconnecting with the token restores seat, score, board and turn; play resumes")

# a stale connection that never closed can be taken over with the token
alice3 = Wire(PORT, "Ann", token=token)
wait(lambda: alice3.get(protocol.WELCOME), "takeover welcome")
wait(lambda: alice2.closed, "the old socket to be closed by the server", 4)
assert len([c for c in srv._joined_clients() if c.name == "Ann"]) == 1
ok(15, "the token also takes over a half-dead connection")

# a wrong token is just a new person
fake = Wire(PORT, "Ann", token="0000000000000000")
w = wait(lambda: fake.get(protocol.WELCOME), "fake welcome")
assert not w["reconnected"] and w["role"] == "spectator" and w["message"] != "Welcome back, Ann."
fake.close()
ok(16, "a wrong token gets no one's seat")

# nobody starts a fresh match while someone is away; the seat expires
new_id2 = alice3.get(protocol.WELCOME)["client_id"]
alice3.close()
wait(lambda: srv._anyone_away(), "away")
run.call(srv.reset_all)
time.sleep(0.5)
assert game.phase == g.PHASE_WAITING, "no match may start without the missing player"
wait(lambda: not srv._anyone_away(), "the grace period to expire", 6)
assert new_id2 not in game.players
ok(17, "no match starts around an absent player; after the grace the seat is released")
leave(bob)

# =====================================================================
# 18. the computer from the start screen, and which engine plays
# =====================================================================
class Spy:
    """Stands in for the trained model: records the temperature it is given."""

    def __init__(self):
        self.temperatures = []

    def choose_cell(self, info, temperature, rng):
        self.temperatures.append(temperature)
        return next(c for c in game.cells() if c not in game.revealed)


real = botbrain._agents.get("classic")
spy = Spy()
levels = {"easy": 0.5, "medium": 0.2, "hard": 0.0}
botbrain._agents["classic"] = (spy, levels)

early = Wire(PORT, "Early", vs="medium")           # chosen on the start screen
early_id = wait(lambda: early.get(protocol.WELCOME), "early")["client_id"]
wait(lambda: game.players == [early_id, -1] and game.phase == g.PHASE_PLAYING,
     "the computer seated by the join itself")
assert srv.bot_level == "medium" and srv._bot_name() == "Computer (Medium)"
ok(18, "choosing the computer on the start screen seats it as the player joins")

wait(lambda: spy.temperatures or game.current_turn == early_id, "a turn")
if game.current_turn == early_id:
    pick(early, open_slot())
wait(lambda: spy.temperatures, "the model to be asked", 8)
assert set(spy.temperatures) == {0.2}
ok(19, "the model plays the computer, at the temperature of the chosen level")

late = Wire(PORT, "Late", vs="hard")               # the seat is already taken
late_id = wait(lambda: late.get(protocol.WELCOME), "late")["client_id"]
wait(lambda: game.players == [early_id, late_id], "the second person to get the seat")
assert srv.bot_level == "medium" and not late.get(protocol.STATE)["bot_seated"]
leave(early, late)
srv.bot_level = "off"
ok(20, "a second arrival asking for the computer just gets the human seat")

botbrain._agents["classic"] = None                 # no model: the solver steps in
fallback = Wire(PORT, "Fallback", vs="hard")
fid = wait(lambda: fallback.get(protocol.WELCOME), "fallback")["client_id"]
wait(lambda: game.players == [fid, -1] and game.phase == g.PHASE_PLAYING, "a match")
assert not fallback.get(protocol.STATE)["bot_learned"]
if game.current_turn == fid:
    pick(fallback, open_slot())
before = len(game.revealed)
wait(lambda: len(game.revealed) > before, "the solver to play", 8)
ok(21, "with no trained model the solver plays, and the state says so")
leave(fallback)
srv.bot_level = "off"
botbrain._agents["classic"] = real

run.stop()
print("\nALL SERVER-FEATURE CHECKS PASSED")
