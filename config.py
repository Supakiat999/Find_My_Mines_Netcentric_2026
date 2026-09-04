"""Shared configuration for Find My Mines.

The client never asks the user for an address or port: it reads them from
here.  When you run the client on a second computer, the only line you need
to change is SERVER_HOST.
"""

# --- Network -----------------------------------------------------------
# Address the client dials.  Use "127.0.0.1" when the server runs on the
# same computer, or the LAN IP of the server machine (e.g. "192.168.1.42").
SERVER_HOST = "127.0.0.1"

# Address the server binds to.  "0.0.0.0" accepts connections from any
# network interface, which is what lets the second laptop reach us.
BIND_HOST = "0.0.0.0"
SERVER_PORT = 55555

# --- Game rules --------------------------------------------------------
GRID_SIZE = 6
BOMB_COUNT = 11
TURN_SECONDS = 10
MAX_PLAYERS = 2

# The brief says a player who finds a bomb "continues their turn until time
# runs out".  We read that as: the countdown keeps running, it is not
# restarted.  Flip this to True if it should restart on every bomb.
RESET_TIMER_ON_BOMB = False
