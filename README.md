# Find_My_Mines_Netcentric_2026

Netcentric Project — a two-player online *Find My Mines* game built with **socket
programming** on a client–server model. One server hosts multiple independent
two-player rooms, with a lobby for creating, joining, and watching games.

The server randomly hides **11 bombs on a 6×6 grid**. Two players take turns
opening slots on a 10-second clock. Find a bomb and you keep your turn; open an
empty slot and it shows how many bombs surround it and the turn passes. One point
per bomb; the match ends when all 11 are found.

**Stack:** Python 3 · `socket` (stdlib TCP) · `pygame` · optional PostgreSQL (Psycopg 3)

---

## Status

| Part | State |
|---|---|
| Server — `config.py` · `protocol.py` · `game.py` · `server.py` | Done, verified by a headless socket test |
| Client — `client.py` | Done, verified by two clients playing a full match |
| Connection aids | Live server address, browser check, address argument |
| Game modes | Classic, Radius 2, Minesweeper, 3D Cube, Custom |
| AI, chat, stats, sound, themes, reconnect | Done, 83 automated checks |
| Elo rating & rank tiers (this branch) | Done, 6 tiers up to Master 2000+, placement K=64, streak bonuses |

Step-by-step setup, including the two-computer demo, is in
**[HOW_TO_RUN.md](HOW_TO_RUN.md)**. For full details on the ranking formulas and tiers, see **[ELO.md](ELO.md)**.

---

## Versions - pick one

Five versions, each containing everything in the one before it. `main` carries
KK Plus; the version demonstrated in class is the `v1-demo`
snapshot. The core game - the rules, the board, the turn clock - is the same
throughout; what changes is what is built around it. Use the **same version on
every computer** in a game.

