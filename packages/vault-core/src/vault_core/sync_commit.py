from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

from .constants import STAGING_DIRNAME
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
    load_vault_state,
    recover_orphaned_commit_lock,
    recover_prepared_commit_cleanup,
    recover_submitted_commit_miss,
    upsert_commit_intent_journal,
    upsert_vault_state,
)
from .paths import move_staging_orphan


PENDING_INTENT_MANIFEST_HASH = "pending"


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


def _normalize_commit_path(path: str) -> str:
    return unicodedata.normalize("NFC", path)


def _validate_commit_document_paths(document: FileMapDocument) -> None:
    active_paths: dict[str, str] = {}
    for record in document.files:
        if record.status != "active":
            continue
        normalized_path = _normalize_commit_path(record.path)
        previous_file_id = active_paths.get(normalized_path)
        if previous_file_id is not None:
            raise ValueError(
                "active file paths collide after NFC normalization: "
                f"{previous_file_id}, {record.file_id}"
            )
        active_paths[normalized_path] = record.file_id

    for record in document.files:
        if record.status != "conflict_copy":
            continue
        normalized_path = _normalize_commit_path(record.path)
        if normalized_path in active_paths:
            raise ValueError(
                "conflict copy path overlaps active file path after NFC normalization: "
                f"{record.file_id}"
            )


def build_commit_manifest(
    document: FileMapDocument,
    *,
    tombstones: Iterable[TombstoneRecord],
    base_revision: int,
    created_by_device: str,
    created_at: int,
) -> ManifestRecord:
    _validate_commit_document_paths(document)
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
class CommitPreparationBundle:
    journal: CommitIntentJournalRecord
    state: VaultStateRecord


@dataclass(frozen=True)
class CommitFinalizeResult:
    manifest: ManifestRecord
    tombstones: list[TombstoneRecord]
    state: VaultStateRecord


@dataclass(frozen=True)
class OrphanedCommitRecoveryResult:
    state: VaultStateRecord
    moved_staging_paths: list[Path]


@dataclass(frozen=True)
class CommitRecoveryPlan:
    mode: str
    should_cleanup_staging: bool
    requires_remote_confirmation: bool


@dataclass(frozen=True)
class LocalCommitRecoveryResult:
    plan: CommitRecoveryPlan
    state: VaultStateRecord
    moved_staging_paths: list[Path]


def prepare_commit_intent(
    connection: sqlite3.Connection,
    *,
    state: VaultStateRecord,
    commit_intent_id: str,
    created_by_device: str,
    created_at: int,
) -> CommitPreparationBundle:
    has_active_commit_journal = load_commit_intent_journal(connection, state.vault_id) is not None
    if should_block_new_commit(state, has_active_commit_journal=has_active_commit_journal):
        raise ValueError("vault_state is not eligible to start a new commit")

    journal = CommitIntentJournalRecord(
        vault_id=state.vault_id,
        commit_intent_id=commit_intent_id,
        intent_manifest_hash=PENDING_INTENT_MANIFEST_HASH,
        base_revision=state.last_applied_revision,
        created_by_device=created_by_device,
        status="prepared",
        created_at=created_at,
        updated_at=created_at,
        intent_delete_seq_upper_bound=state.local_delete_sequence,
    )
    updated_state = apply_commit_submitted_state(state)
    upsert_commit_intent_journal(connection, journal)
    upsert_vault_state(connection, updated_state)
    return CommitPreparationBundle(
        journal=journal,
        state=updated_state,
    )


