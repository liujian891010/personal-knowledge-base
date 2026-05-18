from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a consistent SQLite backup for noteapp-server.")
    parser.add_argument("--sqlite-db", required=True, type=Path, help="Source SQLite database path")
    parser.add_argument("--backup-path", type=Path, help="Destination backup file path")
    parser.add_argument("--backup-dir", type=Path, help="Destination directory when --backup-path is not provided")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing backup path")
    return parser.parse_args()


def default_backup_path(sqlite_db: Path, backup_dir: Path | None) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination_dir = backup_dir or sqlite_db.parent / "backups"
    return destination_dir / f"{sqlite_db.stem}-{timestamp}.sqlite3"


def backup_sqlite(sqlite_db: Path, backup_path: Path, *, force: bool = False) -> Path:
    if not sqlite_db.exists():
        raise FileNotFoundError(f"SQLite database does not exist: {sqlite_db}")
    if sqlite_db.resolve() == backup_path.resolve():
        raise ValueError("Backup path must be different from the source SQLite database.")
    if backup_path.exists() and not force:
        raise FileExistsError(f"Backup path already exists: {backup_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_path.exists():
        backup_path.unlink()

    source = sqlite3.connect(str(sqlite_db))
    try:
        destination = sqlite3.connect(str(backup_path))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return backup_path


def main() -> int:
    args = parse_args()
    backup_path = args.backup_path or default_backup_path(args.sqlite_db, args.backup_dir)
    try:
        created = backup_sqlite(args.sqlite_db, backup_path, force=args.force)
    except (FileExistsError, FileNotFoundError, ValueError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(f"Backed up {args.sqlite_db} to {created}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
