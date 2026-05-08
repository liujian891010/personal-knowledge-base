from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Protocol

from .sync_api import (
    BlobUploadInitRequestPayload,
    BlobUploadInitResponsePayload,
    CommitConflictResponsePayload,
    CreateCommitResponsePayload,
    build_blob_upload_init_request,
    parse_blob_check_response,
    parse_blob_upload_init_response,
    parse_commit_conflict_response,
    parse_create_commit_response,
    serialize_blob_check_request,
    serialize_blob_upload_init_request,
    serialize_create_commit_request,
)
from .sync_commit import (
    BlobUploadPlan,
    CommitNetworkPlan,
    CommitSnapshotTable,
    CommitSubmissionBundle,
    CreateCommitRequestPayload,
    build_blob_check_request,
    build_commit_network_plan,
    resolve_blob_check_result,
)


@dataclass(frozen=True)
class SyncHttpJsonResponse:
    status_code: int
    payload: Mapping[str, object]


class SyncCommitTransport(Protocol):
    def post_blob_check(
        self,
        vault_id: str,
        payload: Mapping[str, object],
    ) -> SyncHttpJsonResponse:
        ...

    def post_blob_upload_init(
        self,
        vault_id: str,
        payload: Mapping[str, object],
    ) -> SyncHttpJsonResponse:
        ...

    def post_create_commit(
        self,
        vault_id: str,
        payload: Mapping[str, object],
    ) -> SyncHttpJsonResponse:
        ...


@dataclass(frozen=True)
class CommitPreflightResult:
    network_plan: CommitNetworkPlan
    upload_init_request: Optional[BlobUploadInitRequestPayload]
    upload_init_response: Optional[BlobUploadInitResponsePayload]


@dataclass(frozen=True)
class CreateCommitExecutionResult:
    status: str
    request: CreateCommitRequestPayload
    response: Optional[CreateCommitResponsePayload] = None
    conflict: Optional[CommitConflictResponsePayload] = None


def _require_status(response: SyncHttpJsonResponse, expected_status: int, label: str) -> None:
    if response.status_code != expected_status:
        raise ValueError(
            f"{label} returned unexpected status: {response.status_code}"
        )


def _validate_upload_capabilities(
    upload_plan: BlobUploadPlan,
    response: BlobUploadInitResponsePayload,
) -> None:
    expected_blob_ids = {entry.blob_id for entry in upload_plan.entries}
    returned_blob_ids = {entry.blob_id for entry in response.uploads}
    if returned_blob_ids != expected_blob_ids:
        raise ValueError("upload-init response blob_ids do not match upload plan")


def execute_commit_preflight(
    transport: SyncCommitTransport,
    submission: CommitSubmissionBundle,
    *,
    snapshot_table: CommitSnapshotTable,
) -> CommitPreflightResult:
    blob_check_request = build_blob_check_request(snapshot_table)
    blob_check_http = transport.post_blob_check(
        snapshot_table.vault_id,
        serialize_blob_check_request(blob_check_request),
    )
    _require_status(blob_check_http, 200, "blobs/check")
    blob_check_payload = parse_blob_check_response(blob_check_http.payload)
    blob_check_result = resolve_blob_check_result(
        snapshot_table,
        existing_blob_ids=blob_check_payload.existing_blob_ids,
        missing_blob_ids=blob_check_payload.missing_blob_ids,
    )
    network_plan = build_commit_network_plan(
        submission,
        snapshot_table=snapshot_table,
        blob_check=blob_check_result,
    )

    if not network_plan.blob_uploads.entries:
        return CommitPreflightResult(
            network_plan=network_plan,
            upload_init_request=None,
            upload_init_response=None,
        )

    upload_init_request = build_blob_upload_init_request(network_plan.blob_uploads)
    upload_init_http = transport.post_blob_upload_init(
        snapshot_table.vault_id,
        serialize_blob_upload_init_request(upload_init_request),
    )
    _require_status(upload_init_http, 200, "blobs/upload-init")
    upload_init_response = parse_blob_upload_init_response(upload_init_http.payload)
    _validate_upload_capabilities(network_plan.blob_uploads, upload_init_response)
    return CommitPreflightResult(
        network_plan=network_plan,
        upload_init_request=upload_init_request,
        upload_init_response=upload_init_response,
    )


def execute_create_commit(
    transport: SyncCommitTransport,
    request: CreateCommitRequestPayload,
) -> CreateCommitExecutionResult:
    http_response = transport.post_create_commit(
        request.manifest.vault_id,
        serialize_create_commit_request(request),
    )
    if http_response.status_code == 200:
        return CreateCommitExecutionResult(
            status="committed",
            request=request,
            response=parse_create_commit_response(http_response.payload),
        )
    if http_response.status_code == 409:
        return CreateCommitExecutionResult(
            status="conflict",
            request=request,
            conflict=parse_commit_conflict_response(http_response.payload),
        )
    raise ValueError(f"commits returned unexpected status: {http_response.status_code}")

