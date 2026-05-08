from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request

from clients.desktop import DesktopSyncHttpConfig, build_desktop_sync_service
from clients.desktop.crypto import build_placeholder_blob_id, build_placeholder_encrypted_blob_payload
from clients.desktop.worker import write_desktop_sync_worker_state
from vault_core import (
    FileMapDocument,
    FileRecord,
    VaultStateRecord,
    load_commit_intent_journal,
    load_vault_state,
    open_database,
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

    def build_encrypted_blob_map(self, content_by_file_id: dict[str, bytes]) -> dict[str, bytes]:
        return {
            file_id: self.encrypt_payload(payload)
            for file_id, payload in content_by_file_id.items()
        }


class DesktopSyncServiceTests(unittest.TestCase):
    def _seed_workspace(self, root: Path, *, conflict: bool = False, blob_crypto_provider=None):
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
        )
        service.ensure_initialized(now_ms=1770000030000)

        payload = b"# Live note\n"
        encrypted_payload = b"x" * (len(payload) + 16)
        live_note_path = root / "Notes" / "Live.md"
        live_note_path.parent.mkdir(parents=True, exist_ok=True)
        live_note_path.write_bytes(payload)
        document = FileMapDocument(
            vault_id="vault-001",
            updated_at=1770000030100,
            files=[
                FileRecord(
                    file_id="file-live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000030090,
                    content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
                    meta={
                        "blob_id": "blob-live",
                        "size": len(payload),
                        "mtime": 1770000030080,
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
