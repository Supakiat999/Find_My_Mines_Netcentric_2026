# What changed, and why

A plain-language record of how this project developed, and what the difference
between the two branches actually is.

---

## The two branches

| Branch | Tag | What it is |
|---|---|---|
| **`main`** | `v1-demo` | The version demonstrated in class. The complete game, nothing more. |
| **`enhanced`** | `v2-enhanced` | The same game, plus four aids for getting connected across machines. |
| **`kk`** | `v3-kk` | Everything in `enhanced`, plus three extra game modes and a per-match score reset. |

**The game itself is identical in both.** Same rules, same board, same screens,
same messages on the wire. A client from one branch plays perfectly well against
a server from the other. The branches differ only in how you *find and reach*
the server — nothing about how the game is played.

`main` is the branch to read if you want the assignment; `enhanced` is the one to
use if you are setting the game up across laptops and the network is fighting
you.

---

## What `enhanced` adds

All four exist because of real problems hit while getting two laptops to play
each other over a phone hotspot.

**1. The server re-checks its own address**

The server used to read its LAN address once at start-up and display it forever.
When the network handed out a new address — switching Wi-Fi, or a hotspot
restarting — the console kept advertising the old one, and players were sent to
an address that no longer existed. It is now re-checked every few seconds, shown
in the header, and a change is written to the activity log.

**2. It keeps the last good address when the network drops**

Address detection falls back to `127.0.0.1` when there is no route at all. On a
brief Wi-Fi drop that made the console tell players to connect to their own
machine. It now holds the last working address and logs the outage instead.

**3. It answers a browser**

Opening `http://<server address>:55555` in any browser returns a page confirming
the connection, and showing the visitor their own address. It turns "it doesn't
work" into a five-second test that separates a network problem from a game
problem, before anyone edits a file.

The check is peeked from the socket rather than consumed, so a real client's
first message is untouched. It takes no player seat and cannot disturb a match
in progress.

**4. The client accepts an address argument**

```bash
python client.py 192.168.1.14
```

The default still comes from `config.py`, so players are never *required* to
type an address. The argument is there because editing a tracked file is one
more step to get wrong when the server has just moved.

---

## What `kk` adds

### Scores reset every match

Scores used to accumulate across rematches, so the second match started from
the first one's numbers and a rematch was never a fair contest. A match now
starts level; only the score shown at the end belongs to that match. The
server's Reset still clears everything.

### Three extra modes

The mode is picked on the server console, next to RESET. Changing it deals a
fresh board for everyone. **Classic stays the default**, so the graded game is
never altered by the extras.

**Radius 2** keeps the classic rules and changes only what the numbers mean. A
hint counts **2** for each bomb touching the slot and **1** for each bomb one
ring further out, so a single bomb influences the 8 slots around it *and* the 16
beyond - 24 in all. Hints can therefore exceed 8, which classic can never
produce. Scoring is untouched: one point per bomb found.

**Minesweeper** inverts the goal. Bombs are the hazard: open safe ground for a
point per slot and keep your turn, with a zero cascading open the way it does in
the original game. Hitting a bomb ends your turn and scores nothing. The match
finishes when the last safe slot is opened.

**3D Cube** moves the hunt into three dimensions - a 4x4x4 cube of 64 slots with
19 bombs, keeping roughly the flat board's density. Every interior slot has 26
neighbours rather than 8, so the hints read very differently. All four layers
are drawn side by side on both the client and the server console, so the whole
cube is visible and clickable without paging through slices.

### Flags

Right-click marks a slot in any mode. A flag only blocks the player who planted
it - shared flags would have let either player wall the board off from the
other.

### How the modes fit in one engine

A slot is a coordinate tuple: `(row, col)` on a flat board, `(layer, row, col)`
on the cube. Everything touching geometry goes through one `_neighbours()`
helper that takes a distance, so the third dimension and the two-ring hints both
came out of the same routine rather than a second copy of the rules.

---

## Bugs found and fixed during development

These are in **both** branches — they are part of the game, not the connection
aids.

**Seats followed connection order instead of join order.** Someone who opened
the client and left it sitting on the nickname screen could take a seat from a
player already in a match, the moment they finally typed a name. Seats now
follow the order nicknames arrive.

**Scores overlapped the top row of the board**, and the end-of-match panel let
the board show through behind the text. Both are laid out properly now.

**Bomb slots were drawn as a pale disc**, which read poorly on the red cell.
They are now a dark mine with a highlight and a fuse.

---

## How it is tested

Three suites drive the real code over real sockets, with no mocking:

| Suite | What it proves |
|---|---|
| Rules | 11 bombs placed, neighbour counts correct at edges and corners, match ends exactly on the last bomb |
| Server (8 checks) | Join and welcome, client list, random first player, countdown ticks and timeout, out-of-turn picks refused, bomb keeps the turn, empty passes it, rematch needs both votes with the winner starting, reset clears board and scores, spectators and disconnects |
| Two players | Two real clients playing a full match end to end, then a rematch, then a server reset |

The `enhanced` branch adds a fourth suite for the browser check, including that
it takes no seat and cannot disturb a live match.

`kk` adds two more: one for the rules of all four modes (brute-force recounts
of the radius-2 weighting and the cube's 26 neighbours, the cascade, and the
flag rules), and one that drives the modes over real sockets through real
client objects - switching mode, clicking into a cube layer, and checking that
every one of the 64 slots is reachable on screen.

---

## Other branches on this repository

`feat/cross-machine-discovery`, `fix/server-bind-addr-in-use` and
`fix/client-conn-deadend` are experimental work from a separate session. They
are not merged into either branch above and have not been tested on the setup
this project was demonstrated on.
