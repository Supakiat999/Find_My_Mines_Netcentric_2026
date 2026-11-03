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

if ! "$PY" -c "import pygame, dotenv, psycopg" >/dev/null 2>&1; then
    echo "   Installing dependencies. This happens once and takes a minute..."
    echo
    if ! "$PY" -m pip install --quiet -r requirements.txt; then
        echo "   Could not install dependencies automatically."
        echo "   Try running this by hand:  python3 -m pip install -r requirements.txt"
        echo
        read -n 1 -s -r -p "   Press any key to close..."
        exit 1
    fi
fi

echo "   Looking for a game on your network..."
echo
"$PY" client.py "$@"

echo
read -n 1 -s -r -p "   Press any key to close..."
