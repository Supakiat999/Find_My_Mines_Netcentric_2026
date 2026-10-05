# What changed, and why

A plain-language record of how this project developed, and what the difference
between the two branches actually is.

---

## The branches

| Branch | Tag | What it is |
|---|---|---|
| **`main`** | `v1-demo` | The version demonstrated in class. The complete game, nothing more. |
| **`enhanced`** | `v2-enhanced` | The same game, plus four aids for getting connected across machines. |
| **`kk`** | `v3-kk` | Everything in `enhanced`, plus three extra game modes and a per-match score reset. |
| **`kk-plus`** | `v4-kk-plus` | Everything in `kk`, plus a computer opponent, an AI coach, chat, a hall of fame, sound, themes and automatic reconnecting. |

**The core game is the same in all of them.** Same rules, same board, the same
turn clock. `main`, `enhanced` and `kk` share one wire protocol; `kk-plus`
extends it, and mixing `kk-plus` with an older version has not been tested - use
the same version on every computer.

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

### Custom mode

A fifth mode where the players set the game up themselves: board size, bomb
count, seconds per turn, flat or cube, which hint style, and whether bombs are
points or hazards. The panel opens from the **Custom** button in the game
window, and either player can change anything mid-session - the board is
re-dealt on each change.

It reuses the engine rather than adding rules: the shape feeds `dims`, the hint
style picks the same weighting the Radius 2 mode uses, and "bombs are hazards"
runs the same code path as Minesweeper mode. The only new logic is
`clamp_custom()`, which forces whatever a client sends into something playable -
sizes clipped to the configured limits, and never more bombs than 45% of the
board. Settings arriving from a client are never trusted.

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

## What `kk-plus` adds

Eight features, two of them AI, plus a full audit of the interface.

### The AI engine (`ai.py`)

Both AI features ask one question: given what is visible, how likely is each
covered slot to hold a bomb?

Every opened number is a constraint - "exactly two of these neighbours are
bombs". In the two-ring hint style a touching bomb counts 2 and one a ring
further out counts 1, so the constraint is a weighted sum instead. The covered
slots those numbers touch are split into independent groups; each group is
solved exactly by backtracking, counting every layout that fits, and the groups
are combined by weighting each total by the ways the remaining bombs can be
scattered over the slots no number mentions.

Some positions are too large to count - a 4x4x4 cube has up to 26 neighbours per
number, so a few opened slots already leave around fifty undecided. For those the
engine samples many valid layouts by randomised backtracking and averages them,
under a hard time limit so a move never stalls the server. The result is marked
as an estimate.

The engine is handed only the visible board - never the bomb layout - and a test
confirms its answers depend on nothing else.

**Measured, not assumed:**

| Check | Result |
|---|---|
| Calibration on classic: "30% likely" slots really are bombs 30% of the time | 18,900 predictions, worst bin off by 0.025 |
| Calibration on the two-ring style | 14,000 predictions, worst bin off by 0.028 |
| The cube (sampled): least-likely fifth vs most-likely fifth | bombs 10% of the time vs 51%; the average matches exactly |
| Hard computer vs random play | classic 60-0, Minesweeper 39-1, Radius 2 30-0, cube 14-0 |
| Hard computer vs the Easy computer | 48-12 |
| Slowest single move, 5x5x5 cube with 56 bombs | 0.4 seconds |

One thing this showed up along the way: a first sampler that moved one bomb at a
time looked fine but was badly overconfident on the cube - it predicted 0% for
slots that held bombs 20% of the time - because that kind of move almost never
succeeds when every number is tight. The calibration check caught it; it was
replaced before it shipped.

### Play the computer

Easy reads each number in isolation and sometimes just guesses. Medium uses the
full engine but slips now and then. Hard always plays the best slot. The computer
sits in the second seat when exactly one person is connected, takes its turn by
itself after a short pause, always agrees to a rematch, steps aside when a second
person joins and returns if they leave. It is never added to the hall of fame.

