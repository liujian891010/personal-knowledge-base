from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from .manifest import EMPTY_VAULT_FINAL_MANIFEST_SUMMARY, compute_manifest_summary_hash
from .models import (
    CommitIntentJournalRecord,
    FileRecord,
    ManifestRecord,
    SyncApplyJournalRecord,
    VaultStateRecord,
    WikiTaskRecord,
)
from .recovery import (
    apply_commit_success_state,
    apply_orphaned_commit_lock_recovery,
    apply_prepared_commit_recovery,
    apply_submitted_commit_match_recovery,
    apply_submitted_commit_miss_recovery,
    apply_sync_finalizing_recovery,
    normalize_commit_journal_for_recovery,
)

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

        CREATE TABLE IF NOT EXISTS sync_apply_journal (
          vault_id TEXT PRIMARY KEY,
          journal_id TEXT NOT NULL,
          target_revision INTEGER NOT NULL,
          target_manifest_hash TEXT NOT NULL,
          phase TEXT NOT NULL,
          ops_hash TEXT,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS commit_intent_journal (
          vault_id TEXT PRIMARY KEY,
          commit_intent_id TEXT NOT NULL,
          intent_manifest_hash TEXT NOT NULL,
          base_revision INTEGER NOT NULL,
          created_by_device TEXT NOT NULL,
          status TEXT NOT NULL,
          intent_delete_seq_upper_bound INTEGER,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS note_links (
          vault_id TEXT NOT NULL,
          source_file_id TEXT NOT NULL,
          source_path TEXT NOT NULL,
          link_text TEXT NOT NULL,
          target_file_id TEXT,
          target_path TEXT,
          ordinal INTEGER NOT NULL,
          PRIMARY KEY (vault_id, source_file_id, ordinal)
        );

        CREATE INDEX IF NOT EXISTS idx_note_links_target_file
          ON note_links (vault_id, target_file_id);
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


def build_initial_vault_state(
    vault_id: str,
    *,
    local_delete_sequence: int = 0,
    has_unresolved_conflicts: bool = False,
    meta: Optional[Dict[str, Any]] = None,
) -> VaultStateRecord:
    return VaultStateRecord(
        vault_id=vault_id,
        last_applied_revision=0,
        remote_head_revision=0,
        acked_revision=0,
        pending_ack_to_server=[],
        commit_in_progress=False,
        last_manifest_summary=EMPTY_VAULT_FINAL_MANIFEST_SUMMARY,
        last_manifest_summary_status="valid",
        local_delete_sequence=local_delete_sequence,
        has_unresolved_conflicts=has_unresolved_conflicts,
        meta=meta,
    )


def initialize_vault_state(
    connection: sqlite3.Connection,
    vault_id: str,
    *,
    local_delete_sequence: int = 0,
    has_unresolved_conflicts: bool = False,
    meta: Optional[Dict[str, Any]] = None,
) -> VaultStateRecord:
    existing = load_vault_state(connection, vault_id)
    if existing is not None:
        return existing

    record = build_initial_vault_state(
        vault_id,
        local_delete_sequence=local_delete_sequence,
        has_unresolved_conflicts=has_unresolved_conflicts,
        meta=meta,
    )
    upsert_vault_state(connection, record)
    return record


def upsert_vault_state(connection: sqlite3.Connection, record: VaultStateRecord) -> None:
    _upsert_vault_state(connection, record)
    connection.commit()


def _upsert_vault_state(connection: sqlite3.Connection, record: VaultStateRecord) -> None:
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


def _search_index_is_fts(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'search_index'"
    ).fetchone()
    return row is not None and "VIRTUAL TABLE" in (row["sql"] or "").upper()


def replace_search_index_entries(
    connection: sqlite3.Connection,
    vault_id: str,
    entries: List[Dict[str, str]],
) -> None:
    with connection:
        connection.execute("DELETE FROM search_index WHERE vault_id = ?", (vault_id,))
        connection.executemany(
            """
            INSERT INTO search_index (vault_id, file_id, path, content)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    vault_id,
                    entry["file_id"],
                    entry["path"],
                    entry["content"],
                )
                for entry in entries
            ],
        )


def _build_fts_query(query: str) -> str:
    terms = [term.strip().replace('"', '""') for term in query.split() if term.strip()]
    if not terms:
        raise ValueError("search query must contain at least one term")
    return " ".join(f'"{term}"' for term in terms)


def search_index(
    connection: sqlite3.Connection,
    vault_id: str,
    query: str,
    *,
    limit: int = 20,
) -> List[sqlite3.Row]:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("search query must be non-empty")
    resolved_limit = max(1, min(limit, 100))
    if _search_index_is_fts(connection):
        return list(
            connection.execute(
                """
                SELECT
                  file_id,
                  path,
                  snippet(search_index, 3, '[', ']', '...', 12) AS snippet
                FROM search_index
                WHERE vault_id = ?
                  AND search_index MATCH ?
                ORDER BY bm25(search_index)
                LIMIT ?
                """,
                (vault_id, _build_fts_query(normalized_query), resolved_limit),
            ).fetchall()
        )

    pattern = f"%{normalized_query}%"
    return list(
        connection.execute(
            """
            SELECT file_id, path, substr(content, 1, 240) AS snippet
            FROM search_index
            WHERE vault_id = ?
              AND (path LIKE ? OR content LIKE ?)
            ORDER BY path, file_id
            LIMIT ?
            """,
            (vault_id, pattern, pattern, resolved_limit),
        ).fetchall()
    )


def replace_note_links(
    connection: sqlite3.Connection,
    vault_id: str,
    entries: List[Dict[str, Any]],
) -> None:
    with connection:
        connection.execute("DELETE FROM note_links WHERE vault_id = ?", (vault_id,))
        connection.executemany(
            """
            INSERT INTO note_links (
              vault_id,
              source_file_id,
              source_path,
              link_text,
              target_file_id,
              target_path,
              ordinal
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    vault_id,
                    entry["source_file_id"],
                    entry["source_path"],
                    entry["link_text"],
                    entry.get("target_file_id"),
                    entry.get("target_path"),
                    entry["ordinal"],
                )
                for entry in entries
            ],
        )


def list_note_links_for_file(
    connection: sqlite3.Connection,
    vault_id: str,
    file_id: str,
) -> List[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT *
            FROM note_links
            WHERE vault_id = ?
              AND source_file_id = ?
            ORDER BY ordinal
            """,
            (vault_id, file_id),
        ).fetchall()
    )


def list_note_backlinks_for_file(
    connection: sqlite3.Connection,
    vault_id: str,
    file_id: str,
) -> List[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT *
            FROM note_links
            WHERE vault_id = ?
              AND target_file_id = ?
            ORDER BY source_path, ordinal
            """,
            (vault_id, file_id),
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


def upsert_sync_apply_journal(connection: sqlite3.Connection, record: SyncApplyJournalRecord) -> None:
    connection.execute(
        """
        INSERT INTO sync_apply_journal (
          vault_id,
          journal_id,
          target_revision,
          target_manifest_hash,
          phase,
          ops_hash,
          created_at,
          updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(vault_id) DO UPDATE SET
          journal_id = excluded.journal_id,
          target_revision = excluded.target_revision,
          target_manifest_hash = excluded.target_manifest_hash,
          phase = excluded.phase,
          ops_hash = excluded.ops_hash,
          created_at = excluded.created_at,
          updated_at = excluded.updated_at
        """,
        (
            record.vault_id,
            record.journal_id,
            record.target_revision,
            record.target_manifest_hash,
            record.phase,
            record.ops_hash,
            record.created_at,
            record.updated_at,
        ),
    )
    connection.commit()


def load_sync_apply_journal(connection: sqlite3.Connection, vault_id: str) -> Optional[SyncApplyJournalRecord]:
    row = connection.execute("SELECT * FROM sync_apply_journal WHERE vault_id = ?", (vault_id,)).fetchone()
    if row is None:
        return None
    return SyncApplyJournalRecord(
        vault_id=row["vault_id"],
        journal_id=row["journal_id"],
        target_revision=row["target_revision"],
        target_manifest_hash=row["target_manifest_hash"],
        phase=row["phase"],
        ops_hash=row["ops_hash"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def clear_sync_apply_journal(connection: sqlite3.Connection, vault_id: str) -> None:
    connection.execute("DELETE FROM sync_apply_journal WHERE vault_id = ?", (vault_id,))
    connection.commit()


def upsert_commit_intent_journal(connection: sqlite3.Connection, record: CommitIntentJournalRecord) -> None:
    connection.execute(
        """
        INSERT INTO commit_intent_journal (
          vault_id,
          commit_intent_id,
          intent_manifest_hash,
          base_revision,
          created_by_device,
          status,
          intent_delete_seq_upper_bound,
          created_at,
          updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(vault_id) DO UPDATE SET
          commit_intent_id = excluded.commit_intent_id,
          intent_manifest_hash = excluded.intent_manifest_hash,
          base_revision = excluded.base_revision,
          created_by_device = excluded.created_by_device,
          status = excluded.status,
          intent_delete_seq_upper_bound = excluded.intent_delete_seq_upper_bound,
          created_at = excluded.created_at,
          updated_at = excluded.updated_at
        """,
        (
            record.vault_id,
            record.commit_intent_id,
            record.intent_manifest_hash,
            record.base_revision,
            record.created_by_device,
            record.status,
            record.intent_delete_seq_upper_bound,
            record.created_at,
            record.updated_at,
        ),
    )
    connection.commit()


def load_commit_intent_journal(connection: sqlite3.Connection, vault_id: str) -> Optional[CommitIntentJournalRecord]:
    row = connection.execute("SELECT * FROM commit_intent_journal WHERE vault_id = ?", (vault_id,)).fetchone()
    if row is None:
        return None
    return CommitIntentJournalRecord(
        vault_id=row["vault_id"],
        commit_intent_id=row["commit_intent_id"],
        intent_manifest_hash=row["intent_manifest_hash"],
        base_revision=row["base_revision"],
        created_by_device=row["created_by_device"],
        status=row["status"],
        intent_delete_seq_upper_bound=row["intent_delete_seq_upper_bound"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def normalize_legacy_acknowledged_commit_intent(
    connection: sqlite3.Connection,
    vault_id: str,
    *,
    normalized_at: int,
) -> Optional[CommitIntentJournalRecord]:
    current = load_commit_intent_journal(connection, vault_id)
    if current is None:
        return None
    if current.status != "acknowledged":
        return current

    normalized = CommitIntentJournalRecord(
        vault_id=current.vault_id,
        commit_intent_id=current.commit_intent_id,
        intent_manifest_hash=current.intent_manifest_hash,
        base_revision=current.base_revision,
        created_by_device=current.created_by_device,
        status="submitted",
        intent_delete_seq_upper_bound=current.intent_delete_seq_upper_bound,
        created_at=current.created_at,
        updated_at=normalized_at,
    )
    upsert_commit_intent_journal(connection, normalized)
    return normalized


def clear_commit_intent_journal(connection: sqlite3.Connection, vault_id: str) -> None:
    connection.execute("DELETE FROM commit_intent_journal WHERE vault_id = ?", (vault_id,))
    connection.commit()


def recover_sync_apply_finalizing_state(
    connection: sqlite3.Connection,
    vault_id: str,
) -> VaultStateRecord:
    state = load_vault_state(connection, vault_id)
    journal = load_sync_apply_journal(connection, vault_id)
    if state is None or journal is None:
        raise KeyError("vault_state and sync_apply_journal must both exist")

    recovered = apply_sync_finalizing_recovery(state, journal)
    with connection:
        _upsert_vault_state(connection, recovered)
        connection.execute("DELETE FROM sync_apply_journal WHERE vault_id = ?", (vault_id,))
    return recovered


def recover_prepared_commit_cleanup(
    connection: sqlite3.Connection,
    vault_id: str,
) -> VaultStateRecord:
    state = load_vault_state(connection, vault_id)
    journal = load_commit_intent_journal(connection, vault_id)
    if state is None or journal is None:
        raise KeyError("vault_state and commit_intent_journal must both exist")
    if journal.status != "prepared":
        raise ValueError("prepared recovery requires a prepared journal")

    recovered = apply_prepared_commit_recovery(state)
    with connection:
        _upsert_vault_state(connection, recovered)
        connection.execute("DELETE FROM commit_intent_journal WHERE vault_id = ?", (vault_id,))
    return recovered


def recover_orphaned_commit_lock(
    connection: sqlite3.Connection,
    vault_id: str,
) -> VaultStateRecord:
    state = load_vault_state(connection, vault_id)
    journal = load_commit_intent_journal(connection, vault_id)
    if state is None:
        raise KeyError("vault_state must exist")
    if journal is not None:
        raise ValueError("orphaned commit lock recovery requires no active journal")
    if not state.commit_in_progress:
        raise ValueError("orphaned commit lock recovery requires commit_in_progress=true")

    recovered = apply_orphaned_commit_lock_recovery(state)
    with connection:
        _upsert_vault_state(connection, recovered)
    return recovered


def finalize_committed_state(
    connection: sqlite3.Connection,
    vault_id: str,
    *,
    committed_revision: int,
    manifest_summary: str,
) -> VaultStateRecord:
    state = load_vault_state(connection, vault_id)
    journal = load_commit_intent_journal(connection, vault_id)
    if state is None or journal is None:
        raise KeyError("vault_state and commit_intent_journal must both exist")
    if journal.status != "submitted":
        raise ValueError("commit finalization requires a submitted journal")

    updated = apply_commit_success_state(
        state,
        committed_revision=committed_revision,
        manifest_summary=manifest_summary,
    )
    with connection:
        _upsert_vault_state(connection, updated)
        connection.execute("DELETE FROM commit_intent_journal WHERE vault_id = ?", (vault_id,))
    return updated


def recover_submitted_commit_match(
    connection: sqlite3.Connection,
    vault_id: str,
    *,
    matched_revision: int,
    observed_head_revision: int,
    matched_manifest_summary: Optional[str],
    normalized_at: int,
) -> VaultStateRecord:
    state = load_vault_state(connection, vault_id)
    journal = load_commit_intent_journal(connection, vault_id)
    if state is None or journal is None:
        raise KeyError("vault_state and commit_intent_journal must both exist")

    normalized = normalize_commit_journal_for_recovery(journal, normalized_at=normalized_at)
    recovered = apply_submitted_commit_match_recovery(
        state,
        matched_revision=matched_revision,
        observed_head_revision=observed_head_revision,
        matched_manifest_summary=matched_manifest_summary,
    )
    with connection:
        if normalized != journal:
            connection.execute(
                """
                UPDATE commit_intent_journal
                SET status = ?, updated_at = ?
                WHERE vault_id = ?
                """,
                (normalized.status, normalized.updated_at, vault_id),
            )
        _upsert_vault_state(connection, recovered)
        connection.execute("DELETE FROM commit_intent_journal WHERE vault_id = ?", (vault_id,))
    return recovered


def recover_submitted_commit_from_manifest(
    connection: sqlite3.Connection,
    vault_id: str,
    *,
    matched_manifest: Optional[ManifestRecord],
    matched_revision: int,
    observed_head_revision: int,
    normalized_at: int,
) -> VaultStateRecord:
    return recover_submitted_commit_match(
        connection,
        vault_id,
        matched_revision=matched_revision,
        observed_head_revision=observed_head_revision,
        matched_manifest_summary=(
            None if matched_manifest is None else compute_manifest_summary_hash(matched_manifest)
        ),
        normalized_at=normalized_at,
    )


def recover_submitted_commit_miss(
    connection: sqlite3.Connection,
    vault_id: str,
    *,
    normalized_at: int,
) -> VaultStateRecord:
    state = load_vault_state(connection, vault_id)
    journal = load_commit_intent_journal(connection, vault_id)
    if state is None or journal is None:
        raise KeyError("vault_state and commit_intent_journal must both exist")

    normalized = normalize_commit_journal_for_recovery(journal, normalized_at=normalized_at)
    recovered = apply_submitted_commit_miss_recovery(state)
    with connection:
        if normalized != journal:
            connection.execute(
                """
                UPDATE commit_intent_journal
                SET status = ?, updated_at = ?
                WHERE vault_id = ?
                """,
                (normalized.status, normalized.updated_at, vault_id),
            )
        _upsert_vault_state(connection, recovered)
        connection.execute("DELETE FROM commit_intent_journal WHERE vault_id = ?", (vault_id,))
    return recovered
