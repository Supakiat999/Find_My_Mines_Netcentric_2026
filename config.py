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

# --- Game modes --------------------------------------------------------
# The mode ids live in game.py, next to the rules they drive.  "classic" is
# the game the assignment asks for; the others are extra modes chosen from
# the server console.
DEFAULT_MODE = "classic"

# The cube mode plays in three dimensions instead of on a flat grid.  19
# bombs in 64 slots keeps roughly the density of the flat board's 11 in 36.
GRID_3D = (4, 4, 4)
BOMB_COUNT_3D = 19

# --- Custom mode -------------------------------------------------------
# What the players start from when they pick Custom.  Every value can be
# changed from the game window while playing; game.py clamps whatever
# arrives to the limits below.
DEFAULT_CUSTOM = {
    "size": 6,             # board is size x size, or size x size x size
    "bombs": 11,
    "turn_seconds": 10,
    "hints": "simple",     # "simple" counts touching bombs, "radius2" weights 2 then 1
    "goal": "collect",     # "collect" scores bombs, "avoid" makes them the hazard
    "shape": "flat",       # "flat" or "cube"
}

CUSTOM_LIMITS = {
    "size_flat": (4, 10),
    "size_cube": (3, 5),
    "turn_seconds": (5, 60),
    "max_bomb_share": 0.45,   # never so many bombs that the board is unplayable
}

# The brief says a player who finds a bomb "continues their turn until time
# runs out".  We read that as: the countdown keeps running, it is not
# restarted.  Flip this to True if it should restart on every bomb.
RESET_TIMER_ON_BOMB = False

# --- Extra features ----------------------------------------------------
# How many times each player may ask the AI coach for advice per match.
HINTS_PER_MATCH = 3

# How long a dropped player's seat is held for them to reconnect, in seconds.
# The match pauses while they are away; after this the seat is given up.
RECONNECT_GRACE = 30

# The computer opponent's thinking time, in seconds (shortest, longest).
BOT_THINK_SECONDS = (0.7, 1.4)

# Where the hall of fame is saved.  "auto" puts stats.json beside the game;
# None keeps it in memory only (the tests do this).
STATS_FILE = "auto"

# Where the game window remembers your theme and sound choice.  "auto" puts
# client_prefs.json beside the game; None turns remembering off (tests do this).
PREFS_FILE = "auto"
