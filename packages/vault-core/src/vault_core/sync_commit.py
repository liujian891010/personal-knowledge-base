from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

from .ledger import rewrite_tombstone_ledger
from .manifest import compute_intent_manifest_hash, finalize_manifest_revision
from .models import (
    CommitIntentJournalRecord,
    FileMapDocument,
    FileRecord,
    ManifestFileEntry,
    ManifestRecord,
    TombstoneRecord,
    VaultStateRecord,
)
from .recovery import apply_committed_tombstones, should_block_new_commit
from .sqlite_store import (
    finalize_committed_state,
    load_commit_intent_journal,
    upsert_commit_intent_journal,
    upsert_vault_state,
)


def _manifest_file_meta(record: FileRecord) -> Mapping[str, object]:
    if record.meta is None:
        raise ValueError(f"active file is missing manifest metadata: {record.file_id}")
    return record.meta


def _build_manifest_file_entry(record: FileRecord) -> ManifestFileEntry:
    if record.content_hash is None:
        raise ValueError(f"active file is missing content_hash: {record.file_id}")
    meta = _manifest_file_meta(record)
    blob_id = meta.get("blob_id")
    size = meta.get("size")
    mtime = meta.get("mtime")
    mime_type = meta.get("mime_type")
    if not isinstance(blob_id, str) or not blob_id:
        raise ValueError(f"active file is missing blob_id: {record.file_id}")
    if not isinstance(size, int) or size < 0:
        raise ValueError(f"active file is missing size: {record.file_id}")
    if not isinstance(mtime, int) or mtime < 0:
        raise ValueError(f"active file is missing mtime: {record.file_id}")
    if mime_type is not None and not isinstance(mime_type, str):
        raise ValueError(f"active file has invalid mime_type: {record.file_id}")

    return ManifestFileEntry(
        file_id=record.file_id,
        path=record.path,
        type=record.type,
        content_hash=record.content_hash,
        blob_id=blob_id,
        size=size,
        mtime=mtime,
        mime_type=mime_type,
    )


def build_commit_manifest(
    document: FileMapDocument,
    *,
    tombstones: Iterable[TombstoneRecord],
    base_revision: int,
    created_by_device: str,
    created_at: int,
) -> ManifestRecord:
    tombstone_list = list(tombstones)
    tombstone_ids = {record.file_id for record in tombstone_list}
    deleted_ids = {
        record.file_id
        for record in document.files
        if record.status == "deleted"
    }
    missing_tombstones = sorted(deleted_ids - tombstone_ids)
    if missing_tombstones:
        raise ValueError(f"deleted filemap entries are missing tombstones: {', '.join(missing_tombstones)}")

    files = [
        _build_manifest_file_entry(record)
        for record in document.files
        if record.status == "active"
    ]
    return ManifestRecord(
        vault_id=document.vault_id,
        revision=0,
        base_revision=base_revision,
        created_by_device=created_by_device,
        created_at=created_at,
        files=files,
        tombstones=tombstone_list,
        summary_hash="pending",
    )


def apply_commit_submitted_state(state: VaultStateRecord) -> VaultStateRecord:
    return VaultStateRecord(
        vault_id=state.vault_id,
        last_applied_revision=state.last_applied_revision,
        remote_head_revision=state.remote_head_revision,
        acked_revision=state.acked_revision,
        pending_ack_to_server=list(state.pending_ack_to_server),
        commit_in_progress=True,
        last_manifest_summary=state.last_manifest_summary,
        last_manifest_summary_status=state.last_manifest_summary_status,
        local_delete_sequence=state.local_delete_sequence,
        has_unresolved_conflicts=state.has_unresolved_conflicts,
        schema_version=state.schema_version,
        meta=state.meta,
    )


@dataclass(frozen=True)
class CommitSubmissionBundle:
    manifest: ManifestRecord
    intent_manifest_hash: str
    journal: CommitIntentJournalRecord
    state: VaultStateRecord


@dataclass(frozen=True)
class CommitFinalizeResult:
    manifest: ManifestRecord
    tombstones: list[TombstoneRecord]
    state: VaultStateRecord


def prepare_commit_submission(
    connection: sqlite3.Connection,
    *,
    state: VaultStateRecord,
    document: FileMapDocument,
    tombstones: Iterable[TombstoneRecord],
    commit_intent_id: str,
    created_by_device: str,
    created_at: int,
) -> CommitSubmissionBundle:
    has_active_commit_journal = load_commit_intent_journal(connection, state.vault_id) is not None
    if should_block_new_commit(state, has_active_commit_journal=has_active_commit_journal):
        raise ValueError("vault_state is not eligible to start a new commit")

    manifest = build_commit_manifest(
        document,
        tombstones=tombstones,
        base_revision=state.last_applied_revision,
        created_by_device=created_by_device,
        created_at=created_at,
    )
    intent_manifest_hash = compute_intent_manifest_hash(manifest)
    journal = CommitIntentJournalRecord(
        vault_id=state.vault_id,
        commit_intent_id=commit_intent_id,
        intent_manifest_hash=intent_manifest_hash,
        base_revision=state.last_applied_revision,
        created_by_device=created_by_device,
        status="submitted",
        created_at=created_at,
        updated_at=created_at,
        intent_delete_seq_upper_bound=state.local_delete_sequence,
    )
    updated_state = apply_commit_submitted_state(state)
    upsert_commit_intent_journal(connection, journal)
    upsert_vault_state(connection, updated_state)
    return CommitSubmissionBundle(
        manifest=manifest,
        intent_manifest_hash=intent_manifest_hash,
        journal=journal,
        state=updated_state,
    )


def finalize_commit_manifest(
    manifest: ManifestRecord,
    *,
    committed_revision: int,
) -> ManifestRecord:
    return finalize_manifest_revision(manifest, revision=committed_revision)


def finalize_commit_submission(
    connection: sqlite3.Connection,
    *,
    ledger_path: Path,
    manifest: ManifestRecord,
    local_tombstones: Iterable[TombstoneRecord],
    committed_revision: int,
) -> CommitFinalizeResult:
    journal = load_commit_intent_journal(connection, manifest.vault_id)
    if journal is None:
        raise KeyError(f"commit_intent_journal not found: {manifest.vault_id}")
    if journal.status != "submitted":
        raise ValueError("commit finalization requires a submitted journal")

    finalized_manifest = finalize_commit_manifest(
        manifest,
        committed_revision=committed_revision,
    )
    updated_tombstones = apply_committed_tombstones(
        list(local_tombstones),
        journal,
        committed_revision=committed_revision,
    )
    rewrite_tombstone_ledger(ledger_path, updated_tombstones)
    updated_state = finalize_committed_state(
        connection,
        manifest.vault_id,
        committed_revision=committed_revision,
        manifest_summary=finalized_manifest.summary_hash,
    )
    return CommitFinalizeResult(
        manifest=finalized_manifest,
        tombstones=updated_tombstones,
        state=updated_state,
    )
