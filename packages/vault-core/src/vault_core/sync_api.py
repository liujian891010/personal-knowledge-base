from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping, Optional

from .models import (
    FileVersionCommitDirective,
    FileVersionRecord,
    FileVersionRetentionPolicy,
    ManifestRecord,
)
from .sync_commit import BlobCheckRequest, BlobUploadPlan, CreateCommitRequestPayload


@dataclass(frozen=True)
class BlobCheckResponsePayload:
    existing_blob_ids: list[str]
    missing_blob_ids: list[str]


@dataclass(frozen=True)
class VaultHeadResponsePayload:
    vault_id: str
    head_revision: int
    manifest_summary: Optional[str]


@dataclass(frozen=True)
class VaultDeviceRecordPayload:
    device_id: str
    device_name: str
    platform: str
    protocol_version: str
    acked_revision: int
    is_current_device: bool
    is_revoked: bool
    is_inactive_candidate: bool
    app_version: Optional[str] = None
    registered_at_ms: Optional[int] = None
    last_seen_at_ms: Optional[int] = None


@dataclass(frozen=True)
class VaultDeviceListResponsePayload:
    vault_id: str
    head_revision: int
    inactive_after_ms: int
    devices: list[VaultDeviceRecordPayload]


@dataclass(frozen=True)
class VaultDeviceHeartbeatResponsePayload:
    vault_id: str
    device_id: str
    last_seen_at_ms: int
    acked_revision: int
    head_revision: int


@dataclass(frozen=True)
class TombstoneGcRequestPayload:
    now_ms: Optional[int] = None
    min_retention_ms: Optional[int] = None
    inactive_after_ms: Optional[int] = None


@dataclass(frozen=True)
class TombstoneGcTombstonePayload:
    file_id: str
    deleted_revision: Optional[int]
    deleted_at: Optional[int]
    last_known_path: Optional[str] = None
    deleted_by_device: Optional[str] = None


@dataclass(frozen=True)
class TombstoneGcBlockedDevicePayload:
    device_id: str
    acked_revision: int
    required_revision: int


@dataclass(frozen=True)
class TombstoneGcBlockedTombstonePayload:
    tombstone: TombstoneGcTombstonePayload
    reason: str
    retention_remaining_ms: int
    blocked_by_devices: list[TombstoneGcBlockedDevicePayload]


@dataclass(frozen=True)
class TombstoneGcResponsePayload:
    vault_id: str
    run_id: str
    ran_at_ms: int
    base_revision: int
    new_revision: Optional[int]
    head_revision: int
    active_device_ids: list[str]
    active_device_count: int
    min_retention_ms: int
    inactive_after_ms: int
    reclaimed_tombstones: list[TombstoneGcTombstonePayload]
    blocked_tombstones: list[TombstoneGcBlockedTombstonePayload]
    reclaimed_count: int
    reason: str


@dataclass(frozen=True)
class BlobUploadInitRequestItem:
    blob_id: str
    encrypted_size: int
    content_hash: str


@dataclass(frozen=True)
class BlobUploadInitRequestPayload:
    blobs: list[BlobUploadInitRequestItem]


@dataclass(frozen=True)
class BlobUploadCapability:
    blob_id: str
    upload_url: str
    expires_at: str
    method: str = "PUT"
    headers: Optional[dict[str, str]] = None


@dataclass(frozen=True)
class BlobUploadInitResponsePayload:
    uploads: list[BlobUploadCapability]


@dataclass(frozen=True)
class BlobDownloadInitRequestPayload:
    blob_ids: list[str]


@dataclass(frozen=True)
class BlobDownloadCapability:
    blob_id: str
    download_url: str
    encrypted_size: int
    expires_at: str
    headers: Optional[dict[str, str]] = None


@dataclass(frozen=True)
class BlobDownloadInitResponsePayload:
    downloads: list[BlobDownloadCapability]


DEFAULT_RESUMABLE_BLOB_CHUNK_SIZE = 4 * 1024 * 1024


@dataclass(frozen=True)
class BlobChunkDescriptor:
    chunk_id: str
    offset: int
    size: int


@dataclass(frozen=True)
class ResumableBlobUploadInitRequestItem:
    blob_id: str
    encrypted_size: int
    content_hash: str
    encrypted_sha256: str
    chunk_size: int = DEFAULT_RESUMABLE_BLOB_CHUNK_SIZE


@dataclass(frozen=True)
class ResumableBlobUploadInitRequestPayload:
    blobs: list[ResumableBlobUploadInitRequestItem]


@dataclass(frozen=True)
class ResumableBlobUploadChunkState:
    chunk_id: str
    offset: int
    size: int
    status: str


