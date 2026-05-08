from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from vault_core import (
    AckExecutionResult,
    BlobCheckResult,
    BlobDownloadCapability,
    BlobDownloadInitExecutionResult,
    BlobDownloadInitResponsePayload,
    BlobDownloadSessionResult,
    BlobUploadCapability,
    BlobUploadInitRequestPayload,
    BlobUploadInitResponsePayload,
    BlobUploadPlan,
    BlobUploadPlanEntry,
    CommitIntentJournalRecord,
    CommitNetworkPlan,
    CommitPreflightResult,
    CommitRecoverySessionResult,
    CommitSubmissionExecutionResult,
    CommitSnapshotEntry,
    CommitSnapshotTable,
    CommitSubmissionBundle,
    FileMapDocument,
    PullSyncSessionResult,
    ResolveCommitIntentExecutionResult,
    ResolveCommitIntentResponsePayload,
    PullReconcileSessionResult,
    SubmittedResolveIntentRecoveryExecutionResult,
    CreateCommitBlobRef,
    CreateCommitExecutionResult,
    CreateCommitRequestPayload,
    CreateCommitResponsePayload,
    ManifestFileEntry,
    ManifestRecord,
    SyncHttpJsonResponse,
    TombstoneRecord,
    VaultHeadResponsePayload,
    VaultStateRecord,
    VaultSyncSession,
    bootstrap_database,
    compute_manifest_summary_hash,
    load_commit_intent_journal,
    load_tombstone_ledger,
    load_vault_state,
    open_database,
    upsert_commit_intent_journal,
    upsert_vault_state,
    execute_ack_revisions,
    execute_ack_pending_revisions,
    execute_blob_download_init,
    execute_blob_download_session,
    execute_blob_downloads,
    execute_blob_uploads,
    execute_commit_preflight,
    execute_commit_recovery_session,
    execute_create_commit,
    execute_commit_submission,
    execute_pull_reconcile_session,
    execute_pull_sync_session,
    execute_resolve_commit_intent,
    execute_submitted_recovery_via_resolve_intent,
)


class FakeSyncCommitTransport:
    def __init__(
        self,
        *,
        blob_check: SyncHttpJsonResponse,
        blob_upload_init: SyncHttpJsonResponse | None = None,
        create_commit: SyncHttpJsonResponse | None = None,
        resolve_commit_intent: SyncHttpJsonResponse | None = None,
        manifest: SyncHttpJsonResponse | None = None,
        head: SyncHttpJsonResponse | None = None,
        ack: SyncHttpJsonResponse | None = None,
        blob_download_init: SyncHttpJsonResponse | None = None,
    ) -> None:
        self.blob_check_response = blob_check
        self.blob_upload_init_response = blob_upload_init
        self.create_commit_response = create_commit
        self.resolve_commit_intent_response = resolve_commit_intent
        self.manifest_response = manifest
        self.head_response = head
        self.ack_response = ack
        self.blob_download_init_response = blob_download_init
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def post_blob_check(self, vault_id: str, payload: dict[str, object]) -> SyncHttpJsonResponse:
        self.calls.append(("blob_check", vault_id, payload))
        return self.blob_check_response

    def post_blob_upload_init(self, vault_id: str, payload: dict[str, object]) -> SyncHttpJsonResponse:
        self.calls.append(("blob_upload_init", vault_id, payload))
        if self.blob_upload_init_response is None:
            raise AssertionError("blob upload init response was not configured")
        return self.blob_upload_init_response

    def post_create_commit(self, vault_id: str, payload: dict[str, object]) -> SyncHttpJsonResponse:
        self.calls.append(("create_commit", vault_id, payload))
        if self.create_commit_response is None:
            raise AssertionError("create commit response was not configured")
        return self.create_commit_response

    def post_resolve_commit_intent(self, vault_id: str, payload: dict[str, object]) -> SyncHttpJsonResponse:
        self.calls.append(("resolve_commit_intent", vault_id, payload))
        if self.resolve_commit_intent_response is None:
            raise AssertionError("resolve commit intent response was not configured")
        return self.resolve_commit_intent_response

    def get_manifest(self, vault_id: str, revision: int) -> SyncHttpJsonResponse:
        self.calls.append(("get_manifest", vault_id, {"revision": revision}))
        if self.manifest_response is None:
            raise AssertionError("manifest response was not configured")
        return self.manifest_response

    def get_vault_head(self, vault_id: str) -> SyncHttpJsonResponse:
        self.calls.append(("get_vault_head", vault_id, {}))
        if self.head_response is None:
            raise AssertionError("head response was not configured")
        return self.head_response

    def post_ack(self, vault_id: str, payload: dict[str, object]) -> SyncHttpJsonResponse:
        self.calls.append(("post_ack", vault_id, payload))
        if self.ack_response is None:
            raise AssertionError("ack response was not configured")
        return self.ack_response

    def post_blob_download_init(self, vault_id: str, payload: dict[str, object]) -> SyncHttpJsonResponse:
        self.calls.append(("blob_download_init", vault_id, payload))
        if self.blob_download_init_response is None:
            raise AssertionError("blob download init response was not configured")
        return self.blob_download_init_response


