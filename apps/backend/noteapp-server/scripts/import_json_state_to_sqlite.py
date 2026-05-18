from __future__ import annotations

import argparse
import sys
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from sync_repository import SQLiteStateRepository, StateRepositoryConflict  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import noteapp-server JSON state into the SQLite repository.")
    parser.add_argument("--json-state", required=True, type=Path, help="Path to .data/sync-state.json")
    parser.add_argument("--sqlite-db", required=True, type=Path, help="Destination SQLite database path")
    parser.add_argument("--force", action="store_true", help="Replace an existing SQLite sync_state row")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.json_state.exists():
        print(f"JSON state does not exist: {args.json_state}", file=sys.stderr)
        return 2
    try:
        SQLiteStateRepository(args.sqlite_db).import_json_file(args.json_state, force=args.force)
    except StateRepositoryConflict as error:
        print(f"{error.code}: {error}", file=sys.stderr)
        return 3
    print(f"Imported {args.json_state} into {args.sqlite_db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
