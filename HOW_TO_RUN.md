# How to Run Find My Mines

Two ways to run it: everything on one computer (quick test), or the real
two-computer setup the assignment asks for.

---

## The easy way: double-click a launcher

| You are on | Play | Host the game |
|---|---|---|
| Windows | `PLAY-Windows.bat` | `HOST-Windows.bat` |
| macOS | `PLAY-Mac.command` | `HOST-Mac.command` |

The launcher checks for Python, installs pygame the first time, and asks for
the server address - press Enter to use the one already in `config.py`. The
host's launcher just starts the server and shows the address to hand out.

On macOS the executable bit is often lost when a folder is zipped and mailed
around. If double-clicking does nothing, run this once in Terminal in that
folder:

```bash
chmod +x PLAY-Mac.command HOST-Mac.command
```

The rest of this guide is the manual route, which does the same thing.

---

## Before anything: install pygame

On **every** computer that will run the game:

```bash
pip install -r requirements.txt
```

That installs pygame, the only dependency. Python 3.8 or newer — check with
`python --version`.

Optional, on the **server computer only**: `pip install -r bot/requirements.txt`
lets the computer opponent use the trained models. Without it the computer still
plays, using the older solver.

---

## A. One computer (quick test)

Nothing to configure — `config.py` already points at `127.0.0.1`.

**Terminal 1 — the server:**

```bash
python server.py
```

**Terminal 2 — first player:**

```bash
python client.py
```

**Terminal 3 — second player:**

```bash
python client.py
```

Type a nickname in each client, press **Enter**, and the match starts by itself
as soon as the second player joins.

---

## B. Two computers (the real setup)

### Step 1 — On the server computer, find its IP

```bash
python server.py
```

The window header shows it, like `(LAN 192.168.1.14)`. You can also run:

```bash
ipconfig
```

and read the **IPv4 Address** under your Wi-Fi adapter.

> Your IP is handed out by the router and **changes** when you switch networks
> or reconnect. Always re-check it on the day, do not reuse an old one.

### Step 2 — On the server computer, open the firewall

Windows shows a prompt the first time you run the server — tick **Private
networks** and click **Allow**. If you missed it, open PowerShell **as
Administrator** and run:

```bash
netsh advfirewall firewall add rule name="FindMyMines" dir=in action=allow protocol=TCP localport=55555
```

Without this, other computers cannot reach the server even on the same Wi-Fi.

### Step 3 — On the other computer, point the client at the server

Either edit `config.py`:

```python
SERVER_HOST = "192.168.1.14"
```

...or skip the file entirely and pass the address when you start the client:

```bash
python client.py 192.168.1.14
```

Both do the same thing on this branch. The argument is the safer one when the
server has just moved, because there is no file to forget to save - but note it
only exists here on `enhanced`, not on `main`.
See [CHANGELOG.md](CHANGELOG.md) for what separates the two.

### Step 4 — Play

```bash
python client.py
```

Type a nickname, press Enter. The server computer can run a client too — the
assignment expects exactly that: one computer runs the server *and* a client,
the other runs only a client.

---

## What to send your friends

> 1. Install Python 3, then run `pip install -r requirements.txt`
> 2. Download the code: https://github.com/Supakiat999/Find_My_Mines_Netcentric_2026
> 3. Run `python client.py <my IP>` — type a nickname, press Enter
>    (or set `SERVER_HOST` in `config.py` and just run `python client.py`)
> 5. You must be on the same Wi-Fi as me. If it will not connect, join my phone
>    hotspot and I will send the new IP.

---

## First: the 10-second connection test

Before anyone edits a file, open a **browser** on the other laptop (or on your
phone) and go to the server's address:

```
http://172.20.10.2:55555
```

- **You see "Connection works"** — the network and the firewall are fine. Any
  remaining problem is the address in that person's `config.py`.
- **It times out or refuses** — nothing is reaching the server. Fix that first;
  the game cannot work until this page loads.

The page also prints the visitor's own address, which is a quick way to confirm
they are really on the same network as you.

