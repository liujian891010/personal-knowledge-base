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
    snapshot_plan: Optional[ContentSnapshotPlan] = None


@dataclass(frozen=True)
class CommitPreparationBundle:
    journal: CommitIntentJournalRecord
    state: VaultStateRecord


@dataclass(frozen=True)
class ContentSnapshotFile:
    file_id: str
    path: str
    type: str
    content_hash: str
    blob_id: str
    source_version_token: str
    snapshot_path: str
    blob_staging_path: str


@dataclass(frozen=True)
class ContentSnapshotPlan:
    vault_id: str
    base_revision: int
    created_at: int
    files: list[ContentSnapshotFile]


@dataclass(frozen=True)
class FrozenCommitPreparationBundle:
    journal: CommitIntentJournalRecord
    state: VaultStateRecord
    snapshot_plan: ContentSnapshotPlan


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


@dataclass(frozen=True)
class RevisionMetadata:
    revision: int
    commit_intent_id: str
    intent_manifest_hash: str
    created_by_device: str
    created_at: int


@dataclass(frozen=True)
class SubmittedConfirmationPlan:
    mode: str
    observed_head_revision: int
    matched_revision: Optional[int]
    scan_from_revision: Optional[int]
    scan_to_revision: Optional[int]


@dataclass(frozen=True)
class SubmittedConfirmationResolution:
    plan: SubmittedConfirmationPlan
    matched_metadata: Optional[RevisionMetadata]


def _snapshot_plain_path(file_id: str) -> str:
    return f"{STAGING_DIRNAME}/{file_id}.snapshot.plain"


def _blob_staging_path(blob_id: str) -> str:
    return f"{STAGING_DIRNAME}/{blob_id}.blob.staging"


def _derive_snapshot_source_version(record: FileRecord) -> str:
    meta = record.meta or {}
    source_version_token = meta.get("source_version_token")
    if source_version_token is not None:
        if not isinstance(source_version_token, str) or not source_version_token:
            raise ValueError(f"active file has invalid source_version_token: {record.file_id}")
        return source_version_token

    if record.content_hash is None:
        raise ValueError(f"active file is missing content_hash: {record.file_id}")

    mtime = meta.get("mtime")
    size = meta.get("size")
    if isinstance(mtime, int) and mtime >= 0 and isinstance(size, int) and size >= 0:
        return f"mtime:{mtime}:size:{size}:hash:{record.content_hash}"
    return f"updated_at:{record.updated_at}:hash:{record.content_hash}"


def build_content_snapshot_plan(
    document: FileMapDocument,
    *,
    base_revision: int,
    created_at: int,
) -> ContentSnapshotPlan:
    _validate_commit_document_paths(document)
    files = []
    for record in document.sorted_files():
        if record.status != "active":
            continue
        entry = _build_manifest_file_entry(record)
        files.append(
            ContentSnapshotFile(
                file_id=record.file_id,
                path=record.path,
                type=record.type,
                content_hash=entry.content_hash,
                blob_id=entry.blob_id,
                source_version_token=_derive_snapshot_source_version(record),
                snapshot_path=_snapshot_plain_path(record.file_id),
                blob_staging_path=_blob_staging_path(entry.blob_id),
            )
        )
    return ContentSnapshotPlan(
        vault_id=document.vault_id,
        base_revision=base_revision,
        created_at=created_at,
        files=files,
    )


def detect_content_snapshot_drift(
    plan: ContentSnapshotPlan,
    document: FileMapDocument,
) -> list[str]:
    current_active = {
        record.file_id: record
        for record in document.files
        if record.status == "active"
    }
    drifted_file_ids: list[str] = []
    for frozen in plan.files:
        current = current_active.get(frozen.file_id)
        if current is None:
            drifted_file_ids.append(frozen.file_id)
            continue
        if current.path != frozen.path or current.type != frozen.type:
            drifted_file_ids.append(frozen.file_id)
            continue
        if _derive_snapshot_source_version(current) != frozen.source_version_token:
            drifted_file_ids.append(frozen.file_id)
    return drifted_file_ids