### The AI coach

Ask on your turn and it names the best slot and tints every covered slot with its
odds. Three questions per player per match, spent only on a successful answer.
In Minesweeper mode it recommends the *safest* slot instead of the likeliest bomb.

### Chat, stats and the rest

- **Chat** for players and spectators, with quick replies. Messages are trimmed
  to 120 characters, one person's messages must be at least 0.6 seconds apart,
  and someone who has not joined cannot talk. Late joiners see recent history.
- **Hall of fame and match stats.** The end screen shows picks, best chain (the
  most picks in a row that kept your turn) and hit rate. Wins and points are kept
  per nickname in `stats.json`; a missing or damaged file just means an empty
  table.
- **Sound effects** are built from sine tones and filtered noise, so there are no
  audio files. If a machine has no audio device the game simply stays quiet.
- **Themes.** Dark, Light, and a colour-blind-safe palette. The theme and mute
  setting are saved in `client_prefs.json`.
- **Automatic reconnecting.** A dropped player's seat is held for 30 seconds and
  the match pauses. The window retries by itself and proves who it is with a
  token handed out at join, so score, board and turn come back intact. The same
  token takes over a connection that is half-dead but not yet noticed. While
  someone is away no match will start around them.
- **Scale-to-fit window.** Everything is drawn on a fixed canvas and scaled to the
  screen, so the game fits a small laptop and can be resized. Mouse positions are
  mapped back through the scaling.
- Bombs are ringed in the colour of the player who found them (with a small 1 or
  2 for colour-blind players), the last move is outlined, and newly opened slots
  animate for a moment.

### Interface problems found and fixed

An automated audit records every piece of text drawn, with its exact rectangle,
across 41 scenarios (every mode, 4x4 to 10x10 boards, 3x3x3 to 5x5x5 cubes,
longest-possible nicknames, eight spectators, every overlay). It found:

- **Server console:** the mode description sat on top of the Custom button; log
  lines and client rows ran out of their panels; a 5-layer cube ran off the
  window.
- **Game window:** long nicknames collided with the scores; 8x8 and 10x10 boards
  ran into the footer or off the window; a long connection error left the window;
  the footer list overflowed with many spectators; the end-of-match card let
  names and scores overlap.

All are fixed, and the audit now runs as a test.

### Problems the tests caught on the way

- Clicking the board while typing in the chat box left the chat capturing the
  keyboard. Clicking anywhere else now stops typing.
- The contrast test found the player-colour ring around a bomb was too faint in
  the Light and colour-blind themes, and the colour-blind hover button's text
  was under the legibility target. The palettes were adjusted.

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

**An honest note about history.** The suites described under `main`, `enhanced`
and `kk` below were scratch scripts that lived outside the repository, and they
did not survive. `kk-plus` rebuilds them properly and ships them: everything now
lives in `tests/`, and `python tests/run_all.py` runs it all.

| Suite | Checks | What it proves |
|---|---|---|
| `test_rules` | 12 | Every mode's rules, scoring, the per-match score reset, match stats, bomb ownership, the coach allowance, seat renaming, and that the public view leaks nothing |
| `test_ai` | 13 | Hand-worked cases, calibration, strength against weaker play, that answers use only the visible board, and speed |
| `test_network` | 13 | The server over real TCP: join, welcome, clock, turn order, a whole match, rematch, reset, spectators, the browser page, and every mode and the custom settings over the wire |
| `test_server_features` | 17 | Chat, the coach, the computer opponent, the leaderboard, and reconnecting |
| `test_client` | 9 | Two real windows playing each other with the mouse |
| `test_client_features` | 18 | The opponent picker, chat typing, the coach, sound, animation, scale-to-fit click mapping, auto-reconnect, themes, contrast and saved preferences |
| `test_layout` | 41 scenarios | No text overlaps, spills out of its panel, or leaves the window |

Everything runs headless, with no window and no sound. The earlier history, as
it was written at the time:

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