| Version | Branch | Snapshot | What it is |
|---|---|---|---|
| **Classic** | *(snapshot only)* | [`v1-demo`](https://github.com/Supakiat999/Find_My_Mines_Netcentric_2026/tree/v1-demo) | The version demonstrated in class. The assignment and nothing else. It used to be the `main` branch. |
| **Enhanced** | [`enhanced`](https://github.com/Supakiat999/Find_My_Mines_Netcentric_2026/tree/enhanced) | [`v2-enhanced`](https://github.com/Supakiat999/Find_My_Mines_Netcentric_2026/tree/v2-enhanced) | Classic plus four aids for connecting across machines. |
| **KK** | [`kk`](https://github.com/Supakiat999/Find_My_Mines_Netcentric_2026/tree/kk) | [`v3-kk`](https://github.com/Supakiat999/Find_My_Mines_Netcentric_2026/tree/v3-kk) | Enhanced plus five game modes, a custom game, and per-match scoring. |
| **KK Plus** | [`main`](https://github.com/Supakiat999/Find_My_Mines_Netcentric_2026/tree/main) and [`kk-plus`](https://github.com/Supakiat999/Find_My_Mines_Netcentric_2026/tree/kk-plus) | [`v4-kk-plus`](https://github.com/Supakiat999/Find_My_Mines_Netcentric_2026/tree/v4-kk-plus) | KK plus a computer opponent, an AI coach, chat, a hall of fame, sound, themes and automatic reconnecting. |
| **Elo Progression** | [`feature/elo`](https://github.com/Supakiat999/Find_My_Mines_Netcentric_2026/tree/feature/elo) | *(this branch)* | KK Plus with complete Elo rating system, rank tiers, placement calibration, streak bonuses, and peak tracking. |

Click a branch or snapshot above to browse it on GitHub, or switch locally:

```bash
git checkout v1-demo     # the version shown in class
git checkout main        # stable KK Plus
git checkout feature/elo # competitive Elo progression
```

You are reading the `feature/elo` branch.

---

## KK Plus features

### Rooms and lobby

Enter a nickname to open the room list. **Create Room** chooses the room name,
game mode, custom board, computer difficulty, and Ranked/Casual setting. **Join**
takes an available player seat; **Watch** joins as a spectator. Human matches start
when two players join; computer rooms start immediately and reserve one seat for
the bot. **Leave Room** returns to the lobby without closing the connection.

Settings are fixed for the room and its rematches. Create another room to change
them. Boards, clocks, chat, hints, rematches, reconnect pauses, and resets are
room-local. Empty rooms disappear after reconnect reservations and pending saves
finish. Rooms do not survive a server restart; completed match records do.

Everything in KK, plus eight more features. **Two are AI.**

| # | Feature | What it does |
|---|---|---|
| 1 | **Play the computer** (AI) | Choose **2 Players, Easy, Medium or Hard** when creating a room. Computer rooms reserve the second seat for the bot; other visitors watch. The computer plays automatically and agrees to rematches. Trained models fall back to the probability solver when unavailable. |
| 2 | **AI coach** (AI) | On your turn, **Ask the coach** (or press **H**) names the best slot and tints every covered slot with its odds. Three questions per player per match. On boards too big to count exactly the answer is marked as an estimate. |
| 3 | **Chat** | A chat panel for players and spectators, with quick replies (GG, Nice!, Oops, Again?). Messages are trimmed to 120 characters and rate-limited; people who join late see recent history. |
| 4 | **Hall of fame and match stats** | The end screen shows picks, best chain and hit rate for each player. Wins, losses and points are kept per nickname in `stats.json`, so the table survives restarts. The computer is never listed. |
| 5 | **Sound effects** | Slot clicks, a chime for a bomb you want, a thump for one you do not, a ding on your turn, a warning tick in the last three seconds, and win/lose/draw tunes. All synthesised from maths - there are no audio files. **M** mutes. |
| 6 | **Themes** | Dark, Light and a colour-blind-safe palette (**T** or the Theme button). Your choice and the mute setting are remembered in `client_prefs.json`. |
| 7 | **Automatic reconnecting** | If your Wi-Fi drops, the server holds your seat, score and turn for 30 seconds and pauses the match, and your window reconnects by itself. A token proves it is you, so nobody else can take the seat. |
| 8 | **Scale-to-fit window** | The game is drawn on a fixed canvas and scaled to your screen, so it fits small laptops and can be resized freely. Clicks are mapped back correctly. |
| 9 | **Competitive Elo & Rank Tiers** | Real-time per-mode Elo ratings, 6 competitive tiers (Bronze to Master 2000+), provisional calibration ($K=64$), win streak bonuses, peak rating tracking, and a casual toggle. Full details in [**ELO.md**](ELO.md). |

Also new: each bomb is ringed in the colour of the player who found it (with a
small 1 or 2 for colour-blind players), the last move is outlined, and newly
opened slots animate.

**Keys:** `Enter` type in the chat - `M` sound - `T` theme - `H` ask the coach -
right-click flags a slot.

**How the AI works.** Every opened number is a constraint ("exactly two of these
neighbours are bombs"). The covered slots those numbers touch are split into
independent groups and each is solved exactly by backtracking, then the groups
are weighted by the ways the remaining bombs can fall elsewhere. That gives a
real probability for every covered slot, not a guess. Positions too large to
enumerate (the cube, big custom boards) fall back to sampling many valid layouts.
The engine in `ai.py` is handed only the visible board, so neither the computer
nor the coach can see hidden bombs. Measured results are in
[CHANGELOG.md](CHANGELOG.md).

### Running the tests

```bash
python tests/run_all.py
```

Headless suites (the bot one skips without torch) - no window opens and no sound plays. Run one
with `python tests/run_all.py ai`.

---

## Competitive Elo Rating System

The `feature/elo` branch adds skill-based matchmaking ratings and tier progression:
* **Per-mode ratings:** Independent Elo ratings for Classic, Radius 2, Minesweeper, and 3D Cube.
* **6 competitive rank tiers:** Bronze (`<1100`), Silver (`1100`), Gold (`1300`), Platinum (`1500`), Diamond (`1700`), and Master (`2000+`).
* **Placement calibration:** Accelerated $K=64$ factor for a player's first 5 matches per mode.
* **Win streak bonus:** Flat $+6$ Elo bonus for streaks of 3 or more consecutive wins.
* **Peak Elo tracking:** All-time high rating preserved and shown in the Hall of Fame card.
* **Ranked vs. Casual toggle:** In-game mode toggle with automatic board reset to prevent mid-match rating manipulation.

👉 **For complete mathematical formulas, tier tables, and architecture details, see [ELO.md](ELO.md).**

---

## Game modes

Choose the mode on **Create Room**. It stays fixed for that room, including
rematches. `Classic` remains the default assignment rules.

| Mode | Board | How it plays |
|---|---|---|
| **Classic** | 6x6, 11 bombs | Find bombs, one point each. A bomb keeps your turn, an empty slot passes it. |
| **Radius 2** | 6x6, 11 bombs | Same rules, but a hint counts **2** for every bomb touching the slot and **1** for every bomb a ring further out - so each bomb influences 24 slots instead of 8, and hints can run past 8. |
| **Minesweeper** | 6x6, 11 bombs | Inverted: bombs are the hazard. Open safe ground for a point per slot and keep your turn; a zero cascades open; hitting a bomb ends your turn for nothing. The match ends when the last safe slot is open. |
| **3D Cube** | 4x4x4, 19 bombs | The classic hunt in three dimensions. Every slot has up to **26** neighbours instead of 8. All four layers are drawn side by side, so the whole cube is clickable at once. |
| **Custom** | you decide | Set the board size, the bomb count, the seconds per turn, flat or cube, which hint style, and whether bombs are points or hazards. Any combination of the above. |

### Custom settings

Pick **Custom** when creating a room to set its board and rules before anyone
plays. In-game settings are read-only.

| Setting | Choices |
|---|---|
| Board size | 4-10 flat, 3-5 as a cube |
| Bombs | 1 up to 45% of the slots |
| Seconds per turn | 5 to 60 |
| Shape | Flat grid or cube |
| Hints | Touching bombs only, or the two-ring 2/1 weighting |
| Bombs are | Points to collect, or hazards to avoid |

Every value is clamped on the **server** by `game.clamp_custom()`, so a client
cannot ask for a 500x500 board or more bombs than there are slots.

**Flags.** Right-click marks a slot in any mode. A flag only blocks the player
who planted it, so it is a note to yourself and cannot be used to wall the board
off from your opponent.

---

## Files

| File | Purpose |
|---|---|
| `config.py` | Server address, port, and game constants. The **only** file you edit to connect a second computer. |
| `protocol.py` | Newline-delimited JSON framing over TCP, plus a reader that reassembles messages split across packets. |
| `game.py` | Pure game rules — bomb placement, neighbour counts, turn order, scoring. No sockets, no GUI. |
| `server.py` | TCP accept loop, one thread per client, the authoritative turn clock, and the pygame admin console. |
| `room.py` | Independent games, room membership, clocks, bots, chat, reconnects, and match results. |
| `client.py` | The game client: nickname screen, board, scoreboard, countdown, win/lost overlay and rematch. |
| `requirements.txt` | Pygame, Psycopg 3, and python-dotenv for `.env` configuration. |
| `database.py`, `db/`, `compose.yaml` | Transactional PostgreSQL storage and Docker setup. |
| `ai.py` | The probability engine behind the coach, and the computer opponent's fallback. Sees only the visible board. |
| `botbrain.py` | Picks who plays the computer's moves: the trained model, or `ai.py` when a mode has none. |
| `stats.py` | The hall of fame, saved to `stats.json`. |
| `sound.py` | Sound effects, synthesised from maths - no audio files. |
| `themes.py` | The three colour themes, and the contrast measure the tests use. |
| `bot/` | Reinforcement-learning opponents, one per mode. See [Training the bots](#training-the-bots). |
| `tests/` | Eight headless test suites. Run them with `python tests/run_all.py`. |
| `PLAY-Windows.bat` / `PLAY-Mac.command` | Double-click launchers for players - check Python, install pygame, ask for the address. |
| `HOST-Windows.bat` / `HOST-Mac.command` | Double-click launchers that start the server. |
| `ARCHITECTURE.md` | How the system works layer by layer, from Wi-Fi frames up to the game rules — written for presenting in class. |
| `HOW_TO_RUN.md` | Setup and troubleshooting, including what to send the other players. |

---

## Requirements

Python 3.8+ (developed on 3.13). Pygame runs the game; Psycopg 3 enables optional
PostgreSQL persistence; python-dotenv loads `.env` configuration automatically.
Networking (`socket`, `threading`, `json`, `queue`) ships with Python:

```bash
pip install -r requirements.txt
```

For Docker PostgreSQL, JSON migration, backups, and database tests, see
[PostgreSQL persistence](HOW_TO_RUN.md#postgresql-persistence-optional).

---

## Training the bots

Optional, and nothing is trained yet. Run from the project root:

```bash
pip install -r bot/requirements.txt           # torch, numpy, scipy
python -m bot.train --mode classic            # or radius2, sweeper, cube, all
python -m bot.evaluate --mode classic         # trained vs random, mean turns
```

Weights are saved to `bot/weights/<mode>.pt`. `--steps N` shortens a run and
`--mode custom --custom '{"size": 8, "bombs": 14}'` trains a custom board.
Details are in [`bot/README.md`](bot/README.md). Only the server needs torch, and
only to play the trained model: without it (or without weights for a mode, or on
a custom board) the computer falls back to the `ai.py` solver. After training,
`python -m bot.calibrate` sets what Easy and Medium mean.

---

## Running it

### Same computer (quick test)

Leave `config.py` as it is and open two terminals:

```bash
python server.py
```

```bash
python client.py
```

### Two computers (the real setup)

**On the server computer:**

1. Start the server:

   ```bash
   python server.py
   ```

   The top of the window shows its address, e.g. `(LAN 192.168.1.14)`. You can
   also find it with `ipconfig` — use the **IPv4 Address** of your Wi-Fi adapter.

2. Allow Python through the firewall. Windows shows a prompt on first run — tick
   **Private networks** → *Allow*. If you missed it, run in an **Administrator**
   PowerShell:

   ```powershell
   netsh advfirewall firewall add rule name="FindMyMines" dir=in action=allow protocol=TCP localport=55555
   ```

**On the client computer:**

3. Edit one line in `config.py`:

   ```python
   SERVER_HOST = "192.168.1.14"   # the server computer's IPv4 address
   ```

4. Start the client:

   ```bash
   python client.py
   ```

Per the assignment, players never type an IP or port in the game itself — the
address lives in the source.

**Troubleshooting**

- Both computers must be on the **same network**. University Wi-Fi often blocks
  device-to-device traffic; if the connection fails, share a **phone hotspot**
  and connect both laptops to it.
- Check reachability first: `ping 192.168.1.14`
- The computer running the server can also run a client, with
  `SERVER_HOST = "127.0.0.1"`.

---

## Server console

The pygame window is the server's control panel:

- **Connected clients** — live count and the list of who is online, with their
  address, role, and score
- **Match** — phase, whose turn it is, the countdown, bombs remaining, scores
- **Board (server view)** — the only place unfound bombs are visible, drawn as
  dim dots
- **Activity** — a running log of joins, picks, timeouts, and match results
- **RESET GAME** — clears the board *and* both scores, then deals a fresh match

Use **Previous** and **Next** to select a room. The console shows and resets only
that room; other matches keep running.

---

## How it works

Clients send lobby requests and room-scoped actions; the server decides everything else and pushes
the resulting state back. The board sent to clients never contains unfound bomb
positions, so a modified client cannot read them off the network.

| Direction | Message | Payload |
|---|---|---|
| client → server | `join` | `nickname` |
| client → server | `list_rooms` | — |
| client → server | `create_room` | name, mode, custom, bot_level, ranked |
| client → server | `join_room` | room_id, watch |
| client → server | `leave_room` | — |
| server → client | `rooms` | room summaries |
| server → client | `room_left` | room_id |
| client → server | `pick` | `row`, `col` |
| client → server | `rematch` | — |
| server → client | `welcome` | your id, role, `"Welcome, Alice."`, board size |
| server → client | `clients` | online count and the client list |
| server → client | `state` | phase, board, players, scores, whose turn, bombs left |
| server → client | `tick` | seconds left on the current turn |
| server → client | `match_end` | winner, draw flag, final scores |
| server → client | `server_reset` | the admin pressed Reset |
| server → client | `error` | e.g. `"not your turn"` |

**Threading.** An accept thread takes new connections and gives each client its
own reader thread; those threads only push decoded messages onto a queue. The
pygame main loop drains that queue, runs the clock, and does every state change
and every send — so the game rules never need a lock. It updates every room's
clock and bot independently; lobby users are not assigned a game automatically.

**Turn clock.** The countdown is owned by the server and broadcast once a second,
so both players see the same time and no client can stall its own turn.

---

## Rules as implemented

- 11 bombs, 6×6 grid, 10 seconds per turn (all set in `config.py`)
- The server picks the first player **at random** for the first match
- Bomb → 1 point and you keep the turn · empty slot → shows the surrounding bomb
  count and the turn passes · timeout → the turn passes
- Every opened slot is disabled for the rest of the match
- Match ends when all 11 bombs are found; both clients then show **Win**/**Lost**
  with both scores and a Rematch button
- A rematch needs **both** players to agree, and the previous **winner starts**
- Every match starts level: scores belong to the match, not the session

Two readings of the brief were settled as follows, both changeable in one line:

- `RESET_TIMER_ON_BOMB = False` — finding a bomb lets a player continue inside
  the *same* 10-second window rather than restarting it.
- A third or later client joins as a **spectator**: it appears in the connected
  list and follows the board. If a player leaves, the first spectator takes the
  empty seat.
