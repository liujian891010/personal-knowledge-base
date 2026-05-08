from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from unittest import mock
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.request import Request

from clients.desktop import (
    DesktopPullApplyDeleteFile,
    DesktopPullApplyMoveFile,
    DesktopPullApplyPlan,
    DesktopPullApplyWriteFile,
    DesktopPullApplyStagingResult,
    DesktopPullRequiredBlobFile,
    DesktopPullRequiredBlobPlan,
    DesktopPullRequiredBlobResult,
    DesktopSyncHttpConfig,
    DesktopWorkspaceSnapshot,
    build_desktop_sync_service,
)
from clients.desktop.crypto import (
    build_placeholder_blob_id,
    build_placeholder_encrypted_blob_payload,
    decrypt_placeholder_encrypted_blob_payload,
)
from clients.desktop.worker import write_desktop_sync_worker_state
from vault_core import (
    AppliedManifestResult,
    BlobDownloadCapability,
    BlobDownloadInitExecutionResult,
    BlobDownloadInitRequestPayload,
    BlobDownloadInitResponsePayload,
    BlobDownloadSessionResult,
    FileMapDocument,
    FileRecord,
    ManifestConvergenceResult,
    ManifestFileEntry,
    ManifestRecord,
    PullReconcileSessionResult,
    PullSyncSessionResult,
    ReconcileResult,
    SyncApplyJournalRecord,
    VaultHeadResponsePayload,
    VaultStateRecord,
    load_commit_intent_journal,
    load_sync_apply_journal,
    load_vault_state,
    open_database,
    upsert_sync_apply_journal,
    upsert_vault_state,
    write_filemap_atomic,
)


@dataclass
class FakeHttpResponse:
    status_code: int
    body: bytes

    def read(self) -> bytes:
        return self.body

    def getcode(self) -> int:
        return self.status_code

    def close(self) -> None:
        return None


class RecordingApiOpener:
    def __init__(self, *, conflict: bool = False) -> None:
        self.conflict = conflict
        self.calls: list[tuple[str, str, object | None, float]] = []

    def __call__(self, request: Request, timeout: float) -> FakeHttpResponse:
        body = None if request.data is None else json.loads(request.data.decode("utf-8"))
        self.calls.append((request.get_method(), request.full_url, body, timeout))

        if request.get_method() == "POST" and request.full_url.endswith("/vaults/vault-001/blobs/check"):
            blob_ids = [] if body is None else list(body.get("blob_ids", []))
            return self._json_response({"existing_blob_ids": [], "missing_blob_ids": blob_ids})
        if request.get_method() == "POST" and request.full_url.endswith("/vaults/vault-001/blobs/upload-init"):
            blobs = [] if body is None else list(body.get("blobs", []))
            return self._json_response(
                {
                    "uploads": [
                        {
                            "blob_id": item["blob_id"],
                            "upload_url": f"https://blob.example.com/upload/{item['blob_id']}",
                            "expires_at": "2026-05-08T12:00:00Z",
                            "headers": {"x-upload-token": "upload-1"},
                        }
                        for item in blobs
                    ]
                }
            )
        if request.get_method() == "POST" and request.full_url.endswith("/vaults/vault-001/commits"):
            if self.conflict:
                return self._json_response(
                    {
                        "code": "base_revision_conflict",
                        "current_head_revision": 9,
                        "current_manifest_summary": "sha256:head9",
                    },
                    status_code=409,
                )
            return self._json_response(
                {
                    "vault_id": "vault-001",
                    "new_revision": 8,
                    "head_manifest_summary": "sha256:head8",
                    "acked_revision_for_device": 8,
                }
            )
        raise AssertionError(f"unexpected API request: {request.get_method()} {request.full_url}")

    def _json_response(self, payload: dict[str, object], *, status_code: int = 200) -> FakeHttpResponse:
        return FakeHttpResponse(
            status_code=status_code,
            body=json.dumps(payload).encode("utf-8"),
        )


class RecordingBlobOpener:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes | None, dict[str, str], float]] = []

    def __call__(self, request: Request, timeout: float) -> FakeHttpResponse:
        headers = {key.lower(): value for key, value in request.header_items()}
        self.calls.append((request.get_method(), request.full_url, request.data, headers, timeout))
        return FakeHttpResponse(status_code=200, body=b"")


class CustomBlobCryptoProvider:
    def build_blob_id(self, content_hash: str) -> str:
        return "blob-custom-" + content_hash.split(":", 1)[-1][:8]

    def encrypt_payload(self, payload: bytes) -> bytes:
        tag = len(payload).to_bytes(16, "big")
        return payload + tag

    def decrypt_payload(self, encrypted_payload: bytes, *, content_hash: str) -> bytes:
        if len(encrypted_payload) < 16:
            raise ValueError("encrypted payload is too short")
        payload = encrypted_payload[:-16]
        expected_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
        if expected_hash != content_hash:
            raise ValueError(
                f"decrypted payload hash mismatch: expected {content_hash}, got {expected_hash}"
            )
        return payload

    def build_encrypted_blob_map(self, content_by_file_id: dict[str, bytes]) -> dict[str, bytes]:
        return {
            file_id: self.encrypt_payload(payload)
            for file_id, payload in content_by_file_id.items()
        }


