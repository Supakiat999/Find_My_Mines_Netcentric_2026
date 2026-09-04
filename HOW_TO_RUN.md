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

The window header shows it, like `192.168.1.14:55555`. If the log lists
“Other addresses on this machine”, those are the other NICs (VPN,
Ethernet) — try each one. You can also run:

- Windows: `ipconfig` → **IPv4 Address** under Wi-Fi
- macOS: `ipconfig getifaddr en0`
- Linux: `ip addr show` → `inet` under Wi-Fi/hotspot

> Your IP is handed out by the router and **changes** when you switch networks
> or reconnect. Always re-check it on the day, do not reuse an old one.

### Step 2 — On the server computer, open the firewall

Allow **TCP 55555** (game) and **UDP 55556** (auto-discovery):

- Windows: tick **Private networks** → *Allow* on first run. If you missed
  it, open PowerShell **as Administrator**:

```powershell
netsh advfirewall firewall add rule name="FindMyMines" dir=in action=allow protocol=TCP localport=55555
netsh advfirewall firewall add rule name="FindMyMines-Discovery" dir=in action=allow protocol=UDP localport=55556
```

- macOS: System Settings → Network → Firewall → allow incoming for Python.
- Linux: `sudo ufw allow 55555/tcp && sudo ufw allow 55556/udp`

Without this, other computers cannot reach the server even on the same Wi-Fi.

### Step 3 — On the other computer, find and join the server

Just start the client — it listens ~3s for the server beacon and lists
what it heard on the nickname screen:

```bash
python client.py
```

Press `1`-`9` to join a listed server, `F5` to scan again, `R` to retry the
TCP connection if the server started late.

No server listed (isolated Wi-Fi) or want to skip the scan? Connect
directly — this overrides `config.py` without editing any file:

```bash
python client.py 192.168.1.14
python client.py 192.168.1.14 55555
```

Fallback: edit `config.py`:

```python
SERVER_HOST = "192.168.1.14"
```

The direct address always wins over discovery.

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
> 3. Run `python client.py` and pick my server from the list (press `1`)
>    — or run `python client.py <my IP>` if nothing is listed
> 5. You must be on the same Wi-Fi as me (disable VPN). If it will not
>    connect, join my phone hotspot and I will send the new IP.

---

## Phone-hotspot runbook (reliable demo)

1. Host starts the hotspot; **both** laptops join it (server last, so its
   IP is fresh). Disable VPNs on both machines.
2. On the server: `python server.py`, read the header IP (often
   `172.20.10.x`), keep the window visible.
3. On each client: `python client.py`, wait ~3s, press the server number —
   or `python client.py <server-ip>` as fallback.
4. If the browser test below fails from the client machine, re-check the
   IP and the firewall before touching the game.

---

## First: the 10-second connection test

Before anyone edits a file, open a **browser** on the other laptop (or on your
phone) and go to the server's address — use the IP from the **server window
header**, e.g.:

```
http://192.168.1.14:55555
```

- **You see "Connection works"** — the network and the firewall are fine. Any
  remaining problem is the address the client is dialing.
- **It times out or refuses** — nothing is reaching the server. Fix that first;
  the game cannot work until this page loads.

The page also prints the visitor's own address, which is a quick way to confirm
they are really on the same network as you.

---

## If it will not connect

The client shows what it tried and why. Work down this list:

| Symptom | Cause | Fix |
|---|---|---|
| "Cannot reach the server" straight away | Server not running, or wrong IP | Start `server.py`; re-check the IP in its window header; try `python client.py <ip>` |
| No servers listed on the nickname screen | UDP beacon blocked (isolated Wi-Fi) or firewall | Use the direct `python client.py <ip>` path; open UDP 55556 inbound on the server |
| It hangs, then fails | Firewall is dropping the connection | Open TCP 55555 inbound on the **server** computer (see step 2) |
| Works on your own machine, not from theirs | Dialing `127.0.0.1` from their copy | Pick the discovered server (press `1`) or pass the server's LAN IP |
| Correct IP, still nothing | The Wi-Fi blocks device-to-device traffic | Use a **phone hotspot** and connect both laptops to it; disable VPNs |
| Worked yesterday, not today | The router gave the server a new IP | Re-read the IP and reconnect (no file edit needed with the CLI arg) |

Better than ping — ping uses ICMP, which is often blocked even when the
game port is open:

```bash
# Windows
Test-NetConnection 192.168.1.14 -Port 55555
# macOS / Linux
nc -vz 192.168.1.14 55555
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
