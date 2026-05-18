from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: run_in_directory.py <cwd> <command> [args...]", file=sys.stderr)
        return 2

    cwd = Path(sys.argv[1]).resolve()
    command = sys.argv[2:]
    if not cwd.is_dir():
        print(f"working directory was not found: {cwd}", file=sys.stderr)
        return 2

    return subprocess.run(command, cwd=cwd).returncode


if __name__ == "__main__":
    raise SystemExit(main())