class DesktopSyncServiceTests(unittest.TestCase):
    def _build_pull_result(
        self,
        *,
        required_blob_ids: list[str],
        manifest_files: list[ManifestFileEntry],
        revision: int = 8,
    ) -> PullSyncSessionResult:
        manifest = ManifestRecord(
            vault_id="vault-001",
            revision=revision,
            base_revision=max(revision - 1, 0),
            created_by_device="desktop-remote",
            created_at=1770000030500,
            summary_hash=f"sha256:head{revision}",
            files=manifest_files,
            tombstones=[],
        )
        return PullSyncSessionResult(
            pull=PullReconcileSessionResult(
                head=VaultHeadResponsePayload(
                    vault_id="vault-001",
                    head_revision=revision,
                    manifest_summary=f"sha256:head{revision}",
                ),
                manifest=manifest,
                reconcile=ReconcileResult(
                    plan=type(
                        "FakePlan",
                        (),
                        {
                            "target_revision": revision,
                            "should_download_manifest": True,
                            "requires_full_pull": False,
                            "can_use_summary_shortcut": False,
                        },
                    )(),
                    state=VaultStateRecord(
                        vault_id="vault-001",
                        last_applied_revision=revision,
                        remote_head_revision=revision,
                        acked_revision=revision,
                        pending_ack_to_server=[revision],
                        commit_in_progress=False,
                        last_manifest_summary=f"sha256:head{revision}",
                        last_manifest_summary_status="valid",
                        local_delete_sequence=1,
                    ),
                    applied=AppliedManifestResult(
                        convergence=ManifestConvergenceResult(
                            filemap=FileMapDocument(vault_id="vault-001", updated_at=1770000030600),
                            tombstones=[],
                            reclaimed_tombstones=[],
                        ),
                        state=VaultStateRecord(
                            vault_id="vault-001",
                            last_applied_revision=revision,
                            remote_head_revision=revision,
                            acked_revision=revision,
                            pending_ack_to_server=[revision],
                            commit_in_progress=False,
                            last_manifest_summary=f"sha256:head{revision}",
                            last_manifest_summary_status="valid",
                            local_delete_sequence=1,
                        ),
                        required_blob_ids=required_blob_ids,
                    ),
                ),
            ),
            ack=None,
        )

    def _seed_workspace(
        self,
        root: Path,
        *,
        conflict: bool = False,
        blob_crypto_provider=None,
        file_id_builder=None,
    ):
        api_opener = RecordingApiOpener(conflict=conflict)
        blob_opener = RecordingBlobOpener()
        service = build_desktop_sync_service(
            DesktopSyncHttpConfig(
                base_url="https://sync.example.com",
                vault_id="vault-001",
                device_id="desktop-shanghai",
            ),
            root,
            api_opener=api_opener,
            blob_opener=blob_opener,
            blob_crypto_provider=blob_crypto_provider,
            file_id_builder=file_id_builder,
        )
        service.ensure_initialized(now_ms=1770000030000)

        payload = b"# Live note\n"
        encrypted_payload = b"x" * (len(payload) + 16)
        live_note_path = root / "Notes" / "Live.md"
        live_note_path.parent.mkdir(parents=True, exist_ok=True)
        live_note_path.write_bytes(payload)
        live_stat = live_note_path.stat()
        live_mtime_ms = live_stat.st_mtime_ns // 1_000_000
        document = FileMapDocument(
            vault_id="vault-001",
            updated_at=1770000030100,
            files=[
                FileRecord(
                    file_id="file-live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=live_mtime_ms,
                    content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
                    meta={
                        "blob_id": "blob-live",
                        "size": len(payload),
                        "mtime": live_mtime_ms,
                        "mime_type": "text/markdown",
                    },
                )
            ],
        )
        write_filemap_atomic(service.workspace.paths.filemap_path, document)

        with closing(open_database(service.workspace.paths.db_path)) as connection:
            current = load_vault_state(connection, "vault-001")
            self.assertIsNotNone(current)
            upsert_vault_state(
                connection,
                VaultStateRecord(
                    vault_id="vault-001",
                    last_applied_revision=7,
                    remote_head_revision=7,
                    acked_revision=7,
                    pending_ack_to_server=[],
                    commit_in_progress=False,
                    last_manifest_summary="sha256:head7",
                    last_manifest_summary_status="valid",
                    local_delete_sequence=1,
                    meta=current.meta,
                ),
            )

        return service, api_opener, blob_opener, payload, encrypted_payload

    def test_load_workspace_content_reads_file_bytes_from_vault_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, payload, _ = self._seed_workspace(Path(tmpdir))

            content_by_file_id = service.load_workspace_content(["file-live"])

            self.assertEqual(content_by_file_id, {"file-live": payload})

    def test_load_workspace_content_rejects_workspace_snapshot_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, payload, _ = self._seed_workspace(Path(tmpdir))
            snapshot = service.load_snapshot()
            live_path = Path(tmpdir) / "Notes" / "Live.md"
            live_path.write_bytes(payload + b"-drift")

            with mock.patch.object(type(service.workspace), "load_snapshot", return_value=snapshot):
                with self.assertRaisesRegex(
                    ValueError,
                    "workspace snapshot drift detected: file-live",
                ):
                    service.load_workspace_content(["file-live"])

    def test_load_workspace_content_for_document_validates_snapshot_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, payload, _ = self._seed_workspace(Path(tmpdir))
            snapshot = service.load_snapshot()

            content_by_file_id = service.load_workspace_content_for_document(snapshot.document)

            self.assertEqual(content_by_file_id, {"file-live": payload})

    def test_placeholder_decrypt_round_trip_validates_content_hash(self) -> None:
        payload = b"# round trip\n"
        encrypted_payload = build_placeholder_encrypted_blob_payload(payload)

        decrypted = decrypt_placeholder_encrypted_blob_payload(
            encrypted_payload,
            content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
        )

        self.assertEqual(decrypted, payload)

    def test_prepare_commit_materializes_snapshot_and_blob_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, payload, encrypted_payload = self._seed_workspace(Path(tmpdir))

            prepared = service.prepare_commit(
                created_at=1770000030200,
                commit_intent_id="intent-001",
                content_by_file_id={"file-live": payload},
                encrypted_blob_by_file_id={"file-live": encrypted_payload},
            )

            self.assertEqual(prepared.submission.journal.commit_intent_id, "intent-001")
            self.assertEqual(prepared.snapshot_table.entries[0].blob_id, "blob-live")
            self.assertTrue(prepared.snapshot_materialization.files[0].snapshot_path.exists())
            self.assertTrue(prepared.blob_staging_materialization.files[0].blob_staging_path.exists())

            with closing(open_database(service.workspace.paths.db_path)) as connection:
                journal = load_commit_intent_journal(connection, "vault-001")
                self.assertIsNotNone(journal)
                self.assertEqual(journal.status, "submitted")

    def test_prepare_commit_generates_placeholder_encrypted_blob_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, payload, _ = self._seed_workspace(Path(tmpdir))

            prepared = service.prepare_commit(
                created_at=1770000030200,
                commit_intent_id="intent-001",
                content_by_file_id={"file-live": payload},
            )

            blob_path = prepared.blob_staging_materialization.files[0].blob_staging_path
            self.assertEqual(
                blob_path.read_bytes(),
                build_placeholder_encrypted_blob_payload(payload),
            )

    def test_prepare_commit_uses_injected_blob_crypto_provider_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = CustomBlobCryptoProvider()
            service, _, _, payload, _ = self._seed_workspace(
                Path(tmpdir),
                blob_crypto_provider=provider,
            )

            prepared = service.prepare_commit(
                created_at=1770000030200,
                commit_intent_id="intent-001",
                content_by_file_id={"file-live": payload},
            )

            blob_path = prepared.blob_staging_materialization.files[0].blob_staging_path
            self.assertEqual(blob_path.read_bytes(), provider.encrypt_payload(payload))

    def test_submit_commit_success_finalizes_state_and_cleans_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, api_opener, blob_opener, payload, encrypted_payload = self._seed_workspace(Path(tmpdir))

            result = service.submit_commit(
                created_at=1770000030200,
                commit_intent_id="intent-001",
                content_by_file_id={"file-live": payload},
                encrypted_blob_by_file_id={"file-live": encrypted_payload},
            )

            self.assertEqual(result.network.commit.status, "committed")
            self.assertIsNotNone(result.finalized)
            self.assertIsNone(result.cleanup)
            self.assertEqual(result.finalized.finalized.state.last_applied_revision, 8)
            self.assertFalse(result.finalized.finalized.state.commit_in_progress)
            self.assertEqual(
                [call[0:2] for call in api_opener.calls],
                [
                    ("POST", "https://sync.example.com/vaults/vault-001/blobs/check"),
                    ("POST", "https://sync.example.com/vaults/vault-001/blobs/upload-init"),
                    ("POST", "https://sync.example.com/vaults/vault-001/commits"),
                ],
            )
            self.assertEqual(
                [call[0:2] for call in blob_opener.calls],
                [("PUT", "https://blob.example.com/upload/blob-live")],
            )
            self.assertEqual(blob_opener.calls[0][2], encrypted_payload)
            self.assertEqual(blob_opener.calls[0][3]["x-upload-token"], "upload-1")
            self.assertFalse((Path(tmpdir) / ".noteapp" / "staging" / "file-live.snapshot.plain").exists())
            self.assertFalse((Path(tmpdir) / ".noteapp" / "staging" / "blob-live.blob.staging").exists())

            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_commit_intent_journal(connection, "vault-001"))
                state = load_vault_state(connection, "vault-001")
                self.assertIsNotNone(state)
                self.assertEqual(state.last_applied_revision, 8)

    def test_submit_commit_conflict_cleans_journal_state_and_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, api_opener, blob_opener, payload, encrypted_payload = self._seed_workspace(
                Path(tmpdir),
                conflict=True,
            )

            result = service.submit_commit(
                created_at=1770000030200,
                commit_intent_id="intent-001",
                cleanup_normalized_at=1770000030300,
                content_by_file_id={"file-live": payload},
                encrypted_blob_by_file_id={"file-live": encrypted_payload},
            )

            self.assertEqual(result.network.commit.status, "conflict")
            self.assertIsNone(result.finalized)
            self.assertIsNotNone(result.cleanup)
            self.assertFalse(result.cleanup.state.commit_in_progress)
            self.assertEqual(
                [call[0:2] for call in api_opener.calls],
                [
                    ("POST", "https://sync.example.com/vaults/vault-001/blobs/check"),
                    ("POST", "https://sync.example.com/vaults/vault-001/blobs/upload-init"),
                    ("POST", "https://sync.example.com/vaults/vault-001/commits"),
                ],
            )
            self.assertEqual(len(blob_opener.calls), 1)
            self.assertFalse((Path(tmpdir) / ".noteapp" / "staging" / "file-live.snapshot.plain").exists())
            self.assertFalse((Path(tmpdir) / ".noteapp" / "staging" / "blob-live.blob.staging").exists())

            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_commit_intent_journal(connection, "vault-001"))
                state = load_vault_state(connection, "vault-001")
                self.assertIsNotNone(state)
                self.assertEqual(state.last_applied_revision, 7)
                self.assertFalse(state.commit_in_progress)

    def test_submit_workspace_commit_auto_generates_placeholder_encrypted_blobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, api_opener, blob_opener, payload, _ = self._seed_workspace(Path(tmpdir))

            result = service.submit_workspace_commit(
                created_at=1770000030200,
                file_ids=["file-live"],
                commit_intent_id="intent-001",
            )

            self.assertEqual(result.network.commit.status, "committed")
            self.assertEqual(
                [call[0:2] for call in api_opener.calls],
                [
                    ("POST", "https://sync.example.com/vaults/vault-001/blobs/check"),
                    ("POST", "https://sync.example.com/vaults/vault-001/blobs/upload-init"),
                    ("POST", "https://sync.example.com/vaults/vault-001/commits"),
                ],
            )
            self.assertEqual(
                blob_opener.calls[0][2],
                build_placeholder_encrypted_blob_payload(payload),
            )

    def test_submit_workspace_commit_reads_workspace_files_before_submit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, api_opener, blob_opener, payload, encrypted_payload = self._seed_workspace(Path(tmpdir))

            result = service.submit_workspace_commit(
                created_at=1770000030200,
                file_ids=["file-live"],
                commit_intent_id="intent-001",
                encrypted_blob_by_file_id={"file-live": encrypted_payload},
            )

            self.assertEqual(result.network.commit.status, "committed")
            self.assertEqual(
                [call[0:2] for call in api_opener.calls],
                [
                    ("POST", "https://sync.example.com/vaults/vault-001/blobs/check"),
                    ("POST", "https://sync.example.com/vaults/vault-001/blobs/upload-init"),
                    ("POST", "https://sync.example.com/vaults/vault-001/commits"),
                ],
            )
            self.assertEqual(blob_opener.calls[0][2], encrypted_payload)
            self.assertEqual((Path(tmpdir) / "Notes" / "Live.md").read_bytes(), payload)

    def test_submit_workspace_commit_rejects_workspace_snapshot_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, api_opener, blob_opener, payload, _ = self._seed_workspace(Path(tmpdir))
            snapshot = service.load_snapshot()
            live_path = Path(tmpdir) / "Notes" / "Live.md"
            live_path.write_bytes(payload + b"-drift")

            with mock.patch.object(type(service.workspace), "load_snapshot", return_value=snapshot):
                with self.assertRaisesRegex(
                    ValueError,
                    "workspace snapshot drift detected: file-live",
                ):
                    service.submit_workspace_commit(
                        created_at=1770000030200,
                        file_ids=["file-live"],
                        commit_intent_id="intent-001",
                    )

            self.assertEqual(api_opener.calls, [])
            self.assertEqual(blob_opener.calls, [])

    def test_build_pull_required_blob_plan_collects_manifest_file_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))
            pull = self._build_pull_result(
                required_blob_ids=["blob-shared"],
                manifest_files=[
                    ManifestFileEntry(
                        file_id="file-a",
                        path="Notes/A.md",
                        type="note",
                        content_hash="sha256:shared",
                        blob_id="blob-shared",
                        size=10,
                        mtime=1770000030001,
                    ),
                    ManifestFileEntry(
                        file_id="file-b",
                        path="Notes/B.md",
                        type="note",
                        content_hash="sha256:shared",
                        blob_id="blob-shared",
                        size=10,
                        mtime=1770000030002,
                    ),
                ],
            )

            plan = service.build_pull_required_blob_plan(pull)

            self.assertEqual(plan.vault_id, "vault-001")
            self.assertEqual(plan.revision, 8)
            self.assertEqual(plan.blob_ids, ["blob-shared"])
            self.assertEqual([item.file_id for item in plan.files], ["file-a", "file-b"])

    def test_download_and_decrypt_pull_required_blobs_reuses_shared_blob_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))
            payload = b"# shared\n"
            content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
            encrypted_payload = build_placeholder_encrypted_blob_payload(payload)
            pull = self._build_pull_result(
                required_blob_ids=["blob-shared"],
                manifest_files=[
                    ManifestFileEntry(
                        file_id="file-a",
                        path="Notes/A.md",
                        type="note",
                        content_hash=content_hash,
                        blob_id="blob-shared",
                        size=len(payload),
                        mtime=1770000030001,
                    ),
                    ManifestFileEntry(
                        file_id="file-b",
                        path="Notes/B.md",
                        type="note",
                        content_hash=content_hash,
                        blob_id="blob-shared",
                        size=len(payload),
                        mtime=1770000030002,
                    ),
                ],
            )
            download_result = BlobDownloadSessionResult(
                init=BlobDownloadInitExecutionResult(
                    request=BlobDownloadInitRequestPayload(blob_ids=["blob-shared"]),
                    response=BlobDownloadInitResponsePayload(
                        downloads=[
                            BlobDownloadCapability(
                                blob_id="blob-shared",
                                download_url="https://blob.example.com/download/blob-shared",
                                encrypted_size=len(encrypted_payload),
                                expires_at="2026-05-08T12:00:00Z",
                            )
                        ]
                    ),
                ),
                downloaded_blobs={"blob-shared": encrypted_payload},
            )

            with mock.patch.object(
                type(service),
                "download_blobs",
                return_value=download_result,
            ) as download_mock:
                resolved = service.download_and_decrypt_pull_required_blobs(pull)

            download_mock.assert_called_once_with(["blob-shared"])
            self.assertEqual(resolved.plan.blob_ids, ["blob-shared"])
            self.assertEqual(resolved.download, download_result)
            self.assertEqual(resolved.plaintext_by_file_id["file-a"], payload)
            self.assertEqual(resolved.plaintext_by_file_id["file-b"], payload)

    def test_build_pull_required_blob_plan_rejects_required_blob_ids_missing_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))
            pull = self._build_pull_result(
                required_blob_ids=["blob-missing"],
                manifest_files=[],
            )

            with self.assertRaisesRegex(
                ValueError,
                "required blob_ids are missing from pull manifest: blob-missing",
            ):
                service.build_pull_required_blob_plan(pull)

    def test_download_and_decrypt_pull_required_blobs_rejects_missing_downloaded_blob_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))
            payload = b"# decrypt\n"
            content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
            pull = self._build_pull_result(
                required_blob_ids=["blob-live"],
                manifest_files=[
                    ManifestFileEntry(
                        file_id="file-a",
                        path="Notes/A.md",
                        type="note",
                        content_hash=content_hash,
                        blob_id="blob-live",
                        size=len(payload),
                        mtime=1770000030001,
                    )
                ],
            )
            download_result = BlobDownloadSessionResult(
                init=BlobDownloadInitExecutionResult(
                    request=BlobDownloadInitRequestPayload(blob_ids=["blob-live"]),
                    response=BlobDownloadInitResponsePayload(
                        downloads=[
                            BlobDownloadCapability(
                                blob_id="blob-live",
                                download_url="https://blob.example.com/download/blob-live",
                                encrypted_size=len(payload) + 16,
                                expires_at="2026-05-08T12:00:00Z",
                            )
                        ]
                    ),
                ),
                downloaded_blobs={},
            )

            with mock.patch.object(type(service), "download_blobs", return_value=download_result):
                with self.assertRaisesRegex(KeyError, "downloaded blob payload not found: blob-live"):
                    service.download_and_decrypt_pull_required_blobs(pull)

    def test_materialize_pull_required_plaintext_writes_manifest_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))
            pull = self._build_pull_result(required_blob_ids=[], manifest_files=[])
            resolved = DesktopPullRequiredBlobResult(
                pull=pull,
                plan=DesktopPullRequiredBlobPlan(
                    vault_id="vault-001",
                    revision=8,
                    blob_ids=["blob-a"],
                    files=[
                        DesktopPullRequiredBlobFile(
                            file_id="file-a",
                            path="Notes/A.md",
                            type="note",
                            blob_id="blob-a",
                            content_hash="sha256:abc",
                        )
                    ],
                ),
                download=None,
                plaintext_by_file_id={"file-a": b"# materialized\n"},
            )

            written_paths = service.materialize_pull_required_plaintext(
                resolved,
                Path(tmpdir) / "materialized",
            )

            self.assertEqual(
                written_paths,
                {"file-a": Path(tmpdir) / "materialized" / "Notes" / "A.md"},
            )
            self.assertEqual(
                (Path(tmpdir) / "materialized" / "Notes" / "A.md").read_bytes(),
                b"# materialized\n",
            )

    def test_stage_pull_required_plaintext_for_apply_writes_staging_and_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))
            pull = self._build_pull_result(
                required_blob_ids=["blob-a"],
                manifest_files=[
                    ManifestFileEntry(
                        file_id="file-a",
                        path="Notes/A.md",
                        type="note",
                        blob_id="blob-a",
                        content_hash="sha256:abc",
                        size=4,
                        mtime=1770000040000,
                    )
                ],
            )
            resolved = DesktopPullRequiredBlobResult(
                pull=pull,
                plan=DesktopPullRequiredBlobPlan(
                    vault_id="vault-001",
                    revision=8,
                    blob_ids=["blob-a"],
                    files=[
                        DesktopPullRequiredBlobFile(
                            file_id="file-a",
                            path="Notes/A.md",
                            type="note",
                            blob_id="blob-a",
                            content_hash="sha256:abc",
                        )
                    ],
                ),
                download=None,
                plaintext_by_file_id={"file-a": b"# A\n"},
            )

            staged = service.stage_pull_required_plaintext_for_apply(
                resolved,
                started_at=1770000040100,
            )

            self.assertIsInstance(staged, DesktopPullApplyStagingResult)
            self.assertEqual(staged.journal.vault_id, "vault-001")
            self.assertEqual(staged.journal.target_revision, 8)
            self.assertEqual(staged.journal.target_manifest_hash, "sha256:head8")
            self.assertEqual(staged.journal.phase, "staging")
            self.assertTrue(staged.journal.ops_hash.startswith("sha256:"))
            self.assertEqual(
                staged.written_staging_paths,
                {"file-a": Path(tmpdir) / ".noteapp" / "staging" / "file-a.staging"},
            )
            self.assertEqual(
                (Path(tmpdir) / ".noteapp" / "staging" / "file-a.staging").read_bytes(),
                b"# A\n",
            )

            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertEqual(load_sync_apply_journal(connection, "vault-001"), staged.journal)

    def test_stage_pull_required_plaintext_for_apply_skips_empty_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))
            pull = self._build_pull_result(required_blob_ids=[], manifest_files=[])
            resolved = DesktopPullRequiredBlobResult(
                pull=pull,
                plan=DesktopPullRequiredBlobPlan(
                    vault_id="vault-001",
                    revision=8,
                    blob_ids=[],
                    files=[],
                ),
                download=None,
                plaintext_by_file_id={},
            )

            staged = service.stage_pull_required_plaintext_for_apply(
                resolved,
                started_at=1770000040100,
            )

            self.assertEqual(staged, DesktopPullApplyStagingResult(journal=None, written_staging_paths={}))
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_sync_apply_journal(connection, "vault-001"))

    def test_stage_pull_required_plaintext_for_apply_creates_journal_for_move_only_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))
            pull = self._build_pull_result(required_blob_ids=[], manifest_files=[])
            resolved = DesktopPullRequiredBlobResult(
                pull=pull,
                plan=DesktopPullRequiredBlobPlan(
                    vault_id="vault-001",
                    revision=8,
                    blob_ids=[],
                    files=[],
                ),
                download=None,
                plaintext_by_file_id={},
            )
            apply_plan = DesktopPullApplyPlan(
                vault_id="vault-001",
                revision=8,
                writes=[],
                moves=[
                    DesktopPullApplyMoveFile(
                        file_id="file-a",
                        source_path="Notes/A-old.md",
                        target_path="Notes/A.md",
                        type="note",
                        content_hash="sha256:abc",
                    )
                ],
                deletes=[],
                blocking_paths=[],
                ops_hash="sha256:ops8",
            )

            staged = service.stage_pull_required_plaintext_for_apply(
                resolved,
                started_at=1770000040100,
                apply_plan=apply_plan,
            )

            self.assertIsNotNone(staged.journal)
            self.assertEqual(staged.journal.phase, "staging")
            self.assertEqual(staged.journal.ops_hash, "sha256:ops8")
            self.assertEqual(staged.written_staging_paths, {})
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertEqual(load_sync_apply_journal(connection, "vault-001"), staged.journal)

    def test_build_pull_apply_plan_emits_write_move_and_delete_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, payload, _ = self._seed_workspace(Path(tmpdir))
            before_snapshot = DesktopWorkspaceSnapshot(
                document=FileMapDocument(
                    vault_id="vault-001",
                    updated_at=1770000040000,
                    files=[
                        FileRecord(
                            file_id="file-a",
                            path="Notes/A-old.md",
                            type="note",
                            status="active",
                            updated_at=1770000039000,
                            content_hash="sha256:same",
                        ),
                        FileRecord(
                            file_id="file-b",
                            path="Notes/B.md",
                            type="note",
                            status="active",
                            updated_at=1770000039001,
                            content_hash="sha256:old",
                        ),
                        FileRecord(
                            file_id="file-c",
                            path="Notes/C.md",
                            type="note",
                            status="active",
                            updated_at=1770000039002,
                            content_hash="sha256:gone",
                        ),
                    ],
                ),
                state=service.load_snapshot().state,
                tombstones=[],
            )
            pull = self._build_pull_result(
                required_blob_ids=["blob-b"],
                manifest_files=[
                    ManifestFileEntry(
                        file_id="file-a",
                        path="Notes/A.md",
                        type="note",
                        blob_id="blob-a",
                        content_hash="sha256:same",
                        size=len(payload),
                        mtime=1770000040100,
                    ),
                    ManifestFileEntry(
                        file_id="file-b",
                        path="Notes/B.md",
                        type="note",
                        blob_id="blob-b",
                        content_hash="sha256:new",
                        size=len(payload),
                        mtime=1770000040200,
                    ),
                ],
            )

            plan = service._build_pull_apply_plan(before_snapshot, pull)

            self.assertEqual(plan.vault_id, "vault-001")
            self.assertEqual(plan.revision, 8)
            self.assertEqual(
                [(item.file_id, item.source_path, item.target_path) for item in plan.moves],
                [("file-a", "Notes/A-old.md", "Notes/A.md")],
            )
            self.assertEqual(
                [(item.file_id, item.target_path, item.staging_path) for item in plan.writes],
                [("file-b", "Notes/B.md", ".noteapp/staging/file-b.staging")],
            )
            self.assertEqual(
                [(item.file_id, item.path, item.reason) for item in plan.deletes],
                [("file-c", "Notes/C.md", "deleted")],
            )
            self.assertEqual(plan.blocking_paths, [])
            self.assertTrue(plan.ops_hash.startswith("sha256:"))

    def test_build_pull_apply_plan_flags_blocking_paths_for_path_swap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))
            before_snapshot = DesktopWorkspaceSnapshot(
                document=FileMapDocument(
                    vault_id="vault-001",
                    updated_at=1770000040000,
                    files=[
                        FileRecord(
                            file_id="file-a",
                            path="Notes/A.md",
                            type="note",
                            status="active",
                            updated_at=1770000039000,
                            content_hash="sha256:a",
                        ),
                        FileRecord(
                            file_id="file-b",
                            path="Notes/B.md",
                            type="note",
                            status="active",
                            updated_at=1770000039001,
                            content_hash="sha256:b",
                        ),
                    ],
                ),
                state=service.load_snapshot().state,
                tombstones=[],
            )
            pull = self._build_pull_result(
                required_blob_ids=[],
                manifest_files=[
                    ManifestFileEntry(
                        file_id="file-a",
                        path="Notes/B.md",
                        type="note",
                        blob_id="blob-a",
                        content_hash="sha256:a",
                        size=1,
                        mtime=1770000040100,
                    ),
                    ManifestFileEntry(
                        file_id="file-b",
                        path="Notes/A.md",
                        type="note",
                        blob_id="blob-b",
                        content_hash="sha256:b",
                        size=1,
                        mtime=1770000040200,
                    ),
                ],
            )

            plan = service._build_pull_apply_plan(before_snapshot, pull)

            self.assertEqual(
                sorted((item.file_id, item.source_path, item.target_path) for item in plan.moves),
                [
                    ("file-a", "Notes/A.md", "Notes/B.md"),
                    ("file-b", "Notes/B.md", "Notes/A.md"),
                ],
            )
            self.assertEqual(plan.writes, [])
            self.assertEqual(plan.deletes, [])
            self.assertEqual(plan.blocking_paths, ["Notes/A.md", "Notes/B.md"])

    def test_apply_staged_pull_plan_materializes_nonblocking_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            move_source = root / "Notes" / "Move-old.md"
            move_source.write_bytes(b"# move\n")
            delete_path = root / "Notes" / "Delete.md"
            delete_path.write_bytes(b"# delete\n")
            staging_path = root / ".noteapp" / "staging" / "file-live.staging"
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            staging_path.write_bytes(b"# rewritten\n")

            journal = SyncApplyJournalRecord(
                vault_id="vault-001",
                journal_id="journal-1",
                target_revision=8,
                target_manifest_hash="sha256:head8",
                phase="staging",
                ops_hash="sha256:old",
                created_at=1770000040100,
                updated_at=1770000040100,
            )
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                upsert_sync_apply_journal(connection, journal)

            plan = DesktopPullApplyPlan(
                vault_id="vault-001",
                revision=8,
                writes=[
                    DesktopPullApplyWriteFile(
                        file_id="file-live",
                        target_path="Notes/Live.md",
                        staging_path=".noteapp/staging/file-live.staging",
                        type="note",
                        content_hash="sha256:" + hashlib.sha256(b"# rewritten\n").hexdigest(),
                    )
                ],
                moves=[
                    DesktopPullApplyMoveFile(
                        file_id="file-move",
                        source_path="Notes/Move-old.md",
                        target_path="Notes/Move-new.md",
                        type="note",
                        content_hash="sha256:" + hashlib.sha256(b"# move\n").hexdigest(),
                    )
                ],
                deletes=[
                    DesktopPullApplyDeleteFile(
                        file_id="file-delete",
                        path="Notes/Delete.md",
                        reason="deleted",
                    )
                ],
                blocking_paths=[],
                ops_hash="sha256:ops8",
            )
            staged = DesktopPullApplyStagingResult(
                journal=journal,
                written_staging_paths={"file-live": staging_path},
            )

            execution = service.apply_staged_pull_plan(
                plan,
                staged,
                materialized_at=1770000040200,
            )

            self.assertEqual((root / "Notes" / "Live.md").read_bytes(), b"# rewritten\n")
            self.assertFalse(move_source.exists())
            self.assertEqual((root / "Notes" / "Move-new.md").read_bytes(), b"# move\n")
            self.assertFalse(delete_path.exists())
            self.assertEqual(
                execution.written_paths,
                {"file-live": root / "Notes" / "Live.md"},
            )
            self.assertEqual(
                execution.moved_paths,
                {"file-move": root / "Notes" / "Move-new.md"},
            )
            self.assertEqual(execution.deleted_paths, [root / "Notes" / "Delete.md"])
            self.assertEqual(execution.journal.phase, "materializing")
            self.assertEqual(execution.journal.ops_hash, "sha256:ops8")
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                loaded_journal = load_sync_apply_journal(connection, "vault-001")
                self.assertEqual(loaded_journal.phase, "materializing")
                self.assertEqual(loaded_journal.ops_hash, "sha256:ops8")

    def test_pull_and_apply_nonblocking_rejects_blocking_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))
            before_snapshot = service.load_snapshot()
            pull = self._build_pull_result(required_blob_ids=[], manifest_files=[])
            blocking_plan = DesktopPullApplyPlan(
                vault_id="vault-001",
                revision=8,
                writes=[],
                moves=[],
                deletes=[],
                blocking_paths=["Notes/A.md"],
                ops_hash="sha256:ops8",
            )

            with mock.patch.object(type(service), "_pull_and_ack_with_snapshot", return_value=(before_snapshot, pull)):
                with mock.patch.object(type(service), "_build_pull_apply_plan", return_value=blocking_plan):
                    with self.assertRaisesRegex(
                        ValueError,
                        "pull apply requires a later two-phase materialization boundary for blocking paths: Notes/A.md",
                    ):
                        service.pull_and_apply_nonblocking(rewritten_at=1770000040100)

    def test_prepare_commit_rejects_active_sync_apply_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, payload, encrypted_payload = self._seed_workspace(Path(tmpdir))

            with closing(open_database(service.workspace.paths.db_path)) as connection:
                upsert_sync_apply_journal(
                    connection,
                    SyncApplyJournalRecord(
                        vault_id="vault-001",
                        journal_id="journal-1",
                        target_revision=8,
                        target_manifest_hash="sha256:head8",
                        phase="staging",
                        ops_hash="sha256:ops8",
                        created_at=1770000040100,
                        updated_at=1770000040100,
                    ),
                )

            with self.assertRaisesRegex(
                ValueError,
                "commit is blocked while sync_apply_journal is active: journal-1 \\(staging\\)",
            ):
                service.prepare_commit(
                    created_at=1770000040200,
                    content_by_file_id={"file-live": payload},
                    encrypted_blob_by_file_id={"file-live": encrypted_payload},
                )

    def test_load_worker_state_and_health_routes_workspace_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))
            write_desktop_sync_worker_state(
                service.workspace.paths.worker_state_path,
                type(
                    "FakeResult",
                    (),
                    {
                        "started_at_ms": 1770000031000,
                        "finished_at_ms": 1770000031001,
                        "effective_step_ms": 0,
                        "state_path": service.workspace.paths.worker_state_path,
                        "config": type(
                            "FakeConfig",
                            (),
                            {
                                "iterations": 1,
                                "interval_seconds": 30.0,
                                "step_ms": None,
                                "continue_on_error": True,
                                "init_now_ms": None,
                                "recovery_normalized_at": None,
                                "submit_created_at": None,
                                "submit_file_ids": None,
                                "commit_intent_id": None,
                                "cleanup_normalized_at": None,
                                "pull_rewritten_at": None,
                                "encrypted_blob_by_file_id": None,
                            },
                        )(),
                        "time_plan": type(
                            "FakePlan",
                            (),
                            {
                                "base_now_ms": 1770000031000,
                                "init_now_ms": 1770000031000,
                                "recovery_normalized_at": 1770000031010,
                                "submit_created_at": None,
                                "cleanup_normalized_at": None,
                                "pull_rewritten_at": 1770000031020,
                            },
                        )(),
                        "loop": type(
                            "FakeLoop",
                            (),
                            {
                                "success_count": 1,
                                "failure_count": 0,
                                "stopped_early": False,
                                "iterations": [],
                            },
                        )(),
                    },
                )(),
            )

            state = service.load_worker_state()
            health = service.load_worker_health()
            self.assertEqual(state.started_at_ms, 1770000031000)
            self.assertEqual(health.status, "healthy")

    def test_detect_local_changes_routes_workspace_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, payload, _ = self._seed_workspace(Path(tmpdir))

            (Path(tmpdir) / "Notes" / "Live.md").write_bytes(payload + b"updated")
            changes = service.detect_local_changes()

            self.assertEqual(changes.modified_file_ids, ["file-live"])
            self.assertEqual(changes.change_count, 1)

    def test_submit_detected_changes_if_needed_returns_none_for_clean_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, api_opener, blob_opener, _, _ = self._seed_workspace(Path(tmpdir))

            result = service.submit_detected_changes_if_needed(
                created_at=1770000030200,
                commit_intent_id="intent-skip-001",
            )

            self.assertIsNone(result)
            self.assertEqual(api_opener.calls, [])
            self.assertEqual(blob_opener.calls, [])

    def test_submit_detected_changes_if_needed_ignores_ai_raw_and_log_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, api_opener, blob_opener, _, _ = self._seed_workspace(Path(tmpdir))
            (Path(tmpdir) / ".ai" / "raw").mkdir(parents=True, exist_ok=True)
            (Path(tmpdir) / ".ai" / "raw" / "capture.txt").write_text("raw", encoding="utf-8")
            (Path(tmpdir) / ".ai" / "log.md").write_text("log", encoding="utf-8")

            result = service.submit_detected_changes_if_needed(
                created_at=1770000030200,
                commit_intent_id="intent-ai-skip-001",
            )

            self.assertIsNone(result)
            self.assertEqual(api_opener.calls, [])
            self.assertEqual(blob_opener.calls, [])

    def test_submit_detected_changes_commits_modified_tracked_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, api_opener, blob_opener, payload, _ = self._seed_workspace(Path(tmpdir))
            updated_payload = payload + b"updated"
            updated_hash = "sha256:" + hashlib.sha256(updated_payload).hexdigest()
            updated_blob_id = build_placeholder_blob_id(updated_hash)
            (Path(tmpdir) / "Notes" / "Live.md").write_bytes(updated_payload)

            result = service.submit_detected_changes(
                created_at=1770000030200,
                commit_intent_id="intent-detected-001",
            )

            self.assertEqual(result.network.commit.status, "committed")
            self.assertEqual(
                [call[0:2] for call in api_opener.calls],
                [
                    ("POST", "https://sync.example.com/vaults/vault-001/blobs/check"),
                    ("POST", "https://sync.example.com/vaults/vault-001/blobs/upload-init"),
                    ("POST", "https://sync.example.com/vaults/vault-001/commits"),
                ],
            )
            self.assertEqual(
                [call[0:2] for call in blob_opener.calls],
                [("PUT", f"https://blob.example.com/upload/{updated_blob_id}")],
            )
            self.assertEqual(
                blob_opener.calls[0][2],
                build_placeholder_encrypted_blob_payload(updated_payload),
            )

    def test_submit_detected_changes_rejects_workspace_snapshot_drift_after_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, api_opener, blob_opener, payload, _ = self._seed_workspace(Path(tmpdir))
            updated_payload = payload + b"updated"
            drifted_payload = updated_payload + b"-drift"
            live_path = Path(tmpdir) / "Notes" / "Live.md"
            live_path.write_bytes(updated_payload)
            service = replace(
                service,
                detected_submit_plan_hook=lambda _: live_path.write_bytes(drifted_payload),
            )

            with self.assertRaisesRegex(ValueError, "workspace snapshot drift detected: file-live"):
                service.submit_detected_changes(
                    created_at=1770000030200,
                    commit_intent_id="intent-drift-001",
                )

            self.assertEqual(api_opener.calls, [])
            self.assertEqual(blob_opener.calls, [])

    def test_submit_detected_changes_uses_injected_blob_crypto_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = CustomBlobCryptoProvider()
            service, api_opener, blob_opener, payload, _ = self._seed_workspace(
                Path(tmpdir),
                blob_crypto_provider=provider,
            )
            updated_payload = payload + b"updated"
            updated_hash = "sha256:" + hashlib.sha256(updated_payload).hexdigest()
            updated_blob_id = provider.build_blob_id(updated_hash)
            (Path(tmpdir) / "Notes" / "Live.md").write_bytes(updated_payload)

            result = service.submit_detected_changes(
                created_at=1770000030200,
                commit_intent_id="intent-detected-provider-001",
            )

            self.assertEqual(result.network.commit.status, "committed")
            self.assertEqual(
                [call[0:2] for call in api_opener.calls],
                [
                    ("POST", "https://sync.example.com/vaults/vault-001/blobs/check"),
                    ("POST", "https://sync.example.com/vaults/vault-001/blobs/upload-init"),
                    ("POST", "https://sync.example.com/vaults/vault-001/commits"),
                ],
            )
            self.assertEqual(
                [call[0:2] for call in blob_opener.calls],
                [("PUT", f"https://blob.example.com/upload/{updated_blob_id}")],
            )
            self.assertEqual(blob_opener.calls[0][2], provider.encrypt_payload(updated_payload))

    def test_submit_detected_changes_rejects_modified_conflict_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, payload, _ = self._seed_workspace(Path(tmpdir))
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                FileMapDocument(
                    vault_id="vault-001",
                    updated_at=1770000030100,
                    files=[
                        FileRecord(
                            file_id="file-live",
                            path="Notes/Live.md",
                            type="note",
                            status="conflict_copy",
                            updated_at=1770000030090,
                            content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
                            conflict_source_file_id="file-source",
                            meta={
                                "blob_id": "blob-live",
                                "size": len(payload),
                                "mtime": 1770000030080,
                                "mime_type": "text/markdown",
                            },
                        )
                    ],
                ),
            )
            (Path(tmpdir) / "Notes" / "Live.md").write_bytes(payload + b"updated")

            with self.assertRaisesRegex(ValueError, "unsupported items"):
                service.submit_detected_changes(created_at=1770000030200)

    def test_submit_detected_changes_commits_missing_tracked_file_as_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, api_opener, blob_opener, _, _ = self._seed_workspace(Path(tmpdir))
            (Path(tmpdir) / "Notes" / "Live.md").unlink()

            result = service.submit_detected_changes(
                created_at=1770000030200,
                commit_intent_id="intent-delete-001",
            )

            self.assertEqual(result.network.commit.status, "committed")
            self.assertEqual([call[0] for call in api_opener.calls], ["POST"])
            self.assertEqual(api_opener.calls[0][1], "https://sync.example.com/vaults/vault-001/commits")
            self.assertEqual(blob_opener.calls, [])
            self.assertEqual(result.prepared.submission.manifest.files, [])
            self.assertEqual(len(result.prepared.submission.manifest.tombstones), 1)

    def test_submit_detected_changes_commits_untracked_file_as_new_active_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, api_opener, blob_opener, _, _ = self._seed_workspace(Path(tmpdir))
            (Path(tmpdir) / "Notes" / "Live.md").unlink()
            new_payload = b"# New note\n"
            new_path = Path(tmpdir) / "Notes" / "New.md"
            new_path.write_bytes(new_payload)

            result = service.submit_detected_changes(
                created_at=1770000030200,
                commit_intent_id="intent-add-001",
            )

            self.assertEqual(result.network.commit.status, "committed")
            self.assertEqual(
                [call[0:2] for call in api_opener.calls],
                [
                    ("POST", "https://sync.example.com/vaults/vault-001/blobs/check"),
                    ("POST", "https://sync.example.com/vaults/vault-001/blobs/upload-init"),
                    ("POST", "https://sync.example.com/vaults/vault-001/commits"),
                ],
            )
            self.assertEqual(len(blob_opener.calls), 1)
            self.assertEqual(blob_opener.calls[0][2], build_placeholder_encrypted_blob_payload(new_payload))

    def test_submit_detected_changes_uses_injected_file_id_builder_for_untracked_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, api_opener, blob_opener, _, _ = self._seed_workspace(
                Path(tmpdir),
                file_id_builder=lambda _: "11111111-1111-1111-1111-111111111111",
            )
            (Path(tmpdir) / "Notes" / "Live.md").unlink()
            new_payload = b"# New note\n"
            new_path = Path(tmpdir) / "Notes" / "New.md"
            new_path.write_bytes(new_payload)

            result = service.submit_detected_changes(
                created_at=1770000030200,
                commit_intent_id="intent-add-provider-001",
            )

            self.assertEqual(result.network.commit.status, "committed")
            self.assertEqual(
                result.prepared.submission.manifest.files[0].file_id,
                "11111111-1111-1111-1111-111111111111",
            )
            self.assertEqual(len(blob_opener.calls), 1)
            self.assertEqual(blob_opener.calls[0][2], build_placeholder_encrypted_blob_payload(new_payload))

    def test_submit_detected_changes_folds_local_rename_to_existing_file_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, api_opener, blob_opener, payload, _ = self._seed_workspace(Path(tmpdir))
            (Path(tmpdir) / "Notes" / "Live.md").unlink()
            renamed_path = Path(tmpdir) / "Notes" / "Renamed.md"
            renamed_path.write_bytes(payload)

            result = service.submit_detected_changes(
                created_at=1770000030200,
                commit_intent_id="intent-rename-001",
            )

            self.assertEqual(result.network.commit.status, "committed")
            self.assertEqual(
                [call[0:2] for call in api_opener.calls],
                [
                    ("POST", "https://sync.example.com/vaults/vault-001/blobs/check"),
                    ("POST", "https://sync.example.com/vaults/vault-001/blobs/upload-init"),
                    ("POST", "https://sync.example.com/vaults/vault-001/commits"),
                ],
            )
            self.assertEqual(len(blob_opener.calls), 1)
            self.assertEqual(blob_opener.calls[0][2], build_placeholder_encrypted_blob_payload(payload))
            self.assertEqual(result.prepared.submission.manifest.files[0].file_id, "file-live")
            self.assertEqual(result.prepared.submission.manifest.files[0].path, "Notes/Renamed.md")
            self.assertEqual(result.prepared.submission.manifest.tombstones, [])


if __name__ == "__main__":
    unittest.main()
