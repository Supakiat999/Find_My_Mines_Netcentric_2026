#!/usr/bin/env python3
"""Import/export stats JSON using DATABASE_URL."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stats
from database import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("import", "export"))
    parser.add_argument("path", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        parser.error("set DATABASE_URL before importing or exporting")
    store = Store(url)
    if args.action == "import":
        with args.path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict) or not raw or not all(isinstance(k, str) and isinstance(v, dict)
                                               for k, v in raw.items()):
            parser.error("expected a nonempty nickname-to-record JSON object")
        board = stats.Leaderboard(None)
        board.load_records(raw)
        if len(board.data) != len(raw):
            parser.error("nicknames collide after normalization; resolve them before import")
        store.import_data(board.data)
    else:
        if args.path.exists() and not args.force:
            parser.error("refusing to overwrite existing file; pass --force")
        with args.path.open("w" if args.force else "x", encoding="utf-8") as handle:
            json.dump(store.load(), handle, indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