Use the address shown in the **server window header** — it updates by itself if
the network hands out a new one.

---

## If it will not connect

The client shows what it tried and why. Work down this list:

| Symptom | Cause | Fix |
|---|---|---|
| "Cannot reach the server" straight away | Server not running, or wrong IP | Start `server.py`; re-check the IP in its window header |
| It hangs, then fails | Firewall is dropping the connection | Run the `netsh` rule from step 2 on the **server** computer |
| Works on your own machine, not from theirs | `SERVER_HOST` is still `127.0.0.1` on their copy | Set it to the server's LAN IP on **their** computer |
| Correct IP, still nothing | The Wi-Fi blocks device-to-device traffic | Use a **phone hotspot** and connect both laptops to it |
| Worked yesterday, not today | The router gave the server a new IP | Re-read the IP and update `config.py` |

Quick test from the other computer — if this fails, it is the network, not the
game:

```bash
ping 192.168.1.14
```

University and dorm Wi-Fi very often isolate clients from each other. A phone
hotspot is the reliable fallback for the demo, so set one up in advance.

---

## Playing

- Whoever joins first is player 1; the second is player 2. The match starts
  automatically and the **server picks who goes first at random**.
- You get **10 seconds** per turn. The countdown is at the top.
- Click a covered slot. A **bomb** scores 1 point and you keep your turn; an
  **empty** slot shows how many bombs touch it and passes the turn.
- Opened slots stay open and cannot be clicked again.
- The match ends when all 11 bombs are found. Both players see **YOU WIN** or
  **YOU LOST** with the scores, and a **REMATCH** button.
- A rematch starts when **both** players click it; the previous winner goes
  first.
- A third person can connect and watch — they appear in the ONLINE list at the
  bottom and follow the board, but cannot click.

## The extras (KK Plus)

- **Play alone:** in the OPPONENT card on the right, pick Easy, Medium or Hard.
  Pick Player to wait for a friend instead. It only works while one person is
  connected.
- **AI coach:** on your turn press **Ask the coach** (or **H**). The best slot is
  outlined and every covered slot shows its odds. You get three questions per
  match; **Odds** turns the tint on and off.
- **Chat:** press **Enter** or click the box, type, press Enter again. The four
  buttons send quick replies. Click anywhere else to stop typing.
- **Keys:** `M` sound on/off - `T` theme - `H` coach - right-click flags a slot.
- **If your Wi-Fi drops:** do nothing. The match pauses, your seat is held for 30
  seconds and the window reconnects by itself.
- **Small screen?** The window scales itself to fit. You can also drag its edges.

Your theme, your mute setting and the hall of fame are saved next to the game in
`client_prefs.json` and `stats.json`. They are yours - delete them any time to
start fresh.

## Running the tests

```bash
python tests/run_all.py
```

No window opens and no sound plays. It takes about two minutes, most of it the AI
accuracy check. To run just one part: `python tests/run_all.py ai`.

## The server window

- **CONNECTED CLIENTS** — how many are online and who they are
- **MATCH** — phase, whose turn, countdown, bombs left, scores
- **BOARD (server view)** — the only screen showing bombs nobody has found yet
- **RESET GAME** — clears the board *and* both scores, then deals a new match

Close the server window (or press Esc) to shut everything down.

## PostgreSQL persistence (optional)

The server uses PostgreSQL when `DATABASE_URL` is set. Otherwise it keeps using
`stats.json`. Clients never connect to the database and need no database credentials.

Install Docker with Compose, then run from the project root:

```bash
cp .env.example .env
# Replace both password placeholders with different random hex passwords.
docker compose up -d --wait
pip install -r requirements.txt
python server.py
```

Python loads the project-root `.env` automatically using `python-dotenv`, including
`${RUNTIME_PASSWORD}` expansion. Existing environment variables take precedence.
This works from any working directory and also applies to seed and migration scripts.
Keep passwords and `.env` out of Git.

PostgreSQL 17 binds only to `127.0.0.1:5432`. The `runtime` role can read and
record results but cannot create, alter, or delete tables. The `mines_admin` role
is for setup, migration, and backups. The database is named `find_my_mines`.

