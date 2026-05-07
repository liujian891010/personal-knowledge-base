from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List

from .models import FileMapDocument, FileRecord, ManifestRecord, TombstoneRecord


def _infer_file_type(path: str) -> str:
    if path.startswith("Attachments/"):
        return "attachment"
    if path.startswith(".ai/wiki/"):
        return "ai_wiki"
    if path.startswith(".ai/index/"):
        return "ai_index"
    if path == "AGENTS.md" or path.startswith(".ai/agents/"):
        return "ai_agents"
    return "note"


def _merge_tombstone(local: TombstoneRecord, remote: TombstoneRecord) -> TombstoneRecord:
    return TombstoneRecord(
        file_id=local.file_id,
        deleted_revision=local.deleted_revision if local.deleted_revision is not None else remote.deleted_revision,
        deleted_at=max(local.deleted_at, remote.deleted_at),
        local_delete_seq=local.local_delete_seq,
        last_known_path=local.last_known_path or remote.last_known_path,
        deleted_by_device=local.deleted_by_device or remote.deleted_by_device,
        meta=local.meta if local.meta is not None else remote.meta,
    )


def merge_manifest_tombstones(
    local_tombstones: Iterable[TombstoneRecord],
    manifest_tombstones: Iterable[TombstoneRecord],
) -> List[TombstoneRecord]:
    merged: List[TombstoneRecord] = []
    manifest_by_id = {record.file_id: record for record in manifest_tombstones}

    for record in local_tombstones:
        remote = manifest_by_id.pop(record.file_id, None)
        if remote is None:
            merged.append(record)
            continue
        merged.append(_merge_tombstone(record, remote))

    for record in sorted(manifest_by_id.values(), key=lambda item: item.file_id):
        merged.append(
            TombstoneRecord(
                file_id=record.file_id,
                deleted_revision=record.deleted_revision,
                deleted_at=record.deleted_at,
                local_delete_seq=0,
                last_known_path=record.last_known_path,
                deleted_by_device=record.deleted_by_device,
                meta=record.meta,
            )
        )

    return merged


def select_reclaimable_tombstones(
    local_tombstones: Iterable[TombstoneRecord],
    manifest_tombstones: Iterable[TombstoneRecord],
    *,
    target_revision: int,
) -> List[TombstoneRecord]:
    manifest_ids = {record.file_id for record in manifest_tombstones}
    reclaimable: List[TombstoneRecord] = []

    for record in local_tombstones:
        if record.file_id in manifest_ids:
            continue
        if record.deleted_revision is None:
            continue
        if target_revision >= record.deleted_revision:
            reclaimable.append(record)

    return reclaimable


def rebuild_filemap_from_manifest(
    current_document: FileMapDocument,
    manifest: ManifestRecord,
    *,
    local_tombstones: Iterable[TombstoneRecord],
    rewritten_at: int,
) -> FileMapDocument:
    current_by_id: Dict[str, FileRecord] = {
        record.file_id: record
        for record in current_document.files
        if record.status != "conflict_copy"
    }
    manifest_file_ids = {record.file_id for record in manifest.files}
    retained_tombstones = [
        record
        for record in local_tombstones
        if record.file_id not in manifest_file_ids
    ]

    rebuilt_files: List[FileRecord] = []
    for record in manifest.sorted_files():
        existing = current_by_id.get(record.file_id)
        rebuilt_files.append(
            FileRecord(
                file_id=record.file_id,
                path=record.path,
                type=record.type,
                status="active",
                updated_at=rewritten_at,
                content_hash=record.content_hash,
                last_known_revision=manifest.revision,
                meta=existing.meta if existing is not None else None,
            )
        )

    for tombstone in sorted(retained_tombstones, key=lambda item: ((item.last_known_path or ""), item.file_id)):
        existing = current_by_id.get(tombstone.file_id)
        path = tombstone.last_known_path or (existing.path if existing is not None else None)
        if path is None:
            raise ValueError(f"cannot rebuild deleted record without a path: {tombstone.file_id}")
        rebuilt_files.append(
            FileRecord(
                file_id=tombstone.file_id,
                path=path,
                type=existing.type if existing is not None else _infer_file_type(path),
                status="deleted",
                updated_at=tombstone.deleted_at,
                content_hash=existing.content_hash if existing is not None else None,
                last_known_revision=tombstone.deleted_revision,
                meta=existing.meta if existing is not None else None,
            )
        )

    rebuilt_files.extend(record for record in current_document.files if record.status == "conflict_copy")
    return current_document.replace_files(rebuilt_files, updated_at=rewritten_at)


@dataclass(frozen=True)
class ManifestConvergenceResult:
    filemap: FileMapDocument
    tombstones: List[TombstoneRecord]
    reclaimed_tombstones: List[TombstoneRecord]


def converge_manifest_state(
    current_document: FileMapDocument,
    manifest: ManifestRecord,
    *,
    local_tombstones: Iterable[TombstoneRecord],
    rewritten_at: int,
) -> ManifestConvergenceResult:
    merged_tombstones = merge_manifest_tombstones(local_tombstones, manifest.tombstones)
    reclaimed = select_reclaimable_tombstones(
        merged_tombstones,
        manifest.tombstones,
        target_revision=manifest.revision,
    )
    reclaimed_ids = {record.file_id for record in reclaimed}
    retained = [record for record in merged_tombstones if record.file_id not in reclaimed_ids]
    rebuilt = rebuild_filemap_from_manifest(
        current_document,
        manifest,
        local_tombstones=retained,
        rewritten_at=rewritten_at,
    )
    return ManifestConvergenceResult(
        filemap=rebuilt,
        tombstones=retained,
        reclaimed_tombstones=reclaimed,
    )