class FakeSyncBlobUploader:
    def __init__(self, *, error_blob_id: str | None = None) -> None:
        self.error_blob_id = error_blob_id
        self.calls: list[tuple[BlobUploadPlanEntry, BlobUploadCapability]] = []

    def upload_blob(
        self,
        upload: BlobUploadPlanEntry,
        capability: BlobUploadCapability,
    ) -> None:
        self.calls.append((upload, capability))
        if upload.blob_id == self.error_blob_id:
            raise RuntimeError(f"upload failed for {upload.blob_id}")


class FakeSyncBlobDownloader:
    def __init__(self) -> None:
        self.calls: list[BlobDownloadCapability] = []

    def download_blob(self, capability: BlobDownloadCapability) -> bytes:
        self.calls.append(capability)
        return f"downloaded:{capability.blob_id}".encode("utf-8")


def _build_submission() -> CommitSubmissionBundle:
    manifest = ManifestRecord(
        vault_id="vault_pkb_001",
        revision=0,
        base_revision=7,
        created_by_device="desktop-shanghai",
        created_at=1770000019200,
        summary_hash="pending",
        files=[
            ManifestFileEntry(
                file_id="file_a",
                path="Notes/A.md",
                type="note",
                content_hash="sha256:a",
                blob_id="blob_a",
                size=16,
                mtime=1770000019190,
            ),
            ManifestFileEntry(
                file_id="file_b",
                path="Notes/B.md",
                type="note",
                content_hash="sha256:b",
                blob_id="blob_b",
                size=8,
                mtime=1770000019191,
            ),
        ],
        tombstones=[],
    )
    return CommitSubmissionBundle(
        manifest=manifest,
        intent_manifest_hash="sha256:intent",
        journal=CommitIntentJournalRecord(
            vault_id="vault_pkb_001",
            commit_intent_id="intent_1",
            intent_manifest_hash="sha256:intent",
            base_revision=7,
            created_by_device="desktop-shanghai",
            status="submitted",
            intent_delete_seq_upper_bound=5,
            created_at=1770000019200,
            updated_at=1770000019201,
        ),
        state=VaultStateRecord(
            vault_id="vault_pkb_001",
            last_applied_revision=7,
            remote_head_revision=7,
            acked_revision=7,
            pending_ack_to_server=[],
            commit_in_progress=True,
            last_manifest_summary="sha256:head7",
            last_manifest_summary_status="valid",
            local_delete_sequence=5,
        ),
    )


def _build_snapshot_table() -> CommitSnapshotTable:
    return CommitSnapshotTable(
        vault_id="vault_pkb_001",
        base_revision=7,
        created_at=1770000019200,
        entries=[
            CommitSnapshotEntry(
                file_id="file_a",
                path="Notes/A.md",
                type="note",
                content_hash="sha256:a",
                blob_id="blob_a",
                plaintext_size=16,
                encrypted_size=32,
                mtime=1770000019190,
                mime_type=None,
                snapshot_path=Path("C:/tmp/file_a.snapshot.plain"),
                blob_staging_path=Path("C:/tmp/blob_a.blob.staging"),
            ),
            CommitSnapshotEntry(
                file_id="file_b",
                path="Notes/B.md",
                type="note",
                content_hash="sha256:b",
                blob_id="blob_b",
                plaintext_size=8,
                encrypted_size=24,
                mtime=1770000019191,
                mime_type=None,
                snapshot_path=Path("C:/tmp/file_b.snapshot.plain"),
                blob_staging_path=Path("C:/tmp/blob_b.blob.staging"),
            ),
        ],
    )


