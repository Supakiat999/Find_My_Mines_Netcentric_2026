# What changed, and why

A plain-language record of how this project developed, and what the difference
between the two branches actually is.

---

## The two branches

| Branch | Tag | What it is |
|---|---|---|
| **`main`** | `v1-demo` | The version demonstrated in class. The complete game, nothing more. |
| **`enhanced`** | `v2-enhanced` | The same game, plus four aids for getting connected across machines. |

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

---

## Other branches on this repository

`feat/cross-machine-discovery`, `fix/server-bind-addr-in-use` and
`fix/client-conn-deadend` are experimental work from a separate session. They
are not merged into either branch above and have not been tested on the setup
this project was demonstrated on.
