from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List, Optional

from .models import FileRecord, VaultStateRecord, WikiTaskRecord

SCHEMA_VERSION = 1


def open_database(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def bootstrap_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          applied_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vault_state (
          vault_id TEXT PRIMARY KEY,
          schema_version TEXT NOT NULL,
          last_applied_revision INTEGER NOT NULL,
          remote_head_revision INTEGER NOT NULL,
          acked_revision INTEGER NOT NULL,
          pending_ack_to_server TEXT NOT NULL,
          commit_in_progress INTEGER NOT NULL,
          last_manifest_summary TEXT,
          last_manifest_summary_status TEXT NOT NULL,
          local_delete_sequence INTEGER NOT NULL,
          has_unresolved_conflicts INTEGER NOT NULL DEFAULT 0,
          meta TEXT
        );

        CREATE TABLE IF NOT EXISTS file_index (
          vault_id TEXT NOT NULL,
          file_id TEXT NOT NULL,
          path TEXT NOT NULL,
          type TEXT NOT NULL,
          status TEXT NOT NULL,
          content_hash TEXT,
          last_known_revision INTEGER,
          local_mtime INTEGER,
          size INTEGER,
          updated_at INTEGER NOT NULL,
          PRIMARY KEY (vault_id, file_id)
        );

        CREATE TABLE IF NOT EXISTS wiki_tasks (
          task_id TEXT PRIMARY KEY,
          target_wiki_path TEXT NOT NULL,
          task_base_page_hash TEXT,
          task_base_revision INTEGER NOT NULL,
          task_sources_hash TEXT NOT NULL,
          status TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_wiki_tasks_active_path
          ON wiki_tasks (target_wiki_path)
          WHERE status IN ('pending', 'running');
        """
    )
    try:
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS search_index
            USING fts5(
              vault_id UNINDEXED,
              file_id UNINDEXED,
              path,
              content
            );
            """
        )
    except sqlite3.OperationalError:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS search_index (
              vault_id TEXT NOT NULL,
              file_id TEXT NOT NULL,
              path TEXT NOT NULL,
              content TEXT NOT NULL,
              PRIMARY KEY (vault_id, file_id)
            );
            """
        )

    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, strftime('%s','now') * 1000)",
        (SCHEMA_VERSION,),
    )
    connection.commit()


def upsert_vault_state(connection: sqlite3.Connection, record: VaultStateRecord) -> None:
    payload = record.to_dict()
    connection.execute(
        """
        INSERT INTO vault_state (
          vault_id,
          schema_version,
          last_applied_revision,
          remote_head_revision,
          acked_revision,
          pending_ack_to_server,
          commit_in_progress,
          last_manifest_summary,
          last_manifest_summary_status,
          local_delete_sequence,
          has_unresolved_conflicts,
          meta
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(vault_id) DO UPDATE SET
          schema_version = excluded.schema_version,
          last_applied_revision = excluded.last_applied_revision,
          remote_head_revision = excluded.remote_head_revision,
          acked_revision = excluded.acked_revision,
          pending_ack_to_server = excluded.pending_ack_to_server,
          commit_in_progress = excluded.commit_in_progress,
          last_manifest_summary = excluded.last_manifest_summary,
          last_manifest_summary_status = excluded.last_manifest_summary_status,
          local_delete_sequence = excluded.local_delete_sequence,
          has_unresolved_conflicts = excluded.has_unresolved_conflicts,
          meta = excluded.meta
        """,
        (
            payload["vault_id"],
            payload["schema_version"],
            payload["last_applied_revision"],
            payload["remote_head_revision"],
            payload["acked_revision"],
            json.dumps(payload["pending_ack_to_server"]),
            int(payload["commit_in_progress"]),
            payload["last_manifest_summary"],
            payload["last_manifest_summary_status"],
            payload["local_delete_sequence"],
            int(payload["has_unresolved_conflicts"]),
            json.dumps(payload["meta"]) if payload.get("meta") is not None else None,
        ),
    )
    connection.commit()


def load_vault_state(connection: sqlite3.Connection, vault_id: str) -> Optional[VaultStateRecord]:
    row = connection.execute("SELECT * FROM vault_state WHERE vault_id = ?", (vault_id,)).fetchone()
    if row is None:
        return None
    return VaultStateRecord(
        schema_version=row["schema_version"],
        vault_id=row["vault_id"],
        last_applied_revision=row["last_applied_revision"],
        remote_head_revision=row["remote_head_revision"],
        acked_revision=row["acked_revision"],
        pending_ack_to_server=json.loads(row["pending_ack_to_server"]),
        commit_in_progress=bool(row["commit_in_progress"]),
        last_manifest_summary=row["last_manifest_summary"],
        last_manifest_summary_status=row["last_manifest_summary_status"],
        local_delete_sequence=row["local_delete_sequence"],
        has_unresolved_conflicts=bool(row["has_unresolved_conflicts"]),
        meta=json.loads(row["meta"]) if row["meta"] else None,
    )


def upsert_file_index_entry(
    connection: sqlite3.Connection,
    *,
    vault_id: str,
    record: FileRecord,
    local_mtime: Optional[int] = None,
    size: Optional[int] = None,
) -> None:
    connection.execute(
        """
        INSERT INTO file_index (
          vault_id,
          file_id,
          path,
          type,
          status,
          content_hash,
          last_known_revision,
          local_mtime,
          size,
          updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(vault_id, file_id) DO UPDATE SET
          path = excluded.path,
          type = excluded.type,
          status = excluded.status,
          content_hash = excluded.content_hash,
          last_known_revision = excluded.last_known_revision,
          local_mtime = excluded.local_mtime,
          size = excluded.size,
          updated_at = excluded.updated_at
        """,
        (
            vault_id,
            record.file_id,
            record.path,
            record.type,
            record.status,
            record.content_hash,
            record.last_known_revision,
            local_mtime,
            size,
            record.updated_at,
        ),
    )
    connection.commit()


def list_file_index(connection: sqlite3.Connection, vault_id: str) -> List[sqlite3.Row]:
    return list(
        connection.execute(
            "SELECT * FROM file_index WHERE vault_id = ? ORDER BY path, file_id",
            (vault_id,),
        ).fetchall()
    )


def replace_active_wiki_task(connection: sqlite3.Connection, record: WikiTaskRecord) -> None:
    with connection:
        connection.execute(
            """
            UPDATE wiki_tasks
            SET status = 'superseded',
                updated_at = ?
            WHERE target_wiki_path = ?
              AND status IN ('pending', 'running')
            """,
            (record.updated_at, record.target_wiki_path),
        )
        connection.execute(
            """
            INSERT INTO wiki_tasks (
              task_id,
              target_wiki_path,
              task_base_page_hash,
              task_base_revision,
              task_sources_hash,
              status,
              created_at,
              updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.task_id,
                record.target_wiki_path,
                record.task_base_page_hash,
                record.task_base_revision,
                record.task_sources_hash,
                record.status,
                record.created_at,
                record.updated_at,
            ),
        )
