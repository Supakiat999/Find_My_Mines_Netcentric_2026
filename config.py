"""Shared configuration for Find My Mines.

The client never asks the user for an address or port inside the game: it
reads the defaults from here.  To reach a server on another computer,
prefer the command-line override (no file edit needed):

    python client.py <server-ip> [port]

Editing SERVER_HOST below is the fallback for the same thing.
Servers can also override the port:  python server.py [port]
"""

# --- Network -----------------------------------------------------------
# Address the client dials when no command-line address is given.
# Use "127.0.0.1" when the server runs on the same computer, or the LAN
# IP of the server machine (e.g. "192.168.1.42" or "172.20.10.2").
SERVER_HOST = "127.0.0.1"

# Address the server binds to.  "0.0.0.0" accepts connections from any
# network interface, which is what lets the second laptop reach us.
BIND_HOST = "0.0.0.0"
SERVER_PORT = 55555

# --- LAN auto-discovery (UDP beacon) -----------------------------------
# The server broadcasts a small "I am here" packet every
# DISCOVERY_INTERVAL_S seconds; clients listen for a few seconds and offer
# what they heard.  Manual address (CLI arg / SERVER_HOST) always wins.
DISCOVERY_MAGIC = "find-my-mines-v1"
DISCOVERY_PORT = 55556
DISCOVERY_INTERVAL_S = 1.0
DISCOVERY_LISTEN_S = 3.0

# --- Game rules --------------------------------------------------------
GRID_SIZE = 6
BOMB_COUNT = 11
TURN_SECONDS = 10
MAX_PLAYERS = 2

# The brief says a player who finds a bomb "continues their turn until time
# runs out".  We read that as: the countdown keeps running, it is not
# restarted.  Flip this to True if it should restart on every bomb.
RESET_TIMER_ON_BOMB = False