def assert_content_snapshot_plan_matches(
    plan: ContentSnapshotPlan,
    document: FileMapDocument,
) -> None:
    drifted_file_ids = detect_content_snapshot_drift(plan, document)
    if drifted_file_ids:
        raise ValueError(
            "content snapshot drift detected: " + ", ".join(sorted(drifted_file_ids))
        )


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


def prepare_frozen_commit_intent(
    connection: sqlite3.Connection,
    *,
    state: VaultStateRecord,
    document: FileMapDocument,
    commit_intent_id: str,
    created_by_device: str,
    created_at: int,
) -> FrozenCommitPreparationBundle:
    preparation = prepare_commit_intent(
        connection,
        state=state,
        commit_intent_id=commit_intent_id,
        created_by_device=created_by_device,
        created_at=created_at,
    )
    snapshot_plan = build_content_snapshot_plan(
        document,
        base_revision=preparation.journal.base_revision,
        created_at=created_at,
    )
    return FrozenCommitPreparationBundle(
        journal=preparation.journal,
        state=preparation.state,
        snapshot_plan=snapshot_plan,
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
    frozen = prepare_frozen_commit_intent(
        connection,
        state=state,
        document=document,
        commit_intent_id=commit_intent_id,
        created_by_device=created_by_device,
        created_at=created_at,
    )
    submitted = submit_prepared_commit(
        connection,
        vault_id=state.vault_id,
        document=document,
        tombstones=tombstones,
        submitted_at=created_at,
    )
    return CommitSubmissionBundle(
        manifest=submitted.manifest,
        intent_manifest_hash=submitted.intent_manifest_hash,
        journal=submitted.journal,
        state=submitted.state,
        snapshot_plan=frozen.snapshot_plan,
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


def plan_submitted_confirmation(
    journal: CommitIntentJournalRecord,
    *,
    observed_head_revision: int,
    head_commit_intent_id: str,
) -> SubmittedConfirmationPlan:
    if observed_head_revision < journal.base_revision:
        raise ValueError("observed_head_revision cannot be older than journal.base_revision")

    if head_commit_intent_id == journal.commit_intent_id:
        return SubmittedConfirmationPlan(
            mode="head_match",
            observed_head_revision=observed_head_revision,
            matched_revision=observed_head_revision,
            scan_from_revision=None,
            scan_to_revision=None,
        )

    scan_from_revision = journal.base_revision + 1
    if scan_from_revision > observed_head_revision:
        return SubmittedConfirmationPlan(
            mode="miss",
            observed_head_revision=observed_head_revision,
            matched_revision=None,
            scan_from_revision=None,
            scan_to_revision=None,
        )

    return SubmittedConfirmationPlan(
        mode="scan_range",
        observed_head_revision=observed_head_revision,
        matched_revision=None,
        scan_from_revision=scan_from_revision,
        scan_to_revision=observed_head_revision,
    )


def find_matching_revision_metadata(
    revisions: Iterable[RevisionMetadata],
    *,
    commit_intent_id: str,
) -> Optional[RevisionMetadata]:
    for record in revisions:
        if record.commit_intent_id == commit_intent_id:
            return record
    return None


def resolve_submitted_confirmation(
    plan: SubmittedConfirmationPlan,
    *,
    commit_intent_id: str,
    revisions: Iterable[RevisionMetadata] = (),
) -> SubmittedConfirmationResolution:
    if plan.mode == "head_match":
        return SubmittedConfirmationResolution(
            plan=plan,
            matched_metadata=None,
        )
    if plan.mode == "miss":
        return SubmittedConfirmationResolution(
            plan=plan,
            matched_metadata=None,
        )
    if plan.mode != "scan_range":
        raise ValueError(f"unsupported submitted confirmation plan mode: {plan.mode}")

    matched_metadata = find_matching_revision_metadata(
        revisions,
        commit_intent_id=commit_intent_id,
    )
    return SubmittedConfirmationResolution(
        plan=plan,
        matched_metadata=matched_metadata,
    )