@dataclass(frozen=True)
class ResumableBlobUploadCapability:
    blob_id: str
    session_id: str
    upload_url: str
    expires_at: str
    chunk_size: int
    encrypted_size: int
    uploaded_chunks: list[ResumableBlobUploadChunkState]
    missing_chunks: list[ResumableBlobUploadChunkState]
    method: str = "PUT"
    headers: Optional[dict[str, str]] = None


@dataclass(frozen=True)
class ResumableBlobUploadInitResponsePayload:
    uploads: list[ResumableBlobUploadCapability]


@dataclass(frozen=True)
class ResumableBlobUploadCompleteRequestItem:
    blob_id: str
    session_id: str
    encrypted_size: int
    encrypted_sha256: str
    uploaded_chunk_ids: list[str]


@dataclass(frozen=True)
class ResumableBlobUploadCompleteRequestPayload:
    uploads: list[ResumableBlobUploadCompleteRequestItem]


@dataclass(frozen=True)
class ResumableBlobUploadCompleteResponseItem:
    blob_id: str
    status: str
    missing_chunks: list[BlobChunkDescriptor]


@dataclass(frozen=True)
class ResumableBlobUploadCompleteResponsePayload:
    uploads: list[ResumableBlobUploadCompleteResponseItem]


@dataclass(frozen=True)
class BlobDownloadRange:
    offset: int
    size: int


@dataclass(frozen=True)
class ResumableBlobDownloadInitRequestItem:
    blob_id: str
    ranges: Optional[list[BlobDownloadRange]] = None


@dataclass(frozen=True)
class ResumableBlobDownloadInitRequestPayload:
    blobs: list[ResumableBlobDownloadInitRequestItem]


@dataclass(frozen=True)
class ResumableBlobDownloadCapability:
    blob_id: str
    download_url: str
    encrypted_size: int
    expires_at: str
    ranges: list[BlobDownloadRange]
    headers: Optional[dict[str, str]] = None


@dataclass(frozen=True)
class ResumableBlobDownloadInitResponsePayload:
    downloads: list[ResumableBlobDownloadCapability]


@dataclass(frozen=True)
class FileVersionListRequestPayload:
    file_id: str
    limit: int = 50
    cursor: Optional[str] = None
    include_pinned: bool = True


@dataclass(frozen=True)
class FileVersionListResponsePayload:
    file_id: str
    versions: list[FileVersionRecord]
    next_cursor: Optional[str] = None
    retention_policy: Optional[FileVersionRetentionPolicy] = None


@dataclass(frozen=True)
class FileVersionUpdateRequestPayload:
    version_label: Optional[str] = None
    change_note: Optional[str] = None
    is_pinned: Optional[bool] = None


@dataclass(frozen=True)
class FileVersionUpdateResponsePayload:
    version: FileVersionRecord


@dataclass(frozen=True)
class ResolveCommitIntentRequestPayload:
    commit_intent_id: str
    intent_manifest_hash: str


@dataclass(frozen=True)
class ResolveCommitIntentResponsePayload:
    status: str
    matched_revision: Optional[int]
    observed_head_revision: Optional[int]
    head_manifest_summary: Optional[str]


@dataclass(frozen=True)
class AckRequestPayload:
    revisions: list[int]


@dataclass(frozen=True)
class AckResponsePayload:
    max_acked_revision: int


@dataclass(frozen=True)
class CreateCommitResponsePayload:
    vault_id: str
    new_revision: int
    head_manifest_summary: str
    acked_revision_for_device: int


@dataclass(frozen=True)
class CommitConflictResponsePayload:
    code: str
    current_head_revision: int
    current_manifest_summary: Optional[str]


def _require_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _require_non_negative_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