def submit_prepared_commit(
    connection: sqlite3.Connection,
    *,
    vault_id: str,
    document: FileMapDocument,
    tombstones: Iterable[TombstoneRecord],
    submitted_at: int,
) -> CommitSubmissionBundle:
    journal = load_commit_intent_journal(connection, vault_id)
    if journal is None:
        raise KeyError(f"commit_intent_journal not found: {vault_id}")
    if journal.status != "prepared":
        raise ValueError("prepared journal is required before submitting a commit")

    state = load_vault_state(connection, vault_id)
    if state is None:
        raise KeyError(f"vault_state not found: {vault_id}")

    manifest = build_commit_manifest(
        document,
        tombstones=tombstones,
        base_revision=journal.base_revision,
        created_by_device=journal.created_by_device,
        created_at=journal.created_at,
    )
    intent_manifest_hash = compute_intent_manifest_hash(manifest)
    submitted_journal = CommitIntentJournalRecord(
        vault_id=journal.vault_id,
        commit_intent_id=journal.commit_intent_id,
        intent_manifest_hash=intent_manifest_hash,
        base_revision=journal.base_revision,
        created_by_device=journal.created_by_device,
        status="submitted",
        created_at=journal.created_at,
        updated_at=submitted_at,
        intent_delete_seq_upper_bound=journal.intent_delete_seq_upper_bound,
    )
    upsert_commit_intent_journal(connection, submitted_journal)
    return CommitSubmissionBundle(
        manifest=manifest,
        intent_manifest_hash=intent_manifest_hash,
        journal=submitted_journal,
        state=state,
    )


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
    prepare_commit_intent(
        connection,
        state=state,
        commit_intent_id=commit_intent_id,
        created_by_device=created_by_device,
        created_at=created_at,
    )
    return submit_prepared_commit(
        connection,
        vault_id=state.vault_id,
        document=document,
        tombstones=tombstones,
        submitted_at=created_at,
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


def cleanup_failed_commit_submission(
    connection: sqlite3.Connection,
    vault_id: str,
    *,
    normalized_at: int,
) -> VaultStateRecord:
    journal = load_commit_intent_journal(connection, vault_id)
    if journal is None:
        raise KeyError(f"commit_intent_journal not found: {vault_id}")

    if journal.status == "prepared":
        return recover_prepared_commit_cleanup(connection, vault_id)
    if journal.status in {"submitted", "acknowledged"}:
        return recover_submitted_commit_miss(
            connection,
            vault_id,
            normalized_at=normalized_at,
        )
    raise ValueError(f"unsupported commit journal status: {journal.status}")


def isolate_staging_orphans(vault_root: Path) -> list[Path]:
    staging_root = vault_root / STAGING_DIRNAME
    if not staging_root.exists():
        return []

    moved_paths: list[Path] = []
    for staging_path in sorted(
        (path for path in staging_root.rglob("*") if path.is_file()),
        key=lambda item: str(item.relative_to(staging_root)),
    ):
        moved_paths.append(move_staging_orphan(vault_root, staging_path))
    return moved_paths


def recover_orphaned_commit_session(
    connection: sqlite3.Connection,
    *,
    vault_id: str,
    vault_root: Path,
) -> OrphanedCommitRecoveryResult:
    moved_staging_paths = isolate_staging_orphans(vault_root)
    state = recover_orphaned_commit_lock(connection, vault_id)
    return OrphanedCommitRecoveryResult(
        state=state,
        moved_staging_paths=moved_staging_paths,
    )


def plan_commit_recovery(
    state: VaultStateRecord,
    *,
    journal: Optional[CommitIntentJournalRecord],
) -> CommitRecoveryPlan:
    if journal is None:
        if state.commit_in_progress:
            return CommitRecoveryPlan(
                mode="orphaned_lock",
                should_cleanup_staging=True,
                requires_remote_confirmation=False,
            )
        return CommitRecoveryPlan(
            mode="idle",
            should_cleanup_staging=False,
            requires_remote_confirmation=False,
        )

    if journal.status == "prepared":
        return CommitRecoveryPlan(
            mode="prepared_cleanup",
            should_cleanup_staging=True,
            requires_remote_confirmation=False,
        )
    if journal.status in {"submitted", "acknowledged"}:
        return CommitRecoveryPlan(
            mode="submitted_confirmation",
            should_cleanup_staging=False,
            requires_remote_confirmation=True,
        )
    raise ValueError(f"unsupported commit journal status: {journal.status}")


def recover_local_commit_state(
    connection: sqlite3.Connection,
    *,
    vault_id: str,
    vault_root: Path,
) -> LocalCommitRecoveryResult:
    state = load_vault_state(connection, vault_id)
    if state is None:
        raise KeyError(f"vault_state not found: {vault_id}")

    journal = load_commit_intent_journal(connection, vault_id)
    plan = plan_commit_recovery(state, journal=journal)

    if plan.mode == "idle":
        return LocalCommitRecoveryResult(
            plan=plan,
            state=state,
            moved_staging_paths=[],
        )

    if plan.mode == "orphaned_lock":
        recovered = recover_orphaned_commit_session(
            connection,
            vault_id=vault_id,
            vault_root=vault_root,
        )
        return LocalCommitRecoveryResult(
            plan=plan,
            state=recovered.state,
            moved_staging_paths=recovered.moved_staging_paths,
        )

    if plan.mode == "prepared_cleanup":
        moved_staging_paths = isolate_staging_orphans(vault_root)
        recovered_state = recover_prepared_commit_cleanup(connection, vault_id)
        return LocalCommitRecoveryResult(
            plan=plan,
            state=recovered_state,
            moved_staging_paths=moved_staging_paths,
        )

    raise ValueError("local commit recovery requires remote confirmation for submitted journal state")
