from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Protocol

from .models import CommitIntentJournalRecord, ManifestRecord, TombstoneRecord
from .sqlite_store import load_commit_intent_journal, recover_submitted_commit_miss
from .sync_apply import SubmittedRecoveryResult, recover_submitted_commit_flow
from .sync_api import (
    BlobUploadCapability,
    BlobUploadInitRequestPayload,
    BlobUploadInitResponsePayload,
    CommitConflictResponsePayload,
    CreateCommitResponsePayload,
    ResolveCommitIntentRequestPayload,
    ResolveCommitIntentResponsePayload,
    build_blob_upload_init_request,
    parse_blob_check_response,
    parse_blob_upload_init_response,
    parse_commit_conflict_response,
    parse_create_commit_response,
    parse_manifest_response,
    parse_resolve_commit_intent_response,
    serialize_blob_check_request,
    serialize_blob_upload_init_request,
    serialize_create_commit_request,
    serialize_resolve_commit_intent_request,
)
from .sync_commit import (
    BlobUploadPlan,
    BlobUploadPlanEntry,
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

    def post_resolve_commit_intent(
        self,
        vault_id: str,
        payload: Mapping[str, object],
    ) -> SyncHttpJsonResponse:
        ...

    def get_manifest(
        self,
        vault_id: str,
        revision: int,
    ) -> SyncHttpJsonResponse:
        ...


class SyncBlobUploader(Protocol):
    def upload_blob(
        self,
        upload: BlobUploadPlanEntry,
        capability: BlobUploadCapability,
    ) -> None:
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


@dataclass(frozen=True)
class CommitSubmissionExecutionResult:
    preflight: CommitPreflightResult
    uploaded_blob_ids: list[str]
    commit: CreateCommitExecutionResult


@dataclass(frozen=True)
class ResolveCommitIntentExecutionResult:
    request: ResolveCommitIntentRequestPayload
    response: ResolveCommitIntentResponsePayload
    matched_manifest: Optional[ManifestRecord] = None
    matched_manifest_missing: bool = False


@dataclass(frozen=True)
class SubmittedResolveIntentRecoveryExecutionResult:
    resolved: ResolveCommitIntentExecutionResult
    recovery: SubmittedRecoveryResult


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


def _index_upload_capabilities(
    response: BlobUploadInitResponsePayload,
) -> dict[str, BlobUploadCapability]:
    capability_by_blob_id: dict[str, BlobUploadCapability] = {}
    for capability in response.uploads:
        if capability.blob_id in capability_by_blob_id:
            raise ValueError("upload-init response contains duplicate blob_ids")
        capability_by_blob_id[capability.blob_id] = capability
    return capability_by_blob_id


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


def execute_blob_uploads(
    uploader: SyncBlobUploader,
    upload_plan: BlobUploadPlan,
    upload_init_response: BlobUploadInitResponsePayload,
) -> list[str]:
    if not upload_plan.entries:
        if upload_init_response.uploads:
            raise ValueError("upload-init response must be empty when no uploads are planned")
        return []

    _validate_upload_capabilities(upload_plan, upload_init_response)
    capability_by_blob_id = _index_upload_capabilities(upload_init_response)
    uploaded_blob_ids: list[str] = []
    for upload in upload_plan.entries:
        uploader.upload_blob(upload, capability_by_blob_id[upload.blob_id])
        uploaded_blob_ids.append(upload.blob_id)
    return uploaded_blob_ids


def execute_resolve_commit_intent(
    transport: SyncCommitTransport,
    journal: CommitIntentJournalRecord,
) -> ResolveCommitIntentExecutionResult:
    request = ResolveCommitIntentRequestPayload(
        commit_intent_id=journal.commit_intent_id,
        intent_manifest_hash=journal.intent_manifest_hash,
    )
    http_response = transport.post_resolve_commit_intent(
        journal.vault_id,
        serialize_resolve_commit_intent_request(request),
    )
    _require_status(http_response, 200, "commits/resolve-intent")
    response = parse_resolve_commit_intent_response(http_response.payload)

    if response.status != "found":
        return ResolveCommitIntentExecutionResult(
            request=request,
            response=response,
        )

    manifest_http = transport.get_manifest(
        journal.vault_id,
        response.matched_revision,
    )
    if manifest_http.status_code == 404:
        return ResolveCommitIntentExecutionResult(
            request=request,
            response=response,
            matched_manifest=None,
            matched_manifest_missing=True,
        )
    _require_status(
        manifest_http,
        200,
        f"manifests/{response.matched_revision}",
    )
    matched_manifest = parse_manifest_response(manifest_http.payload)
    if matched_manifest.vault_id != journal.vault_id:
        raise ValueError("matched manifest vault_id does not match resolve-intent journal vault")
    if matched_manifest.revision != response.matched_revision:
        raise ValueError("matched manifest revision does not match resolve-intent response")
    return ResolveCommitIntentExecutionResult(
        request=request,
        response=response,
        matched_manifest=matched_manifest,
    )


def execute_submitted_recovery_via_resolve_intent(
    transport: SyncCommitTransport,
    connection: sqlite3.Connection,
    *,
    ledger_path: Path,
    vault_id: str,
    local_tombstones: Iterable[TombstoneRecord],
    normalized_at: int,
) -> SubmittedResolveIntentRecoveryExecutionResult:
    journal = load_commit_intent_journal(connection, vault_id)
    if journal is None:
        raise KeyError(f"commit_intent_journal not found: {vault_id}")
    if journal.status not in {"submitted", "acknowledged"}:
        raise ValueError("submitted or acknowledged journal is required for resolve-intent recovery")

    local_tombstone_list = list(local_tombstones)
    resolved = execute_resolve_commit_intent(transport, journal)
    observed_head_revision = resolved.response.observed_head_revision
    if observed_head_revision is None:
        raise ValueError("resolve-intent response must include observed_head_revision for submitted recovery")

    if resolved.response.status == "mismatched":
        raise ValueError("resolve-intent reported mismatched status for submitted recovery")

    if resolved.response.status == "not_found":
        recovery = SubmittedRecoveryResult(
            state=recover_submitted_commit_miss(
                connection,
                vault_id,
                normalized_at=normalized_at,
            ),
            tombstones=local_tombstone_list,
            requires_full_pull=False,
        )
        return SubmittedResolveIntentRecoveryExecutionResult(
            resolved=resolved,
            recovery=recovery,
        )

    matched_revision = resolved.response.matched_revision
    if matched_revision is None:
        raise ValueError("resolve-intent found status must include matched_revision")
    recovery = recover_submitted_commit_flow(
        connection,
        ledger_path=ledger_path,
        vault_id=vault_id,
        local_tombstones=local_tombstone_list,
        matched_revision=matched_revision,
        observed_head_revision=observed_head_revision,
        normalized_at=normalized_at,
        matched_manifest=resolved.matched_manifest,
    )
    return SubmittedResolveIntentRecoveryExecutionResult(
        resolved=resolved,
        recovery=recovery,
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


def execute_commit_submission(
    transport: SyncCommitTransport,
    uploader: SyncBlobUploader,
    submission: CommitSubmissionBundle,
    *,
    snapshot_table: CommitSnapshotTable,
) -> CommitSubmissionExecutionResult:
    preflight = execute_commit_preflight(
        transport,
        submission,
        snapshot_table=snapshot_table,
    )
    uploaded_blob_ids: list[str] = []
    if preflight.upload_init_response is not None:
        uploaded_blob_ids = execute_blob_uploads(
            uploader,
            preflight.network_plan.blob_uploads,
            preflight.upload_init_response,
        )
    commit = execute_create_commit(
        transport,
        preflight.network_plan.request,
    )
    return CommitSubmissionExecutionResult(
        preflight=preflight,
        uploaded_blob_ids=uploaded_blob_ids,
        commit=commit,
    )
