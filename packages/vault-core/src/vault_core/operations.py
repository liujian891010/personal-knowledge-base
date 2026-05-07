from __future__ import annotations

from typing import Optional, Tuple

from .models import FileMapDocument, FileRecord, TombstoneRecord


def _assert_path_available(document: FileMapDocument, *, path: str, ignore_file_id: Optional[str] = None) -> None:
    for record in document.files:
        if ignore_file_id is not None and record.file_id == ignore_file_id:
            continue
        if record.status == "deleted":
            continue
        if record.path == path:
            raise ValueError(f"path already occupied: {path}")


def add_file(
    document: FileMapDocument,
    *,
    file_id: str,
    path: str,
    type: str,
    updated_at: int,
    content_hash: Optional[str] = None,
    last_known_revision: Optional[int] = None,
) -> FileMapDocument:
    _assert_path_available(document, path=path)
    updated_files = list(document.files)
    updated_files.append(
        FileRecord(
            file_id=file_id,
            path=path,
            type=type,
            status="active",
            updated_at=updated_at,
            content_hash=content_hash,
            last_known_revision=last_known_revision,
        )
    )
    return document.replace_files(updated_files, updated_at=updated_at)


def rename_file(
    document: FileMapDocument,
    *,
    file_id: str,
    new_path: str,
    updated_at: int,
) -> FileMapDocument:
    _assert_path_available(document, path=new_path, ignore_file_id=file_id)
    updated_files = []
    found = False
    for record in document.files:
        if record.file_id == file_id:
            found = True
            updated_files.append(
                FileRecord(
                    file_id=record.file_id,
                    path=new_path,
                    type=record.type,
                    status=record.status,
                    updated_at=updated_at,
                    content_hash=record.content_hash,
                    last_known_revision=record.last_known_revision,
                    conflict_source_file_id=record.conflict_source_file_id,
                    meta=record.meta,
                )
            )
        else:
            updated_files.append(record)
    if not found:
        raise KeyError(f"file_id not found: {file_id}")
    return document.replace_files(updated_files, updated_at=updated_at)


def register_conflict_copy(
    document: FileMapDocument,
    *,
    source_file_id: str,
    conflict_file_id: str,
    conflict_path: str,
    updated_at: int,
    content_hash: Optional[str] = None,
) -> FileMapDocument:
    source_record = None
    for record in document.files:
        if record.file_id == source_file_id:
            source_record = record
            break
    if source_record is None:
        raise KeyError(f"source file_id not found: {source_file_id}")
    _assert_path_available(document, path=conflict_path)

    updated_files = list(document.files)
    updated_files.append(
        FileRecord(
            file_id=conflict_file_id,
            path=conflict_path,
            type=source_record.type,
            status="conflict_copy",
            updated_at=updated_at,
            content_hash=content_hash,
            last_known_revision=None,
            conflict_source_file_id=source_file_id,
        )
    )
    return document.replace_files(updated_files, updated_at=updated_at)


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
