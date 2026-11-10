#!/usr/bin/env python3
"""Read-only live check against a real Music Assistant server.

Makes no playback, volume, queue, group, favorite, or announcement changes.
Requires MASS_SERVER_URL and MASS_ACCESS_TOKEN. Run from the project root:

    MASS_LIVE_TESTS=1 .venv/bin/python tests/live_readonly_check.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACTL = ROOT / "scripts" / "mactl.py"

READ_ONLY_CASES = [
    ["players"],
    ["recent", "--limit", "3"],
    ["search", "miles davis", "--type", "track", "--limit", "2"],
]


def main() -> int:
    if os.environ.get("MASS_LIVE_TESTS") != "1":
        print("set MASS_LIVE_TESTS=1 to run live checks", file=sys.stderr)
        return 2
    cases = list(READ_ONLY_CASES)
    if os.environ.get("MASS_DEFAULT_PLAYER"):
        cases.insert(1, ["status"])  # needs a resolvable player
    failures = 0
    for args in cases:
        proc = subprocess.run(
            [sys.executable, str(MACTL), *args],
            capture_output=True, text=True, timeout=120, env=os.environ.copy(),
        )
        status = "PASS" if proc.returncode == 0 else f"FAIL (exit {proc.returncode})"
        print(f"{status:<18} mactl {' '.join(args)}")
        if proc.returncode != 0:
            failures += 1
            print(proc.stderr.strip() or proc.stdout.strip(), file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
