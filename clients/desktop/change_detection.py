from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Optional

from vault_core import FileMapDocument, FileRecord, TombstoneRecord
from vault_core.constants import NOTEAPP_DIRNAME, VAULTINFO_FILENAME
from vault_core.operations import add_file, mark_deleted, rename_file

from .crypto import build_placeholder_blob_id


def _normalize_relative_path(value: str) -> str:
    path = Path(value)
    if path.anchor or path.drive or ".." in path.parts:
        raise ValueError(f"workspace file path is not safe: {value!r}")
    return PurePosixPath(*path.parts).as_posix()


def _resolve_workspace_file_path(vault_root: Path, relative_path: str) -> Path:
    normalized = _normalize_relative_path(relative_path)
    return vault_root.joinpath(*PurePosixPath(normalized).parts)


def _compute_content_hash(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _infer_file_type(relative_path: str) -> str:
    normalized = PurePosixPath(relative_path)
    if normalized == PurePosixPath(".ai/index.md"):
        return "ai_index"
    if normalized.parts[:2] == (".ai", "wiki") and normalized.suffix.lower() in {".md", ".markdown"}:
        return "ai_wiki"
    if normalized.parts[:2] == (".ai", "agents"):
        return "ai_agents"
    if normalized.suffix.lower() in {".md", ".markdown"}:
        return "note"
    return "attachment"


def build_placeholder_file_id(relative_path: str) -> str:
    digest = hashlib.sha256(
        b"pkb-placeholder-file-id-v1\x00" + relative_path.encode("utf-8")
    ).hexdigest()
    return f"file-{digest}"


def _iter_workspace_files(vault_root: Path) -> Iterable[Path]:
    for path in vault_root.rglob("*"):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(vault_root).parts
        if NOTEAPP_DIRNAME in relative_parts:
            continue
        if relative_parts == (VAULTINFO_FILENAME,):
            continue
        yield path


@dataclass(frozen=True)
class DesktopWorkspaceChangeRecord:
    kind: str
    path: str
    file_id: Optional[str]
    file_type: str
    record_status: Optional[str]
    content_hash: Optional[str]
    size_bytes: Optional[int]
    mtime_ms: Optional[int]


@dataclass(frozen=True)
class DesktopWorkspaceChangeSet:
    vault_id: str
    tracked_record_count: int
    change_count: int
    modified_file_ids: list[str]
    missing_file_ids: list[str]
    changes: list[DesktopWorkspaceChangeRecord]


@dataclass(frozen=True)
class DesktopTrackedChangeCommitPlan:
    change_set: DesktopWorkspaceChangeSet
    document: FileMapDocument
    content_by_file_id: dict[str, bytes]
    tombstones: list[TombstoneRecord]
    local_delete_sequence: int


def _build_modified_record(
    vault_root: Path,
    record: FileRecord,
) -> Optional[DesktopWorkspaceChangeRecord]:
    path = _resolve_workspace_file_path(vault_root, record.path)
    normalized_path = _normalize_relative_path(record.path)
    if not path.exists() or not path.is_file():
        return DesktopWorkspaceChangeRecord(
            kind="missing",
            path=normalized_path,
            file_id=record.file_id,
            file_type=record.type,
            record_status=record.status,
            content_hash=record.content_hash,
            size_bytes=None,
            mtime_ms=None,
        )

    payload = path.read_bytes()
    content_hash = _compute_content_hash(payload)
    if content_hash == record.content_hash:
        return None

    stat = path.stat()
    return DesktopWorkspaceChangeRecord(
        kind="modified",
        path=normalized_path,
        file_id=record.file_id,
        file_type=record.type,
        record_status=record.status,
        content_hash=content_hash,
        size_bytes=len(payload),
        mtime_ms=stat.st_mtime_ns // 1_000_000,
    )


def detect_local_workspace_changes(
    vault_root: Path,
    document: FileMapDocument,
) -> DesktopWorkspaceChangeSet:
    tracked_records = [record for record in document.files if record.status != "deleted"]
    tracked_paths = {
        _normalize_relative_path(record.path): record
        for record in tracked_records
    }

    changes: list[DesktopWorkspaceChangeRecord] = []
    modified_file_ids: list[str] = []
    missing_file_ids: list[str] = []

    for record in tracked_records:
        change = _build_modified_record(vault_root, record)
        if change is None:
            continue
        changes.append(change)
        if change.kind == "modified" and change.file_id is not None:
            modified_file_ids.append(change.file_id)
        if change.kind == "missing" and change.file_id is not None:
            missing_file_ids.append(change.file_id)

    for path in _iter_workspace_files(vault_root):
        relative_path = PurePosixPath(path.relative_to(vault_root)).as_posix()
        if relative_path in tracked_paths:
            continue
        payload = path.read_bytes()
        stat = path.stat()
        changes.append(
            DesktopWorkspaceChangeRecord(
                kind="untracked",
                path=relative_path,
                file_id=None,
                file_type=_infer_file_type(relative_path),
                record_status=None,
                content_hash=_compute_content_hash(payload),
                size_bytes=len(payload),
                mtime_ms=stat.st_mtime_ns // 1_000_000,
            )
        )

    changes.sort(key=lambda item: (item.path, item.kind, item.file_id or ""))
    return DesktopWorkspaceChangeSet(
        vault_id=document.vault_id,
        tracked_record_count=len(tracked_records),
        change_count=len(changes),
        modified_file_ids=sorted(modified_file_ids),
        missing_file_ids=sorted(missing_file_ids),
        changes=changes,
    )


def _resolve_mime_type(record: FileRecord, relative_path: str) -> Optional[str]:
    meta = record.meta or {}
    mime_type = meta.get("mime_type")
    if isinstance(mime_type, str):
        return mime_type
    guessed, _ = mimetypes.guess_type(relative_path)
    return guessed


def _match_local_rename_candidates(
    change_set: DesktopWorkspaceChangeSet,
    document: FileMapDocument,
) -> dict[str, DesktopWorkspaceChangeRecord]:
    active_record_by_file_id = {
        record.file_id: record
        for record in document.files
        if record.status == "active"
    }
    missing_by_key: dict[tuple[str, str], list[DesktopWorkspaceChangeRecord]] = {}
    untracked_by_key: dict[tuple[str, str], list[DesktopWorkspaceChangeRecord]] = {}

    for change in change_set.changes:
        if change.content_hash is None:
            continue
        if change.kind == "missing" and change.file_id is not None:
            record = active_record_by_file_id.get(change.file_id)
            if record is None:
                continue
            key = (change.content_hash, record.type)
            missing_by_key.setdefault(key, []).append(change)
            continue
        if change.kind == "untracked":
            key = (change.content_hash, change.file_type)
            untracked_by_key.setdefault(key, []).append(change)

    rename_targets_by_file_id: dict[str, DesktopWorkspaceChangeRecord] = {}
    for key, missing_changes in missing_by_key.items():
        untracked_changes = untracked_by_key.get(key, [])
        if len(missing_changes) != 1 or len(untracked_changes) != 1:
            continue
        missing_change = missing_changes[0]
        untracked_change = untracked_changes[0]
        if missing_change.file_id is None or missing_change.path == untracked_change.path:
            continue
        rename_targets_by_file_id[missing_change.file_id] = untracked_change
    return rename_targets_by_file_id


def build_tracked_change_commit_plan(
    vault_root: Path,
    document: FileMapDocument,
    change_set: Optional[DesktopWorkspaceChangeSet] = None,
    *,
    tombstones: Optional[Iterable[TombstoneRecord]] = None,
    current_local_delete_sequence: int = 0,
    deleted_by_device: Optional[str] = None,
    file_id_builder: Optional[Callable[[str], str]] = None,
) -> DesktopTrackedChangeCommitPlan:
    resolved_change_set = (
        detect_local_workspace_changes(vault_root, document)
        if change_set is None
        else change_set
    )
    unsupported = [
        change
        for change in resolved_change_set.changes
        if (
            change.kind in {"modified", "missing"}
            and (change.record_status != "active" or change.file_id is None)
        )
        or change.kind not in {"modified", "missing", "untracked"}
    ]
    if unsupported:
        unsupported_kinds = ", ".join(
            sorted({f"{item.kind}:{item.record_status or 'none'}" for item in unsupported})
        )
        raise ValueError(
            "detected local changes include unsupported items for tracked submit: "
            + unsupported_kinds
        )
    if not resolved_change_set.changes:
        raise ValueError("no supported tracked changes detected")

    modified_file_id_set = set(resolved_change_set.modified_file_ids)
    content_by_file_id: dict[str, bytes] = {}
    latest_updated_at = document.updated_at
    working_document = document
    resolved_tombstones = list(tombstones or [])
    local_delete_sequence = current_local_delete_sequence
    resolve_file_id = build_placeholder_file_id if file_id_builder is None else file_id_builder
    record_by_file_id = {record.file_id: record for record in document.files}
    rename_targets_by_file_id = _match_local_rename_candidates(resolved_change_set, document)
    consumed_untracked_paths = {
        change.path for change in rename_targets_by_file_id.values()
    }

    updated_files: list[FileRecord] = []

    for record in document.files:
        if record.file_id not in modified_file_id_set:
            updated_files.append(record)
            continue

        relative_path = _normalize_relative_path(record.path)
        path = _resolve_workspace_file_path(vault_root, relative_path)
        payload = path.read_bytes()
        stat = path.stat()
        content_hash = _compute_content_hash(payload)
        size_bytes = len(payload)
        mtime_ms = stat.st_mtime_ns // 1_000_000
        meta = dict(record.meta or {})
        meta.update(
            {
                "blob_id": build_placeholder_blob_id(content_hash),
                "size": size_bytes,
                "mtime": mtime_ms,
                "mime_type": _resolve_mime_type(record, relative_path),
            }
        )
        updated_files.append(
            FileRecord(
                file_id=record.file_id,
                path=record.path,
                type=record.type,
                status=record.status,
                updated_at=mtime_ms,
                content_hash=content_hash,
                last_known_revision=record.last_known_revision,
                conflict_source_file_id=record.conflict_source_file_id,
                meta=meta,
            )
        )
        content_by_file_id[record.file_id] = payload
        latest_updated_at = max(latest_updated_at, mtime_ms)

    working_document = document.replace_files(updated_files, updated_at=latest_updated_at)
    for file_id, rename_target in sorted(
        rename_targets_by_file_id.items(),
        key=lambda item: (item[1].path, item[0]),
    ):
        current_record = record_by_file_id.get(file_id)
        if current_record is None:
            raise KeyError(f"file_id not found in document: {file_id}")

        relative_path = _normalize_relative_path(rename_target.path)
        path = _resolve_workspace_file_path(vault_root, relative_path)
        payload = path.read_bytes()
        stat = path.stat()
        content_hash = _compute_content_hash(payload)
        if content_hash != current_record.content_hash:
            raise ValueError(
                f"rename candidate content hash mismatch for file_id: {file_id}"
            )
        mtime_ms = stat.st_mtime_ns // 1_000_000
        renamed_at = max(working_document.updated_at, current_record.updated_at, mtime_ms) + 1
        working_document = rename_file(
            working_document,
            file_id=file_id,
            new_path=relative_path,
            updated_at=renamed_at,
        )
        rewritten_files: list[FileRecord] = []
        for record in working_document.files:
            if record.file_id != file_id:
                rewritten_files.append(record)
                continue
            meta = dict(record.meta or {})
            blob_id = meta.get("blob_id")
            resolved_blob_id = (
                blob_id
                if isinstance(blob_id, str) and blob_id
                else build_placeholder_blob_id(content_hash)
            )
            meta.update(
                {
                    "blob_id": resolved_blob_id,
                    "size": len(payload),
                    "mtime": mtime_ms,
                    "mime_type": _resolve_mime_type(record, relative_path),
                }
            )
            rewritten_files.append(
                FileRecord(
                    file_id=record.file_id,
                    path=relative_path,
                    type=record.type,
                    status=record.status,
                    updated_at=renamed_at,
                    content_hash=content_hash,
                    last_known_revision=record.last_known_revision,
                    conflict_source_file_id=record.conflict_source_file_id,
                    meta=meta,
                )
            )
        working_document = working_document.replace_files(
            rewritten_files,
            updated_at=max(working_document.updated_at, renamed_at),
        )
        content_by_file_id[file_id] = payload
        latest_updated_at = max(latest_updated_at, renamed_at)

    missing_file_id_set = set(resolved_change_set.missing_file_ids) - set(rename_targets_by_file_id)
    for record in document.files:
        if record.file_id not in missing_file_id_set:
            continue
        local_delete_sequence += 1
        deleted_at = max(working_document.updated_at, record.updated_at) + 1
        working_document, tombstone = mark_deleted(
            working_document,
            file_id=record.file_id,
            deleted_at=deleted_at,
            local_delete_seq=local_delete_sequence,
            deleted_revision=record.last_known_revision,
            deleted_by_device=deleted_by_device,
        )
        resolved_tombstones.append(tombstone)

    for change in resolved_change_set.changes:
        if change.kind != "untracked":
            continue
        if change.path in consumed_untracked_paths:
            continue
        relative_path = _normalize_relative_path(change.path)
        path = _resolve_workspace_file_path(vault_root, relative_path)
        payload = path.read_bytes()
        stat = path.stat()
        content_hash = _compute_content_hash(payload)
        file_id = resolve_file_id(relative_path)
        mtime_ms = stat.st_mtime_ns // 1_000_000
        working_document = add_file(
            working_document,
            file_id=file_id,
            path=relative_path,
            type=change.file_type,
            updated_at=mtime_ms,
            content_hash=content_hash,
        )
        appended = []
        for record in working_document.files:
            if record.file_id != file_id:
                appended.append(record)
                continue
            appended.append(
                FileRecord(
                    file_id=file_id,
                    path=relative_path,
                    type=change.file_type,
                    status="active",
                    updated_at=mtime_ms,
                    content_hash=content_hash,
                    last_known_revision=None,
                    meta={
                        "blob_id": build_placeholder_blob_id(content_hash),
                        "size": len(payload),
                        "mtime": mtime_ms,
                        "mime_type": mimetypes.guess_type(relative_path)[0],
                    },
                )
            )
        working_document = working_document.replace_files(appended, updated_at=max(working_document.updated_at, mtime_ms))
        content_by_file_id[file_id] = payload

    for record in working_document.files:
        if record.status != "active" or record.file_id in content_by_file_id:
            continue
        path = _resolve_workspace_file_path(vault_root, record.path)
        payload = path.read_bytes()
        content_hash = _compute_content_hash(payload)
        if content_hash != record.content_hash:
            raise ValueError(
                f"active file drifted outside detected change plan: {record.file_id}"
            )
        content_by_file_id[record.file_id] = payload

    return DesktopTrackedChangeCommitPlan(
        change_set=resolved_change_set,
        document=working_document,
        content_by_file_id=content_by_file_id,
        tombstones=resolved_tombstones,
        local_delete_sequence=local_delete_sequence,
    )
