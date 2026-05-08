from __future__ import annotations

import hashlib
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
    size: int
    mtime: int
    mime_type: Optional[str]
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
class MaterializedContentSnapshotFile:
    file_id: str
    snapshot_path: Path
    content_hash: str
    size_bytes: int


@dataclass(frozen=True)
class ContentSnapshotMaterializationResult:
    snapshot_plan: ContentSnapshotPlan
    files: list[MaterializedContentSnapshotFile]


@dataclass(frozen=True)
class MaterializedBlobStagingFile:
    file_id: str
    blob_id: str
    snapshot_path: Path
    blob_staging_path: Path
    content_hash: str
    plaintext_size: int
    encrypted_size: int


@dataclass(frozen=True)
class BlobStagingMaterializationResult:
    snapshot_plan: ContentSnapshotPlan
    files: list[MaterializedBlobStagingFile]


@dataclass(frozen=True)
class CommitSnapshotEntry:
    file_id: str
    path: str
    type: str
    content_hash: str
    blob_id: str
    plaintext_size: int
    encrypted_size: int
    mtime: int
    mime_type: Optional[str]
    snapshot_path: Path
    blob_staging_path: Path


@dataclass(frozen=True)
class CommitSnapshotTable:
    vault_id: str
    base_revision: int
    created_at: int
    entries: list[CommitSnapshotEntry]


@dataclass(frozen=True)
class BlobCheckRequest:
    blob_ids: list[str]


@dataclass(frozen=True)
class BlobCheckResult:
    requested_blob_ids: list[str]
    existing_blob_ids: list[str]
    missing_blob_ids: list[str]


@dataclass(frozen=True)
class BlobUploadPlanEntry:
    blob_id: str
    content_hash: str
    encrypted_size: int
    blob_staging_path: Path
    file_ids: list[str]


@dataclass(frozen=True)
class BlobUploadPlan:
    vault_id: str
    entries: list[BlobUploadPlanEntry]


@dataclass(frozen=True)
class CreateCommitBlobRef:
    blob_id: str
    file_id: str


@dataclass(frozen=True)
class CreateCommitRequestPayload:
    commit_intent_id: str
    base_revision: int
    created_by_device: str
    intent_manifest_hash: str
    intent_delete_seq_upper_bound: Optional[int]
    manifest: ManifestRecord
    blob_refs: list[CreateCommitBlobRef]


@dataclass(frozen=True)
class CommitNetworkPlan:
    request: CreateCommitRequestPayload
    blob_check: BlobCheckResult
    blob_uploads: BlobUploadPlan


@dataclass(frozen=True)
class SnapshotDriftAbortResult:
    state: VaultStateRecord
    drifted_file_ids: list[str]
    removed_staging_paths: list[Path]


@dataclass(frozen=True)
class CommitFinalizeCleanupResult:
    finalized: CommitFinalizeResult
    removed_staging_paths: list[Path]


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


def _is_commit_staging_artifact(path: Path) -> bool:
    return path.name.endswith(".snapshot.plain") or path.name.endswith(".blob.staging")


