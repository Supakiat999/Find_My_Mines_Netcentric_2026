"""Check automatic .env loading without touching project credentials."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from helpers import ROOT, ok


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    shutil.copyfile(Path(ROOT, "config.py"), root / "config.py")
    cwd = root / "other_directory"
    cwd.mkdir()

    def check(expected, override=None):
        env = dict(os.environ, PYTHONPATH=str(root))
        env.pop("DATABASE_URL", None)
        env.pop("TEST_PASSWORD", None)
        if override is not None:
            env["DATABASE_URL"] = override
        code = "import config; assert config.DATABASE_URL == %r" % expected
        subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env, check=True)

    check(None)
    (root / ".env").write_text(
        "TEST_PASSWORD=mock-secret\n"
        "DATABASE_URL=postgresql://runtime:${TEST_PASSWORD}@localhost/test\n",
        encoding="utf-8")
    check("postgresql://runtime:mock-secret@localhost/test")
    check("postgresql://override/test", override="postgresql://override/test")
    check(None, override="")
    ok(1, ".env loads from project directory, expands variables and preserves explicit environment overrides")
