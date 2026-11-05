"""Shared configuration for Find My Mines.

SERVER_HOST is the default target for the client.  "auto" finds a server on
the local network; the start screen can also accept an address (for example
the Elastic IP of the AWS deployment), which is remembered in
client_prefs.json.  `python client.py <host> [port]` overrides both.
"""

import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def _env_flag(name, default):
    """Read a boolean environment variable, keeping the code default when unset."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


# PostgreSQL is opt-in; an empty DATABASE_URL preserves JSON persistence.
DATABASE_URL = os.environ.get("DATABASE_URL") or None

# --- Network -----------------------------------------------------------
# Address the client dials.  "auto" (the default) finds a server on the same
# network by itself, see "Auto-discovery" below.  To skip that, give an address:
# "127.0.0.1" for the same computer, or the LAN IP of the server machine
# (e.g. "192.168.1.42").  `python client.py <ip>` does the same without
# editing this file.
SERVER_HOST = "auto"

# Address the server binds to.  "0.0.0.0" accepts connections from any
# network interface, which is what lets the second laptop reach us.  Override
# these for containers or managed hosts without editing this file.
BIND_HOST = os.environ.get("BIND_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT") or "55550")

# Address shown to players and in the browser reachability page. Leave it
# unset to use the LAN address the server detects. Set this to the public
# address when the server runs behind NAT or in a container.
ADVERTISE_HOST = os.environ.get("ADVERTISE_HOST") or None

# --- Auto-discovery ----------------------------------------------------
# The server announces itself with a small UDP broadcast; a client started
# with SERVER_HOST = "auto" listens for it and connects to what it hears.
DISCOVERY = _env_flag("DISCOVERY", True)   # False: the server stays silent
DISCOVERY_PORT = 55551               # UDP; open it in the firewall if needed
DISCOVERY_MAGIC = "find-my-mines-v1"
DISCOVERY_INTERVAL_S = 0.5           # how often the server announces itself
DISCOVERY_LISTEN_S = 3.0             # how long a client listens before giving up

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
# an empty value keeps it in memory only (the tests do this).
STATS_FILE = os.environ.get("STATS_FILE", "auto")

# Where the game window remembers your theme and sound choice.  "auto" puts
# client_prefs.json beside the game; an empty value turns remembering off.
PREFS_FILE = os.environ.get("PREFS_FILE", "auto")

# --- Elo Rating System -------------------------------------------------
ELO_STARTING = 1200                  # Default starting rating
ELO_K_FACTOR = 32                    # Sensitivity factor
ELO_MINIMUM = 100                    # Floor rating
ELO_RANKED_MODES = ("classic", "radius2", "sweeper", "cube")

# Tiers: (name, min_rating, rgb_tuple)
ELO_TIERS = [
    ("Bronze",   0,    (195, 130, 80)),    # Warm Bronze
    ("Silver",   1100, (185, 195, 205)),   # Bright Silver
    ("Gold",     1300, (240, 185, 50)),    # Radiant Gold
    ("Platinum", 1500, (65, 215, 195)),    # Cyan Platinum
    ("Diamond",  1700, (170, 130, 250)),   # Violet Diamond
    ("Master",   2000, (255, 80, 120)),    # Crimson Master
]

# Placement / Provisional Calibration
ELO_PROVISIONAL_MATCHES = 5          # Number of placement matches
ELO_PROVISIONAL_K = 64               # Accelerated K-factor for new players

# Win Streak Bonus
ELO_STREAK_THRESHOLD = 3             # Streak required for bonus
ELO_STREAK_BONUS = 6                 # Flat bonus Elo added per win while on streak