Adminer runs at http://127.0.0.1:8181. Select **PostgreSQL**, server `postgres`,
database `find_my_mines`, and username `mines_admin` with `POSTGRES_PASSWORD`
from `.env`. Use `runtime` with `RUNTIME_PASSWORD` for restricted access instead.
Adminer binds only to localhost; credentials are entered at login, not stored in its configuration.

Completed matches, both participants (including bots), player totals, and per-mode
ratings are stored. Boards, chat, and reconnect tokens remain in memory. Nicknames
remain case-sensitive identities, not authenticated accounts.

### Import existing stats and roll back

Stop the game server before migration. Back up `stats.json`, then use the owner URL:

```bash
cp stats.json stats.backup.json
set -a; . ./.env; set +a
DATABASE_URL="postgresql://mines_admin:${POSTGRES_PASSWORD}@127.0.0.1:5432/find_my_mines" \
  python tools/migrate_stats.py import stats.json
```

Import preserves normalized totals, streaks, Elo, peaks, and placement counts.
Repeating the same import is safe before any matches have been recorded. Different
existing records or any completed database match cause import to fail. Historical
match rows cannot be reconstructed from aggregate JSON and are not invented.

For rollback after playing database-backed matches, stop the server and export:

```bash
python tools/migrate_stats.py export stats.export.json
```

Export refuses to overwrite a file without `--force`. Back up the current JSON,
replace it with the export, set `DATABASE_URL=` in `.env` or the environment, and
restart the server. There is
no automatic JSON fallback or dual writing when PostgreSQL is configured.

### Mock data

Stop the game server, configure `DATABASE_URL` in `.env`, then run:

```bash
python seed/seed.py
```

This adds four `Mock_` players and eleven completed matches across all five modes,
including ranked, casual, and computer-opponent examples. It uses the real game
rules and database recording logic, so totals, Elo, and participant statistics agree.
Fixed match UUIDs make reruns safe, including after a partial failure. Existing
non-mock player records are unchanged; no records are deleted. Use this only in a
development database. Restart the server afterward to refresh its leaderboard cache.

### Lifecycle and failures

```bash
docker compose ps
docker compose logs postgres
docker compose restart postgres
docker compose stop
docker compose up -d --wait
```

The named volume preserves records through container restarts and recreation.
**`docker compose down -v` deletes database storage. Do not use it to apply schema changes.**
Initialization files in `db/` run only for an empty volume. Apply future schema
changes explicitly after backing up; changing initialization SQL does not migrate
an existing database. Changing `.env` passwords also does not rotate existing roles.

Backup and restore use the owner role, not runtime:

```bash
docker compose exec -T postgres pg_dump -U mines_admin -d find_my_mines > mines.backup.sql
# Restore only into a newly created database with no game tables; stop the server first.
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U mines_admin -d find_my_mines < mines.backup.sql
```

The restore example assumes `find_my_mines` has no tables. An initialized Docker
database already has tables; restore into a separate empty database instead of
running this against live records.

If startup cannot reach PostgreSQL, the server fails rather than showing empty stats.
During play, database writes run on one worker. Reads use the server's cache.
A completed match waits for commit before final Elo is announced. Transient failures
retry with the same match UUID; chat and networking stay responsive, but new matches,
settings changes, and resets wait. Permanent errors require operator intervention.

Pending results live only in memory. A crash before commit can lose the result;
keep the server running until saving completes. Shutdown warns when a result is pending.
The cache targets one game server; sharing players across servers requires cache refresh.

### Database checks

The database suites create and remove uniquely named temporary schemas. They do not
modify live game tables. Use a test database owner URL, never the restricted runtime URL:

```bash
set -a; . ./.env; set +a
TEST_DATABASE_URL="postgresql://mines_admin:${POSTGRES_PASSWORD}@127.0.0.1:5432/find_my_mines" \
  python tests/run_all.py database
```

Without `TEST_DATABASE_URL`, these suites skip. Other suites keep using in-memory
stats even when `DATABASE_URL` is exported.