def _require_positive_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _require_string_list(
    payload: Mapping[str, object],
    key: str,
    *,
    allow_empty: bool = True,
) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    if not allow_empty and not value:
        raise ValueError(f"{key} must be a non-empty list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError(f"{key} items must be non-empty strings")
        result.append(item)
    return result


def _optional_headers(payload: Mapping[str, object], key: str) -> Optional[dict[str, str]]:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object when provided")
    headers: dict[str, str] = {}
    for header_name, header_value in value.items():
        if not isinstance(header_name, str) or not header_name:
            raise ValueError(f"{key} header names must be non-empty strings")
        if not isinstance(header_value, str):
            raise ValueError(f"{key} header values must be strings")
        headers[header_name] = header_value
    return headers


def _optional_non_empty_string(payload: Mapping[str, object], key: str) -> Optional[str]:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string or null")
    return value


def _optional_positive_int(payload: Mapping[str, object], key: str) -> Optional[int]:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{key} must be a positive integer or null")
    return value


def _optional_non_negative_int(payload: Mapping[str, object], key: str) -> Optional[int]:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer or null")
    return value


def _require_object_list(payload: Mapping[str, object], key: str, *, allow_empty: bool = True) -> list[Mapping[str, object]]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    if not allow_empty and not value:
        raise ValueError(f"{key} must be a non-empty list")
    result: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{key} items must be objects")
        result.append(item)
    return result


def _require_bool(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _require_supported_status(value: object, *, key: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{key} must be one of: {', '.join(sorted(allowed))}")
    return value


def _require_hash_string(payload: Mapping[str, object], key: str) -> str:
    value = _require_string(payload, key)
    if ":" not in value:
        raise ValueError(f"{key} must include a hash algorithm prefix")
    return value


def build_file_version_id(
    *,
    vault_id: str,
    file_id: str,
    revision: int,
    blob_id: str,
    source: str,
) -> str:
    if not vault_id:
        raise ValueError("vault_id must be non-empty")
    if not file_id:
        raise ValueError("file_id must be non-empty")
    if revision <= 0:
        raise ValueError("revision must be a positive integer")
    if not blob_id:
        raise ValueError("blob_id must be non-empty")
    if not source:
        raise ValueError("source must be non-empty")
    digest = hashlib.sha256(
        f"{vault_id}:{file_id}:{revision}:{blob_id}:{source}".encode("utf-8")
    ).hexdigest()[:32]
    return f"fv_{digest}"


def build_file_version_records_from_manifest(
    manifest: ManifestRecord,
    *,
    directives: Optional[list[FileVersionCommitDirective]] = None,
    default_source: str = "commit_success",
) -> list[FileVersionRecord]:
    if manifest.revision <= 0:
        raise ValueError("manifest revision must be finalized before building file version records")
    directive_by_file_id: dict[str, FileVersionCommitDirective] = {}
    for directive in directives or []:
        if directive.file_id in directive_by_file_id:
            raise ValueError("file version directives must have unique file_id values")
        directive_by_file_id[directive.file_id] = directive

    records: list[FileVersionRecord] = []
    for file_entry in manifest.sorted_files():
        directive = directive_by_file_id.get(file_entry.file_id)
        source = directive.source if directive is not None else default_source
        version_label = directive.version_label if directive is not None else None
        change_note = directive.change_note if directive is not None else None
        is_pinned = directive.is_pinned if directive is not None else False
        records.append(
            FileVersionRecord(
                version_id=build_file_version_id(
                    vault_id=manifest.vault_id,
                    file_id=file_entry.file_id,
                    revision=manifest.revision,
                    blob_id=file_entry.blob_id,
                    source=source,
                ),
                file_id=file_entry.file_id,
                path_at_revision=file_entry.path,
                revision=manifest.revision,
                content_hash=file_entry.content_hash,
                blob_id=file_entry.blob_id,
                size=file_entry.size,
                mtime=file_entry.mtime,
                created_at=manifest.created_at,
                created_by_device=manifest.created_by_device,
                source=source,
                version_label=version_label,
                change_note=change_note,
                is_pinned=is_pinned,
            )
        )
    unknown_file_ids = sorted(set(directive_by_file_id) - {record.file_id for record in records})
    if unknown_file_ids:
        raise ValueError("file version directives reference files not present in manifest: " + ", ".join(unknown_file_ids))
    return records


def build_blob_chunk_descriptors(
    *,
    blob_id: str,
    encrypted_size: int,
    chunk_size: int = DEFAULT_RESUMABLE_BLOB_CHUNK_SIZE,
) -> list[BlobChunkDescriptor]:
    if not blob_id:
        raise ValueError("blob_id must be non-empty")
    if encrypted_size < 0:
        raise ValueError("encrypted_size must be non-negative")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    chunks: list[BlobChunkDescriptor] = []
    offset = 0
    while offset < encrypted_size:
        size = min(chunk_size, encrypted_size - offset)
        digest = hashlib.sha256(f"{blob_id}:{offset}:{size}".encode("utf-8")).hexdigest()[:32]
        chunks.append(BlobChunkDescriptor(chunk_id=f"chunk-{digest}", offset=offset, size=size))
        offset += size
    if encrypted_size == 0:
        digest = hashlib.sha256(f"{blob_id}:0:0".encode("utf-8")).hexdigest()[:32]
        return [BlobChunkDescriptor(chunk_id=f"chunk-{digest}", offset=0, size=0)]
    return chunks


def _serialize_chunk_descriptor(chunk: BlobChunkDescriptor) -> dict[str, object]:
    return {
        "chunk_id": chunk.chunk_id,
        "offset": chunk.offset,
        "size": chunk.size,
    }


def _parse_chunk_descriptor(payload: Mapping[str, object]) -> BlobChunkDescriptor:
    return BlobChunkDescriptor(
        chunk_id=_require_string(payload, "chunk_id"),
        offset=_require_non_negative_int(payload, "offset"),
        size=_require_non_negative_int(payload, "size"),
    )


def _parse_upload_chunk_state(payload: Mapping[str, object]) -> ResumableBlobUploadChunkState:
    return ResumableBlobUploadChunkState(
        chunk_id=_require_string(payload, "chunk_id"),
        offset=_require_non_negative_int(payload, "offset"),
        size=_require_non_negative_int(payload, "size"),
        status=_require_supported_status(
            payload.get("status"),
            key="status",
            allowed={"missing", "uploaded"},
        ),
    )


def _serialize_download_range(download_range: BlobDownloadRange) -> dict[str, object]:
    if download_range.offset < 0:
        raise ValueError("download range offset must be non-negative")
    if download_range.size <= 0:
        raise ValueError("download range size must be positive")
    return {
        "offset": download_range.offset,
        "size": download_range.size,
    }


def _parse_download_range(payload: Mapping[str, object]) -> BlobDownloadRange:
    return BlobDownloadRange(
        offset=_require_non_negative_int(payload, "offset"),
        size=_require_positive_int(payload, "size"),
    )


def serialize_blob_check_request(request: BlobCheckRequest) -> dict[str, object]:
    return {
        "blob_ids": list(request.blob_ids),
    }


def parse_vault_head_response(payload: Mapping[str, object]) -> VaultHeadResponsePayload:
    manifest_summary = payload.get("manifest_summary")
    if manifest_summary is not None and (not isinstance(manifest_summary, str) or not manifest_summary):
        raise ValueError("manifest_summary must be a non-empty string or null")
    return VaultHeadResponsePayload(
        vault_id=_require_string(payload, "vault_id"),
        head_revision=_require_non_negative_int(payload, "head_revision"),
        manifest_summary=manifest_summary,
    )


def _parse_vault_device_record(payload: Mapping[str, object]) -> VaultDeviceRecordPayload:
    return VaultDeviceRecordPayload(
        device_id=_require_string(payload, "device_id"),
        device_name=_require_string(payload, "device_name"),
        platform=_require_string(payload, "platform"),
        app_version=_optional_non_empty_string(payload, "app_version"),
        protocol_version=_require_string(payload, "protocol_version"),
        registered_at_ms=_optional_non_negative_int(payload, "registered_at_ms"),
        last_seen_at_ms=_optional_non_negative_int(payload, "last_seen_at_ms"),
        acked_revision=_require_non_negative_int(payload, "acked_revision"),
        is_current_device=_require_bool(payload, "is_current_device"),
        is_revoked=_require_bool(payload, "is_revoked"),
        is_inactive_candidate=_require_bool(payload, "is_inactive_candidate"),
    )


def parse_vault_device_list_response(
    payload: Mapping[str, object],
) -> VaultDeviceListResponsePayload:
    return VaultDeviceListResponsePayload(
        vault_id=_require_string(payload, "vault_id"),
        head_revision=_require_non_negative_int(payload, "head_revision"),
        inactive_after_ms=_require_positive_int(payload, "inactive_after_ms"),
        devices=[
            _parse_vault_device_record(item)
            for item in _require_object_list(payload, "devices")
        ],
    )


def parse_vault_device_heartbeat_response(
    payload: Mapping[str, object],
) -> VaultDeviceHeartbeatResponsePayload:
    return VaultDeviceHeartbeatResponsePayload(
        vault_id=_require_string(payload, "vault_id"),
        device_id=_require_string(payload, "device_id"),
        last_seen_at_ms=_require_non_negative_int(payload, "last_seen_at_ms"),
        acked_revision=_require_non_negative_int(payload, "acked_revision"),
        head_revision=_require_non_negative_int(payload, "head_revision"),
    )


def serialize_tombstone_gc_request(request: TombstoneGcRequestPayload) -> dict[str, object]:
    payload: dict[str, object] = {}
    if request.now_ms is not None:
        if request.now_ms < 0:
            raise ValueError("now_ms must be non-negative")
        payload["now_ms"] = request.now_ms
    if request.min_retention_ms is not None:
        if request.min_retention_ms < 0:
            raise ValueError("min_retention_ms must be non-negative")
        payload["min_retention_ms"] = request.min_retention_ms
    if request.inactive_after_ms is not None:
        if request.inactive_after_ms <= 0:
            raise ValueError("inactive_after_ms must be positive")
        payload["inactive_after_ms"] = request.inactive_after_ms
    return payload


def _parse_tombstone_gc_tombstone(payload: Mapping[str, object]) -> TombstoneGcTombstonePayload:
    return TombstoneGcTombstonePayload(
        file_id=_require_string(payload, "file_id"),
        deleted_revision=_optional_positive_int(payload, "deleted_revision"),
        deleted_at=_optional_non_negative_int(payload, "deleted_at"),
        last_known_path=_optional_non_empty_string(payload, "last_known_path"),
        deleted_by_device=_optional_non_empty_string(payload, "deleted_by_device"),
    )


def _parse_tombstone_gc_blocked_device(payload: Mapping[str, object]) -> TombstoneGcBlockedDevicePayload:
    return TombstoneGcBlockedDevicePayload(
        device_id=_require_string(payload, "device_id"),
        acked_revision=_require_non_negative_int(payload, "acked_revision"),
        required_revision=_require_positive_int(payload, "required_revision"),
    )


def _parse_tombstone_gc_blocked_tombstone(payload: Mapping[str, object]) -> TombstoneGcBlockedTombstonePayload:
    return TombstoneGcBlockedTombstonePayload(
        tombstone=_parse_tombstone_gc_tombstone(payload),
        reason=_require_string(payload, "reason"),
        retention_remaining_ms=_require_non_negative_int(payload, "retention_remaining_ms"),
        blocked_by_devices=[
            _parse_tombstone_gc_blocked_device(item)
            for item in _require_object_list(payload, "blocked_by_devices")
        ],
    )


def parse_tombstone_gc_response(payload: Mapping[str, object]) -> TombstoneGcResponsePayload:
    new_revision = _optional_positive_int(payload, "new_revision")
    return TombstoneGcResponsePayload(
        vault_id=_require_string(payload, "vault_id"),
        run_id=_require_string(payload, "run_id"),
        ran_at_ms=_require_non_negative_int(payload, "ran_at_ms"),
        base_revision=_require_non_negative_int(payload, "base_revision"),
        new_revision=new_revision,
        head_revision=_require_non_negative_int(payload, "head_revision"),
        active_device_ids=_require_string_list(payload, "active_device_ids"),
        active_device_count=_require_non_negative_int(payload, "active_device_count"),
        min_retention_ms=_require_non_negative_int(payload, "min_retention_ms"),
        inactive_after_ms=_require_positive_int(payload, "inactive_after_ms"),
        reclaimed_tombstones=[
            _parse_tombstone_gc_tombstone(item)
            for item in _require_object_list(payload, "reclaimed_tombstones")
        ],
        blocked_tombstones=[
            _parse_tombstone_gc_blocked_tombstone(item)
            for item in _require_object_list(payload, "blocked_tombstones")
        ],
        reclaimed_count=_require_non_negative_int(payload, "reclaimed_count"),
        reason=_require_string(payload, "reason"),
    )


def parse_blob_check_response(payload: Mapping[str, object]) -> BlobCheckResponsePayload:
    return BlobCheckResponsePayload(
        existing_blob_ids=_require_string_list(payload, "existing_blob_ids"),
        missing_blob_ids=_require_string_list(payload, "missing_blob_ids"),
    )


def build_blob_upload_init_request(
    upload_plan: BlobUploadPlan,
) -> BlobUploadInitRequestPayload:
    if not upload_plan.entries:
        raise ValueError("blob upload plan does not contain any upload entries")
    return BlobUploadInitRequestPayload(
        blobs=[
            BlobUploadInitRequestItem(
                blob_id=entry.blob_id,
                encrypted_size=entry.encrypted_size,
                content_hash=entry.content_hash,
            )
            for entry in upload_plan.entries
        ]
    )


def serialize_blob_upload_init_request(
    request: BlobUploadInitRequestPayload,
) -> dict[str, object]:
    return {
        "blobs": [
            {
                "blob_id": item.blob_id,
                "encrypted_size": item.encrypted_size,
                "content_hash": item.content_hash,
            }
            for item in request.blobs
        ]
    }


def parse_blob_upload_init_response(
    payload: Mapping[str, object],
) -> BlobUploadInitResponsePayload:
    uploads_payload = payload.get("uploads")
    if not isinstance(uploads_payload, list):
        raise ValueError("uploads must be a list")

    uploads: list[BlobUploadCapability] = []
    for item in uploads_payload:
        if not isinstance(item, dict):
            raise ValueError("uploads items must be objects")
        uploads.append(
            BlobUploadCapability(
                blob_id=_require_string(item, "blob_id"),
                upload_url=_require_string(item, "upload_url"),
                expires_at=_require_string(item, "expires_at"),
                method=item.get("method", "PUT") if isinstance(item.get("method", "PUT"), str) else "PUT",
                headers=_optional_headers(item, "headers"),
            )
        )
    return BlobUploadInitResponsePayload(uploads=uploads)


def serialize_blob_download_init_request(
    request: BlobDownloadInitRequestPayload,
) -> dict[str, object]:
    blob_ids = _require_string_list(
        {"blob_ids": request.blob_ids},
        "blob_ids",
        allow_empty=False,
    )
    if len(set(blob_ids)) != len(blob_ids):
        raise ValueError("blob_ids must be unique")
    return {
        "blob_ids": blob_ids,
    }


def parse_blob_download_init_response(
    payload: Mapping[str, object],
) -> BlobDownloadInitResponsePayload:
    downloads_payload = payload.get("downloads")
    if not isinstance(downloads_payload, list):
        raise ValueError("downloads must be a list")

    downloads: list[BlobDownloadCapability] = []
    for item in downloads_payload:
        if not isinstance(item, dict):
            raise ValueError("downloads items must be objects")
        downloads.append(
            BlobDownloadCapability(
                blob_id=_require_string(item, "blob_id"),
                download_url=_require_string(item, "download_url"),
                encrypted_size=_require_non_negative_int(item, "encrypted_size"),
                expires_at=_require_string(item, "expires_at"),
                headers=_optional_headers(item, "headers"),
            )
        )
    return BlobDownloadInitResponsePayload(downloads=downloads)


def serialize_resumable_blob_upload_init_request(
    request: ResumableBlobUploadInitRequestPayload,
) -> dict[str, object]:
    if not request.blobs:
        raise ValueError("resumable upload-init request must contain at least one blob")
    blob_ids = [item.blob_id for item in request.blobs]
    if len(set(blob_ids)) != len(blob_ids):
        raise ValueError("resumable upload-init blob_ids must be unique")
    return {
        "blobs": [
            {
                "blob_id": item.blob_id,
                "encrypted_size": item.encrypted_size,
                "content_hash": item.content_hash,
                "encrypted_sha256": item.encrypted_sha256,
                "chunk_size": item.chunk_size,
                "chunks": [
                    _serialize_chunk_descriptor(chunk)
                    for chunk in build_blob_chunk_descriptors(
                        blob_id=item.blob_id,
                        encrypted_size=item.encrypted_size,
                        chunk_size=item.chunk_size,
                    )
                ],
            }
            for item in request.blobs
        ]
    }


def parse_resumable_blob_upload_init_response(
    payload: Mapping[str, object],
) -> ResumableBlobUploadInitResponsePayload:
    uploads: list[ResumableBlobUploadCapability] = []
    for item in _require_object_list(payload, "uploads"):
        uploads.append(
            ResumableBlobUploadCapability(
                blob_id=_require_string(item, "blob_id"),
                session_id=_require_string(item, "session_id"),
                upload_url=_require_string(item, "upload_url"),
                expires_at=_require_string(item, "expires_at"),
                chunk_size=_require_positive_int(item, "chunk_size"),
                encrypted_size=_require_non_negative_int(item, "encrypted_size"),
                uploaded_chunks=[
                    _parse_upload_chunk_state(chunk)
                    for chunk in _require_object_list(item, "uploaded_chunks")
                ],
                missing_chunks=[
                    _parse_upload_chunk_state(chunk)
                    for chunk in _require_object_list(item, "missing_chunks")
                ],
                method=item.get("method", "PUT") if isinstance(item.get("method", "PUT"), str) else "PUT",
                headers=_optional_headers(item, "headers"),
            )
        )
    return ResumableBlobUploadInitResponsePayload(uploads=uploads)


def serialize_resumable_blob_upload_complete_request(
    request: ResumableBlobUploadCompleteRequestPayload,
) -> dict[str, object]:
    if not request.uploads:
        raise ValueError("resumable upload complete request must contain at least one upload")
    return {
        "uploads": [
            {
                "blob_id": item.blob_id,
                "session_id": item.session_id,
                "encrypted_size": item.encrypted_size,
                "encrypted_sha256": item.encrypted_sha256,
                "uploaded_chunk_ids": _require_string_list(
                    {"uploaded_chunk_ids": item.uploaded_chunk_ids},
                    "uploaded_chunk_ids",
                    allow_empty=False,
                ),
            }
            for item in request.uploads
        ]
    }


def parse_resumable_blob_upload_complete_response(
    payload: Mapping[str, object],
) -> ResumableBlobUploadCompleteResponsePayload:
    uploads: list[ResumableBlobUploadCompleteResponseItem] = []
    for item in _require_object_list(payload, "uploads"):
        uploads.append(
            ResumableBlobUploadCompleteResponseItem(
                blob_id=_require_string(item, "blob_id"),
                status=_require_supported_status(
                    item.get("status"),
                    key="status",
                    allowed={"accepted", "already_exists", "incomplete"},
                ),
                missing_chunks=[
                    _parse_chunk_descriptor(chunk)
                    for chunk in _require_object_list(item, "missing_chunks")
                ],
            )
        )
    return ResumableBlobUploadCompleteResponsePayload(uploads=uploads)


def serialize_resumable_blob_download_init_request(
    request: ResumableBlobDownloadInitRequestPayload,
) -> dict[str, object]:
    if not request.blobs:
        raise ValueError("resumable download-init request must contain at least one blob")
    blob_ids = [item.blob_id for item in request.blobs]
    if len(set(blob_ids)) != len(blob_ids):
        raise ValueError("resumable download-init blob_ids must be unique")
    blobs: list[dict[str, object]] = []
    for item in request.blobs:
        blob: dict[str, object] = {"blob_id": item.blob_id}
        if item.ranges is not None:
            blob["ranges"] = [_serialize_download_range(download_range) for download_range in item.ranges]
        blobs.append(blob)
    return {"blobs": blobs}


def parse_resumable_blob_download_init_response(
    payload: Mapping[str, object],
) -> ResumableBlobDownloadInitResponsePayload:
    downloads: list[ResumableBlobDownloadCapability] = []
    for item in _require_object_list(payload, "downloads"):
        downloads.append(
            ResumableBlobDownloadCapability(
                blob_id=_require_string(item, "blob_id"),
                download_url=_require_string(item, "download_url"),
                encrypted_size=_require_non_negative_int(item, "encrypted_size"),
                expires_at=_require_string(item, "expires_at"),
                ranges=[
                    _parse_download_range(download_range)
                    for download_range in _require_object_list(item, "ranges")
                ],
                headers=_optional_headers(item, "headers"),
            )
        )
    return ResumableBlobDownloadInitResponsePayload(downloads=downloads)


def serialize_file_version_commit_directives(
    directives: list[FileVersionCommitDirective],
) -> list[dict[str, object]]:
    seen_file_ids: set[str] = set()
    serialized: list[dict[str, object]] = []
    for directive in directives:
        if directive.file_id in seen_file_ids:
            raise ValueError("file version directives must have unique file_id values")
        seen_file_ids.add(directive.file_id)
        serialized.append(directive.to_dict())
    return serialized


def parse_file_version_commit_directives(
    payload: object,
) -> list[FileVersionCommitDirective]:
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise ValueError("file_version_directives must be a list when provided")
    directives: list[FileVersionCommitDirective] = []
    seen_file_ids: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("file_version_directives items must be objects")
        directive = FileVersionCommitDirective.from_dict(dict(item))
        if directive.file_id in seen_file_ids:
            raise ValueError("file_version_directives file_id values must be unique")
        seen_file_ids.add(directive.file_id)
        directives.append(directive)
    return directives


def serialize_file_version_list_request(
    request: FileVersionListRequestPayload,
) -> dict[str, object]:
    if not request.file_id:
        raise ValueError("file_id must be non-empty")
    if request.limit <= 0:
        raise ValueError("limit must be positive")
    if request.cursor is not None and not request.cursor:
        raise ValueError("cursor must be non-empty when provided")
    if not isinstance(request.include_pinned, bool):
        raise ValueError("include_pinned must be a boolean")
    payload: dict[str, object] = {
        "file_id": request.file_id,
        "limit": request.limit,
        "include_pinned": request.include_pinned,
    }
    if request.cursor is not None:
        payload["cursor"] = request.cursor
    return payload


def parse_file_version_list_response(
    payload: Mapping[str, object],
) -> FileVersionListResponsePayload:
    retention_policy_payload = payload.get("retention_policy")
    retention_policy: Optional[FileVersionRetentionPolicy] = None
    if retention_policy_payload is not None:
        if not isinstance(retention_policy_payload, dict):
            raise ValueError("retention_policy must be an object when provided")
        retention_policy = FileVersionRetentionPolicy.from_dict(dict(retention_policy_payload))
    return FileVersionListResponsePayload(
        file_id=_require_string(payload, "file_id"),
        versions=[
            FileVersionRecord.from_dict(dict(item))
            for item in _require_object_list(payload, "versions")
        ],
        next_cursor=_optional_non_empty_string(payload, "next_cursor"),
        retention_policy=retention_policy,
    )


def serialize_file_version_update_request(
    request: FileVersionUpdateRequestPayload,
) -> dict[str, object]:
    payload: dict[str, object] = {}
    if request.version_label is not None:
        if not request.version_label:
            raise ValueError("version_label must be non-empty when provided")
        payload["version_label"] = request.version_label
    if request.change_note is not None:
        if not request.change_note:
            raise ValueError("change_note must be non-empty when provided")
        payload["change_note"] = request.change_note
    if request.is_pinned is not None:
        if not isinstance(request.is_pinned, bool):
            raise ValueError("is_pinned must be a boolean when provided")
        payload["is_pinned"] = request.is_pinned
    if not payload:
        raise ValueError("file version update request must contain at least one field")
    return payload


def parse_file_version_update_response(
    payload: Mapping[str, object],
) -> FileVersionUpdateResponsePayload:
    version_payload = payload.get("version")
    if not isinstance(version_payload, dict):
        raise ValueError("version must be an object")
    return FileVersionUpdateResponsePayload(
        version=FileVersionRecord.from_dict(dict(version_payload)),
    )


def serialize_resolve_commit_intent_request(
    request: ResolveCommitIntentRequestPayload,
) -> dict[str, object]:
    return {
        "commit_intent_id": request.commit_intent_id,
        "intent_manifest_hash": request.intent_manifest_hash,
    }


def parse_resolve_commit_intent_response(
    payload: Mapping[str, object],
) -> ResolveCommitIntentResponsePayload:
    status = _require_string(payload, "status")
    if status not in {"found", "not_found", "mismatched"}:
        raise ValueError("status must be a supported resolve-intent result")

    matched_revision = _optional_positive_int(payload, "matched_revision")
    observed_head_revision = _optional_non_negative_int(payload, "observed_head_revision")
    head_manifest_summary = _optional_non_empty_string(payload, "head_manifest_summary")

    if status == "found" and matched_revision is None:
        raise ValueError("matched_revision is required when resolve-intent status is found")
    if status != "found" and matched_revision is not None:
        raise ValueError("matched_revision is only allowed when resolve-intent status is found")
    return ResolveCommitIntentResponsePayload(
        status=status,
        matched_revision=matched_revision,
        observed_head_revision=observed_head_revision,
        head_manifest_summary=head_manifest_summary,
    )


def parse_manifest_response(
    payload: Mapping[str, object],
) -> ManifestRecord:
    if not isinstance(payload, dict):
        raise ValueError("manifest response payload must be an object")
    return ManifestRecord.from_dict(dict(payload))


def serialize_ack_request(
    request: AckRequestPayload,
) -> dict[str, object]:
    if not request.revisions:
        raise ValueError("ack request must contain at least one revision")
    for revision in request.revisions:
        if not isinstance(revision, int) or revision <= 0:
            raise ValueError("ack request revisions must be positive integers")
    if len(set(request.revisions)) != len(request.revisions):
        raise ValueError("ack request revisions must be unique")
    return {
        "revisions": list(request.revisions),
    }


def parse_ack_response(
    payload: Mapping[str, object],
) -> AckResponsePayload:
    return AckResponsePayload(
        max_acked_revision=_require_positive_int(payload, "max_acked_revision"),
    )


def serialize_create_commit_request(
    request: CreateCommitRequestPayload,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "commit_intent_id": request.commit_intent_id,
        "base_revision": request.base_revision,
        "created_by_device": request.created_by_device,
        "intent_manifest_hash": request.intent_manifest_hash,
        "manifest": request.manifest.to_dict(),
        "blob_refs": [
            {
                "blob_id": item.blob_id,
                "file_id": item.file_id,
            }
            for item in request.blob_refs
        ],
    }
    if request.intent_delete_seq_upper_bound is not None:
        payload["intent_delete_seq_upper_bound"] = request.intent_delete_seq_upper_bound
    if request.file_version_directives:
        payload["file_version_directives"] = serialize_file_version_commit_directives(
            request.file_version_directives
        )
    return payload


def parse_create_commit_response(
    payload: Mapping[str, object],
) -> CreateCommitResponsePayload:
    return CreateCommitResponsePayload(
        vault_id=_require_string(payload, "vault_id"),
        new_revision=_require_positive_int(payload, "new_revision"),
        head_manifest_summary=_require_string(payload, "head_manifest_summary"),
        acked_revision_for_device=_require_positive_int(payload, "acked_revision_for_device"),
    )


def parse_commit_conflict_response(
    payload: Mapping[str, object],
) -> CommitConflictResponsePayload:
    code = _require_string(payload, "code")
    if code not in {"base_revision_conflict", "manifest_conflict"}:
        raise ValueError("code must be a supported commit conflict code")

    summary = payload.get("current_manifest_summary")
    if summary is not None and (not isinstance(summary, str) or not summary):
        raise ValueError("current_manifest_summary must be a non-empty string or null")

    return CommitConflictResponsePayload(
        code=code,
        current_head_revision=_require_non_negative_int(payload, "current_head_revision"),
        current_manifest_summary=summary,
    )
