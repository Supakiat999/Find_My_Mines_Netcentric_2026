#!/bin/bash
# Double-click this to run the server on a Mac.
# Everyone else connects to the address shown in the window that opens.
# If it will not open, run this once in Terminal:  chmod +x HOST-Mac.command
cd "$(dirname "$0")" || exit 1

echo
echo "   FIND MY MINES - server"
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
    "$PY" -m pip install --quiet pygame || {
        echo "   Could not install pygame automatically."
        echo "   Try running this by hand:  python3 -m pip install pygame"
        echo
        read -n 1 -s -r -p "   Press any key to close..."
        exit 1
    }
fi

echo "   Starting the server. Read the address at the top of the window"
echo "   and send that to the other players. Keep this window open."
echo
"$PY" server.py

echo
read -n 1 -s -r -p "   Press any key to close..."
