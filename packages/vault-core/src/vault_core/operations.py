from __future__ import annotations

from typing import Optional, Tuple

from .models import FileMapDocument, FileRecord, TombstoneRecord


def mark_deleted(
    document: FileMapDocument,
    *,
    file_id: str,
    deleted_at: int,
    local_delete_seq: int,
    deleted_revision: Optional[int] = None,
    deleted_by_device: Optional[str] = None,
) -> Tuple[FileMapDocument, TombstoneRecord]:
    updated_files = []
    target = None
    for record in document.files:
        if record.file_id == file_id:
            target = record
            updated_files.append(
                FileRecord(
                    file_id=record.file_id,
                    path=record.path,
                    type=record.type,
                    status="deleted",
                    updated_at=deleted_at,
                    content_hash=record.content_hash,
                    last_known_revision=deleted_revision if deleted_revision is not None else record.last_known_revision,
                    meta=record.meta,
                )
            )
        else:
            updated_files.append(record)

    if target is None:
        raise KeyError(f"file_id not found: {file_id}")

    tombstone = TombstoneRecord(
        file_id=target.file_id,
        last_known_path=target.path,
        deleted_revision=deleted_revision,
        deleted_at=deleted_at,
        local_delete_seq=local_delete_seq,
        deleted_by_device=deleted_by_device,
    )
    updated_document = document.replace_files(updated_files, updated_at=deleted_at)
    return updated_document, tombstone
