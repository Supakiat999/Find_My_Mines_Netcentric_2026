"""Run every test suite and print one summary.

    python tests/run_all.py            everything
    python tests/run_all.py rules ai   just those (matches the file names)

Each suite is its own script and runs in its own process, headless: no window
opens and no sound plays.  Exit status is 0 only if every suite passes.
"""

import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

SUITES = [
    ("rules", "test_rules.py", "every mode, scoring, stats, seat renaming"),
    ("elo", "test_elo.py", "per-mode Elo formulas, persistence, isolation, migration"),
    ("database", "test_database.py", "PostgreSQL persistence (skips without TEST_DATABASE_URL)"),
    ("database_server", "test_database_server.py", "server commit, retry and restart over PostgreSQL"),
    ("ai", "test_ai.py", "probability engine: accuracy, strength, speed"),
    ("network", "test_network.py", "the server over real TCP, all modes"),
    ("server_features", "test_server_features.py",
     "chat, coach, computer opponent, leaderboard, reconnect"),
    ("client", "test_client.py", "two real windows playing each other"),
    ("client_features", "test_client_features.py",
     "opponent picker, chat, coach, sound, themes, scaling, reconnect"),
    ("layout", "test_layout.py", "no overlapping or spilling text, 41 scenarios"),
    ("bot", "test_bot.py", "RL bots: observation, symmetry, solo env (skips without torch)"),
]


def main():
    wanted = sys.argv[1:]
    chosen = [s for s in SUITES if not wanted or any(w in s[0] for w in wanted)]
    if not chosen:
        print("no suite matches", wanted)
        return 2

    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy",
               PYGAME_HIDE_SUPPORT_PROMPT="1", PYTHONIOENCODING="utf-8")
    failures = []
    print("%-16s %-8s %s" % ("suite", "result", "what it covers"))
    print("-" * 78)
    for name, script, blurb in chosen:
        started = time.time()
        proc = subprocess.run([sys.executable, os.path.join(HERE, script)],
                              cwd=HERE, env=env, capture_output=True, text=True)
        seconds = time.time() - started
        passed = proc.returncode == 0
        checks = sum(1 for line in proc.stdout.splitlines()
                     if re.match(r"^\d+[a-z]? OK ", line))
        print("%-16s %-8s %s   (%d checks, %.0fs)"
              % (name, "passed" if passed else "FAILED", blurb, checks, seconds))
        if not passed:
            failures.append((name, proc))
    print("-" * 78)
    for name, proc in failures:
        print("\n=== %s failed ===" % name)
        print("\n".join(proc.stdout.splitlines()[-15:]))
        print(proc.stderr[-1500:])
    if failures:
        print("\n%d of %d suites failed" % (len(failures), len(chosen)))
        return 1
    print("all %d suites passed" % len(chosen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
