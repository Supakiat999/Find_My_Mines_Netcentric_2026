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
| Extra features | Not started |

Step-by-step setup, including the two-computer demo, is in
**[HOW_TO_RUN.md](HOW_TO_RUN.md)**.

---

## Versions

Two branches, both playable. The game is identical in each - same rules, same
screens, same wire protocol - so a client from one branch talks happily to a
server from the other. They differ only in how you *find and reach* the server.

| Branch | Tag | What it is |
|---|---|---|
| **`main`** | `v1-demo` | The version demonstrated in class. The game and nothing else. |
| **`enhanced`** | `v2-enhanced` | The same game plus four connection aids (below). |

What `enhanced` adds, all of it about getting connected:

1. The server **re-checks its own IP** every few seconds and shows it in the
   header, so it never advertises an address it has stopped using.
2. If the network drops, it **keeps showing the last good address** instead of
   falling back to `127.0.0.1`.
3. It **answers a browser** at `http://<server address>:55555` with a
   "Connection works" page - a five-second way to prove the network is fine
   before anyone edits a file. It takes no player seat and cannot disturb a
   match in progress.
4. The client accepts the address as an argument: **`python client.py <ip>`**,
   so nobody has to edit `config.py`. The default still comes from the source.

Switching between them:

```bash
git checkout enhanced
git checkout main
```

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
- Scores carry over between rematches, and only the server's Reset clears them

Two readings of the brief were settled as follows, both changeable in one line:

- `RESET_TIMER_ON_BOMB = False` — finding a bomb lets a player continue inside
  the *same* 10-second window rather than restarting it.
- A third or later client joins as a **spectator**: it appears in the connected
  list and follows the board. If a player leaves, the first spectator takes the
  empty seat.
