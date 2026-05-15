from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Protocol

from .models import CommitIntentJournalRecord, FileMapDocument, ManifestRecord, TombstoneRecord, VaultStateRecord
from .sqlite_store import load_commit_intent_journal, load_vault_state, recover_submitted_commit_miss
from .recovery import requires_full_pull
from .sync_apply import SubmittedRecoveryResult, recover_submitted_commit_flow
from .sync_api import (
    AckRequestPayload,
    AckResponsePayload,
    BlobDownloadCapability,
    BlobDownloadInitRequestPayload,
    BlobDownloadInitResponsePayload,
    BlobUploadCapability,
    BlobUploadInitRequestPayload,
    BlobUploadInitResponsePayload,
    ResumableBlobDownloadCapability,
    ResumableBlobDownloadInitRequestItem,
    ResumableBlobDownloadInitRequestPayload,
    ResumableBlobDownloadInitResponsePayload,
    ResumableBlobUploadCapability,
    ResumableBlobUploadCompleteRequestItem,
    ResumableBlobUploadCompleteRequestPayload,
    ResumableBlobUploadCompleteResponsePayload,
    ResumableBlobUploadInitRequestItem,
    ResumableBlobUploadInitRequestPayload,
    ResumableBlobUploadInitResponsePayload,
    CommitConflictResponsePayload,
    CreateCommitResponsePayload,
    FileVersionListRequestPayload,
    FileVersionListResponsePayload,
    FileVersionUpdateRequestPayload,
    FileVersionUpdateResponsePayload,
    ResolveCommitIntentRequestPayload,
    ResolveCommitIntentResponsePayload,
    TombstoneGcRequestPayload,
    TombstoneGcResponsePayload,
    VaultDeviceHeartbeatResponsePayload,
    VaultDeviceListResponsePayload,
    VaultHeadResponsePayload,
    build_blob_upload_init_request,
    parse_ack_response,
    parse_blob_download_init_response,
    parse_blob_check_response,
    parse_blob_upload_init_response,
    parse_commit_conflict_response,
    parse_create_commit_response,
    parse_file_version_list_response,
    parse_file_version_update_response,
    parse_manifest_response,
    parse_resolve_commit_intent_response,
    parse_resumable_blob_upload_complete_response,
    parse_resumable_blob_download_init_response,
    parse_resumable_blob_upload_init_response,
    parse_tombstone_gc_response,
    parse_vault_device_heartbeat_response,
    parse_vault_device_list_response,
    parse_vault_head_response,
    serialize_ack_request,
    serialize_blob_download_init_request,
    serialize_blob_check_request,
    serialize_blob_upload_init_request,
    serialize_create_commit_request,
    serialize_file_version_list_request,
    serialize_file_version_update_request,
    serialize_resolve_commit_intent_request,
    serialize_resumable_blob_upload_complete_request,
    serialize_resumable_blob_download_init_request,
    serialize_resumable_blob_upload_init_request,
    serialize_tombstone_gc_request,
)
from .sync_plan import plan_pull_reconcile
from .sync_reconcile import ReconcileResult, execute_pull_reconcile
from .sync_commit import (
    BlobCheckResult,
    BlobUploadPlan,
    BlobUploadPlanEntry,
    CommitNetworkPlan,
    LocalCommitRecoveryResult,
    CommitSnapshotTable,
    CommitSubmissionBundle,
    CreateCommitRequestPayload,
    build_blob_check_request,
    build_commit_network_plan,
    plan_commit_recovery,
    recover_local_commit_state,
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

    def post_resumable_blob_upload_init(
        self,
        vault_id: str,
        payload: Mapping[str, object],
    ) -> SyncHttpJsonResponse:
        ...

    def post_resumable_blob_upload_complete(
        self,
        vault_id: str,
        payload: Mapping[str, object],
    ) -> SyncHttpJsonResponse:
        ...

    def post_blob_download_init(
        self,
        vault_id: str,
        payload: Mapping[str, object],
    ) -> SyncHttpJsonResponse:
        ...

    def post_resumable_blob_download_init(
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

    def post_file_versions_list(
        self,
        vault_id: str,
        payload: Mapping[str, object],
    ) -> SyncHttpJsonResponse:
        ...

    def patch_file_version(
        self,
        vault_id: str,
        version_id: str,
        payload: Mapping[str, object],
    ) -> SyncHttpJsonResponse:
        ...

    def get_vault_head(
        self,
        vault_id: str,
    ) -> SyncHttpJsonResponse:
        ...

    def get_vault_devices(
        self,
        vault_id: str,
    ) -> SyncHttpJsonResponse:
        ...

    def post_vault_device_heartbeat(
        self,
        vault_id: str,
    ) -> SyncHttpJsonResponse:
        ...

    def post_tombstone_gc(
        self,
        vault_id: str,
        payload: Mapping[str, object],
    ) -> SyncHttpJsonResponse:
        ...

    def delete_device(
        self,
        device_id: str,
    ) -> SyncHttpJsonResponse:
        ...

    def post_ack(
        self,
        vault_id: str,
        payload: Mapping[str, object],
    ) -> SyncHttpJsonResponse:
        ...


class SyncBlobUploader(Protocol):
    def upload_blob(
        self,
        upload: BlobUploadPlanEntry,
        capability: BlobUploadCapability,
    ) -> None:
        ...


class SyncResumableBlobUploader(Protocol):
    def upload_blob_chunks(
        self,
        upload: BlobUploadPlanEntry,
        capability: ResumableBlobUploadCapability,
    ) -> list[str]:
        ...


class SyncBlobDownloader(Protocol):
    def download_blob(
        self,
        capability: BlobDownloadCapability,
    ) -> bytes:
        ...


@dataclass(frozen=True)
class ResumableBlobDownloadedRange:
    blob_id: str
    offset: int
    payload: bytes


class SyncResumableBlobDownloader(Protocol):
    def download_blob_ranges(
        self,
        capability: ResumableBlobDownloadCapability,
    ) -> list[ResumableBlobDownloadedRange]:
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


@dataclass(frozen=True)
class AckExecutionResult:
    request: AckRequestPayload
    response: AckResponsePayload


@dataclass(frozen=True)
class PullReconcileSessionResult:
    head: VaultHeadResponsePayload
    manifest: Optional[ManifestRecord]
    reconcile: ReconcileResult


@dataclass(frozen=True)
class BlobDownloadInitExecutionResult:
    request: BlobDownloadInitRequestPayload
    response: BlobDownloadInitResponsePayload


@dataclass(frozen=True)
class BlobDownloadSessionResult:
    init: BlobDownloadInitExecutionResult
    downloaded_blobs: dict[str, bytes]


@dataclass(frozen=True)
class ResumableBlobDownloadInitExecutionResult:
    request: ResumableBlobDownloadInitRequestPayload
    response: ResumableBlobDownloadInitResponsePayload


@dataclass(frozen=True)
class ResumableBlobDownloadSessionResult:
    init: ResumableBlobDownloadInitExecutionResult
    downloaded_ranges_by_blob_id: dict[str, list[ResumableBlobDownloadedRange]]
    assembled_blobs: dict[str, bytes]


@dataclass(frozen=True)
class ResumableBlobUploadInitExecutionResult:
    request: ResumableBlobUploadInitRequestPayload
    response: ResumableBlobUploadInitResponsePayload


@dataclass(frozen=True)
class ResumableBlobUploadSessionResult:
    init: ResumableBlobUploadInitExecutionResult
    uploaded_chunk_ids_by_blob_id: dict[str, list[str]]
    complete_request: ResumableBlobUploadCompleteRequestPayload
    complete_response: ResumableBlobUploadCompleteResponsePayload


@dataclass(frozen=True)
class FileVersionListExecutionResult:
    request: FileVersionListRequestPayload
    response: FileVersionListResponsePayload


@dataclass(frozen=True)
class FileVersionUpdateExecutionResult:
    request: FileVersionUpdateRequestPayload
    response: FileVersionUpdateResponsePayload


@dataclass(frozen=True)
class VaultDeviceListExecutionResult:
    response: VaultDeviceListResponsePayload


@dataclass(frozen=True)
class VaultDeviceHeartbeatExecutionResult:
    response: VaultDeviceHeartbeatResponsePayload


@dataclass(frozen=True)
class TombstoneGcExecutionResult:
    request: TombstoneGcRequestPayload
    response: TombstoneGcResponsePayload


@dataclass(frozen=True)
class PullSyncSessionResult:
    pull: PullReconcileSessionResult
    ack: Optional[AckExecutionResult] = None


@dataclass(frozen=True)
class CommitRecoverySessionResult:
    mode: str
    requires_full_pull: bool = False
    local: Optional[LocalCommitRecoveryResult] = None
    submitted: Optional[SubmittedResolveIntentRecoveryExecutionResult] = None


@dataclass(frozen=True)
class VaultSyncSession:
    transport: SyncCommitTransport
    uploader: Optional[SyncBlobUploader] = None
    resumable_uploader: Optional[SyncResumableBlobUploader] = None
    downloader: Optional[SyncBlobDownloader] = None
    resumable_downloader: Optional[SyncResumableBlobDownloader] = None

    def submit_commit(
        self,
        submission: CommitSubmissionBundle,
        *,
        snapshot_table: CommitSnapshotTable,
    ) -> CommitSubmissionExecutionResult:
        preflight = execute_commit_preflight(
            self.transport,
            submission,
            snapshot_table=snapshot_table,
            request_upload_capabilities=self.resumable_uploader is None,
        )
        uploaded_blob_ids: list[str] = []
        if preflight.upload_init_response is not None:
            if self.uploader is None:
                raise ValueError("blob uploader is required for commit submissions with missing blobs")
            uploaded_blob_ids = execute_blob_uploads(
                self.uploader,
                preflight.network_plan.blob_uploads,
                preflight.upload_init_response,
            )
        elif preflight.network_plan.blob_uploads.entries and self.resumable_uploader is not None:
            resumable_result = execute_resumable_blob_upload_session(
                self.transport,
                self.resumable_uploader,
                preflight.network_plan.blob_uploads,
            )
            uploaded_blob_ids = [item.blob_id for item in resumable_result.complete_response.uploads]
        commit = execute_create_commit(
            self.transport,
            preflight.network_plan.request,
        )
        return CommitSubmissionExecutionResult(
            preflight=preflight,
            uploaded_blob_ids=uploaded_blob_ids,
            commit=commit,
        )

    def resolve_commit_intent(
        self,
        journal: CommitIntentJournalRecord,
    ) -> ResolveCommitIntentExecutionResult:
        return execute_resolve_commit_intent(self.transport, journal)

    def recover_submitted_commit(
        self,
        connection: sqlite3.Connection,
        *,
        ledger_path: Path,
        vault_id: str,
        local_tombstones: Iterable[TombstoneRecord],
        normalized_at: int,
    ) -> SubmittedResolveIntentRecoveryExecutionResult:
        return execute_submitted_recovery_via_resolve_intent(
            self.transport,
            connection,
            ledger_path=ledger_path,
            vault_id=vault_id,
            local_tombstones=local_tombstones,
            normalized_at=normalized_at,
        )

    def pull_reconcile(
        self,
        connection: sqlite3.Connection,
        *,
        filemap_path: Path,
        ledger_path: Path,
        current_document: FileMapDocument,
        current_state: VaultStateRecord,
        local_tombstones: Iterable[TombstoneRecord],
        rewritten_at: int,
    ) -> PullReconcileSessionResult:
        return execute_pull_reconcile_session(
            self.transport,
            connection,
            filemap_path=filemap_path,
            ledger_path=ledger_path,
            current_document=current_document,
            current_state=current_state,
            local_tombstones=local_tombstones,
            rewritten_at=rewritten_at,
        )

    def pull_and_ack(
        self,
        connection: sqlite3.Connection,
        *,
        filemap_path: Path,
        ledger_path: Path,
        current_document: FileMapDocument,
        current_state: VaultStateRecord,
        local_tombstones: Iterable[TombstoneRecord],
        rewritten_at: int,
    ) -> PullSyncSessionResult:
        return execute_pull_sync_session(
            self.transport,
            connection,
            filemap_path=filemap_path,
            ledger_path=ledger_path,
            current_document=current_document,
            current_state=current_state,
            local_tombstones=local_tombstones,
            rewritten_at=rewritten_at,
        )

    def ack_pending_revisions(
        self,
        state: VaultStateRecord,
    ) -> Optional[AckExecutionResult]:
        return execute_ack_pending_revisions(
            self.transport,
            state,
        )

    def download_blobs(
        self,
        *,
        vault_id: str,
        blob_ids: Iterable[str],
    ) -> BlobDownloadSessionResult:
        if self.downloader is None:
            raise ValueError("blob downloader is required for blob download sessions")
        return execute_blob_download_session(
            self.transport,
            self.downloader,
            vault_id=vault_id,
            blob_ids=blob_ids,
        )

    def upload_blobs_resumable(
        self,
        upload_plan: BlobUploadPlan,
    ) -> ResumableBlobUploadSessionResult:
        if self.resumable_uploader is None:
            raise ValueError("resumable blob uploader is required for resumable blob upload sessions")
        return execute_resumable_blob_upload_session(
            self.transport,
            self.resumable_uploader,
            upload_plan,
        )

    def download_blobs_resumable(
        self,
        *,
        vault_id: str,
        blobs: Iterable[ResumableBlobDownloadInitRequestItem],
    ) -> ResumableBlobDownloadSessionResult:
        if self.resumable_downloader is None:
            raise ValueError("resumable blob downloader is required for resumable blob download sessions")
        return execute_resumable_blob_download_session(
            self.transport,
            self.resumable_downloader,
            vault_id=vault_id,
            blobs=blobs,
        )

    def list_file_versions(
        self,
        *,
        vault_id: str,
        file_id: str,
        limit: int = 50,
        cursor: Optional[str] = None,
        include_pinned: bool = True,
    ) -> FileVersionListExecutionResult:
        return execute_file_version_list(
            self.transport,
            vault_id=vault_id,
            request=FileVersionListRequestPayload(
                file_id=file_id,
                limit=limit,
                cursor=cursor,
                include_pinned=include_pinned,
            ),
        )

    def update_file_version(
        self,
        *,
        vault_id: str,
        version_id: str,
        version_label: Optional[str] = None,
        change_note: Optional[str] = None,
        is_pinned: Optional[bool] = None,
    ) -> FileVersionUpdateExecutionResult:
        return execute_file_version_update(
            self.transport,
            vault_id=vault_id,
            version_id=version_id,
            request=FileVersionUpdateRequestPayload(
                version_label=version_label,
                change_note=change_note,
                is_pinned=is_pinned,
            ),
        )

    def list_vault_devices(
        self,
        *,
        vault_id: str,
    ) -> VaultDeviceListExecutionResult:
        return execute_vault_device_list(
            self.transport,
            vault_id=vault_id,
        )

    def heartbeat_vault_device(
        self,
        *,
        vault_id: str,
    ) -> VaultDeviceHeartbeatExecutionResult:
        return execute_vault_device_heartbeat(
            self.transport,
            vault_id=vault_id,
        )

    def run_tombstone_gc(
        self,
        *,
        vault_id: str,
        now_ms: Optional[int] = None,
        min_retention_ms: Optional[int] = None,
        inactive_after_ms: Optional[int] = None,
    ) -> TombstoneGcExecutionResult:
        return execute_tombstone_gc(
            self.transport,
            vault_id=vault_id,
            request=TombstoneGcRequestPayload(
                now_ms=now_ms,
                min_retention_ms=min_retention_ms,
                inactive_after_ms=inactive_after_ms,
            ),
        )

    def resume_commit_recovery(
        self,
        connection: sqlite3.Connection,
        *,
        vault_root: Path,
        vault_id: str,
        normalized_at: int,
        ledger_path: Optional[Path] = None,
        local_tombstones: Iterable[TombstoneRecord] = (),
    ) -> CommitRecoverySessionResult:
        return execute_commit_recovery_session(
            self.transport,
            connection,
            vault_root=vault_root,
            vault_id=vault_id,
            normalized_at=normalized_at,
            ledger_path=ledger_path,
            local_tombstones=local_tombstones,
        )


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


def _index_download_capabilities(
    response: BlobDownloadInitResponsePayload,
) -> dict[str, BlobDownloadCapability]:
    capability_by_blob_id: dict[str, BlobDownloadCapability] = {}
    for capability in response.downloads:
        if capability.blob_id in capability_by_blob_id:
            raise ValueError("download-init response contains duplicate blob_ids")
        capability_by_blob_id[capability.blob_id] = capability
    return capability_by_blob_id


def _validate_resumable_upload_capabilities(
    upload_plan: BlobUploadPlan,
    response: ResumableBlobUploadInitResponsePayload,
) -> None:
    expected_blob_ids = {entry.blob_id for entry in upload_plan.entries}
    returned_blob_ids = {entry.blob_id for entry in response.uploads}
    if returned_blob_ids != expected_blob_ids:
        raise ValueError("resumable upload-init response blob_ids do not match upload plan")


def _index_resumable_upload_capabilities(
    response: ResumableBlobUploadInitResponsePayload,
) -> dict[str, ResumableBlobUploadCapability]:
    capability_by_blob_id: dict[str, ResumableBlobUploadCapability] = {}
    for capability in response.uploads:
        if capability.blob_id in capability_by_blob_id:
            raise ValueError("resumable upload-init response contains duplicate blob_ids")
        capability_by_blob_id[capability.blob_id] = capability
    return capability_by_blob_id


def execute_commit_preflight(
    transport: SyncCommitTransport,
    submission: CommitSubmissionBundle,
    *,
    snapshot_table: CommitSnapshotTable,
    request_upload_capabilities: bool = True,
) -> CommitPreflightResult:
    if not snapshot_table.entries:
        return CommitPreflightResult(
            network_plan=build_commit_network_plan(
                submission,
                snapshot_table=snapshot_table,
                blob_check=BlobCheckResult(
                    requested_blob_ids=[],
                    existing_blob_ids=[],
                    missing_blob_ids=[],
                ),
            ),
            upload_init_request=None,
            upload_init_response=None,
        )

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

    if not request_upload_capabilities:
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


def execute_blob_download_init(
    transport: SyncCommitTransport,
    vault_id: str,
    blob_ids: Iterable[str],
) -> BlobDownloadInitExecutionResult:
    request = BlobDownloadInitRequestPayload(blob_ids=list(blob_ids))
    http_response = transport.post_blob_download_init(
        vault_id,
        serialize_blob_download_init_request(request),
    )
    _require_status(http_response, 200, "blobs/download-init")
    return BlobDownloadInitExecutionResult(
        request=request,
        response=parse_blob_download_init_response(http_response.payload),
    )


def execute_blob_downloads(
    downloader: SyncBlobDownloader,
    download_init_response: BlobDownloadInitResponsePayload,
) -> dict[str, bytes]:
    capability_by_blob_id = _index_download_capabilities(download_init_response)
    downloaded_blobs: dict[str, bytes] = {}
    for blob_id in sorted(capability_by_blob_id):
        downloaded_blobs[blob_id] = downloader.download_blob(capability_by_blob_id[blob_id])
    return downloaded_blobs


def execute_blob_download_session(
    transport: SyncCommitTransport,
    downloader: SyncBlobDownloader,
    *,
    vault_id: str,
    blob_ids: Iterable[str],
) -> BlobDownloadSessionResult:
    init = execute_blob_download_init(
        transport,
        vault_id,
        blob_ids,
    )
    return BlobDownloadSessionResult(
        init=init,
        downloaded_blobs=execute_blob_downloads(downloader, init.response),
    )


def execute_resumable_blob_download_init(
    transport: SyncCommitTransport,
    vault_id: str,
    blobs: Iterable[ResumableBlobDownloadInitRequestItem],
) -> ResumableBlobDownloadInitExecutionResult:
    request = ResumableBlobDownloadInitRequestPayload(blobs=list(blobs))
    http_response = transport.post_resumable_blob_download_init(
        vault_id,
        serialize_resumable_blob_download_init_request(request),
    )
    _require_status(http_response, 200, "blobs/resumable-download-init")
    return ResumableBlobDownloadInitExecutionResult(
        request=request,
        response=parse_resumable_blob_download_init_response(http_response.payload),
    )


def execute_resumable_blob_downloads(
    downloader: SyncResumableBlobDownloader,
    download_init_response: ResumableBlobDownloadInitResponsePayload,
) -> dict[str, list[ResumableBlobDownloadedRange]]:
    downloaded_ranges_by_blob_id: dict[str, list[ResumableBlobDownloadedRange]] = {}
    for capability in download_init_response.downloads:
        downloaded_ranges_by_blob_id[capability.blob_id] = downloader.download_blob_ranges(capability)
    return downloaded_ranges_by_blob_id


def _assemble_complete_resumable_downloads(
    download_init_response: ResumableBlobDownloadInitResponsePayload,
    downloaded_ranges_by_blob_id: Mapping[str, list[ResumableBlobDownloadedRange]],
) -> dict[str, bytes]:
    assembled: dict[str, bytes] = {}
    for capability in download_init_response.downloads:
        if not capability.ranges:
            if capability.encrypted_size == 0:
                assembled[capability.blob_id] = b""
            continue
        sorted_ranges = sorted(downloaded_ranges_by_blob_id.get(capability.blob_id, []), key=lambda item: item.offset)
        cursor = 0
        payload = bytearray()
        complete = True
        for item in sorted_ranges:
            if item.offset != cursor:
                complete = False
                break
            payload.extend(item.payload)
            cursor += len(item.payload)
        if complete and cursor == capability.encrypted_size:
            assembled[capability.blob_id] = bytes(payload)
    return assembled


def execute_resumable_blob_download_session(
    transport: SyncCommitTransport,
    downloader: SyncResumableBlobDownloader,
    *,
    vault_id: str,
    blobs: Iterable[ResumableBlobDownloadInitRequestItem],
) -> ResumableBlobDownloadSessionResult:
    init = execute_resumable_blob_download_init(transport, vault_id, blobs)
    downloaded_ranges_by_blob_id = execute_resumable_blob_downloads(downloader, init.response)
    return ResumableBlobDownloadSessionResult(
        init=init,
        downloaded_ranges_by_blob_id=downloaded_ranges_by_blob_id,
        assembled_blobs=_assemble_complete_resumable_downloads(init.response, downloaded_ranges_by_blob_id),
    )


def _blob_staging_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def build_resumable_blob_upload_init_request(
    upload_plan: BlobUploadPlan,
) -> ResumableBlobUploadInitRequestPayload:
    if not upload_plan.entries:
        raise ValueError("blob upload plan does not contain any upload entries")
    return ResumableBlobUploadInitRequestPayload(
        blobs=[
            ResumableBlobUploadInitRequestItem(
                blob_id=entry.blob_id,
                encrypted_size=entry.encrypted_size,
                content_hash=entry.content_hash,
                encrypted_sha256=_blob_staging_sha256(entry.blob_staging_path),
            )
            for entry in upload_plan.entries
        ]
    )


def execute_resumable_blob_upload_init(
    transport: SyncCommitTransport,
    upload_plan: BlobUploadPlan,
) -> ResumableBlobUploadInitExecutionResult:
    request = build_resumable_blob_upload_init_request(upload_plan)
    http_response = transport.post_resumable_blob_upload_init(
        upload_plan.vault_id,
        serialize_resumable_blob_upload_init_request(request),
    )
    _require_status(http_response, 200, "blobs/resumable-upload-init")
    response = parse_resumable_blob_upload_init_response(http_response.payload)
    _validate_resumable_upload_capabilities(upload_plan, response)
    return ResumableBlobUploadInitExecutionResult(
        request=request,
        response=response,
    )


def execute_resumable_blob_upload_chunks(
    uploader: SyncResumableBlobUploader,
    upload_plan: BlobUploadPlan,
    upload_init_response: ResumableBlobUploadInitResponsePayload,
) -> dict[str, list[str]]:
    if not upload_plan.entries:
        if upload_init_response.uploads:
            raise ValueError("resumable upload-init response must be empty when no uploads are planned")
        return {}

    _validate_resumable_upload_capabilities(upload_plan, upload_init_response)
    capability_by_blob_id = _index_resumable_upload_capabilities(upload_init_response)
    uploaded_chunk_ids_by_blob_id: dict[str, list[str]] = {}
    for upload in upload_plan.entries:
        uploaded_chunk_ids_by_blob_id[upload.blob_id] = uploader.upload_blob_chunks(
            upload,
            capability_by_blob_id[upload.blob_id],
        )
    return uploaded_chunk_ids_by_blob_id


def build_resumable_blob_upload_complete_request(
    upload_plan: BlobUploadPlan,
    upload_init_response: ResumableBlobUploadInitResponsePayload,
    uploaded_chunk_ids_by_blob_id: Mapping[str, list[str]],
) -> ResumableBlobUploadCompleteRequestPayload:
    request_by_blob_id = {
        item.blob_id: item
        for item in build_resumable_blob_upload_init_request(upload_plan).blobs
    }
    capability_by_blob_id = _index_resumable_upload_capabilities(upload_init_response)
    uploads: list[ResumableBlobUploadCompleteRequestItem] = []
    for upload in upload_plan.entries:
        capability = capability_by_blob_id[upload.blob_id]
        request_item = request_by_blob_id[upload.blob_id]
        known_chunk_ids = {
            chunk.chunk_id
            for chunk in capability.uploaded_chunks + capability.missing_chunks
        }
        uploaded_chunk_ids = list(dict.fromkeys(uploaded_chunk_ids_by_blob_id.get(upload.blob_id, [])))
        if not uploaded_chunk_ids:
            raise ValueError("resumable upload complete requires at least one uploaded chunk id")
        if not set(uploaded_chunk_ids).issubset(known_chunk_ids):
            raise ValueError("resumable upload complete contains unknown uploaded chunk ids")
        uploads.append(
            ResumableBlobUploadCompleteRequestItem(
                blob_id=upload.blob_id,
                session_id=capability.session_id,
                encrypted_size=upload.encrypted_size,
                encrypted_sha256=request_item.encrypted_sha256,
                uploaded_chunk_ids=uploaded_chunk_ids,
            )
        )
    return ResumableBlobUploadCompleteRequestPayload(uploads=uploads)


def execute_resumable_blob_upload_complete(
    transport: SyncCommitTransport,
    vault_id: str,
    request: ResumableBlobUploadCompleteRequestPayload,
) -> ResumableBlobUploadCompleteResponsePayload:
    http_response = transport.post_resumable_blob_upload_complete(
        vault_id,
        serialize_resumable_blob_upload_complete_request(request),
    )
    _require_status(http_response, 200, "blobs/resumable-upload-complete")
    return parse_resumable_blob_upload_complete_response(http_response.payload)


def execute_resumable_blob_upload_session(
    transport: SyncCommitTransport,
    uploader: SyncResumableBlobUploader,
    upload_plan: BlobUploadPlan,
) -> ResumableBlobUploadSessionResult:
    init = execute_resumable_blob_upload_init(transport, upload_plan)
    uploaded_chunk_ids_by_blob_id = execute_resumable_blob_upload_chunks(
        uploader,
        upload_plan,
        init.response,
    )
    complete_request = build_resumable_blob_upload_complete_request(
        upload_plan,
        init.response,
        uploaded_chunk_ids_by_blob_id,
    )
    complete_response = execute_resumable_blob_upload_complete(
        transport,
        upload_plan.vault_id,
        complete_request,
    )
    incomplete = [item.blob_id for item in complete_response.uploads if item.status == "incomplete"]
    if incomplete:
        raise ValueError(f"resumable blob upload incomplete: {', '.join(sorted(incomplete))}")
    return ResumableBlobUploadSessionResult(
        init=init,
        uploaded_chunk_ids_by_blob_id=uploaded_chunk_ids_by_blob_id,
        complete_request=complete_request,
        complete_response=complete_response,
    )


def execute_file_version_list(
    transport: SyncCommitTransport,
    *,
    vault_id: str,
    request: FileVersionListRequestPayload,
) -> FileVersionListExecutionResult:
    http_response = transport.post_file_versions_list(
        vault_id,
        serialize_file_version_list_request(request),
    )
    _require_status(http_response, 200, "file-versions/list")
    return FileVersionListExecutionResult(
        request=request,
        response=parse_file_version_list_response(http_response.payload),
    )


def execute_file_version_update(
    transport: SyncCommitTransport,
    *,
    vault_id: str,
    version_id: str,
    request: FileVersionUpdateRequestPayload,
) -> FileVersionUpdateExecutionResult:
    if not version_id:
        raise ValueError("version_id must be non-empty")
    http_response = transport.patch_file_version(
        vault_id,
        version_id,
        serialize_file_version_update_request(request),
    )
    _require_status(http_response, 200, f"file-versions/{version_id}")
    return FileVersionUpdateExecutionResult(
        request=request,
        response=parse_file_version_update_response(http_response.payload),
    )


def execute_vault_device_list(
    transport: SyncCommitTransport,
    *,
    vault_id: str,
) -> VaultDeviceListExecutionResult:
    if not vault_id:
        raise ValueError("vault_id must be non-empty")
    http_response = transport.get_vault_devices(vault_id)
    _require_status(http_response, 200, "devices")
    response = parse_vault_device_list_response(http_response.payload)
    if response.vault_id != vault_id:
        raise ValueError("devices response vault_id does not match requested vault")
    return VaultDeviceListExecutionResult(
        response=response,
    )


def execute_vault_device_heartbeat(
    transport: SyncCommitTransport,
    *,
    vault_id: str,
) -> VaultDeviceHeartbeatExecutionResult:
    if not vault_id:
        raise ValueError("vault_id must be non-empty")
    http_response = transport.post_vault_device_heartbeat(vault_id)
    _require_status(http_response, 200, "devices/heartbeat")
    response = parse_vault_device_heartbeat_response(http_response.payload)
    if response.vault_id != vault_id:
        raise ValueError("device heartbeat response vault_id does not match requested vault")
    return VaultDeviceHeartbeatExecutionResult(
        response=response,
    )


def execute_tombstone_gc(
    transport: SyncCommitTransport,
    *,
    vault_id: str,
    request: Optional[TombstoneGcRequestPayload] = None,
) -> TombstoneGcExecutionResult:
    if not vault_id:
        raise ValueError("vault_id must be non-empty")
    resolved_request = request or TombstoneGcRequestPayload()
    http_response = transport.post_tombstone_gc(
        vault_id,
        serialize_tombstone_gc_request(resolved_request),
    )
    _require_status(http_response, 200, "tombstones/gc")
    response = parse_tombstone_gc_response(http_response.payload)
    if response.vault_id != vault_id:
        raise ValueError("tombstone gc response vault_id does not match requested vault")
    return TombstoneGcExecutionResult(
        request=resolved_request,
        response=response,
    )


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


def execute_ack_revisions(
    transport: SyncCommitTransport,
    vault_id: str,
    revisions: Iterable[int],
) -> AckExecutionResult:
    request = AckRequestPayload(revisions=list(revisions))
    http_response = transport.post_ack(
        vault_id,
        serialize_ack_request(request),
    )
    _require_status(http_response, 200, "ack")
    return AckExecutionResult(
        request=request,
        response=parse_ack_response(http_response.payload),
    )


def execute_ack_pending_revisions(
    transport: SyncCommitTransport,
    state: VaultStateRecord,
) -> Optional[AckExecutionResult]:
    if not state.pending_ack_to_server:
        return None
    return execute_ack_revisions(
        transport,
        state.vault_id,
        state.pending_ack_to_server,
    )


def execute_pull_reconcile_session(
    transport: SyncCommitTransport,
    connection: sqlite3.Connection,
    *,
    filemap_path: Path,
    ledger_path: Path,
    current_document: FileMapDocument,
    current_state: VaultStateRecord,
    local_tombstones: Iterable[TombstoneRecord],
    rewritten_at: int,
) -> PullReconcileSessionResult:
    head_http = transport.get_vault_head(current_state.vault_id)
    _require_status(head_http, 200, "head")
    head = parse_vault_head_response(head_http.payload)
    if head.vault_id != current_state.vault_id:
        raise ValueError("head response vault_id does not match current state")

    reconcile_plan = plan_pull_reconcile(
        current_state,
        observed_head_revision=head.head_revision,
    )
    manifest: Optional[ManifestRecord] = None
    if reconcile_plan.should_download_manifest:
        manifest_http = transport.get_manifest(
            current_state.vault_id,
            reconcile_plan.target_revision,
        )
        _require_status(
            manifest_http,
            200,
            f"manifests/{reconcile_plan.target_revision}",
        )
        manifest = parse_manifest_response(manifest_http.payload)

    reconcile = execute_pull_reconcile(
        connection,
        filemap_path=filemap_path,
        ledger_path=ledger_path,
        current_document=current_document,
        current_state=current_state,
        local_tombstones=local_tombstones,
        observed_head_revision=head.head_revision,
        rewritten_at=rewritten_at,
        manifest=manifest,
    )
    return PullReconcileSessionResult(
        head=head,
        manifest=manifest,
        reconcile=reconcile,
    )


def execute_pull_sync_session(
    transport: SyncCommitTransport,
    connection: sqlite3.Connection,
    *,
    filemap_path: Path,
    ledger_path: Path,
    current_document: FileMapDocument,
    current_state: VaultStateRecord,
    local_tombstones: Iterable[TombstoneRecord],
    rewritten_at: int,
) -> PullSyncSessionResult:
    pull = execute_pull_reconcile_session(
        transport,
        connection,
        filemap_path=filemap_path,
        ledger_path=ledger_path,
        current_document=current_document,
        current_state=current_state,
        local_tombstones=local_tombstones,
        rewritten_at=rewritten_at,
    )
    return PullSyncSessionResult(
        pull=pull,
        ack=execute_ack_pending_revisions(
            transport,
            pull.reconcile.state,
        ),
    )


def execute_commit_recovery_session(
    transport: SyncCommitTransport,
    connection: sqlite3.Connection,
    *,
    vault_root: Path,
    vault_id: str,
    normalized_at: int,
    ledger_path: Optional[Path] = None,
    local_tombstones: Iterable[TombstoneRecord] = (),
) -> CommitRecoverySessionResult:
    state = load_vault_state(connection, vault_id)
    if state is None:
        raise KeyError(f"vault_state not found: {vault_id}")
    journal = load_commit_intent_journal(connection, vault_id)
    plan = plan_commit_recovery(state, journal=journal)

    if plan.mode != "submitted_confirmation":
        local = recover_local_commit_state(
            connection,
            vault_id=vault_id,
            vault_root=vault_root,
        )
        return CommitRecoverySessionResult(
            mode=plan.mode,
            requires_full_pull=requires_full_pull(local.state),
            local=local,
            submitted=None,
        )

    if ledger_path is None:
        raise ValueError("ledger_path is required for submitted confirmation recovery")
    submitted = execute_submitted_recovery_via_resolve_intent(
        transport,
        connection,
        ledger_path=ledger_path,
        vault_id=vault_id,
        local_tombstones=local_tombstones,
        normalized_at=normalized_at,
    )
    return CommitRecoverySessionResult(
        mode=plan.mode,
        requires_full_pull=submitted.recovery.requires_full_pull,
        local=None,
        submitted=submitted,
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
