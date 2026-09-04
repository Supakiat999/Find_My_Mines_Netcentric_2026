# How to Run Find My Mines

Two ways to run it: everything on one computer (quick test), or the real
two-computer setup the assignment asks for.

---

## Before anything: install pygame

On **every** computer that will run the game:

```bash
pip install -r requirements.txt
```

That installs pygame, the only dependency. Python 3.8 or newer — check with
`python --version`.

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

## The server window

- **CONNECTED CLIENTS** — how many are online and who they are
- **MATCH** — phase, whose turn, countdown, bombs left, scores
- **BOARD (server view)** — the only screen showing bombs nobody has found yet
- **RESET GAME** — clears the board *and* both scores, then deals a new match

Close the server window (or press Esc) to shut everything down.
