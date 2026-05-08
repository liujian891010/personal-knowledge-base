from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Protocol

from .models import CommitIntentJournalRecord, FileMapDocument, ManifestRecord, TombstoneRecord, VaultStateRecord
from .sqlite_store import load_commit_intent_journal, load_vault_state, recover_submitted_commit_miss
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
    CommitConflictResponsePayload,
    CreateCommitResponsePayload,
    ResolveCommitIntentRequestPayload,
    ResolveCommitIntentResponsePayload,
    VaultHeadResponsePayload,
    build_blob_upload_init_request,
    parse_ack_response,
    parse_blob_download_init_response,
    parse_blob_check_response,
    parse_blob_upload_init_response,
    parse_commit_conflict_response,
    parse_create_commit_response,
    parse_manifest_response,
    parse_resolve_commit_intent_response,
    parse_vault_head_response,
    serialize_ack_request,
    serialize_blob_download_init_request,
    serialize_blob_check_request,
    serialize_blob_upload_init_request,
    serialize_create_commit_request,
    serialize_resolve_commit_intent_request,
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

    def post_blob_download_init(
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

    def get_vault_head(
        self,
        vault_id: str,
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


class SyncBlobDownloader(Protocol):
    def download_blob(
        self,
        capability: BlobDownloadCapability,
    ) -> bytes:
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
class PullSyncSessionResult:
    pull: PullReconcileSessionResult
    ack: Optional[AckExecutionResult] = None


@dataclass(frozen=True)
class CommitRecoverySessionResult:
    mode: str
    local: Optional[LocalCommitRecoveryResult] = None
    submitted: Optional[SubmittedResolveIntentRecoveryExecutionResult] = None


@dataclass(frozen=True)
class VaultSyncSession:
    transport: SyncCommitTransport
    uploader: Optional[SyncBlobUploader] = None
    downloader: Optional[SyncBlobDownloader] = None

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


def execute_commit_preflight(
    transport: SyncCommitTransport,
    submission: CommitSubmissionBundle,
    *,
    snapshot_table: CommitSnapshotTable,
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
