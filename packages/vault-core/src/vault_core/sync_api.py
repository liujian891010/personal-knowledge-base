from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from .models import ManifestRecord
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
