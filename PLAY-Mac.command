#!/bin/bash
# Double-click this to play Find My Mines on a Mac.
# If it will not open, run this once in Terminal:  chmod +x PLAY-Mac.command
cd "$(dirname "$0")" || exit 1

echo
echo "   FIND MY MINES"
echo

PY=python3
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "   Python 3 is not installed on this Mac."
    echo "   Get it from https://www.python.org/downloads/"
    echo
    read -n 1 -s -r -p "   Press any key to close..."
    exit 1
fi

if ! "$PY" -c "import pygame" >/dev/null 2>&1; then
    echo "   Installing pygame. This happens once and takes a minute..."
    echo
    if ! "$PY" -m pip install --quiet pygame; then
        echo "   Could not install pygame automatically."
        echo "   Try running this by hand:  python3 -m pip install pygame"
        echo
        read -n 1 -s -r -p "   Press any key to close..."
        exit 1
    fi
fi

echo "   Type the server address the host gave you,"
echo "   or just press Enter to use the one saved in config.py."
echo
read -r -p "   Server address: " ADDR
echo

if [ -z "$ADDR" ]; then
    "$PY" client.py
else
    "$PY" client.py "$ADDR"
fi

echo
read -n 1 -s -r -p "   Press any key to close..."