def _compute_content_hash(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


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
                size=entry.size,
                mtime=entry.mtime,
                mime_type=entry.mime_type,
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


def _build_manifest_file_entry_from_snapshot(
    frozen: ContentSnapshotFile,
) -> ManifestFileEntry:
    return ManifestFileEntry(
        file_id=frozen.file_id,
        path=frozen.path,
        type=frozen.type,
        content_hash=frozen.content_hash,
        blob_id=frozen.blob_id,
        size=frozen.size,
        mtime=frozen.mtime,
        mime_type=frozen.mime_type,
    )


def _assert_snapshot_plan_covers_document(
    plan: ContentSnapshotPlan,
    document: FileMapDocument,
) -> dict[str, ContentSnapshotFile]:
    if document.vault_id != plan.vault_id:
        raise ValueError("document vault_id does not match snapshot plan vault")

    frozen_by_file_id = {item.file_id: item for item in plan.files}
    active_file_ids = {
        record.file_id
        for record in document.files
        if record.status == "active"
    }
    if active_file_ids != set(frozen_by_file_id):
        raise ValueError("content snapshot plan active file set does not match document")

    for record in document.files:
        if record.status != "active":
            continue
        frozen = frozen_by_file_id[record.file_id]
        if frozen.path != record.path or frozen.type != record.type:
            raise ValueError(
                f"content snapshot plan structure mismatch for file_id {record.file_id}"
            )
    return frozen_by_file_id


def build_commit_manifest_from_snapshot_plan(
    document: FileMapDocument,
    *,
    snapshot_plan: ContentSnapshotPlan,
    tombstones: Iterable[TombstoneRecord],
    base_revision: int,
    created_by_device: str,
    created_at: int,
) -> ManifestRecord:
    _validate_commit_document_paths(document)
    frozen_by_file_id = _assert_snapshot_plan_covers_document(snapshot_plan, document)
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
        _build_manifest_file_entry_from_snapshot(frozen_by_file_id[record.file_id])
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


def _resolve_snapshot_output_path(
    vault_root: Path,
    frozen: ContentSnapshotFile,
) -> Path:
    relative_path = Path(frozen.snapshot_path)
    expected_relative_path = Path(STAGING_DIRNAME) / f"{frozen.file_id}.snapshot.plain"
    if relative_path != expected_relative_path:
        raise ValueError(
            f"snapshot plan has unexpected snapshot path for file_id {frozen.file_id}"
        )

    staging_root = (vault_root / Path(STAGING_DIRNAME)).resolve()
    output_path = (vault_root / relative_path).resolve()
    try:
        output_path.relative_to(staging_root)
    except ValueError as exc:
        raise ValueError(
            f"snapshot path escapes staging root for file_id {frozen.file_id}"
        ) from exc
    return output_path


def _resolve_blob_staging_output_path(
    vault_root: Path,
    frozen: ContentSnapshotFile,
) -> Path:
    relative_path = Path(frozen.blob_staging_path)
    expected_relative_path = Path(STAGING_DIRNAME) / f"{frozen.blob_id}.blob.staging"
    if relative_path != expected_relative_path:
        raise ValueError(
            f"snapshot plan has unexpected blob staging path for file_id {frozen.file_id}"
        )

    staging_root = (vault_root / Path(STAGING_DIRNAME)).resolve()
    output_path = (vault_root / relative_path).resolve()
    try:
        output_path.relative_to(staging_root)
    except ValueError as exc:
        raise ValueError(
            f"blob staging path escapes staging root for file_id {frozen.file_id}"
        ) from exc
    return output_path


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    try:
        temp_path.write_bytes(payload)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def materialize_content_snapshot_plan(
    vault_root: Path,
    *,
    plan: ContentSnapshotPlan,
    document: FileMapDocument,
    content_by_file_id: Mapping[str, bytes],
) -> ContentSnapshotMaterializationResult:
    if document.vault_id != plan.vault_id:
        raise ValueError("document vault_id does not match snapshot plan vault")

    assert_content_snapshot_plan_matches(plan, document)

    written_files: list[MaterializedContentSnapshotFile] = []
    written_paths: list[Path] = []
    try:
        for frozen in plan.files:
            if frozen.file_id not in content_by_file_id:
                raise KeyError(f"snapshot content not provided for file_id: {frozen.file_id}")

            payload = content_by_file_id[frozen.file_id]
            content_hash = _compute_content_hash(payload)
            if content_hash != frozen.content_hash:
                raise ValueError(
                    "snapshot content hash mismatch for file_id "
                    f"{frozen.file_id}: expected {frozen.content_hash}, got {content_hash}"
                )
            if len(payload) != frozen.size:
                raise ValueError(
                    "snapshot content size mismatch for file_id "
                    f"{frozen.file_id}: expected {frozen.size}, got {len(payload)}"
                )

            snapshot_path = _resolve_snapshot_output_path(vault_root, frozen)
            _write_bytes_atomic(snapshot_path, payload)
            written_paths.append(snapshot_path)
            written_files.append(
                MaterializedContentSnapshotFile(
                    file_id=frozen.file_id,
                    snapshot_path=snapshot_path,
                    content_hash=content_hash,
                    size_bytes=len(payload),
                )
            )
    except Exception:
        for path in reversed(written_paths):
            path.unlink(missing_ok=True)
        raise

    return ContentSnapshotMaterializationResult(
        snapshot_plan=plan,
        files=written_files,
    )


def materialize_blob_staging_plan(
    vault_root: Path,
    *,
    plan: ContentSnapshotPlan,
    snapshot_materialization: ContentSnapshotMaterializationResult,
    encrypted_blob_by_file_id: Mapping[str, bytes],
) -> BlobStagingMaterializationResult:
    if snapshot_materialization.snapshot_plan != plan:
        raise ValueError("snapshot materialization result does not match snapshot plan")

    snapshots_by_file_id = {
        item.file_id: item
        for item in snapshot_materialization.files
    }

    written_files: list[MaterializedBlobStagingFile] = []
    written_paths: list[Path] = []
    try:
        for frozen in plan.files:
            materialized_snapshot = snapshots_by_file_id.get(frozen.file_id)
            if materialized_snapshot is None:
                raise KeyError(
                    f"materialized snapshot not found for file_id: {frozen.file_id}"
                )
            if materialized_snapshot.content_hash != frozen.content_hash:
                raise ValueError(
                    f"materialized snapshot hash mismatch for file_id {frozen.file_id}"
                )

            snapshot_path = _resolve_snapshot_output_path(vault_root, frozen)
            if materialized_snapshot.snapshot_path != snapshot_path:
                raise ValueError(
                    f"materialized snapshot path mismatch for file_id {frozen.file_id}"
                )

            snapshot_payload = snapshot_path.read_bytes()
            snapshot_content_hash = _compute_content_hash(snapshot_payload)
            if snapshot_content_hash != frozen.content_hash:
                raise ValueError(
                    "snapshot file hash mismatch for file_id "
                    f"{frozen.file_id}: expected {frozen.content_hash}, got {snapshot_content_hash}"
                )
            if len(snapshot_payload) != materialized_snapshot.size_bytes:
                raise ValueError(
                    f"snapshot file size mismatch for file_id {frozen.file_id}"
                )

            if frozen.file_id not in encrypted_blob_by_file_id:
                raise KeyError(f"encrypted blob not provided for file_id: {frozen.file_id}")

            encrypted_payload = encrypted_blob_by_file_id[frozen.file_id]
            expected_encrypted_size = materialized_snapshot.size_bytes + 16
            if len(encrypted_payload) != expected_encrypted_size:
                raise ValueError(
                    "encrypted blob size mismatch for file_id "
                    f"{frozen.file_id}: expected {expected_encrypted_size}, got {len(encrypted_payload)}"
                )

            blob_staging_path = _resolve_blob_staging_output_path(vault_root, frozen)
            _write_bytes_atomic(blob_staging_path, encrypted_payload)
            written_paths.append(blob_staging_path)
            written_files.append(
                MaterializedBlobStagingFile(
                    file_id=frozen.file_id,
                    blob_id=frozen.blob_id,
                    snapshot_path=snapshot_path,
                    blob_staging_path=blob_staging_path,
                    content_hash=frozen.content_hash,
                    plaintext_size=materialized_snapshot.size_bytes,
                    encrypted_size=len(encrypted_payload),
                )
            )
    except Exception:
        for path in reversed(written_paths):
            path.unlink(missing_ok=True)
        raise

    return BlobStagingMaterializationResult(
        snapshot_plan=plan,
        files=written_files,
    )


def build_commit_snapshot_table(
    plan: ContentSnapshotPlan,
    *,
    snapshot_materialization: ContentSnapshotMaterializationResult,
    blob_staging_materialization: BlobStagingMaterializationResult,
) -> CommitSnapshotTable:
    if snapshot_materialization.snapshot_plan != plan:
        raise ValueError("snapshot materialization result does not match snapshot plan")
    if blob_staging_materialization.snapshot_plan != plan:
        raise ValueError("blob staging materialization result does not match snapshot plan")

    snapshots_by_file_id = {
        item.file_id: item
        for item in snapshot_materialization.files
    }
    blobs_by_file_id = {
        item.file_id: item
        for item in blob_staging_materialization.files
    }

    entries: list[CommitSnapshotEntry] = []
    for frozen in plan.files:
        materialized_snapshot = snapshots_by_file_id.get(frozen.file_id)
        if materialized_snapshot is None:
            raise KeyError(f"materialized snapshot not found for file_id: {frozen.file_id}")
        materialized_blob = blobs_by_file_id.get(frozen.file_id)
        if materialized_blob is None:
            raise KeyError(f"materialized blob staging not found for file_id: {frozen.file_id}")
        if materialized_snapshot.content_hash != frozen.content_hash:
            raise ValueError(f"materialized snapshot hash mismatch for file_id {frozen.file_id}")
        if materialized_snapshot.size_bytes != frozen.size:
            raise ValueError(f"materialized snapshot size mismatch for file_id {frozen.file_id}")
        if materialized_blob.blob_id != frozen.blob_id:
            raise ValueError(f"materialized blob id mismatch for file_id {frozen.file_id}")
        if materialized_blob.content_hash != frozen.content_hash:
            raise ValueError(f"materialized blob hash mismatch for file_id {frozen.file_id}")
        if materialized_blob.plaintext_size != frozen.size:
            raise ValueError(f"materialized blob plaintext size mismatch for file_id {frozen.file_id}")
        if materialized_blob.snapshot_path != materialized_snapshot.snapshot_path:
            raise ValueError(f"materialized snapshot path mismatch for file_id {frozen.file_id}")

        entries.append(
            CommitSnapshotEntry(
                file_id=frozen.file_id,
                path=frozen.path,
                type=frozen.type,
                content_hash=frozen.content_hash,
                blob_id=frozen.blob_id,
                plaintext_size=frozen.size,
                encrypted_size=materialized_blob.encrypted_size,
                mtime=frozen.mtime,
                mime_type=frozen.mime_type,
                snapshot_path=materialized_snapshot.snapshot_path,
                blob_staging_path=materialized_blob.blob_staging_path,
            )
        )

    return CommitSnapshotTable(
        vault_id=plan.vault_id,
        base_revision=plan.base_revision,
        created_at=plan.created_at,
        entries=entries,
    )


def build_blob_check_request(snapshot_table: CommitSnapshotTable) -> BlobCheckRequest:
    blob_ids = sorted({entry.blob_id for entry in snapshot_table.entries})
    return BlobCheckRequest(blob_ids=blob_ids)


def resolve_blob_check_result(
    snapshot_table: CommitSnapshotTable,
    *,
    existing_blob_ids: Iterable[str],
    missing_blob_ids: Iterable[str],
) -> BlobCheckResult:
    requested_blob_ids = sorted({entry.blob_id for entry in snapshot_table.entries})
    requested_blob_id_set = set(requested_blob_ids)
    existing_blob_id_set = set(existing_blob_ids)
    missing_blob_id_set = set(missing_blob_ids)

    unknown_blob_ids = sorted((existing_blob_id_set | missing_blob_id_set) - requested_blob_id_set)
    if unknown_blob_ids:
        raise ValueError(
            "blob check response contains unknown blob_ids: " + ", ".join(unknown_blob_ids)
        )
    overlapping_blob_ids = sorted(existing_blob_id_set & missing_blob_id_set)
    if overlapping_blob_ids:
        raise ValueError(
            "blob check response contains overlapping blob_ids: " + ", ".join(overlapping_blob_ids)
        )
    unresolved_blob_ids = sorted(requested_blob_id_set - existing_blob_id_set - missing_blob_id_set)
    if unresolved_blob_ids:
        raise ValueError(
            "blob check response does not cover requested blob_ids: " + ", ".join(unresolved_blob_ids)
        )

    return BlobCheckResult(
        requested_blob_ids=requested_blob_ids,
        existing_blob_ids=sorted(existing_blob_id_set),
        missing_blob_ids=sorted(missing_blob_id_set),
    )


def build_blob_upload_plan(
    snapshot_table: CommitSnapshotTable,
    *,
    missing_blob_ids: Iterable[str],
) -> BlobUploadPlan:
    missing_blob_id_set = set(missing_blob_ids)
    requested_blob_ids = {entry.blob_id for entry in snapshot_table.entries}
    unknown_blob_ids = sorted(missing_blob_id_set - requested_blob_ids)
    if unknown_blob_ids:
        raise ValueError(
            "missing blob_ids are not present in commit snapshot table: "
            + ", ".join(unknown_blob_ids)
        )

    entries_by_blob_id: dict[str, BlobUploadPlanEntry] = {}
    for entry in snapshot_table.entries:
        if entry.blob_id not in missing_blob_id_set:
            continue
        existing = entries_by_blob_id.get(entry.blob_id)
        if existing is None:
            entries_by_blob_id[entry.blob_id] = BlobUploadPlanEntry(
                blob_id=entry.blob_id,
                content_hash=entry.content_hash,
                encrypted_size=entry.encrypted_size,
                blob_staging_path=entry.blob_staging_path,
                file_ids=[entry.file_id],
            )
            continue
        if existing.content_hash != entry.content_hash:
            raise ValueError(f"blob upload plan content hash mismatch for blob_id {entry.blob_id}")
        if existing.encrypted_size != entry.encrypted_size:
            raise ValueError(f"blob upload plan encrypted size mismatch for blob_id {entry.blob_id}")
        if existing.blob_staging_path != entry.blob_staging_path:
            raise ValueError(f"blob upload plan staging path mismatch for blob_id {entry.blob_id}")
        entries_by_blob_id[entry.blob_id] = BlobUploadPlanEntry(
            blob_id=existing.blob_id,
            content_hash=existing.content_hash,
            encrypted_size=existing.encrypted_size,
            blob_staging_path=existing.blob_staging_path,
            file_ids=existing.file_ids + [entry.file_id],
        )

    return BlobUploadPlan(
        vault_id=snapshot_table.vault_id,
        entries=[entries_by_blob_id[blob_id] for blob_id in sorted(entries_by_blob_id)],
    )


def build_create_commit_request_payload(
    submission: CommitSubmissionBundle,
    *,
    snapshot_table: CommitSnapshotTable,
) -> CreateCommitRequestPayload:
    if submission.manifest.vault_id != snapshot_table.vault_id:
        raise ValueError("snapshot table vault_id does not match commit submission manifest")
    if submission.journal.base_revision != snapshot_table.base_revision:
        raise ValueError("snapshot table base_revision does not match commit journal")
    if submission.journal.created_at != snapshot_table.created_at:
        raise ValueError("snapshot table created_at does not match commit journal")
    if submission.manifest.base_revision != submission.journal.base_revision:
        raise ValueError("commit manifest base_revision does not match commit journal")

    manifest_files_by_file_id = {
        item.file_id: item
        for item in submission.manifest.files
    }
    snapshot_entries_by_file_id = {
        item.file_id: item
        for item in snapshot_table.entries
    }
    if set(manifest_files_by_file_id) != set(snapshot_entries_by_file_id):
        raise ValueError("commit submission manifest files do not match snapshot table entries")

    for file_id, manifest_file in manifest_files_by_file_id.items():
        snapshot_entry = snapshot_entries_by_file_id[file_id]
        if manifest_file.path != snapshot_entry.path or manifest_file.type != snapshot_entry.type:
            raise ValueError(f"commit submission manifest structure mismatch for file_id {file_id}")
        if manifest_file.content_hash != snapshot_entry.content_hash:
            raise ValueError(f"commit submission manifest content hash mismatch for file_id {file_id}")
        if manifest_file.blob_id != snapshot_entry.blob_id:
            raise ValueError(f"commit submission manifest blob id mismatch for file_id {file_id}")
        if manifest_file.size != snapshot_entry.plaintext_size:
            raise ValueError(f"commit submission manifest size mismatch for file_id {file_id}")
        if manifest_file.mtime != snapshot_entry.mtime:
            raise ValueError(f"commit submission manifest mtime mismatch for file_id {file_id}")
        if manifest_file.mime_type != snapshot_entry.mime_type:
            raise ValueError(f"commit submission manifest mime_type mismatch for file_id {file_id}")

    blob_refs = [
        CreateCommitBlobRef(
            blob_id=entry.blob_id,
            file_id=entry.file_id,
        )
        for entry in sorted(snapshot_table.entries, key=lambda item: (item.file_id, item.blob_id))
    ]
    return CreateCommitRequestPayload(
        commit_intent_id=submission.journal.commit_intent_id,
        base_revision=submission.journal.base_revision,
        created_by_device=submission.journal.created_by_device,
        intent_manifest_hash=submission.intent_manifest_hash,
        intent_delete_seq_upper_bound=submission.journal.intent_delete_seq_upper_bound,
        manifest=submission.manifest,
        blob_refs=blob_refs,
    )


def build_commit_network_plan(
    submission: CommitSubmissionBundle,
    *,
    snapshot_table: CommitSnapshotTable,
    blob_check: BlobCheckResult,
) -> CommitNetworkPlan:
    requested_blob_ids = sorted({entry.blob_id for entry in snapshot_table.entries})
    if blob_check.requested_blob_ids != requested_blob_ids:
        raise ValueError("blob check result does not match snapshot table blob_ids")

    return CommitNetworkPlan(
        request=build_create_commit_request_payload(
            submission,
            snapshot_table=snapshot_table,
        ),
        blob_check=blob_check,
        blob_uploads=build_blob_upload_plan(
            snapshot_table,
            missing_blob_ids=blob_check.missing_blob_ids,
        ),
    )


def cleanup_commit_staging_artifacts(vault_root: Path) -> list[Path]:
    staging_root = vault_root / STAGING_DIRNAME
    if not staging_root.exists():
        return []

    removed_paths: list[Path] = []
    for staging_path in sorted(
        (path for path in staging_root.rglob("*") if path.is_file() and _is_commit_staging_artifact(path)),
        key=lambda item: str(item.relative_to(staging_root)),
    ):
        staging_path.unlink(missing_ok=True)
        removed_paths.append(staging_path)
    return removed_paths


def abort_drifted_commit_snapshot(
    connection: sqlite3.Connection,
    *,
    vault_id: str,
    vault_root: Path,
    snapshot_plan: ContentSnapshotPlan,
    document: FileMapDocument,
) -> SnapshotDriftAbortResult:
    if snapshot_plan.vault_id != vault_id:
        raise ValueError("snapshot_plan vault_id does not match aborted commit vault")
    if document.vault_id != vault_id:
        raise ValueError("document vault_id does not match aborted commit vault")

    drifted_file_ids = detect_content_snapshot_drift(snapshot_plan, document)
    if not drifted_file_ids:
        raise ValueError("content snapshot plan still matches current document")

    recovered_state = recover_prepared_commit_cleanup(connection, vault_id)
    removed_staging_paths = cleanup_commit_staging_artifacts(vault_root)
    return SnapshotDriftAbortResult(
        state=recovered_state,
        drifted_file_ids=sorted(drifted_file_ids),
        removed_staging_paths=removed_staging_paths,
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
    snapshot_plan: Optional[ContentSnapshotPlan] = None,
) -> CommitSubmissionBundle:
    journal = load_commit_intent_journal(connection, vault_id)
    if journal is None:
        raise KeyError(f"commit_intent_journal not found: {vault_id}")
    if journal.status != "prepared":
        raise ValueError("prepared journal is required before submitting a commit")

    state = load_vault_state(connection, vault_id)
    if state is None:
        raise KeyError(f"vault_state not found: {vault_id}")

    if snapshot_plan is None:
        manifest = build_commit_manifest(
            document,
            tombstones=tombstones,
            base_revision=journal.base_revision,
            created_by_device=journal.created_by_device,
            created_at=journal.created_at,
        )
    else:
        if snapshot_plan.vault_id != vault_id:
            raise ValueError("snapshot_plan vault_id does not match submitted commit vault")
        if snapshot_plan.base_revision != journal.base_revision:
            raise ValueError("snapshot_plan base_revision does not match prepared journal")
        if snapshot_plan.created_at != journal.created_at:
            raise ValueError("snapshot_plan created_at does not match prepared journal")
        manifest = build_commit_manifest_from_snapshot_plan(
            document,
            snapshot_plan=snapshot_plan,
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
        snapshot_plan=frozen.snapshot_plan,
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


def finalize_commit_submission_cleanup(
    connection: sqlite3.Connection,
    *,
    vault_root: Path,
    ledger_path: Path,
    manifest: ManifestRecord,
    local_tombstones: Iterable[TombstoneRecord],
    committed_revision: int,
) -> CommitFinalizeCleanupResult:
    finalized = finalize_commit_submission(
        connection,
        ledger_path=ledger_path,
        manifest=manifest,
        local_tombstones=local_tombstones,
        committed_revision=committed_revision,
    )
    removed_staging_paths = cleanup_commit_staging_artifacts(vault_root)
    return CommitFinalizeCleanupResult(
        finalized=finalized,
        removed_staging_paths=removed_staging_paths,
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
