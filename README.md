# Find_My_Mines_Netcentric_2026

Netcentric Project — a two-player online *Find My Mines* game built with **socket
programming** on a client–server model.

The server randomly hides **11 bombs on a 6×6 grid**. Two players take turns
opening slots on a 10-second clock. Find a bomb and you keep your turn; open an
empty slot and it shows how many bombs surround it and the turn passes. One point
per bomb; the match ends when all 11 are found.

**Stack:** Python 3 · `socket` (stdlib TCP) · `pygame`

---

## Status

| Part | State |
|---|---|
| Server — `config.py` · `protocol.py` · `game.py` · `server.py` | Done, verified by a headless socket test |
| Client — `client.py` | Done, verified by two clients playing a full match |
| Connection aids | Live server address, browser check, address argument |
| Game modes (this branch) | Classic, Radius 2, Minesweeper, 3D Cube |

Step-by-step setup, including the two-computer demo, is in
**[HOW_TO_RUN.md](HOW_TO_RUN.md)**.

---

## Versions - pick one

Three versions live on three branches. **The game itself is identical in all
three** - same rules, same screens, same wire protocol - so a client from one
branch plays perfectly well against a server from another. What differs is what
is built around the game.

| Version | Branch | Snapshot | What it is |
|---|---|---|---|
| **Classic** | [`main`](https://github.com/Supakiat999/Find_My_Mines_Netcentric_2026/tree/main) | [`v1-demo`](https://github.com/Supakiat999/Find_My_Mines_Netcentric_2026/tree/v1-demo) | The version demonstrated in class. The assignment and nothing else. |
| **Enhanced** | [`enhanced`](https://github.com/Supakiat999/Find_My_Mines_Netcentric_2026/tree/enhanced) | [`v2-enhanced`](https://github.com/Supakiat999/Find_My_Mines_Netcentric_2026/tree/v2-enhanced) | Classic plus four aids for connecting across machines. |
| **KK** | [`kk`](https://github.com/Supakiat999/Find_My_Mines_Netcentric_2026/tree/kk) | [`v3-kk`](https://github.com/Supakiat999/Find_My_Mines_Netcentric_2026/tree/v3-kk) | Enhanced plus five game modes, a custom game, and per-match scoring. |

Click a branch above to browse it on GitHub, or switch locally:

```bash
git checkout kk
```

You are reading the **kk** branch.  The front page on
[`main`](https://github.com/Supakiat999/Find_My_Mines_Netcentric_2026#readme) describes every version side by side.

---

## Game modes (`kk` branch)

The mode is chosen on the **server console**, from the row of buttons beside
RESET. Changing it deals a fresh board for everyone at once. `Classic` is the
default and is exactly the game the assignment asks for, so the graded rules are
never disturbed by the extras.

| Mode | Board | How it plays |
|---|---|---|
| **Classic** | 6x6, 11 bombs | Find bombs, one point each. A bomb keeps your turn, an empty slot passes it. |
| **Radius 2** | 6x6, 11 bombs | Same rules, but a hint counts **2** for every bomb touching the slot and **1** for every bomb a ring further out - so each bomb influences 24 slots instead of 8, and hints can run past 8. |
| **Minesweeper** | 6x6, 11 bombs | Inverted: bombs are the hazard. Open safe ground for a point per slot and keep your turn; a zero cascades open; hitting a bomb ends your turn for nothing. The match ends when the last safe slot is open. |
| **3D Cube** | 4x4x4, 19 bombs | The classic hunt in three dimensions. Every slot has up to **26** neighbours instead of 8. All four layers are drawn side by side, so the whole cube is clickable at once. |
| **Custom** | you decide | Set the board size, the bomb count, the seconds per turn, flat or cube, which hint style, and whether bombs are points or hazards. Any combination of the above. |

### Custom settings

Pick **Custom** and a panel opens. Either player can change any of it while you
play; the board is re-dealt the moment something changes.

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
| `client.py` | The game client: nickname screen, board, scoreboard, countdown, win/lost overlay and rematch. |
| `requirements.txt` | The one dependency, pygame. |
| `PLAY-Windows.bat` / `PLAY-Mac.command` | Double-click launchers for players - check Python, install pygame, ask for the address. |
| `HOST-Windows.bat` / `HOST-Mac.command` | Double-click launchers that start the server. |
| `ARCHITECTURE.md` | How the system works layer by layer, from Wi-Fi frames up to the game rules — written for presenting in class. |
| `HOW_TO_RUN.md` | Setup and troubleshooting, including what to send the other players. |

---

## Requirements

Python 3.8+ (developed on 3.13). The only third-party package is pygame —
everything else (`socket`, `threading`, `json`, `queue`) ships with Python:

```bash
pip install -r requirements.txt
```

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

---

## How it works

Clients send only three messages; the server decides everything else and pushes
the resulting state back. The board sent to clients never contains unfound bomb
positions, so a modified client cannot read them off the network.

| Direction | Message | Payload |
|---|---|---|
| client → server | `join` | `nickname` |
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
and every send — so the game rules never need a lock.

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