class SyncClientTests(unittest.TestCase):
    def test_execute_commit_preflight_skips_upload_init_when_no_blobs_are_missing(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "existing_blob_ids": ["blob_a", "blob_b"],
                    "missing_blob_ids": [],
                },
            )
        )

        result = execute_commit_preflight(
            transport,
            _build_submission(),
            snapshot_table=_build_snapshot_table(),
        )

        self.assertEqual(
            result,
            CommitPreflightResult(
                network_plan=CommitNetworkPlan(
                    request=CreateCommitRequestPayload(
                        commit_intent_id="intent_1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        intent_manifest_hash="sha256:intent",
                        intent_delete_seq_upper_bound=5,
                        manifest=_build_submission().manifest,
                        blob_refs=[
                            CreateCommitBlobRef(blob_id="blob_a", file_id="file_a"),
                            CreateCommitBlobRef(blob_id="blob_b", file_id="file_b"),
                        ],
                    ),
                    blob_check=BlobCheckResult(
                        requested_blob_ids=["blob_a", "blob_b"],
                        existing_blob_ids=["blob_a", "blob_b"],
                        missing_blob_ids=[],
                    ),
                    blob_uploads=BlobUploadPlan(vault_id="vault_pkb_001", entries=[]),
                ),
                upload_init_request=None,
                upload_init_response=None,
            ),
        )
        self.assertEqual([call[0] for call in transport.calls], ["blob_check"])

    def test_execute_commit_preflight_requests_upload_capabilities_for_missing_blobs(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "existing_blob_ids": ["blob_b"],
                    "missing_blob_ids": ["blob_a"],
                },
            ),
            blob_upload_init=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "uploads": [
                        {
                            "blob_id": "blob_a",
                            "upload_url": "https://example.com/upload/blob_a",
                            "expires_at": "2026-05-08T12:00:00Z",
                        }
                    ]
                },
            ),
        )

        result = execute_commit_preflight(
            transport,
            _build_submission(),
            snapshot_table=_build_snapshot_table(),
        )

        self.assertEqual(
            result.upload_init_request,
            BlobUploadInitRequestPayload(
                blobs=[
                    result.upload_init_request.blobs[0].__class__(
                        blob_id="blob_a",
                        encrypted_size=32,
                        content_hash="sha256:a",
                    ),
                ]
            ),
        )
        self.assertEqual(
            result.upload_init_response,
            BlobUploadInitResponsePayload(
                uploads=[
                    BlobUploadCapability(
                        blob_id="blob_a",
                        upload_url="https://example.com/upload/blob_a",
                        expires_at="2026-05-08T12:00:00Z",
                    )
                ]
            ),
        )
        self.assertEqual([call[0] for call in transport.calls], ["blob_check", "blob_upload_init"])
        self.assertEqual(transport.calls[1][2]["blobs"][0]["blob_id"], "blob_a")

    def test_execute_commit_preflight_allows_delete_only_commit_without_blob_preflight(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "existing_blob_ids": [],
                    "missing_blob_ids": [],
                },
            )
        )
        submission = CommitSubmissionBundle(
            manifest=ManifestRecord(
                vault_id="vault_pkb_001",
                revision=0,
                base_revision=7,
                created_by_device="desktop-shanghai",
                created_at=1770000019200,
                summary_hash="pending",
                files=[],
                tombstones=[
                    TombstoneRecord(
                        file_id="file_a",
                        deleted_revision=None,
                        deleted_at=1770000019199,
                        local_delete_seq=5,
                        last_known_path="Notes/A.md",
                        deleted_by_device="desktop-shanghai",
                    )
                ],
            ),
            intent_manifest_hash="sha256:intent-delete",
            journal=CommitIntentJournalRecord(
                vault_id="vault_pkb_001",
                commit_intent_id="intent_delete_1",
                intent_manifest_hash="sha256:intent-delete",
                base_revision=7,
                created_by_device="desktop-shanghai",
                status="submitted",
                intent_delete_seq_upper_bound=5,
                created_at=1770000019200,
                updated_at=1770000019201,
            ),
            state=VaultStateRecord(
                vault_id="vault_pkb_001",
                last_applied_revision=7,
                remote_head_revision=7,
                acked_revision=7,
                pending_ack_to_server=[],
                commit_in_progress=True,
                last_manifest_summary="sha256:head7",
                last_manifest_summary_status="valid",
                local_delete_sequence=5,
            ),
        )

        result = execute_commit_preflight(
            transport,
            submission,
            snapshot_table=CommitSnapshotTable(
                vault_id="vault_pkb_001",
                base_revision=7,
                created_at=1770000019200,
                entries=[],
            ),
        )

        self.assertEqual([call[0] for call in transport.calls], [])
        self.assertEqual(result.network_plan.blob_check.requested_blob_ids, [])
        self.assertEqual(result.network_plan.blob_uploads.entries, [])
        self.assertIsNone(result.upload_init_request)
        self.assertIsNone(result.upload_init_response)

    def test_execute_blob_uploads_rejects_duplicate_capabilities(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate blob_ids"):
            execute_blob_uploads(
                FakeSyncBlobUploader(),
                BlobUploadPlan(
                    vault_id="vault_pkb_001",
                    entries=[
                        BlobUploadPlanEntry(
                            blob_id="blob_a",
                            content_hash="sha256:a",
                            encrypted_size=32,
                            blob_staging_path=Path("C:/tmp/blob_a.blob.staging"),
                            file_ids=["file_a"],
                        )
                    ],
                ),
                BlobUploadInitResponsePayload(
                    uploads=[
                        BlobUploadCapability(
                            blob_id="blob_a",
                            upload_url="https://example.com/upload/blob_a_1",
                            expires_at="2026-05-08T12:00:00Z",
                        ),
                        BlobUploadCapability(
                            blob_id="blob_a",
                            upload_url="https://example.com/upload/blob_a_2",
                            expires_at="2026-05-08T12:01:00Z",
                        ),
                    ]
                ),
            )

    def test_execute_blob_download_init_and_downloads(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
            blob_download_init=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "downloads": [
                        {
                            "blob_id": "blob_b",
                            "download_url": "https://example.com/download/blob_b",
                            "encrypted_size": 24,
                            "expires_at": "2026-05-08T12:00:00Z",
                        },
                        {
                            "blob_id": "blob_a",
                            "download_url": "https://example.com/download/blob_a",
                            "encrypted_size": 32,
                            "expires_at": "2026-05-08T12:00:00Z",
                            "headers": {"x-token": "token_a"},
                        },
                    ]
                },
            ),
        )
        downloader = FakeSyncBlobDownloader()

        init = execute_blob_download_init(
            transport,
            "vault_pkb_001",
            ["blob_b", "blob_a"],
        )
        downloaded = execute_blob_downloads(downloader, init.response)

        self.assertEqual(
            init,
            BlobDownloadInitExecutionResult(
                request=init.request,
                response=BlobDownloadInitResponsePayload(
                    downloads=[
                        BlobDownloadCapability(
                            blob_id="blob_b",
                            download_url="https://example.com/download/blob_b",
                            encrypted_size=24,
                            expires_at="2026-05-08T12:00:00Z",
                        ),
                        BlobDownloadCapability(
                            blob_id="blob_a",
                            download_url="https://example.com/download/blob_a",
                            encrypted_size=32,
                            expires_at="2026-05-08T12:00:00Z",
                            headers={"x-token": "token_a"},
                        ),
                    ]
                ),
            ),
        )
        self.assertEqual(
            downloaded,
            {
                "blob_a": b"downloaded:blob_a",
                "blob_b": b"downloaded:blob_b",
            },
        )
        self.assertEqual([call[0] for call in transport.calls], ["blob_download_init"])
        self.assertEqual([capability.blob_id for capability in downloader.calls], ["blob_a", "blob_b"])

    def test_execute_blob_download_session_composes_init_and_download(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
            blob_download_init=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "downloads": [
                        {
                            "blob_id": "blob_a",
                            "download_url": "https://example.com/download/blob_a",
                            "encrypted_size": 32,
                            "expires_at": "2026-05-08T12:00:00Z",
                        }
                    ]
                },
            ),
        )
        downloader = FakeSyncBlobDownloader()

        result = execute_blob_download_session(
            transport,
            downloader,
            vault_id="vault_pkb_001",
            blob_ids=["blob_a"],
        )

        self.assertEqual(
            result,
            BlobDownloadSessionResult(
                init=BlobDownloadInitExecutionResult(
                    request=result.init.request,
                    response=BlobDownloadInitResponsePayload(
                        downloads=[
                            BlobDownloadCapability(
                                blob_id="blob_a",
                                download_url="https://example.com/download/blob_a",
                                encrypted_size=32,
                                expires_at="2026-05-08T12:00:00Z",
                            )
                        ]
                    ),
                ),
                downloaded_blobs={"blob_a": b"downloaded:blob_a"},
            ),
        )
        self.assertEqual([call[0] for call in transport.calls], ["blob_download_init"])

    def test_execute_create_commit_parses_success_and_conflict(self) -> None:
        request = CreateCommitRequestPayload(
            commit_intent_id="intent_1",
            base_revision=7,
            created_by_device="desktop-shanghai",
            intent_manifest_hash="sha256:intent",
            intent_delete_seq_upper_bound=5,
            manifest=_build_submission().manifest,
            blob_refs=[
                CreateCommitBlobRef(blob_id="blob_a", file_id="file_a"),
                CreateCommitBlobRef(blob_id="blob_b", file_id="file_b"),
            ],
        )
        success_transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
            create_commit=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "vault_id": "vault_pkb_001",
                    "new_revision": 8,
                    "head_manifest_summary": "sha256:head8",
                    "acked_revision_for_device": 8,
                },
            ),
        )
        success = execute_create_commit(success_transport, request)
        self.assertEqual(
            success,
            CreateCommitExecutionResult(
                status="committed",
                request=request,
                response=CreateCommitResponsePayload(
                    vault_id="vault_pkb_001",
                    new_revision=8,
                    head_manifest_summary="sha256:head8",
                    acked_revision_for_device=8,
                ),
            ),
        )

        conflict_transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
            create_commit=SyncHttpJsonResponse(
                status_code=409,
                payload={
                    "code": "base_revision_conflict",
                    "current_head_revision": 9,
                    "current_manifest_summary": "sha256:head9",
                },
            ),
        )
        conflict = execute_create_commit(conflict_transport, request)
        self.assertEqual(conflict.status, "conflict")
        self.assertEqual(conflict.conflict.code, "base_revision_conflict")

    def test_execute_commit_submission_uploads_missing_blobs_then_commits(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "existing_blob_ids": ["blob_b"],
                    "missing_blob_ids": ["blob_a"],
                },
            ),
            blob_upload_init=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "uploads": [
                        {
                            "blob_id": "blob_a",
                            "upload_url": "https://example.com/upload/blob_a",
                            "expires_at": "2026-05-08T12:00:00Z",
                            "headers": {"x-upload-token": "token_1"},
                        }
                    ]
                },
            ),
            create_commit=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "vault_id": "vault_pkb_001",
                    "new_revision": 8,
                    "head_manifest_summary": "sha256:head8",
                    "acked_revision_for_device": 8,
                },
            ),
        )
        uploader = FakeSyncBlobUploader()

        result = execute_commit_submission(
            transport,
            uploader,
            _build_submission(),
            snapshot_table=_build_snapshot_table(),
        )

        self.assertEqual(
            result,
            CommitSubmissionExecutionResult(
                preflight=result.preflight,
                uploaded_blob_ids=["blob_a"],
                commit=CreateCommitExecutionResult(
                    status="committed",
                    request=result.preflight.network_plan.request,
                    response=CreateCommitResponsePayload(
                        vault_id="vault_pkb_001",
                        new_revision=8,
                        head_manifest_summary="sha256:head8",
                        acked_revision_for_device=8,
                    ),
                ),
            ),
        )
        self.assertEqual([call[0] for call in transport.calls], ["blob_check", "blob_upload_init", "create_commit"])
        self.assertEqual(len(uploader.calls), 1)
        self.assertEqual(uploader.calls[0][0].blob_id, "blob_a")
        self.assertEqual(
            uploader.calls[0][1],
            BlobUploadCapability(
                blob_id="blob_a",
                upload_url="https://example.com/upload/blob_a",
                expires_at="2026-05-08T12:00:00Z",
                headers={"x-upload-token": "token_1"},
            ),
        )

    def test_execute_commit_submission_stops_before_commit_when_upload_fails(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "existing_blob_ids": [],
                    "missing_blob_ids": ["blob_a", "blob_b"],
                },
            ),
            blob_upload_init=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "uploads": [
                        {
                            "blob_id": "blob_a",
                            "upload_url": "https://example.com/upload/blob_a",
                            "expires_at": "2026-05-08T12:00:00Z",
                        },
                        {
                            "blob_id": "blob_b",
                            "upload_url": "https://example.com/upload/blob_b",
                            "expires_at": "2026-05-08T12:00:00Z",
                        },
                    ]
                },
            ),
            create_commit=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "vault_id": "vault_pkb_001",
                    "new_revision": 8,
                    "head_manifest_summary": "sha256:head8",
                    "acked_revision_for_device": 8,
                },
            ),
        )
        uploader = FakeSyncBlobUploader(error_blob_id="blob_b")

        with self.assertRaisesRegex(RuntimeError, "upload failed for blob_b"):
            execute_commit_submission(
                transport,
                uploader,
                _build_submission(),
                snapshot_table=_build_snapshot_table(),
            )

        self.assertEqual([call[0] for call in transport.calls], ["blob_check", "blob_upload_init"])
        self.assertEqual([call[0].blob_id for call in uploader.calls], ["blob_a", "blob_b"])

    def test_execute_resolve_commit_intent_fetches_matched_manifest_when_found(self) -> None:
        journal = _build_submission().journal
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
            resolve_commit_intent=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "status": "found",
                    "matched_revision": 8,
                    "observed_head_revision": 10,
                    "head_manifest_summary": "sha256:head10",
                },
            ),
            manifest=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "vault_id": "vault_pkb_001",
                    "revision": 8,
                    "base_revision": 7,
                    "created_by_device": "desktop-shanghai",
                    "created_at": 1770000020000,
                    "summary_hash": "sha256:head8",
                    "files": [],
                    "tombstones": [],
                },
            ),
        )

        result = execute_resolve_commit_intent(transport, journal)

        self.assertEqual(
            result,
            ResolveCommitIntentExecutionResult(
                request=result.request,
                response=ResolveCommitIntentResponsePayload(
                    status="found",
                    matched_revision=8,
                    observed_head_revision=10,
                    head_manifest_summary="sha256:head10",
                ),
                matched_manifest=ManifestRecord(
                    vault_id="vault_pkb_001",
                    revision=8,
                    base_revision=7,
                    created_by_device="desktop-shanghai",
                    created_at=1770000020000,
                    summary_hash="sha256:head8",
                    files=[],
                    tombstones=[],
                ),
            ),
        )
        self.assertEqual([call[0] for call in transport.calls], ["resolve_commit_intent", "get_manifest"])
        self.assertEqual(transport.calls[1][2]["revision"], 8)

    def test_execute_resolve_commit_intent_allows_manifest_404_fallback(self) -> None:
        journal = _build_submission().journal
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
            resolve_commit_intent=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "status": "found",
                    "matched_revision": 8,
                    "observed_head_revision": 8,
                    "head_manifest_summary": "sha256:head8",
                },
            ),
            manifest=SyncHttpJsonResponse(status_code=404, payload={"code": "not_found"}),
        )

        result = execute_resolve_commit_intent(transport, journal)

        self.assertTrue(result.matched_manifest_missing)
        self.assertIsNone(result.matched_manifest)
        self.assertEqual([call[0] for call in transport.calls], ["resolve_commit_intent", "get_manifest"])

    def test_execute_resolve_commit_intent_skips_manifest_fetch_for_non_found_statuses(self) -> None:
        journal = _build_submission().journal
        for status in ("not_found", "mismatched"):
            transport = FakeSyncCommitTransport(
                blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
                resolve_commit_intent=SyncHttpJsonResponse(
                    status_code=200,
                    payload={
                        "status": status,
                        "observed_head_revision": 10,
                        "head_manifest_summary": "sha256:head10",
                    },
                ),
            )

            result = execute_resolve_commit_intent(transport, journal)

            self.assertEqual(result.response.status, status)
            self.assertIsNone(result.matched_manifest)
            self.assertFalse(result.matched_manifest_missing)
            self.assertEqual([call[0] for call in transport.calls], ["resolve_commit_intent"])

    def test_execute_submitted_recovery_via_resolve_intent_recovers_found_commit(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
            resolve_commit_intent=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "status": "found",
                    "matched_revision": 8,
                    "observed_head_revision": 10,
                    "head_manifest_summary": "sha256:head10",
                },
            ),
            manifest=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "vault_id": "vault_pkb_001",
                    "revision": 8,
                    "base_revision": 7,
                    "created_by_device": "desktop-shanghai",
                    "created_at": 1770000020000,
                    "summary_hash": "sha256:head8",
                    "files": [],
                    "tombstones": [],
                },
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "tombstone-ledger.jsonl"
            db_path = Path(tmpdir) / "state.sqlite3"
            tombstones = [
                TombstoneRecord(
                    file_id="file_hit",
                    deleted_revision=None,
                    deleted_at=1770000019990,
                    local_delete_seq=2,
                    last_known_path="Notes/Hit.md",
                )
            ]

            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                upsert_vault_state(
                    connection,
                    VaultStateRecord(
                        vault_id="vault_pkb_001",
                        last_applied_revision=7,
                        remote_head_revision=7,
                        acked_revision=7,
                        pending_ack_to_server=[4],
                        commit_in_progress=True,
                        last_manifest_summary="sha256:head7",
                        last_manifest_summary_status="valid",
                        local_delete_sequence=2,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_1",
                        intent_manifest_hash="sha256:intent_1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=2,
                        created_at=1770000019995,
                        updated_at=1770000019995,
                    ),
                )

                result = execute_submitted_recovery_via_resolve_intent(
                    transport,
                    connection,
                    ledger_path=ledger_path,
                    vault_id="vault_pkb_001",
                    local_tombstones=tombstones,
                    normalized_at=1770000020001,
                )

                self.assertIsInstance(result, SubmittedResolveIntentRecoveryExecutionResult)
                self.assertEqual(result.resolved.response.status, "found")
                self.assertEqual(result.recovery.state.last_applied_revision, 8)
                self.assertEqual(result.recovery.state.remote_head_revision, 10)
                self.assertEqual(result.recovery.state.acked_revision, 8)
                self.assertEqual(
                    result.recovery.state.last_manifest_summary,
                    compute_manifest_summary_hash(result.resolved.matched_manifest),
                )
                self.assertEqual(
                    [(item.file_id, item.deleted_revision) for item in load_tombstone_ledger(ledger_path)],
                    [("file_hit", 8)],
                )
                self.assertIsNone(load_commit_intent_journal(connection, "vault_pkb_001"))

    def test_execute_submitted_recovery_via_resolve_intent_recovers_not_found_commit(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
            resolve_commit_intent=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "status": "not_found",
                    "observed_head_revision": 10,
                    "head_manifest_summary": "sha256:head10",
                },
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "tombstone-ledger.jsonl"
            db_path = Path(tmpdir) / "state.sqlite3"
            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                upsert_vault_state(
                    connection,
                    VaultStateRecord(
                        vault_id="vault_pkb_001",
                        last_applied_revision=7,
                        remote_head_revision=7,
                        acked_revision=7,
                        pending_ack_to_server=[4],
                        commit_in_progress=True,
                        last_manifest_summary="sha256:head7",
                        last_manifest_summary_status="valid",
                        local_delete_sequence=0,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_1",
                        intent_manifest_hash="sha256:intent_1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="acknowledged",
                        intent_delete_seq_upper_bound=None,
                        created_at=1770000019995,
                        updated_at=1770000019995,
                    ),
                )

                result = execute_submitted_recovery_via_resolve_intent(
                    transport,
                    connection,
                    ledger_path=ledger_path,
                    vault_id="vault_pkb_001",
                    local_tombstones=[],
                    normalized_at=1770000020001,
                )

                state = load_vault_state(connection, "vault_pkb_001")
                self.assertEqual(result.resolved.response.status, "not_found")
                self.assertFalse(result.recovery.requires_full_pull)
                self.assertEqual(state.last_applied_revision, 7)
                self.assertFalse(state.commit_in_progress)
                self.assertIsNone(load_commit_intent_journal(connection, "vault_pkb_001"))

    def test_execute_submitted_recovery_via_resolve_intent_rejects_mismatched_status(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
            resolve_commit_intent=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "status": "mismatched",
                    "observed_head_revision": 10,
                    "head_manifest_summary": "sha256:head10",
                },
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "tombstone-ledger.jsonl"
            db_path = Path(tmpdir) / "state.sqlite3"
            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                upsert_vault_state(
                    connection,
                    VaultStateRecord(
                        vault_id="vault_pkb_001",
                        last_applied_revision=7,
                        remote_head_revision=7,
                        acked_revision=7,
                        pending_ack_to_server=[],
                        commit_in_progress=True,
                        last_manifest_summary="sha256:head7",
                        last_manifest_summary_status="valid",
                        local_delete_sequence=0,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_1",
                        intent_manifest_hash="sha256:intent_1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=None,
                        created_at=1770000019995,
                        updated_at=1770000019995,
                    ),
                )

                with self.assertRaisesRegex(ValueError, "mismatched status"):
                    execute_submitted_recovery_via_resolve_intent(
                        transport,
                        connection,
                        ledger_path=ledger_path,
                        vault_id="vault_pkb_001",
                        local_tombstones=[],
                        normalized_at=1770000020001,
                    )

                self.assertIsNotNone(load_commit_intent_journal(connection, "vault_pkb_001"))
                self.assertTrue(load_vault_state(connection, "vault_pkb_001").commit_in_progress)

    def test_execute_ack_revisions_posts_protocol_payload(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
            ack=SyncHttpJsonResponse(
                status_code=200,
                payload={"max_acked_revision": 10},
            ),
        )

        result = execute_ack_revisions(
            transport,
            "vault_pkb_001",
            [8, 10],
        )

        self.assertEqual(
            result,
            AckExecutionResult(
                request=result.request,
                response=result.response,
            ),
        )
        self.assertEqual(result.response.max_acked_revision, 10)
        self.assertEqual([call[0] for call in transport.calls], ["post_ack"])
        self.assertEqual(transport.calls[0][2]["revisions"], [8, 10])

    def test_execute_ack_pending_revisions_skips_when_queue_is_empty(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
        )
        state = VaultStateRecord(
            vault_id="vault_pkb_001",
            last_applied_revision=7,
            remote_head_revision=7,
            acked_revision=7,
            pending_ack_to_server=[],
            commit_in_progress=False,
            last_manifest_summary="sha256:head7",
            last_manifest_summary_status="valid",
            local_delete_sequence=0,
        )

        self.assertIsNone(execute_ack_pending_revisions(transport, state))
        self.assertEqual(transport.calls, [])

    def test_execute_pull_reconcile_session_fetches_manifest_when_head_advances(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
            head=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "vault_id": "vault_pkb_001",
                    "head_revision": 8,
                    "manifest_summary": "sha256:head8",
                },
            ),
            manifest=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "vault_id": "vault_pkb_001",
                    "revision": 8,
                    "base_revision": 7,
                    "created_by_device": "desktop-shanghai",
                    "created_at": 1770000021000,
                    "summary_hash": "sha256:head8",
                    "files": [],
                    "tombstones": [],
                },
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            filemap_path = root / "filemap.json"
            ledger_path = root / "tombstone-ledger.jsonl"
            db_path = root / "state.sqlite3"
            current_document = FileMapDocument(
                vault_id="vault_pkb_001",
                updated_at=1770000020000,
                files=[],
            )
            current_state = VaultStateRecord(
                vault_id="vault_pkb_001",
                last_applied_revision=7,
                remote_head_revision=7,
                acked_revision=7,
                pending_ack_to_server=[],
                commit_in_progress=False,
                last_manifest_summary="sha256:head7",
                last_manifest_summary_status="valid",
                local_delete_sequence=0,
            )

            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                upsert_vault_state(connection, current_state)

                result = execute_pull_reconcile_session(
                    transport,
                    connection,
                    filemap_path=filemap_path,
                    ledger_path=ledger_path,
                    current_document=current_document,
                    current_state=current_state,
                    local_tombstones=[],
                    rewritten_at=1770000021001,
                )

                self.assertIsInstance(result, PullReconcileSessionResult)
                self.assertEqual(
                    result.head,
                    VaultHeadResponsePayload(
                        vault_id="vault_pkb_001",
                        head_revision=8,
                        manifest_summary="sha256:head8",
                    ),
                )
                self.assertIsNotNone(result.manifest)
                self.assertEqual(result.reconcile.state.last_applied_revision, 8)
                self.assertEqual(result.reconcile.state.pending_ack_to_server, [8])
                self.assertEqual(result.reconcile.applied.required_blob_ids, [])
                self.assertEqual([call[0] for call in transport.calls], ["get_vault_head", "get_manifest"])

    def test_execute_pull_reconcile_session_skips_manifest_when_head_not_advanced(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
            head=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "vault_id": "vault_pkb_001",
                    "head_revision": 7,
                    "manifest_summary": "sha256:head7",
                },
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            filemap_path = root / "filemap.json"
            ledger_path = root / "tombstone-ledger.jsonl"
            db_path = root / "state.sqlite3"
            current_document = FileMapDocument(
                vault_id="vault_pkb_001",
                updated_at=1770000020000,
                files=[],
            )
            current_state = VaultStateRecord(
                vault_id="vault_pkb_001",
                last_applied_revision=7,
                remote_head_revision=7,
                acked_revision=7,
                pending_ack_to_server=[],
                commit_in_progress=False,
                last_manifest_summary="sha256:head7",
                last_manifest_summary_status="valid",
                local_delete_sequence=0,
            )

            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                upsert_vault_state(connection, current_state)

                result = execute_pull_reconcile_session(
                    transport,
                    connection,
                    filemap_path=filemap_path,
                    ledger_path=ledger_path,
                    current_document=current_document,
                    current_state=current_state,
                    local_tombstones=[],
                    rewritten_at=1770000021001,
                )

                self.assertIsNone(result.manifest)
                self.assertIsNone(result.reconcile.applied)
                self.assertEqual(result.reconcile.state, current_state)
                self.assertEqual([call[0] for call in transport.calls], ["get_vault_head"])

    def test_execute_pull_sync_session_acknowledges_reconciled_revisions(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
            head=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "vault_id": "vault_pkb_001",
                    "head_revision": 8,
                    "manifest_summary": "sha256:head8",
                },
            ),
            manifest=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "vault_id": "vault_pkb_001",
                    "revision": 8,
                    "base_revision": 7,
                    "created_by_device": "desktop-shanghai",
                    "created_at": 1770000021000,
                    "summary_hash": "sha256:head8",
                    "files": [],
                    "tombstones": [],
                },
            ),
            ack=SyncHttpJsonResponse(
                status_code=200,
                payload={"max_acked_revision": 8},
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            filemap_path = root / "filemap.json"
            ledger_path = root / "tombstone-ledger.jsonl"
            db_path = root / "state.sqlite3"
            current_document = FileMapDocument(
                vault_id="vault_pkb_001",
                updated_at=1770000020000,
                files=[],
            )
            current_state = VaultStateRecord(
                vault_id="vault_pkb_001",
                last_applied_revision=7,
                remote_head_revision=7,
                acked_revision=7,
                pending_ack_to_server=[],
                commit_in_progress=False,
                last_manifest_summary="sha256:head7",
                last_manifest_summary_status="valid",
                local_delete_sequence=0,
            )

            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                upsert_vault_state(connection, current_state)

                result = execute_pull_sync_session(
                    transport,
                    connection,
                    filemap_path=filemap_path,
                    ledger_path=ledger_path,
                    current_document=current_document,
                    current_state=current_state,
                    local_tombstones=[],
                    rewritten_at=1770000021001,
                )

                self.assertIsInstance(result, PullSyncSessionResult)
                self.assertIsNotNone(result.ack)
                self.assertEqual(result.ack.response.max_acked_revision, 8)
                self.assertEqual(
                    [call[0] for call in transport.calls],
                    ["get_vault_head", "get_manifest", "post_ack"],
                )

    def test_vault_sync_session_submit_commit_allows_commit_without_uploader_when_no_uploads_needed(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "existing_blob_ids": ["blob_a", "blob_b"],
                    "missing_blob_ids": [],
                },
            ),
            create_commit=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "vault_id": "vault_pkb_001",
                    "new_revision": 8,
                    "head_manifest_summary": "sha256:head8",
                    "acked_revision_for_device": 8,
                },
            ),
        )
        session = VaultSyncSession(transport=transport)

        result = session.submit_commit(
            _build_submission(),
            snapshot_table=_build_snapshot_table(),
        )

        self.assertEqual(result.commit.status, "committed")
        self.assertEqual(result.uploaded_blob_ids, [])
        self.assertEqual(
            [call[0] for call in transport.calls],
            ["blob_check", "create_commit"],
        )

    def test_vault_sync_session_download_blobs_requires_downloader(self) -> None:
        session = VaultSyncSession(
            transport=FakeSyncCommitTransport(
                blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
            )
        )

        with self.assertRaisesRegex(ValueError, "blob downloader is required"):
            session.download_blobs(vault_id="vault_pkb_001", blob_ids=["blob_a"])

    def test_execute_commit_recovery_session_uses_local_recovery_when_remote_confirmation_is_not_needed(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "state.sqlite3"
            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                upsert_vault_state(
                    connection,
                    VaultStateRecord(
                        vault_id="vault_pkb_001",
                        last_applied_revision=7,
                        remote_head_revision=7,
                        acked_revision=7,
                        pending_ack_to_server=[],
                        commit_in_progress=False,
                        last_manifest_summary="sha256:head7",
                        last_manifest_summary_status="valid",
                        local_delete_sequence=0,
                    ),
                )

                result = execute_commit_recovery_session(
                    transport,
                    connection,
                    vault_root=root,
                    vault_id="vault_pkb_001",
                    normalized_at=1770000021001,
                )

                self.assertIsInstance(result, CommitRecoverySessionResult)
                self.assertEqual(result.mode, "idle")
                self.assertFalse(result.requires_full_pull)
                self.assertIsNotNone(result.local)
                self.assertIsNone(result.submitted)
                self.assertEqual(transport.calls, [])

    def test_execute_commit_recovery_session_uses_resolve_intent_for_submitted_confirmation(self) -> None:
        transport = FakeSyncCommitTransport(
            blob_check=SyncHttpJsonResponse(status_code=200, payload={"existing_blob_ids": [], "missing_blob_ids": []}),
            resolve_commit_intent=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "status": "found",
                    "matched_revision": 8,
                    "observed_head_revision": 8,
                    "head_manifest_summary": "sha256:head8",
                },
            ),
            manifest=SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "vault_id": "vault_pkb_001",
                    "revision": 8,
                    "base_revision": 7,
                    "created_by_device": "desktop-shanghai",
                    "created_at": 1770000020000,
                    "summary_hash": "sha256:head8",
                    "files": [],
                    "tombstones": [],
                },
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ledger_path = root / "tombstone-ledger.jsonl"
            db_path = root / "state.sqlite3"
            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                upsert_vault_state(
                    connection,
                    VaultStateRecord(
                        vault_id="vault_pkb_001",
                        last_applied_revision=7,
                        remote_head_revision=7,
                        acked_revision=7,
                        pending_ack_to_server=[],
                        commit_in_progress=True,
                        last_manifest_summary="sha256:head7",
                        last_manifest_summary_status="valid",
                        local_delete_sequence=0,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_1",
                        intent_manifest_hash="sha256:intent_1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=None,
                        created_at=1770000019995,
                        updated_at=1770000019995,
                    ),
                )

                result = execute_commit_recovery_session(
                    transport,
                    connection,
                    vault_root=root,
                    vault_id="vault_pkb_001",
                    normalized_at=1770000021001,
                    ledger_path=ledger_path,
                    local_tombstones=[],
                )

                self.assertEqual(result.mode, "submitted_confirmation")
                self.assertFalse(result.requires_full_pull)
                self.assertIsNone(result.local)
                self.assertIsNotNone(result.submitted)
                self.assertEqual(result.submitted.recovery.state.last_applied_revision, 8)
                self.assertEqual(
                    [call[0] for call in transport.calls],
                    ["resolve_commit_intent", "get_manifest"],
                )


if __name__ == "__main__":
    unittest.main()
