from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Restore a noteapp-server SQLite backup.")
    parser.add_argument("--backup-path", required=True, type=Path, help="Source backup SQLite file")
    parser.add_argument("--sqlite-db", required=True, type=Path, help="Destination SQLite database path")
    parser.add_argument("--force", action="store_true", help="Replace an existing destination database")
    return parser.parse_args()


def restore_sqlite(backup_path: Path, sqlite_db: Path, *, force: bool = False) -> Path:
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup path does not exist: {backup_path}")
    if backup_path.resolve() == sqlite_db.resolve():
        raise ValueError("Restore destination must be different from the backup source.")
    if sqlite_db.exists() and not force:
        raise FileExistsError(f"Destination database already exists: {sqlite_db}")

    sqlite_db.parent.mkdir(parents=True, exist_ok=True)
    temp_path = sqlite_db.with_name(f"{sqlite_db.name}.restore.tmp")
    if temp_path.exists():
        temp_path.unlink()

    source = sqlite3.connect(str(backup_path))
    try:
        destination = sqlite3.connect(str(temp_path))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    if sqlite_db.exists():
        sqlite_db.unlink()
    wal_path = sqlite_db.with_name(f"{sqlite_db.name}-wal")
    shm_path = sqlite_db.with_name(f"{sqlite_db.name}-shm")
    wal_path.unlink(missing_ok=True)
    shm_path.unlink(missing_ok=True)
    temp_path.replace(sqlite_db)
    return sqlite_db


def main() -> int:
    args = parse_args()
    try:
        restored = restore_sqlite(args.backup_path, args.sqlite_db, force=args.force)
    except (FileExistsError, FileNotFoundError, ValueError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(f"Restored {args.backup_path} to {restored}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
