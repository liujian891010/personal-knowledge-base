from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock
from contextlib import closing
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Optional
from urllib.request import Request
from zipfile import ZipFile

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
    E2EEDesktopBlobCryptoProvider,
    POLY1305_TAG_BYTES,
    is_e2ee_crypto_available,
    build_placeholder_blob_id,
    build_placeholder_blob_crypto_provider,
    build_placeholder_encrypted_blob_payload,
    decrypt_placeholder_encrypted_blob_payload,
)
from clients.desktop.service import (
    _LOCAL_SETTINGS_AI_PROVIDER_APIS,
    _LOCAL_SETTINGS_EMBEDDING_STATUSES,
    _LOCAL_SETTINGS_MODEL_STATUSES,
    _LOCAL_SETTINGS_THEMES,
)
from clients.desktop.worker import write_desktop_sync_worker_state
from vault_core import (
    AppliedManifestResult,
    append_tombstone,
    BlobDownloadCapability,
    BlobDownloadInitExecutionResult,
    BlobDownloadInitRequestPayload,
    BlobDownloadInitResponsePayload,
    BlobDownloadSessionResult,
    FileMapDocument,
    FileRecord,
    FileVersionCommitDirective,
    ManifestConvergenceResult,
    ManifestFileEntry,
    ManifestRecord,
    PullReconcileSessionResult,
    PullSyncSessionResult,
    ReconcileResult,
    SyncApplyJournalRecord,
    TombstoneRecord,
    VaultHeadResponsePayload,
    VaultStateRecord,
    load_commit_intent_journal,
    load_filemap,
    load_sync_apply_journal,
    load_tombstone_ledger,
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
    def __init__(
        self,
        *,
        conflict: bool = False,
        remote_payload: bytes = b"# Live note\n",
        file_versions: Optional[dict[str, list[dict[str, object]]]] = None,
        encrypted_size_by_blob_id: Optional[dict[str, int]] = None,
    ) -> None:
        self.conflict = conflict
        self.remote_payload = remote_payload
        self.file_versions = dict(file_versions or {})
        self.encrypted_size_by_blob_id = dict(encrypted_size_by_blob_id or {})
        self.calls: list[tuple[str, str, object | None, float]] = []

    def __call__(self, request: Request, timeout: float) -> FakeHttpResponse:
        body = None if request.data is None else json.loads(request.data.decode("utf-8"))
        self.calls.append((request.get_method(), request.full_url, body, timeout))

        if request.get_method() == "GET" and request.full_url.endswith("/vaults/vault-001/head"):
            return self._json_response(
                {
                    "vault_id": "vault-001",
                    "head_revision": 9,
                    "manifest_summary": "sha256:head9",
                }
            )
        if request.get_method() == "GET" and request.full_url.endswith("/vaults/vault-001/devices"):
            return self._json_response(
                {
                    "vault_id": "vault-001",
                    "head_revision": 9,
                    "inactive_after_ms": 604800000,
                    "devices": [
                        {
                            "device_id": "desktop-shanghai",
                            "device_name": "Desktop",
                            "platform": "desktop",
                            "app_version": "1.0.43",
                            "protocol_version": "v1",
                            "registered_at_ms": 1770000000000,
                            "last_seen_at_ms": 1770000005000,
                            "acked_revision": 9,
                            "is_current_device": True,
                            "is_revoked": False,
                            "is_inactive_candidate": False,
                        },
                        {
                            "device_id": "dev_phone",
                            "device_name": "Phone",
                            "platform": "mobile",
                            "app_version": None,
                            "protocol_version": "v1",
                            "registered_at_ms": 1770000001000,
                            "last_seen_at_ms": 1769000000000,
                            "acked_revision": 6,
                            "is_current_device": False,
                            "is_revoked": False,
                            "is_inactive_candidate": True,
                        },
                    ],
                }
            )
        if request.get_method() == "POST" and request.full_url.endswith("/vaults/vault-001/devices/heartbeat"):
            return self._json_response(
                {
                    "vault_id": "vault-001",
                    "device_id": "desktop-shanghai",
                    "last_seen_at_ms": 1770000006000,
                    "acked_revision": 9,
                    "head_revision": 9,
                }
            )
        if request.get_method() == "DELETE" and request.full_url.endswith("/devices/dev_phone"):
            return FakeHttpResponse(status_code=204, body=b"")
        if request.get_method() == "GET" and request.full_url.endswith("/vaults/vault-001/manifests/9"):
            payload = self.remote_payload
            content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
            return self._json_response(
                {
                    "schema_version": "v1",
                    "vault_id": "vault-001",
                    "revision": 9,
                    "base_revision": 8,
                    "created_by_device": "desktop-remote",
                    "created_at": 1770000040000,
                    "files": [
                        {
                            "file_id": "file-live",
                            "path": "Notes/Live.md",
                            "type": "note",
                            "content_hash": content_hash,
                            "blob_id": build_placeholder_blob_id(content_hash),
                            "size": len(payload),
                            "mtime": 1770000030080,
                            "mime_type": "text/markdown",
                        }
                    ],
                    "tombstones": [],
                    "summary_hash": "sha256:head9",
                }
            )
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
        if request.get_method() == "POST" and request.full_url.endswith("/vaults/vault-001/blobs/download-init"):
            blob_ids = [] if body is None else list(body.get("blob_ids", []))
            return self._json_response(
                {
                    "downloads": [
                        {
                            "blob_id": blob_id,
                            "download_url": f"https://blob.example.com/download/{blob_id}",
                            "encrypted_size": self.encrypted_size_by_blob_id.get(
                                blob_id,
                                len(build_placeholder_encrypted_blob_payload(self.remote_payload)),
                            ),
                            "expires_at": "2026-05-08T12:00:00Z",
                            "headers": {"x-download-token": "download-1"},
                        }
                        for blob_id in blob_ids
                    ]
                }
            )
        if request.get_method() == "POST" and request.full_url.endswith("/vaults/vault-001/file-versions/list"):
            file_id = "" if body is None else str(body.get("file_id", ""))
            versions = list(self.file_versions.get(file_id, []))
            return self._json_response(
                {
                    "file_id": file_id,
                    "versions": versions,
                    "next_cursor": None,
                    "retention_policy": {
                        "keep_latest": 50,
                        "keep_pinned": True,
                    },
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
            base_revision = 7 if body is None else int(body.get("base_revision", 7))
            new_revision = base_revision + 1
            return self._json_response(
                {
                    "vault_id": "vault-001",
                    "new_revision": new_revision,
                    "head_manifest_summary": f"sha256:head{new_revision}",
                    "acked_revision_for_device": new_revision,
                }
            )
        if request.get_method() == "POST" and request.full_url.endswith("/vaults/vault-001/ack"):
            revisions = [] if body is None else list(body.get("revisions", []))
            return self._json_response({"max_acked_revision": max(revisions)})
        raise AssertionError(f"unexpected API request: {request.get_method()} {request.full_url}")

    def _json_response(self, payload: dict[str, object], *, status_code: int = 200) -> FakeHttpResponse:
        return FakeHttpResponse(
            status_code=status_code,
            body=json.dumps(payload).encode("utf-8"),
        )


class RecordingAiOpener:
    def __init__(self, *, status_code: int = 200, answer: str = "Provider answer [1]") -> None:
        self.status_code = status_code
        self.answer = answer
        self.calls: list[tuple[str, str, object | None, float, str | None]] = []

    def __call__(self, request: Request, timeout: float) -> FakeHttpResponse:
        body = None if request.data is None else json.loads(request.data.decode("utf-8"))
        self.calls.append(
            (
                request.get_method(),
                request.full_url,
                body,
                timeout,
                request.headers.get("Authorization"),
            )
        )
        return FakeHttpResponse(
            self.status_code,
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": self.answer,
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        )


class RecordingBlobOpener:
    def __init__(self, *, downloaded_blobs: Optional[dict[str, bytes]] = None) -> None:
        self.calls: list[tuple[str, str, bytes | None, dict[str, str], float]] = []
        self.downloaded_blobs = dict(downloaded_blobs or {})

    def __call__(self, request: Request, timeout: float) -> FakeHttpResponse:
        headers = {key.lower(): value for key, value in request.header_items()}
        self.calls.append((request.get_method(), request.full_url, request.data, headers, timeout))
        if request.get_method() == "GET" and "/download/" in request.full_url:
            blob_id = request.full_url.rsplit("/", 1)[-1]
            if blob_id not in self.downloaded_blobs:
                raise AssertionError(f"unexpected blob download: {blob_id}")
            return FakeHttpResponse(status_code=200, body=self.downloaded_blobs[blob_id])
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
    @staticmethod
    def _build_docx_payload(paragraphs: list[str]) -> bytes:
        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            + "".join(f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs)
            + "</w:body></w:document>"
        )
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr(
                "[Content_Types].xml",
                (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    '<Default Extension="xml" ContentType="application/xml"/>'
                    '<Override PartName="/word/document.xml" '
                    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                    "</Types>"
                ),
            )
            archive.writestr("word/document.xml", document_xml)
        return buffer.getvalue()

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
        remote_payload: bytes = b"# Live note\n",
        file_versions: Optional[dict[str, list[dict[str, object]]]] = None,
        downloaded_blobs: Optional[dict[str, bytes]] = None,
        blob_crypto_provider=None,
        file_id_builder=None,
    ):
        remote_content_hash = "sha256:" + hashlib.sha256(remote_payload).hexdigest()
        remote_blob_id = build_placeholder_blob_id(remote_content_hash)
        resolved_downloaded_blobs = {
            remote_blob_id: build_placeholder_encrypted_blob_payload(remote_payload),
        }
        if downloaded_blobs is not None:
            resolved_downloaded_blobs.update(downloaded_blobs)
        api_opener = RecordingApiOpener(
            conflict=conflict,
            remote_payload=remote_payload,
            file_versions=file_versions,
            encrypted_size_by_blob_id={
                blob_id: len(payload)
                for blob_id, payload in resolved_downloaded_blobs.items()
            },
        )
        blob_opener = RecordingBlobOpener(
            downloaded_blobs=resolved_downloaded_blobs,
        )
        resolved_blob_crypto_provider = (
            build_placeholder_blob_crypto_provider()
            if blob_crypto_provider is None
            else blob_crypto_provider
        )
        service = build_desktop_sync_service(
            DesktopSyncHttpConfig(
                base_url="https://sync.example.com",
                vault_id="vault-001",
                device_id="desktop-shanghai",
            ),
            root,
            api_opener=api_opener,
            blob_opener=blob_opener,
            blob_crypto_provider=resolved_blob_crypto_provider,
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

    def test_default_blob_crypto_provider_rejects_missing_vault_key_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            crypto_store_root = root / "keys"
            with mock.patch.dict(
                os.environ,
                {
                    "NOTEAPP_ALLOW_INSECURE_CRYPTO_STORE": "true",
                    "NOTEAPP_CRYPTO_STORE_DIR": str(crypto_store_root),
                    "NOTEAPP_ALLOW_PLACEHOLDER_CRYPTO": "false",
                    "NOTEAPP_VAULT_KEY_BASE64": "",
                    "NOTEAPP_VAULT_KEY_HEX": "",
                },
                clear=False,
            ):
                service = build_desktop_sync_service(
                    DesktopSyncHttpConfig(
                        base_url="https://sync.example.com",
                        vault_id="vault-001",
                        device_id="desktop-shanghai",
                    ),
                    root,
                )

            with self.assertRaisesRegex(RuntimeError, "e2ee-v1 vault key is required"):
                service.blob_crypto_provider.encrypt_payload(b"payload")

    def test_device_management_calls_remote_device_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, api_opener, _, _, _ = self._seed_workspace(Path(tmpdir))

            listed = service.list_vault_devices()
            heartbeat = service.heartbeat_vault_device()
            revoked = service.revoke_device(device_id="dev_phone")

        self.assertEqual(listed.response.vault_id, "vault-001")
        self.assertEqual([device.device_id for device in listed.response.devices], ["desktop-shanghai", "dev_phone"])
        self.assertTrue(listed.response.devices[0].is_current_device)
        self.assertTrue(listed.response.devices[1].is_inactive_candidate)
        self.assertEqual(heartbeat.response.device_id, "desktop-shanghai")
        self.assertEqual(heartbeat.response.acked_revision, 9)
        self.assertEqual(revoked, {"device_id": "dev_phone", "revoked": True})
        self.assertIn(("GET", "https://sync.example.com/vaults/vault-001/devices", None, 30.0), api_opener.calls)
        self.assertIn(("POST", "https://sync.example.com/vaults/vault-001/devices/heartbeat", None, 30.0), api_opener.calls)
        self.assertIn(("DELETE", "https://sync.example.com/devices/dev_phone", None, 30.0), api_opener.calls)

    def test_load_workspace_content_reads_file_bytes_from_vault_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, payload, _ = self._seed_workspace(Path(tmpdir))

            content_by_file_id = service.load_workspace_content(["file-live"])

            self.assertEqual(content_by_file_id, {"file-live": payload})

    def test_list_workspace_files_returns_filemap_disk_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, payload, _ = self._seed_workspace(root)

            snapshot = service.list_workspace_files()

            self.assertEqual(snapshot.schema_version, "v1")
            self.assertEqual(snapshot.vault_id, "vault-001")
            self.assertEqual(snapshot.device_id, "desktop-shanghai")
            self.assertEqual(snapshot.vault_root, root)
            self.assertEqual(snapshot.total_count, 1)
            self.assertEqual(snapshot.active_count, 1)
            self.assertEqual(snapshot.missing_count, 0)
            self.assertEqual(len(snapshot.files), 1)
            self.assertEqual(snapshot.files[0].file_id, "file-live")
            self.assertEqual(snapshot.files[0].path, "Notes/Live.md")
            self.assertEqual(snapshot.files[0].type, "note")
            self.assertEqual(snapshot.files[0].status, "active")
            self.assertTrue(snapshot.files[0].exists_on_disk)
            self.assertEqual(snapshot.files[0].size_bytes, len(payload))
            self.assertTrue(snapshot.files[0].content_hash.startswith("sha256:"))

    def test_list_workspace_files_marks_missing_active_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            (root / "Notes" / "Live.md").unlink()

            snapshot = service.list_workspace_files()

            self.assertEqual(snapshot.total_count, 1)
            self.assertEqual(snapshot.active_count, 1)
            self.assertEqual(snapshot.missing_count, 1)
            self.assertFalse(snapshot.files[0].exists_on_disk)
            self.assertIsNone(snapshot.files[0].size_bytes)

    def test_import_existing_workspace_files_if_empty_preserves_chinese_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service = build_desktop_sync_service(
                DesktopSyncHttpConfig(
                    base_url="https://sync.example.com",
                    vault_id="vault-001",
                    device_id="desktop-shanghai",
                ),
                root,
                file_id_builder=lambda path: "file-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8],
                allow_placeholder_crypto=True,
            )
            service.ensure_initialized(now_ms=1770000030000)
            nested_path = root / "二级目录" / "中文路径.md"
            nested_path.parent.mkdir()
            nested_path.write_bytes("# 中文标题\n".encode("gb18030"))
            (root / ".noteapp" / "ignored.md").write_text("# ignored\n", encoding="utf-8")

            snapshot = service.import_existing_workspace_files_if_empty()

            self.assertEqual(snapshot.total_count, 1)
            self.assertEqual(snapshot.files[0].path, "二级目录/中文路径.md")
            self.assertEqual(snapshot.files[0].type, "note")
            self.assertTrue(snapshot.files[0].exists_on_disk)
            self.assertEqual(snapshot.files[0].size_bytes, len("# 中文标题\n".encode("gb18030")))
            document = load_filemap(service.workspace.paths.filemap_path)
            self.assertEqual(document.files[0].path, "二级目录/中文路径.md")

    def test_import_existing_workspace_files_if_empty_includes_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service = build_desktop_sync_service(
                DesktopSyncHttpConfig(
                    base_url="https://sync.example.com",
                    vault_id="vault-001",
                    device_id="desktop-shanghai",
                ),
                root,
                file_id_builder=lambda path: "file-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8],
                allow_placeholder_crypto=True,
            )
            service.ensure_initialized(now_ms=1770000030000)
            image_path = root / "Assets" / "photo.png"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
            pdf_path = root / "Docs" / "manual.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.write_bytes(b"%PDF-1.7\n")
            (root / "Docs" / "~$manual.docx").write_bytes(b"office lock")
            (root / ".noteapp" / "ignored.pdf").write_bytes(b"ignored")
            (root / ".ai" / "raw").mkdir(parents=True, exist_ok=True)
            (root / ".ai" / "raw" / "capture.txt").write_text("raw", encoding="utf-8")
            (root / ".ai" / "log.md").write_text("log", encoding="utf-8")

            snapshot = service.import_existing_workspace_files_if_empty()

            self.assertEqual(snapshot.total_count, 2)
            self.assertEqual([item.path for item in snapshot.files], ["Assets/photo.png", "Docs/manual.pdf"])
            self.assertEqual([item.type for item in snapshot.files], ["attachment", "attachment"])
            self.assertEqual([item.mime_type for item in snapshot.files], ["image/png", "application/pdf"])
            self.assertTrue(all(item.exists_on_disk for item in snapshot.files))
            document = load_filemap(service.workspace.paths.filemap_path)
            meta_by_path = {record.path: record.meta for record in document.files}
            self.assertEqual(meta_by_path["Assets/photo.png"]["mime_type"], "image/png")
            self.assertEqual(meta_by_path["Docs/manual.pdf"]["mime_type"], "application/pdf")

    def test_import_existing_workspace_files_prunes_local_office_lock_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            lock_path = root / "Attachments" / "~$report.docx"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.write_bytes(b"office lock")
            document = load_filemap(service.workspace.paths.filemap_path)
            records = list(document.files)
            records.append(
                FileRecord(
                    file_id="file-lock",
                    path="Attachments/~$report.docx",
                    type="attachment",
                    status="active",
                    updated_at=1770000030200,
                    meta={
                        "size": len(b"office lock"),
                        "mtime": 1770000030200,
                        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    },
                )
            )
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                document.replace_files(records, updated_at=1770000030200),
            )

            snapshot = service.import_existing_workspace_files_if_empty()

            self.assertEqual([item.path for item in snapshot.files], ["Notes/Live.md"])
            repaired = load_filemap(service.workspace.paths.filemap_path)
            self.assertEqual([record.path for record in repaired.files], ["Notes/Live.md"])

    def test_import_existing_workspace_files_if_empty_appends_untracked_files_to_existing_filemap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            added_path = root / "Notes" / "Added.md"
            added_payload = b"# Added\n"
            added_path.write_bytes(added_payload)

            snapshot = service.import_existing_workspace_files_if_empty()

            self.assertEqual(snapshot.total_count, 2)
            self.assertEqual([item.path for item in snapshot.files], ["Notes/Added.md", "Notes/Live.md"])
            added_entry = next(item for item in snapshot.files if item.path == "Notes/Added.md")
            self.assertEqual(added_entry.type, "note")
            self.assertTrue(added_entry.exists_on_disk)
            self.assertEqual(added_entry.size_bytes, len(added_payload))
            document = load_filemap(service.workspace.paths.filemap_path)
            self.assertEqual(
                sorted(record.path for record in document.files if record.status == "active"),
                ["Notes/Added.md", "Notes/Live.md"],
            )

    def test_create_workspace_attachment_writes_filemap_and_preview_blob(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(
                root,
                file_id_builder=lambda path: "gen-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8],
            )

            created = service.create_workspace_attachment(
                "photo.png",
                b"\x89PNG\r\n\x1a\n",
                now_ms=1770000032000,
            )

            self.assertEqual(created.operation, "create_attachment")
            self.assertEqual(created.file.path, "Attachments/photo.png")
            self.assertEqual(created.file.type, "attachment")
            self.assertEqual(created.file.mime_type, "image/png")
            self.assertTrue((root / "Attachments" / "photo.png").exists())
            blob = service.load_workspace_file_blob(created.file.file_id)
            self.assertEqual(blob.mime_type, "image/png")
            self.assertEqual(blob.content_base64, "iVBORw0KGgo=")

    def test_markdown_attachment_uses_note_editing_search_and_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(
                root,
                file_id_builder=lambda path: "gen-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8],
            )

            created = service.create_workspace_attachment(
                "Attachment Note.md",
                b"# Attachment Note\n\nFindable attachment context. [[Live]]\n",
                now_ms=1770000032000,
            )
            attachment_file_id = created.file.file_id

            self.assertEqual(created.file.type, "attachment")
            self.assertEqual(created.file.mime_type, "text/markdown")
            self.assertEqual(
                service.load_workspace_file_content(attachment_file_id).text,
                "# Attachment Note\n\nFindable attachment context. [[Live]]\n",
            )
            self.assertEqual(service.search_workspace("findable").results[0].file_id, attachment_file_id)

            links = service.load_workspace_note_links(attachment_file_id)
            self.assertEqual(links.outgoing_count, 1)
            self.assertEqual(links.outgoing[0].target_file_id, "file-live")
            self.assertEqual(links.backlink_count, 0)

            renamed = service.rename_workspace_note(
                attachment_file_id,
                "Renamed Attachment.md",
                now_ms=1770000033000,
            )
            self.assertEqual(renamed.file.path, "Attachments/Renamed Attachment.md")
            self.assertFalse((root / "Attachments" / "Attachment Note.md").exists())
            self.assertTrue((root / "Attachments" / "Renamed Attachment.md").exists())

            moved = service.move_workspace_note(
                attachment_file_id,
                "Notes/Renamed Attachment.md",
                now_ms=1770000034000,
            )
            self.assertEqual(moved.file.file_id, attachment_file_id)
            self.assertEqual(moved.file.path, "Notes/Renamed Attachment.md")
            self.assertFalse((root / "Attachments" / "Renamed Attachment.md").exists())
            self.assertTrue((root / "Notes" / "Renamed Attachment.md").exists())

            content = service.write_workspace_file_content(
                attachment_file_id,
                "# Renamed Attachment\n\nEdited markdown attachment index token.\n",
            )
            self.assertEqual(content.path, "Notes/Renamed Attachment.md")
            self.assertIn("Edited markdown attachment", content.text)
            result = service.search_workspace("token")
            self.assertEqual(result.total_count, 1)
            self.assertEqual(result.results[0].file_id, attachment_file_id)

    def test_non_markdown_attachment_does_not_enter_note_editing_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(
                root,
                file_id_builder=lambda path: "gen-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8],
            )
            created = service.create_workspace_attachment(
                "photo.png",
                b"\x89PNG\r\n\x1a\n",
                now_ms=1770000032000,
            )

            with self.assertRaisesRegex(ValueError, "not editable Markdown"):
                service.rename_workspace_note(created.file.file_id, "photo.md", now_ms=1770000033000)
            with self.assertRaisesRegex(ValueError, "not movable Markdown"):
                service.move_workspace_note(created.file.file_id, "Notes/photo.md", now_ms=1770000034000)

            self.assertTrue((root / "Attachments" / "photo.png").exists())

    def test_delete_restore_workspace_attachment_preserves_trash_and_tombstone(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(
                root,
                file_id_builder=lambda path: "gen-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8],
            )
            created = service.create_workspace_attachment(
                "photo.png",
                b"\x89PNG\r\n\x1a\n",
                now_ms=1770000032000,
            )
            attachment_file_id = created.file.file_id

            deleted = service.delete_workspace_note(attachment_file_id, now_ms=1770000033000)

            self.assertEqual(deleted.file.status, "deleted")
            self.assertEqual(deleted.file.type, "attachment")
            self.assertFalse((root / "Attachments" / "photo.png").exists())
            trash_path = root / ".noteapp" / "trash" / f"1770000033000-{attachment_file_id}.png"
            self.assertEqual(trash_path.read_bytes(), b"\x89PNG\r\n\x1a\n")
            tombstone = load_tombstone_ledger(service.workspace.paths.ledger_path)[-1]
            self.assertEqual(tombstone.file_id, attachment_file_id)
            self.assertIsNone(tombstone.deleted_revision)
            trash = service.list_workspace_trash()
            self.assertEqual(trash.total_count, 1)
            self.assertEqual(trash.items[0].type, "attachment")
            self.assertEqual(trash.items[0].path, "Attachments/photo.png")

            restored = service.restore_workspace_trash_item(attachment_file_id, now_ms=1770000034000)

            self.assertEqual(restored.file.status, "active")
            self.assertEqual(restored.file.type, "attachment")
            self.assertEqual((root / "Attachments" / "photo.png").read_bytes(), b"\x89PNG\r\n\x1a\n")
            self.assertEqual(load_tombstone_ledger(service.workspace.paths.ledger_path), [])

    def test_import_existing_workspace_files_repairs_local_only_stale_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service = build_desktop_sync_service(
                DesktopSyncHttpConfig(
                    base_url="https://sync.example.com",
                    vault_id="vault-001",
                    device_id="desktop-shanghai",
                ),
                root,
                file_id_builder=lambda path: "file-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8],
                allow_placeholder_crypto=True,
            )
            service.ensure_initialized(now_ms=1770000030000)
            nested_path = root / "二级目录" / "中文路径.md"
            nested_path.parent.mkdir()
            nested_path.write_bytes("# 中文标题\n".encode("gb18030"))
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                FileMapDocument(
                    vault_id="vault-001",
                    updated_at=1770000030100,
                    files=[
                        FileRecord(
                            file_id="file-stale",
                            path="乱码路径.md",
                            type="note",
                            status="active",
                            updated_at=1770000030100,
                        )
                    ],
                ),
            )

            snapshot = service.import_existing_workspace_files_if_empty()

            self.assertEqual(snapshot.total_count, 1)
            self.assertEqual(snapshot.files[0].path, "二级目录/中文路径.md")
            self.assertTrue(snapshot.files[0].exists_on_disk)
            document = load_filemap(service.workspace.paths.filemap_path)
            self.assertEqual(document.files[0].path, "二级目录/中文路径.md")

    def test_load_workspace_file_content_returns_utf8_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, payload, _ = self._seed_workspace(root)

            content = service.load_workspace_file_content("file-live")

            self.assertEqual(content.schema_version, "v1")
            self.assertEqual(content.vault_id, "vault-001")
            self.assertEqual(content.device_id, "desktop-shanghai")
            self.assertEqual(content.vault_root, root)
            self.assertEqual(content.file_id, "file-live")
            self.assertEqual(content.path, "Notes/Live.md")
            self.assertEqual(content.type, "note")
            self.assertEqual(content.status, "active")
            self.assertEqual(content.size_bytes, len(payload))
            self.assertEqual(content.encoding, "utf-8")
            self.assertEqual(content.text, payload.decode("utf-8"))
            self.assertEqual(content.content_hash, "sha256:" + hashlib.sha256(payload).hexdigest())
            self.assertEqual(content.tracked_content_hash, content.content_hash)

    def test_load_workspace_file_content_allows_local_edit_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            edited_payload = b"# Local edit\n"
            (root / "Notes" / "Live.md").write_bytes(edited_payload)

            content = service.load_workspace_file_content("file-live")

            self.assertEqual(content.text, edited_payload.decode("utf-8"))
            self.assertEqual(content.content_hash, "sha256:" + hashlib.sha256(edited_payload).hexdigest())
            self.assertNotEqual(content.tracked_content_hash, content.content_hash)

    def test_load_workspace_file_content_returns_gb18030_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            gb_payload = "# 中文标题\n\n本地内容\n".encode("gb18030")
            live_path = root / "Notes" / "Live.md"
            live_path.write_bytes(gb_payload)

            content = service.load_workspace_file_content("file-live")

            self.assertEqual(content.encoding, "gb18030")
            self.assertEqual(content.text, "# 中文标题\n\n本地内容\n")

    def test_write_workspace_file_content_preserves_gb18030_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            live_path = root / "Notes" / "Live.md"
            live_path.write_bytes("# 中文标题\n".encode("gb18030"))

            content = service.write_workspace_file_content("file-live", "# 已编辑\n")

            self.assertEqual(content.encoding, "gb18030")
            self.assertEqual(live_path.read_bytes(), "# 已编辑\n".encode("gb18030"))

    def test_load_workspace_file_content_rejects_unsupported_text_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            binary_payload = b"\xff\xfe\xfd"
            live_path = root / "Notes" / "Live.md"
            live_path.write_bytes(binary_payload)
            binary_mtime_ms = live_path.stat().st_mtime_ns // 1_000_000
            snapshot = service.load_snapshot()
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                snapshot.document.replace_files(
                    [
                        replace(
                            snapshot.document.files[0],
                            updated_at=binary_mtime_ms,
                            content_hash="sha256:" + hashlib.sha256(binary_payload).hexdigest(),
                            meta={
                                "blob_id": "blob-live",
                                "size": len(binary_payload),
                                "mtime": binary_mtime_ms,
                                "mime_type": "application/octet-stream",
                            },
                        )
                    ],
                    updated_at=1770000030200,
                ),
            )

            with self.assertRaisesRegex(ValueError, "workspace file is not valid UTF-8 or GB18030 text: file-live"):
                service.load_workspace_file_content("file-live")

    def test_write_workspace_file_content_writes_disk_without_updating_filemap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, original_payload, _ = self._seed_workspace(root)
            original_snapshot = service.load_snapshot()
            original_hash = original_snapshot.document.files[0].content_hash

            content = service.write_workspace_file_content("file-live", "# Edited\n")

            self.assertEqual((root / "Notes" / "Live.md").read_text(encoding="utf-8"), "# Edited\n")
            self.assertEqual(content.text, "# Edited\n")
            self.assertNotEqual(content.content_hash, "sha256:" + hashlib.sha256(original_payload).hexdigest())
            self.assertEqual(content.tracked_content_hash, original_hash)
            reloaded = service.load_snapshot()
            self.assertEqual(reloaded.document.files[0].content_hash, original_hash)
            changes = service.detect_local_changes()
            self.assertEqual(changes.modified_file_ids, ["file-live"])

    def test_write_workspace_file_content_rejects_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))

            with self.assertRaisesRegex(KeyError, "file_id not found in workspace filemap: missing"):
                service.write_workspace_file_content("missing", "# Missing\n")

    def test_workspace_file_draft_round_trips_and_clears(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            missing = service.load_workspace_file_draft("file-live")
            self.assertFalse(missing.has_draft)
            self.assertIsNone(missing.text)

            draft = service.write_workspace_file_draft("file-live", "# Draft\n")

            self.assertTrue(draft.has_draft)
            self.assertEqual(draft.text, "# Draft\n")
            self.assertEqual((root / ".noteapp" / "drafts" / "file-live.draft").read_text(encoding="utf-8"), "# Draft\n")

            cleared = service.clear_workspace_file_draft("file-live")
            self.assertFalse(cleared.has_draft)
            self.assertFalse((root / ".noteapp" / "drafts" / "file-live.draft").exists())

    def test_workspace_file_draft_does_not_create_cloud_file_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, api_opener, blob_opener, _, _ = self._seed_workspace(root)

            draft = service.write_workspace_file_draft("file-live", "# Meeting draft autosave\n")

            self.assertTrue(draft.has_draft)
            self.assertEqual(
                (root / ".noteapp" / "drafts" / "file-live.draft").read_text(encoding="utf-8"),
                "# Meeting draft autosave\n",
            )
            self.assertEqual(api_opener.calls, [])
            self.assertEqual(blob_opener.calls, [])

    def test_ai_writeback_preview_insert_current_note_does_not_write_draft_or_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            request = {
                "schema_version": "v1",
                "idempotency_key": "writeback-1",
                "mode": "insert_current_note",
                "answer_markdown": "Generated answer.",
                "instruction": "Summarize the current note",
                "sources": [
                    {
                        "file_id": "file-live",
                        "path": "Notes/Live.md",
                        "title": "Live",
                        "excerpt": "# Live note",
                    }
                ],
                "target": {
                    "type": "current_note",
                    "file_id": "file-live",
                    "insert_position": "append",
                },
            }

            preview = service.preview_ai_writeback(request, now_ms=1770000040000)

            self.assertEqual(preview.mode, "insert_current_note")
            self.assertEqual(preview.target_path, "Notes/Live.md")
            self.assertEqual(preview.before_hash, "sha256:" + hashlib.sha256(b"# Live note\n").hexdigest())
            self.assertTrue(preview.confirmation_token.startswith("aiwb1."))
            self.assertIn("## AI Generated -", preview.rendered_markdown)
            self.assertIn("Generated answer.", preview.rendered_markdown)
            self.assertIn("- [[Live]] (`Notes/Live.md`)", preview.rendered_markdown)
            self.assertIn("+## AI Generated -", preview.diff.text)
            self.assertFalse(preview.warnings)
            self.assertEqual((root / "Notes" / "Live.md").read_text(encoding="utf-8"), "# Live note\n")
            self.assertFalse((root / ".noteapp" / "drafts" / "file-live.draft").exists())

    def test_ai_writeback_apply_insert_current_note_writes_draft_only_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            request = {
                "schema_version": "v1",
                "idempotency_key": "writeback-apply-1",
                "mode": "insert_current_note",
                "answer_markdown": "Draft-only AI answer.",
                "sources": [
                    {
                        "file_id": "file-live",
                        "path": "Notes/Live.md",
                        "title": "Live",
                    }
                ],
                "target": {
                    "type": "current_note",
                    "file_id": "file-live",
                    "insert_position": "append",
                },
            }
            preview = service.preview_ai_writeback(request, now_ms=1770000040000)

            result = service.apply_ai_writeback(
                {**request, "confirmation_token": preview.confirmation_token},
                now_ms=1770000040001,
            )

            draft_text = (root / ".noteapp" / "drafts" / "file-live.draft").read_text(encoding="utf-8")
            self.assertEqual(result.status, "applied")
            self.assertEqual(result.file_id, "file-live")
            self.assertEqual(result.path, "Notes/Live.md")
            self.assertEqual(result.content_hash, preview.after_hash)
            self.assertTrue(result.wrote_draft)
            self.assertFalse(result.wrote_file)
            self.assertFalse(result.search_index_refreshed)
            self.assertTrue(result.requires_user_save)
            self.assertIn("Draft-only AI answer.", draft_text)
            self.assertEqual(draft_text.count("Draft-only AI answer."), 1)
            self.assertEqual((root / "Notes" / "Live.md").read_text(encoding="utf-8"), "# Live note\n")

            retry = service.apply_ai_writeback(
                {**request, "confirmation_token": preview.confirmation_token},
                now_ms=1770000040002,
            )

            self.assertEqual(retry.status, "already_applied")
            self.assertFalse(retry.wrote_draft)
            self.assertEqual((root / ".noteapp" / "drafts" / "file-live.draft").read_text(encoding="utf-8"), draft_text)

    def test_ai_writeback_apply_rejects_changed_base_after_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            request = {
                "schema_version": "v1",
                "idempotency_key": "writeback-base-change",
                "mode": "insert_current_note",
                "answer_markdown": "AI answer.",
                "sources": [
                    {
                        "file_id": "file-live",
                        "path": "Notes/Live.md",
                    }
                ],
                "target": {
                    "type": "current_note",
                    "file_id": "file-live",
                    "insert_position": "append",
                },
            }
            preview = service.preview_ai_writeback(request, now_ms=1770000040000)
            service.write_workspace_file_draft("file-live", "# User draft changed first\n")

            with self.assertRaisesRegex(ValueError, "ai_writeback_base_changed"):
                service.apply_ai_writeback(
                    {**request, "confirmation_token": preview.confirmation_token},
                    now_ms=1770000040001,
                )

    def test_ai_writeback_insert_current_note_rejects_locked_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            (root / "Notes" / "Live.md").write_text("---\nlocked: true\n---\n# Live note\n", encoding="utf-8")
            request = {
                "schema_version": "v1",
                "idempotency_key": "writeback-locked",
                "mode": "insert_current_note",
                "answer_markdown": "AI answer.",
                "sources": [
                    {
                        "file_id": "file-live",
                        "path": "Notes/Live.md",
                    }
                ],
                "target": {
                    "type": "current_note",
                    "file_id": "file-live",
                    "insert_position": "append",
                },
            }

            with self.assertRaisesRegex(ValueError, "ai_writeback_target_locked"):
                service.preview_ai_writeback(request, now_ms=1770000040000)

    def test_ai_writeback_preview_create_note_does_not_write_file_or_filemap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            request = {
                "schema_version": "v1",
                "idempotency_key": "writeback-create-preview",
                "mode": "create_note",
                "answer_markdown": "Knowledge page body.",
                "instruction": "Save a knowledge note",
                "sources": [
                    {
                        "file_id": "file-live",
                        "path": "Notes/Live.md",
                        "title": "Live",
                    }
                ],
                "target": {
                    "type": "new_note",
                    "title": "Knowledge Summary",
                },
            }

            preview = service.preview_ai_writeback(request, now_ms=1770000040000)

            self.assertEqual(preview.mode, "create_note")
            self.assertEqual(preview.target_path, "AI Notes/2026-02-02 Knowledge Summary.md")
            self.assertIsNone(preview.before_hash)
            self.assertIn("type: ai_generated_note", preview.rendered_markdown)
            self.assertIn("source_count: 1", preview.rendered_markdown)
            self.assertIn("# Knowledge Summary", preview.rendered_markdown)
            self.assertIn("Knowledge page body.", preview.rendered_markdown)
            self.assertIn("+type: ai_generated_note", preview.diff.text)
            self.assertFalse((root / "AI Notes" / "2026-02-02 Knowledge Summary.md").exists())
            self.assertNotIn(
                "AI Notes/2026-02-02 Knowledge Summary.md",
                [record.path for record in load_filemap(service.workspace.paths.filemap_path).files],
            )

    def test_ai_writeback_apply_create_note_writes_filemap_and_search_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            request = {
                "schema_version": "v1",
                "idempotency_key": "writeback-create-apply",
                "mode": "create_note",
                "answer_markdown": "Durable generated knowledge.",
                "sources": [
                    {
                        "file_id": "file-live",
                        "path": "Notes/Live.md",
                        "title": "Live",
                    }
                ],
                "target": {
                    "type": "new_note",
                    "target_path": "AI Notes/Generated.md",
                    "title": "Generated",
                },
            }
            preview = service.preview_ai_writeback(request, now_ms=1770000040000)

            result = service.apply_ai_writeback(
                {**request, "confirmation_token": preview.confirmation_token},
                now_ms=1770000040001,
            )

            written_path = root / "AI Notes" / "Generated.md"
            written_text = written_path.read_text(encoding="utf-8")
            self.assertEqual(result.status, "applied")
            self.assertEqual(result.path, "AI Notes/Generated.md")
            self.assertTrue(result.wrote_file)
            self.assertFalse(result.wrote_draft)
            self.assertTrue(result.search_index_refreshed)
            self.assertFalse(result.requires_user_save)
            self.assertEqual(result.content_hash, preview.after_hash)
            self.assertIn("user_edited: false", written_text)
            self.assertIn("locked: false", written_text)
            self.assertIn("Durable generated knowledge.", written_text)
            record = next(
                item
                for item in load_filemap(service.workspace.paths.filemap_path).files
                if item.path == "AI Notes/Generated.md"
            )
            self.assertEqual(record.file_id, result.file_id)
            self.assertEqual(record.type, "note")
            search = service.search_workspace("Durable", limit=5)
            self.assertEqual(search.results[0].path, "AI Notes/Generated.md")

            retry = service.apply_ai_writeback(
                {**request, "confirmation_token": preview.confirmation_token},
                now_ms=1770000040002,
            )

            self.assertEqual(retry.status, "already_applied")
            self.assertFalse(retry.wrote_file)

    def test_ai_writeback_create_note_uses_suffix_instead_of_overwriting_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            existing_path = root / "AI Notes" / "Generated.md"
            existing_path.parent.mkdir(parents=True, exist_ok=True)
            existing_path.write_text(
                "---\ntype: ai_generated_note\nuser_edited: true\nlocked: false\n---\n# Existing\n",
                encoding="utf-8",
            )
            request = {
                "schema_version": "v1",
                "idempotency_key": "writeback-create-suffix",
                "mode": "create_note",
                "answer_markdown": "Replacement should not overwrite.",
                "sources": [
                    {
                        "file_id": "file-live",
                        "path": "Notes/Live.md",
                    }
                ],
                "target": {
                    "type": "new_note",
                    "target_path": "AI Notes/Generated.md",
                },
            }

            preview = service.preview_ai_writeback(request, now_ms=1770000040000)
            result = service.apply_ai_writeback(
                {**request, "confirmation_token": preview.confirmation_token},
                now_ms=1770000040001,
            )

            self.assertEqual(preview.target_path, "AI Notes/Generated-2.md")
            self.assertEqual(result.path, "AI Notes/Generated-2.md")
            self.assertTrue((root / "AI Notes" / "Generated-2.md").exists())
            self.assertIn("user_edited: true", existing_path.read_text(encoding="utf-8"))

    def test_ai_writeback_create_note_rejects_reserved_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))
            request = {
                "schema_version": "v1",
                "idempotency_key": "writeback-create-reserved",
                "mode": "create_note",
                "answer_markdown": "AI answer.",
                "sources": [],
                "target": {
                    "type": "new_note",
                    "target_path": ".ai/wiki/Generated.md",
                },
            }

            with self.assertRaisesRegex(ValueError, "ai_writeback_invalid_request"):
                service.preview_ai_writeback(request, now_ms=1770000040000)

    def test_write_workspace_file_content_clears_existing_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            service.write_workspace_file_draft("file-live", "# Draft\n")

            service.write_workspace_file_content("file-live", "# Saved\n")

            self.assertFalse((root / ".noteapp" / "drafts" / "file-live.draft").exists())

    def test_create_rename_delete_workspace_note_updates_filemap_and_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            created = service.create_workspace_note(
                "Notes/New Note.md",
                text="# New\n",
                now_ms=1770000031000,
            )

            self.assertEqual(created.operation, "create")
            self.assertTrue((root / "Notes" / "New Note.md").exists())
            self.assertEqual(created.file.path, "Notes/New Note.md")
            created_file_id = created.file.file_id
            self.assertIn(created_file_id, [record.file_id for record in load_filemap(service.workspace.paths.filemap_path).files])

            renamed = service.rename_workspace_note(
                created_file_id,
                "Renamed Note.md",
                now_ms=1770000032000,
            )

            self.assertEqual(renamed.file.path, "Notes/Renamed Note.md")
            self.assertFalse((root / "Notes" / "New Note.md").exists())
            self.assertEqual((root / "Notes" / "Renamed Note.md").read_text(encoding="utf-8"), "# New\n")

            deleted = service.delete_workspace_note(created_file_id, now_ms=1770000033000)

            self.assertEqual(deleted.file.status, "deleted")
            self.assertFalse((root / "Notes" / "Renamed Note.md").exists())
            trash_path = root / ".noteapp" / "trash" / f"1770000033000-{created_file_id}.md"
            self.assertTrue(trash_path.exists())

            self.assertEqual(trash_path.read_text(encoding="utf-8"), "# New\n")
            self.assertEqual(load_tombstone_ledger(service.workspace.paths.ledger_path)[-1].file_id, created_file_id)
            self.assertEqual(service.load_snapshot().state.local_delete_sequence, 2)

            trash = service.list_workspace_trash()
            self.assertEqual(trash.total_count, 1)
            self.assertEqual(trash.items[0].file_id, created_file_id)
            self.assertTrue(trash.items[0].exists_in_trash)

            restored = service.restore_workspace_trash_item(created_file_id, now_ms=1770000034000)
            self.assertEqual(restored.file.status, "active")
            self.assertTrue((root / "Notes" / "Renamed Note.md").exists())
            self.assertEqual(load_tombstone_ledger(service.workspace.paths.ledger_path), [])
            self.assertEqual(service.list_workspace_trash().total_count, 0)

            service.delete_workspace_note(created_file_id, now_ms=1770000035000)
            trash_path = root / ".noteapp" / "trash" / f"1770000035000-{created_file_id}.md"
            trash_path.unlink()
            purged = service.purge_workspace_trash_item(created_file_id)
            self.assertEqual(purged.total_count, 0)
            purged_record = next(
                record
                for record in load_filemap(service.workspace.paths.filemap_path).files
                if record.file_id == created_file_id
            )
            self.assertEqual(purged_record.status, "deleted")
            self.assertIn("trash_purged_at", purged_record.meta or {})
            self.assertEqual(load_tombstone_ledger(service.workspace.paths.ledger_path)[-1].file_id, created_file_id)

    def test_create_workspace_note_allows_workspace_root_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            created = service.create_workspace_note(
                "Root Note.md",
                text="# Root\n",
                now_ms=1770000034000,
            )

            self.assertEqual(created.operation, "create")
            self.assertTrue((root / "Root Note.md").exists())
            self.assertEqual(created.file.path, "Root Note.md")

    def test_rename_workspace_note_rejects_folder_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            with self.assertRaisesRegex(ValueError, "only supports changing the file name"):
                service.rename_workspace_note("file-live", "Archive/Live.md", now_ms=1770000032000)

            self.assertTrue((root / "Notes" / "Live.md").exists())

    def test_move_workspace_note_allows_folder_moves_and_preserves_file_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            source_path = root / "Notes" / "Source.md"
            source_path.write_text("See [[Live]].\n", encoding="utf-8")
            document = load_filemap(service.workspace.paths.filemap_path)
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                document.replace_files(
                    [
                        *document.files,
                        FileRecord(
                            file_id="file-source",
                            path="Notes/Source.md",
                            type="note",
                            status="active",
                            updated_at=1770000031000,
                        ),
                    ],
                    updated_at=1770000031000,
                ),
            )

            moved = service.move_workspace_note(
                "file-live",
                "Archive/Meetings/Live.md",
                now_ms=1770000032500,
            )

            self.assertEqual(moved.operation, "move")
            self.assertEqual(moved.file.file_id, "file-live")
            self.assertEqual(moved.file.path, "Archive/Meetings/Live.md")
            self.assertFalse((root / "Notes" / "Live.md").exists())
            self.assertEqual(
                (root / "Archive" / "Meetings" / "Live.md").read_text(encoding="utf-8"),
                "# Live note\n",
            )
            records = {record.file_id: record for record in load_filemap(service.workspace.paths.filemap_path).files}
            self.assertEqual(records["file-live"].path, "Archive/Meetings/Live.md")
            links = service.load_workspace_note_links("file-source")
            self.assertEqual(links.outgoing[0].target_file_id, "file-live")
            self.assertEqual(links.outgoing[0].target_path, "Archive/Meetings/Live.md")
            search = service.search_workspace("Live")
            self.assertEqual(search.results[0].file_id, "file-live")
            self.assertEqual(search.results[0].path, "Archive/Meetings/Live.md")

    def test_move_workspace_note_rejects_path_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            occupied_path = root / "Archive" / "Occupied.md"
            occupied_path.parent.mkdir(parents=True, exist_ok=True)
            occupied_path.write_text("# Occupied\n", encoding="utf-8")

            with self.assertRaisesRegex(FileExistsError, "already exists on disk"):
                service.move_workspace_note("file-live", "Archive/Occupied.md", now_ms=1770000032500)

            self.assertTrue((root / "Notes" / "Live.md").exists())

    def test_submit_workspace_commit_after_move_keeps_manifest_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, api_opener, _, _, encrypted_payload = self._seed_workspace(root)

            service.move_workspace_note(
                "file-live",
                "Archive/Meetings/Live.md",
                now_ms=1770000032500,
            )
            result = service.submit_workspace_commit(
                created_at=1770000032600,
                file_ids=["file-live"],
                encrypted_blob_by_file_id={"file-live": encrypted_payload},
                commit_intent_id="intent-move",
            )

            commit_body = next(
                call[2]
                for call in api_opener.calls
                if call[0] == "POST" and call[1].endswith("/vaults/vault-001/commits")
            )
            self.assertEqual(result.network.commit.status, "committed")
            self.assertIsInstance(commit_body, dict)
            manifest = commit_body["manifest"]
            self.assertEqual(manifest["tombstones"], [])
            self.assertEqual(
                [
                    {
                        "file_id": item["file_id"],
                        "path": item["path"],
                        "type": item["type"],
                    }
                    for item in manifest["files"]
                ],
                [
                    {
                        "file_id": "file-live",
                        "path": "Archive/Meetings/Live.md",
                        "type": "note",
                    }
                ],
            )
            self.assertEqual(commit_body["blob_refs"][0]["file_id"], "file-live")
            self.assertEqual(load_tombstone_ledger(service.workspace.paths.ledger_path), [])
            records = {record.file_id: record for record in load_filemap(service.workspace.paths.filemap_path).files}
            self.assertEqual(records["file-live"].path, "Archive/Meetings/Live.md")
            self.assertEqual(records["file-live"].last_known_revision, 8)
            self.assertFalse((root / "Notes" / "Live.md").exists())
            self.assertEqual(service.detect_local_changes().change_count, 0)

    def test_submit_detected_commit_after_attachment_delete_emits_tombstone(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, api_opener, _, _, _ = self._seed_workspace(
                root,
                file_id_builder=lambda path: "gen-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8],
            )
            created = service.create_workspace_attachment(
                "photo.png",
                b"\x89PNG\r\n\x1a\n",
                now_ms=1770000032000,
            )
            attachment_file_id = created.file.file_id
            service.submit_detected_changes(
                created_at=1770000032500,
                commit_intent_id="intent-attachment-create",
            )
            service.delete_workspace_note(attachment_file_id, now_ms=1770000033000)
            self.assertIsNone(load_tombstone_ledger(service.workspace.paths.ledger_path)[-1].deleted_revision)

            result = service.submit_detected_changes(
                created_at=1770000033500,
                commit_intent_id="intent-attachment-delete",
            )

            commit_bodies = [
                call[2]
                for call in api_opener.calls
                if call[0] == "POST" and call[1].endswith("/vaults/vault-001/commits")
            ]
            delete_manifest = commit_bodies[-1]["manifest"]
            self.assertEqual(result.network.commit.status, "committed")
            self.assertEqual(
                [item["file_id"] for item in delete_manifest["tombstones"]],
                [attachment_file_id],
            )
            self.assertEqual(delete_manifest["tombstones"][0]["last_known_path"], "Attachments/photo.png")
            self.assertFalse(any(item["file_id"] == attachment_file_id for item in delete_manifest["files"]))
            records = {record.file_id: record for record in load_filemap(service.workspace.paths.filemap_path).files}
            self.assertEqual(records[attachment_file_id].status, "deleted")
            self.assertEqual(records[attachment_file_id].last_known_revision, 9)

    def test_rename_workspace_note_rewrites_wiki_links_to_new_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            source_path = root / "Notes" / "Source.md"
            source_path.write_text(
                "See [[Live]] and [[Live#Details|the details]].\n",
                encoding="utf-8",
            )
            document = load_filemap(service.workspace.paths.filemap_path)
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                document.replace_files(
                    [
                        *document.files,
                        FileRecord(
                            file_id="file-source",
                            path="Notes/Source.md",
                            type="note",
                            status="active",
                            updated_at=1770000031000,
                        ),
                    ],
                    updated_at=1770000031000,
                ),
            )

            service.rename_workspace_note("file-live", "Renamed.md", now_ms=1770000032000)

            self.assertEqual(
                source_path.read_text(encoding="utf-8"),
                "See [[Renamed]] and [[Renamed#Details|the details]].\n",
            )
            links = service.load_workspace_note_links("file-source")
            self.assertEqual(links.outgoing[0].target_file_id, "file-live")
            self.assertEqual(links.outgoing[0].target_path, "Notes/Renamed.md")

    def test_search_workspace_queries_existing_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            (root / "Notes" / "Live.md").write_text(
                "# Searchable\n\nLocal first knowledge base content\n",
                encoding="utf-8",
            )
            service.rebuild_workspace_search_index()

            result = service.search_workspace("knowledge")

            self.assertEqual(result.schema_version, "v1")
            self.assertEqual(result.query, "knowledge")
            self.assertEqual(result.total_count, 1)
            self.assertEqual(result.results[0].file_id, "file-live")
            self.assertEqual(result.results[0].path, "Notes/Live.md")

    def test_write_workspace_file_content_updates_search_index_incrementally(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            service.write_workspace_file_content("file-live", "# Searchable\n\nIncremental index content\n")

            result = service.search_workspace("incremental")

            self.assertEqual(result.total_count, 1)
            self.assertEqual(result.results[0].file_id, "file-live")

    def test_search_workspace_skips_binary_or_missing_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            (root / "Notes" / "Live.md").write_bytes(b"\xff\xfe\xfd")

            result = service.search_workspace("anything")

            self.assertEqual(result.total_count, 0)

    def test_compile_ai_wiki_writes_generated_pages_and_filemap_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(
                root,
                file_id_builder=lambda path: "gen-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8],
            )
            (root / "Notes" / "Live.md").write_text(
                "# Live Note\n\nLocal first knowledge base content.\n\n## Links\nSee [[Second Brain]].\n",
                encoding="utf-8",
            )

            result = service.compile_ai_wiki(now_ms=1770000045000)

            self.assertEqual(result.source_count, 1)
            self.assertEqual(result.artifact_count, 1)
            self.assertEqual(result.index_path, ".ai/index.md")
            self.assertTrue((root / ".ai" / "index.md").exists())
            self.assertTrue((root / ".ai" / "log.md").exists())
            artifact = result.artifacts[0]
            artifact_path = root.joinpath(*PurePosixPath(artifact.path).parts)
            self.assertTrue(artifact_path.exists())
            artifact_text = artifact_path.read_text(encoding="utf-8")
            self.assertIn("Local first knowledge base content.", artifact_text)
            self.assertIn("[[Second Brain]]", artifact_text)
            filemap = load_filemap(service.workspace.paths.filemap_path)
            records_by_path = {record.path: record for record in filemap.files}
            self.assertEqual(records_by_path[".ai/index.md"].type, "ai_index")
            self.assertEqual(records_by_path[artifact.path].type, "ai_wiki")
            self.assertTrue(records_by_path[artifact.path].meta["ai_generated"])

            unchanged = service.compile_ai_wiki(now_ms=1770000046000)

            self.assertEqual(unchanged.written_count, 1)
            self.assertEqual(unchanged.skipped_count, 1)
            self.assertEqual(unchanged.skipped[0].reason, "unchanged")
            self.assertIn("Skipped", (root / ".ai" / "log.md").read_text(encoding="utf-8"))

    def test_compile_ai_wiki_does_not_overwrite_locked_or_user_edited_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(
                root,
                file_id_builder=lambda path: "gen-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8],
            )
            (root / "Notes" / "Live.md").write_text("# Live Note\n\nSource body.\n", encoding="utf-8")
            first = service.compile_ai_wiki(now_ms=1770000045000)
            artifact = first.artifacts[0]
            artifact_path = root.joinpath(*PurePosixPath(artifact.path).parts)
            locked_text = artifact_path.read_text(encoding="utf-8").replace(
                "locked: false",
                "locked: true",
            ) + "\nManual edit\n"
            artifact_path.write_text(locked_text, encoding="utf-8")
            (root / "Notes" / "Live.md").write_text("# Live Note\n\nChanged source.\n", encoding="utf-8")

            second = service.compile_ai_wiki(now_ms=1770000047000)

            self.assertEqual(second.skipped_count, 1)
            self.assertEqual(second.skipped[0].reason, "locked")
            self.assertIn("Manual edit", artifact_path.read_text(encoding="utf-8"))

    def test_answer_ai_wiki_returns_local_citations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(
                root,
                file_id_builder=lambda path: "gen-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8],
            )
            (root / "Notes" / "Live.md").write_text(
                "# Live Note\n\nKnowledge base notes explain local search.\n",
                encoding="utf-8",
            )
            service.compile_ai_wiki(now_ms=1770000045000)

            answer = service.answer_ai_wiki("knowledge search")

            self.assertEqual(answer.schema_version, "v1")
            self.assertEqual(answer.citation_count, 1)
            self.assertEqual(answer.citations[0].title, "Live Note")
            self.assertIn("Knowledge base", answer.answer)

    def test_answer_ai_wiki_uses_openai_compatible_provider_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ai_opener = RecordingAiOpener(answer="Local search is covered in the wiki [1].")
            service, _, _, _, _ = self._seed_workspace(
                root,
                file_id_builder=lambda path: "gen-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8],
            )
            service = replace(service, ai_opener=ai_opener)
            (root / "Notes" / "Live.md").write_text(
                "# Live Note\n\nKnowledge base notes explain local search.\n",
                encoding="utf-8",
            )
            service.compile_ai_wiki(now_ms=1770000045000)

            with mock.patch.dict(
                os.environ,
                {
                    "NOTEAPP_AI_BASE_URL": "https://llm.example.com/v1",
                    "NOTEAPP_AI_API_KEY": "test-key",
                    "NOTEAPP_AI_MODEL": "test-model",
                    "NOTEAPP_AI_TIMEOUT_SECONDS": "12.5",
                },
                clear=False,
            ):
                answer = service.answer_ai_wiki("knowledge search")

            self.assertEqual(answer.answer, "Local search is covered in the wiki [1].")
            self.assertEqual(answer.model_status, "openai-completions:test-model")
            self.assertEqual(len(ai_opener.calls), 1)
            method, url, payload, timeout, authorization = ai_opener.calls[0]
            self.assertEqual(method, "POST")
            self.assertEqual(url, "https://llm.example.com/v1/chat/completions")
            self.assertEqual(timeout, 12.5)
            self.assertEqual(authorization, "Bearer test-key")
            self.assertEqual(payload["model"], "test-model")
            self.assertIn("Knowledge base", payload["messages"][1]["content"])

    def test_answer_ai_wiki_uses_local_settings_provider_before_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ai_opener = RecordingAiOpener(answer="Settings provider answer [1].")
            service, _, _, _, _ = self._seed_workspace(
                root,
                file_id_builder=lambda path: "gen-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8],
            )
            service = replace(service, ai_opener=ai_opener)
            service.write_local_settings(
                {
                    "appearance": {"theme": "dark"},
                    "ai": {
                        "local_model_status": "available",
                        "embedding_status": "not_configured",
                        "provider_api": "openai-completions",
                        "base_url": "https://ai-settings.example.test/v1",
                        "model_id": "glm-5_codingplan",
                        "api_key": "settings-key",
                    },
                }
            )
            (root / "Notes" / "Live.md").write_text(
                "# Live Note\n\nKnowledge base notes explain local search.\n",
                encoding="utf-8",
            )
            service.compile_ai_wiki(now_ms=1770000045000)

            with mock.patch.dict(
                os.environ,
                {
                    "NOTEAPP_AI_API_KEY": "env-key",
                    "NOTEAPP_AI_MODEL": "env-model",
                },
                clear=False,
            ):
                answer = service.answer_ai_wiki("knowledge search")

            self.assertEqual(answer.answer, "Settings provider answer [1].")
            self.assertEqual(answer.model_status, "openai-completions:glm-5_codingplan")
            self.assertEqual(len(ai_opener.calls), 1)
            _, url, payload, _, authorization = ai_opener.calls[0]
            self.assertEqual(url, "https://ai-settings.example.test/v1/chat/completions")
            self.assertEqual(authorization, "Bearer settings-key")
            self.assertEqual(payload["model"], "glm-5_codingplan")

    def test_answer_ai_wiki_falls_back_when_provider_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ai_opener = RecordingAiOpener(status_code=500)
            service, _, _, _, _ = self._seed_workspace(
                root,
                file_id_builder=lambda path: "gen-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8],
            )
            service = replace(service, ai_opener=ai_opener)
            (root / "Notes" / "Live.md").write_text(
                "# Live Note\n\nKnowledge base notes explain local search.\n",
                encoding="utf-8",
            )
            service.compile_ai_wiki(now_ms=1770000045000)

            with mock.patch.dict(
                os.environ,
                {
                    "NOTEAPP_AI_API_KEY": "test-key",
                    "NOTEAPP_AI_MODEL": "test-model",
                },
                clear=False,
            ):
                answer = service.answer_ai_wiki("knowledge search")

            self.assertEqual(answer.model_status, "openai_compatible_error_fallback")
            self.assertIn("deterministic local answer", answer.answer)

    def test_answer_ai_wiki_marks_configured_provider_without_citations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            service.write_local_settings(
                {
                    "appearance": {"theme": "dark"},
                    "ai": {
                        "local_model_status": "available",
                        "embedding_status": "not_configured",
                        "provider_api": "openai-completions",
                        "base_url": "https://ai-settings.example.test/v1",
                        "model_id": "glm-5_codingplan",
                        "api_key": "settings-key",
                    },
                }
            )

            answer = service.answer_ai_wiki("no matching citations")

            self.assertEqual(answer.citation_count, 0)
            self.assertEqual(answer.model_status, "openai-completions:glm-5_codingplan:no_citations")

    def test_check_ai_provider_health_returns_not_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))

            health = service.check_ai_provider_health()

            self.assertFalse(health.configured)
            self.assertEqual(health.status, "not_configured")
            self.assertIsNone(health.model_id)

    def test_check_ai_provider_health_calls_configured_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ai_opener = RecordingAiOpener(answer="OK")
            service, _, _, _, _ = self._seed_workspace(root)
            service = replace(service, ai_opener=ai_opener)
            service.write_local_settings(
                {
                    "appearance": {"theme": "dark"},
                    "ai": {
                        "local_model_status": "available",
                        "embedding_status": "not_configured",
                        "provider_api": "openai-completions",
                        "base_url": "https://ai-settings.example.test/v1",
                        "model_id": "glm-5_codingplan",
                        "api_key": "settings-key",
                    },
                }
            )

            health = service.check_ai_provider_health()

            self.assertTrue(health.configured)
            self.assertEqual(health.status, "available")
            self.assertEqual(health.provider_api, "openai-completions")
            self.assertEqual(health.model_id, "glm-5_codingplan")
            self.assertEqual(len(ai_opener.calls), 1)

    def test_check_ai_provider_health_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ai_opener = RecordingAiOpener(status_code=500)
            service, _, _, _, _ = self._seed_workspace(root)
            service = replace(service, ai_opener=ai_opener)
            service.write_local_settings(
                {
                    "appearance": {"theme": "dark"},
                    "ai": {
                        "local_model_status": "available",
                        "embedding_status": "not_configured",
                        "provider_api": "openai-completions",
                        "base_url": "https://ai-settings.example.test/v1",
                        "model_id": "glm-5_codingplan",
                        "api_key": "settings-key",
                    },
                }
            )

            health = service.check_ai_provider_health()

            self.assertTrue(health.configured)
            self.assertEqual(health.status, "error")
            self.assertIn("unexpected status", health.message)

    def test_ai_context_task_uses_folder_markdown_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ai_opener = RecordingAiOpener(answer="Folder answer [1].")
            service, _, _, _, _ = self._seed_workspace(root)
            service = replace(service, ai_opener=ai_opener)
            second_path = root / "Notes" / "Specs" / "Plan.md"
            second_path.parent.mkdir(parents=True, exist_ok=True)
            second_path.write_text("# Plan\n\nFolder context content.\n", encoding="utf-8")
            document = load_filemap(service.workspace.paths.filemap_path)
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                replace(
                    document,
                    files=[
                        *document.files,
                        FileRecord(
                            file_id="file-plan",
                            path="Notes/Specs/Plan.md",
                            type="note",
                            status="active",
                            updated_at=1770000040000,
                            content_hash="sha256:plan",
                        ),
                    ],
                ),
            )
            service.write_local_settings(
                {
                    "appearance": {"theme": "dark"},
                    "ai": {
                        "provider_api": "openai-completions",
                        "base_url": "https://llm.example.com/v1",
                        "model_id": "test-model",
                        "api_key": "settings-key",
                    },
                }
            )

            result = service.run_ai_context_task(
                context_type="folder",
                folder_path="Notes/Specs",
                instruction="Summarize these docs",
            )

            self.assertEqual(result.answer, "Folder answer [1].")
            self.assertEqual(result.source_count, 1)
            self.assertEqual(result.sources[0].path, "Notes/Specs/Plan.md")
            self.assertEqual(result.model_status, "openai-completions:test-model")
            self.assertIn("Folder context content", ai_opener.calls[0][2]["messages"][1]["content"])

    def test_ai_context_task_can_use_markdown_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(
                root,
                file_id_builder=lambda path: "gen-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8],
            )
            created = service.create_workspace_attachment(
                "Context Attachment.md",
                b"# Context Attachment\n\nAttachment context source text.\n",
                now_ms=1770000032000,
            )

            result = service.run_ai_context_task(
                context_type="selected_files",
                file_ids=[created.file.file_id],
                instruction="Use this attachment",
            )

            self.assertEqual(result.model_status, "not_configured_context_preview")
            self.assertEqual(result.source_count, 1)
            self.assertEqual(result.sources[0].path, "Attachments/Context Attachment.md")
            self.assertIn("Attachment context source text", result.sources[0].excerpt)

    def test_ai_context_task_extracts_text_docx_and_pdf_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(
                root,
                file_id_builder=lambda path: "gen-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8],
            )
            text_file = service.create_workspace_attachment(
                "context.txt",
                b"Plain text context token.\n",
                now_ms=1770000032000,
            )
            json_file = service.create_workspace_attachment(
                "context.json",
                b'{"message": "JSON context token"}\n',
                now_ms=1770000032100,
            )
            docx_file = service.create_workspace_attachment(
                "context.docx",
                self._build_docx_payload(["DOCX context token."]),
                now_ms=1770000032200,
            )
            pdf_file = service.create_workspace_attachment(
                "context.pdf",
                b"%PDF-1.4\n1 0 obj <<>> stream\nBT /F1 12 Tf 72 720 Td (PDF context token.) Tj ET\nendstream\nendobj\n%%EOF",
                now_ms=1770000032300,
            )

            result = service.run_ai_context_task(
                context_type="selected_files",
                file_ids=[
                    text_file.file.file_id,
                    json_file.file.file_id,
                    docx_file.file.file_id,
                    pdf_file.file.file_id,
                ],
                instruction="Find context tokens",
                max_total_chars=20000,
            )

            excerpts = "\n".join(source.excerpt for source in result.sources)
            self.assertEqual(result.source_count, 4)
            self.assertIn("Plain text context token", excerpts)
            self.assertIn("JSON context token", excerpts)
            self.assertIn("DOCX context token", excerpts)
            self.assertIn("PDF context token", excerpts)

    def test_ai_context_task_uses_cached_extraction_for_unchanged_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(
                root,
                file_id_builder=lambda path: "gen-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8],
            )
            created = service.create_workspace_attachment(
                "cached.docx",
                self._build_docx_payload(["Cached DOCX context token."]),
                now_ms=1770000032000,
            )

            first = service.run_ai_context_task(
                context_type="selected_files",
                file_ids=[created.file.file_id],
                instruction="Use cache",
            )
            second = service.run_ai_context_task(
                context_type="selected_files",
                file_ids=[created.file.file_id],
                instruction="Use cache",
            )

            cache_path = root / ".noteapp" / "ai-context-cache" / f"{created.file.file_id}.json"
            self.assertTrue(cache_path.exists())
            self.assertIn("Cached DOCX context token", first.sources[0].excerpt)
            self.assertEqual(second.sources[0].excerpt, first.sources[0].excerpt)

    def test_ai_context_task_selected_files_respects_configurable_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            (root / "Notes" / "Live.md").write_text("# Live note\n\nabcdef", encoding="utf-8")

            result = service.run_ai_context_task(
                context_type="selected_files",
                file_ids=["file-live"],
                instruction="Use this context",
                max_chars_per_file=8,
                max_total_chars=8,
            )

            self.assertEqual(result.model_status, "not_configured_context_preview")
            self.assertEqual(result.source_count, 1)
            self.assertEqual(result.sources[0].excerpt, "# Live n")
            self.assertTrue(result.truncation.truncated)
            self.assertIn("local context preview", result.answer)

    def test_ai_context_task_selected_files_can_retrieve_relevant_late_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            (root / "Notes" / "Live.md").write_text(
                "# Live note\n\n"
                + ("intro " * 40)
                + "\n\n## API Notes\n\nThe context task returns the selected document answer.\n",
                encoding="utf-8",
            )

            result = service.run_ai_context_task(
                context_type="selected_files",
                file_ids=["file-live"],
                instruction="What does the context task return?",
                max_chars_per_file=80,
                max_total_chars=80,
            )

            self.assertEqual(result.truncation.included_file_count, 1)
            self.assertTrue(
                any("returns the selected document answer" in source.excerpt for source in result.sources)
            )
            self.assertTrue(result.truncation.truncated)

    def test_load_workspace_note_links_resolves_outgoing_and_backlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            (root / "Notes" / "Target.md").write_text("# Target\n\n[[Live]]\n", encoding="utf-8")
            (root / "Notes" / "Live.md").write_text("# Live\n\n[[Target]] and [[Missing]]\n", encoding="utf-8")
            snapshot = service.load_snapshot()
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                snapshot.document.replace_files(
                    [
                        snapshot.document.files[0],
                        FileRecord(
                            file_id="file-target",
                            path="Notes/Target.md",
                            type="note",
                            status="active",
                            updated_at=1770000030200,
                        ),
                    ],
                    updated_at=1770000030200,
                ),
            )

            links = service.load_workspace_note_links("file-live")

            self.assertEqual(links.file_id, "file-live")
            self.assertEqual(links.outgoing_count, 2)
            self.assertEqual(links.outgoing[0].link_text, "Target")
            self.assertEqual(links.outgoing[0].target_file_id, "file-target")
            self.assertEqual(links.outgoing[1].target_file_id, None)
            self.assertEqual(links.backlink_count, 1)
            self.assertEqual(links.backlinks[0].source_file_id, "file-target")

    def test_export_vault_package_excludes_runtime_state_and_optional_raw(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "vault"
            service, _, _, _, _ = self._seed_workspace(root)
            (root / ".ai" / "wiki" / "Topic.md").write_text("# Topic\n", encoding="utf-8")
            (root / ".ai" / "raw" / "capture.txt").write_text("raw capture", encoding="utf-8")
            (root / ".ai" / "log.md").write_text("local log", encoding="utf-8")
            (root / ".noteapp" / "conflict-orphans" / "Orphan.md").write_text(
                "orphan conflict",
                encoding="utf-8",
            )
            (root / ".noteapp" / "drafts" / "scratch.md").write_text("draft", encoding="utf-8")
            (root / ".noteapp" / "staging" / "file-live.staging").write_text("staging", encoding="utf-8")
            (root / ".noteapp" / "staging-orphans" / "leftover.staging").write_text(
                "staging orphan",
                encoding="utf-8",
            )
            service.workspace.paths.worker_state_path.write_text("{}", encoding="utf-8")
            service.workspace.paths.sync_apply_plan_path.write_text("{}", encoding="utf-8")

            package_path = Path(tmpdir) / "vault-export.zip"
            result = service.export_vault_package(package_path, include_ai_raw=False)

            self.assertEqual(result.package_path, package_path.resolve())
            self.assertFalse(result.included_ai_raw)
            self.assertTrue(result.included_conflict_orphans)
            with ZipFile(package_path, "r") as archive:
                exported_paths = sorted(
                    info.filename for info in archive.infolist() if not info.is_dir()
                )
            self.assertIn(".vaultinfo", exported_paths)
            self.assertIn(".noteapp/filemap.json", exported_paths)
            self.assertIn(".noteapp/tombstone-ledger.jsonl", exported_paths)
            self.assertIn("Notes/Live.md", exported_paths)
            self.assertIn(".ai/wiki/Topic.md", exported_paths)
            self.assertIn(".noteapp/conflict-orphans/Orphan.md", exported_paths)
            self.assertNotIn(".ai/raw/capture.txt", exported_paths)
            self.assertNotIn(".ai/log.md", exported_paths)
            self.assertNotIn(".noteapp/state.sqlite3", exported_paths)
            self.assertNotIn(".noteapp/sync-worker-state.json", exported_paths)
            self.assertNotIn(".noteapp/sync-apply-plan.json", exported_paths)
            self.assertNotIn(".noteapp/drafts/scratch.md", exported_paths)
            self.assertNotIn(".noteapp/staging/file-live.staging", exported_paths)
            self.assertNotIn(".noteapp/staging-orphans/leftover.staging", exported_paths)

    def test_import_vault_package_restores_workspace_and_marks_state_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            source_root = tmp_path / "source"
            source_service, _, _, _, _ = self._seed_workspace(source_root)
            (source_root / ".ai" / "raw" / "capture.txt").write_text("raw capture", encoding="utf-8")
            (source_root / ".noteapp" / "conflict-orphans" / "Orphan.md").write_text(
                "orphan conflict",
                encoding="utf-8",
            )
            append_tombstone(
                source_service.workspace.paths.ledger_path,
                TombstoneRecord(
                    file_id="file-deleted",
                    deleted_revision=7,
                    deleted_at=1770000040400,
                    local_delete_seq=4,
                    last_known_path="Notes/Deleted.md",
                    deleted_by_device="desktop-shanghai",
                ),
            )
            package_path = tmp_path / "vault-export.zip"
            source_service.export_vault_package(package_path, include_ai_raw=True)

            target_root = tmp_path / "target"
            target_service = build_desktop_sync_service(
                DesktopSyncHttpConfig(
                    base_url="https://sync.example.com",
                    vault_id="vault-001",
                    device_id="desktop-shanghai",
                ),
                target_root,
                allow_placeholder_crypto=True,
            )

            result = target_service.import_vault_package(package_path)

            self.assertTrue(result.restored_ai_raw)
            self.assertTrue(result.restored_conflict_orphans)
            self.assertEqual(result.state.last_manifest_summary_status, "stale")
            self.assertIsNone(result.state.last_manifest_summary)
            self.assertEqual(result.state.last_applied_revision, 0)
            self.assertEqual(result.state.remote_head_revision, 0)
            self.assertEqual(result.state.acked_revision, 0)
            self.assertEqual(result.state.local_delete_sequence, 4)
            self.assertTrue(result.state.has_unresolved_conflicts)
            self.assertTrue((target_root / "Notes" / "Live.md").is_file())
            self.assertEqual(
                (target_root / ".ai" / "raw" / "capture.txt").read_text(encoding="utf-8"),
                "raw capture",
            )
            self.assertTrue((target_root / ".noteapp" / "conflict-orphans" / "Orphan.md").is_file())

            snapshot = target_service.load_snapshot()
            self.assertEqual(snapshot.document.vault_id, "vault-001")
            self.assertEqual(snapshot.state.last_manifest_summary_status, "stale")
            self.assertTrue(snapshot.state.has_unresolved_conflicts)
            with self.assertRaisesRegex(ValueError, "vault_state is not eligible to start a new commit"):
                target_service.submit_commit(
                    created_at=1770000040500,
                    content_by_file_id={"file-live": (target_root / "Notes" / "Live.md").read_bytes()},
                )

    def test_summarize_vault_aggregates_commit_gate_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            orphan_path = root / ".noteapp" / "conflict-orphans" / "Orphan.md"
            orphan_path.parent.mkdir(parents=True, exist_ok=True)
            orphan_path.write_text("orphan conflict", encoding="utf-8")
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                state = load_vault_state(connection, "vault-001")
                self.assertIsNotNone(state)
                upsert_vault_state(
                    connection,
                    replace(
                        state,
                        has_unresolved_conflicts=False,
                        last_manifest_summary=None,
                        last_manifest_summary_status="stale",
                    ),
                )
                upsert_sync_apply_journal(
                    connection,
                    SyncApplyJournalRecord(
                        vault_id="vault-001",
                        journal_id="journal-123",
                        target_revision=8,
                        target_manifest_hash="sha256:head8",
                        phase="staging",
                        created_at=1770000040600,
                        updated_at=1770000040600,
                        ops_hash="sha256:ops8",
                    ),
                )

            summary = service.summarize_vault()

            self.assertIsNone(summary.worker_health)
            self.assertFalse(summary.commit_gate.can_submit_commit)
            self.assertEqual(
                summary.commit_gate.blocking_reasons,
                [
                    "sync_apply_journal:staging",
                    "unresolved_conflicts",
                    "requires_full_pull",
                ],
            )
            self.assertTrue(summary.commit_gate.has_active_sync_apply_journal)
            self.assertFalse(summary.commit_gate.has_active_commit_journal)
            self.assertTrue(summary.commit_gate.requires_full_pull)
            self.assertTrue(summary.conflicts.actual_has_unresolved_conflicts)
            self.assertTrue(summary.state.has_unresolved_conflicts)
            self.assertEqual(summary.changes.change_count, 0)

    def test_build_sync_panel_model_prioritizes_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                upsert_sync_apply_journal(
                    connection,
                    SyncApplyJournalRecord(
                        vault_id="vault-001",
                        journal_id="journal-123",
                        target_revision=8,
                        target_manifest_hash="sha256:head8",
                        phase="materializing",
                        created_at=1770000040600,
                        updated_at=1770000040600,
                        ops_hash="sha256:ops8",
                    ),
                )

            panel = service.build_sync_panel_model(now_ms=1770000040999)

            self.assertEqual(panel.level, "danger")
            self.assertEqual(panel.primary_action.action_id, "recover-pull-apply")
            self.assertEqual(panel.primary_action.command, "recover-pull-apply")
            self.assertEqual(panel.primary_action.argv, ["--normalized-at", "1770000040999"])
            self.assertEqual(panel.conflict_badge_count, 0)
            self.assertEqual(panel.change_badge_count, 0)

    def test_build_sync_panel_model_prioritizes_conflicts_over_pending_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, payload, _ = self._seed_workspace(root)
            conflict_path = root / "Notes" / "Live (conflict 2026-04-29 Desktop-Win).md"
            conflict_path.write_bytes(payload + b" conflict")
            conflict_hash = "sha256:" + hashlib.sha256(conflict_path.read_bytes()).hexdigest()
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                FileMapDocument(
                    vault_id="vault-001",
                    updated_at=1770000040100,
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
                        ),
                        FileRecord(
                            file_id="file-conflict",
                            path="Notes/Live (conflict 2026-04-29 Desktop-Win).md",
                            type="note",
                            status="conflict_copy",
                            updated_at=1770000040100,
                            content_hash=conflict_hash,
                            conflict_source_file_id="file-live",
                        ),
                    ],
                ),
            )
            live_path = root / "Notes" / "Live.md"
            live_path.write_bytes(payload + b" modified")

            panel = service.build_sync_panel_model(now_ms=1770000040999)

            self.assertEqual(panel.level, "warning")
            self.assertEqual(panel.primary_action.action_id, "list-conflicts")
            self.assertEqual(panel.primary_action.command, "list-conflicts")
            self.assertEqual(panel.conflict_badge_count, 1)
            self.assertGreaterEqual(panel.change_badge_count, 1)
            self.assertEqual(panel.secondary_actions[-1].action_id, "resolve-conflicts-all")
            self.assertEqual(
                panel.secondary_actions[-1].argv,
                ["--resolved-at", "1770000040999", "--all"],
            )
            self.assertTrue(panel.secondary_actions[-1].requires_confirmation)

    def test_build_sync_panel_model_marks_pending_changes_ready_for_submit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, payload, _ = self._seed_workspace(root)
            live_path = root / "Notes" / "Live.md"
            live_path.write_bytes(payload + b" modified")

            panel = service.build_sync_panel_model(now_ms=1770000040999)

            self.assertEqual(panel.level, "info")
            self.assertEqual(panel.primary_action.action_id, "submit-detected-commit")
            self.assertTrue(panel.primary_action.enabled)
            self.assertEqual(panel.primary_action.command, "submit-detected-commit")
            self.assertEqual(panel.primary_action.argv, ["--created-at", "1770000040999"])
            self.assertGreaterEqual(panel.change_badge_count, 1)
            self.assertEqual(panel.conflict_badge_count, 0)

    def test_build_sync_center_model_collects_multiple_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, payload, _ = self._seed_workspace(root)
            conflict_path = root / "Notes" / "Live (conflict 2026-04-29 Desktop-Win).md"
            conflict_path.write_bytes(payload + b" conflict")
            conflict_hash = "sha256:" + hashlib.sha256(conflict_path.read_bytes()).hexdigest()
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                FileMapDocument(
                    vault_id="vault-001",
                    updated_at=1770000040100,
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
                        ),
                        FileRecord(
                            file_id="file-conflict",
                            path="Notes/Live (conflict 2026-04-29 Desktop-Win).md",
                            type="note",
                            status="conflict_copy",
                            updated_at=1770000040100,
                            content_hash=conflict_hash,
                            conflict_source_file_id="file-live",
                        ),
                    ],
                ),
            )
            live_path = root / "Notes" / "Live.md"
            live_path.write_bytes(payload + b" modified")
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                state = load_vault_state(connection, "vault-001")
                self.assertIsNotNone(state)
                upsert_vault_state(
                    connection,
                    replace(state, last_manifest_summary=None, last_manifest_summary_status="stale"),
                )

            center = service.build_sync_center_model(now_ms=1770000040999)

            self.assertEqual(center.panel.level, "warning")
            self.assertEqual(
                [card.card_id for card in center.cards],
                ["conflicts", "baseline", "local-changes"],
            )
            self.assertEqual(center.cards[0].actions[0].command, "list-conflicts")
            self.assertEqual(center.cards[1].actions[0].argv, ["--rewritten-at", "1770000040999"])
            self.assertEqual(center.cards[2].actions[1].argv, ["--created-at", "1770000040999"])

    def test_build_sync_center_model_returns_healthy_overview_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            center = service.build_sync_center_model(now_ms=1770000040999)

            self.assertEqual(len(center.cards), 1)
            self.assertEqual(center.cards[0].card_id, "healthy")
            self.assertEqual(center.cards[0].level, "success")
            self.assertEqual(center.cards[0].actions[0].argv, ["--rewritten-at", "1770000040999"])

    def test_execute_sync_action_returns_disabled_result_for_unavailable_worker_health(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            result = service.execute_sync_action("worker-health", now_ms=1770000040999)

            self.assertEqual(result.status, "disabled")
            self.assertEqual(result.action.action_id, "worker-health")
            self.assertIsNone(result.payload)
            self.assertEqual(result.message, "No worker state recorded yet")

    def test_execute_sync_action_can_run_resolve_conflicts_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, payload, _ = self._seed_workspace(root)
            conflict_path = root / "Notes" / "Live (conflict 2026-04-29 Desktop-Win).md"
            conflict_path.write_bytes(payload + b" conflict")
            conflict_hash = "sha256:" + hashlib.sha256(conflict_path.read_bytes()).hexdigest()
            orphan_path = root / ".noteapp" / "conflict-orphans" / "Orphan.md"
            orphan_path.parent.mkdir(parents=True, exist_ok=True)
            orphan_path.write_text("orphan conflict", encoding="utf-8")
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                FileMapDocument(
                    vault_id="vault-001",
                    updated_at=1770000040100,
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
                        ),
                        FileRecord(
                            file_id="file-conflict",
                            path="Notes/Live (conflict 2026-04-29 Desktop-Win).md",
                            type="note",
                            status="conflict_copy",
                            updated_at=1770000040100,
                            content_hash=conflict_hash,
                            conflict_source_file_id="file-live",
                        ),
                    ],
                ),
            )

            result = service.execute_sync_action("resolve-conflicts-all", now_ms=1770000040999)

            self.assertEqual(result.status, "executed")
            self.assertEqual(result.action.command, "resolve-conflicts")
            self.assertFalse(conflict_path.exists())
            self.assertFalse(orphan_path.exists())
            self.assertIsNotNone(result.payload)
            self.assertFalse(result.payload.state.has_unresolved_conflicts)

    def test_list_sync_activity_returns_recent_window_and_total_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            service.execute_sync_action("show-vault-summary", now_ms=1770000040901)
            service.execute_sync_action("worker-health", now_ms=1770000040902)

            feed = service.list_sync_activity(limit=1)

            self.assertEqual(feed.total_count, 2)
            self.assertEqual(len(feed.records), 1)
            self.assertEqual(feed.records[0].action_id, "worker-health")
            self.assertEqual(feed.records[0].status, "disabled")
            self.assertEqual(feed.records[0].level, "warning")
            self.assertEqual(feed.records[0].message, "No worker state recorded yet")

    def test_build_sync_center_model_includes_recent_activity_feed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            service.execute_sync_action("show-vault-summary", now_ms=1770000040901)

            center = service.build_sync_center_model(now_ms=1770000040999)

            self.assertEqual([card.card_id for card in center.cards], ["healthy", "activity"])
            self.assertEqual(center.recent_activity.total_count, 1)
            self.assertEqual(len(center.recent_activity.records), 1)
            self.assertEqual(center.recent_activity.records[0].action_id, "show-vault-summary")
            self.assertEqual(center.recent_activity.records[0].status, "executed")
            self.assertEqual(center.cards[-1].actions[0].action_id, "sync-activity")
            self.assertEqual(center.cards[-1].actions[0].command, "sync-activity")

    def test_build_sync_shell_snapshot_includes_metadata_and_requested_activity_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            service.execute_sync_action("show-vault-summary", now_ms=1770000040901)
            service.execute_sync_action("sync-activity", now_ms=1770000040902)

            snapshot = service.build_sync_shell_snapshot(
                now_ms=1770000040999,
                activity_limit=1,
            )

            self.assertEqual(snapshot.generated_at_ms, 1770000040999)
            self.assertEqual(snapshot.vault_id, "vault-001")
            self.assertEqual(snapshot.device_id, "desktop-shanghai")
            self.assertEqual(snapshot.vault_root, root)
            self.assertEqual(snapshot.sync_center.panel.level, "success")
            self.assertEqual(snapshot.sync_center.recent_activity.total_count, 2)
            self.assertEqual(snapshot.activity_feed.total_count, 2)
            self.assertEqual(len(snapshot.activity_feed.records), 1)
            self.assertEqual(snapshot.activity_feed.records[0].action_id, "sync-activity")

    def test_execute_sync_action_records_unsupported_activity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            action = service._build_panel_action(
                action_id="open-shell-debug",
                label="Open Shell Debug",
                command="open-shell-debug",
            )

            with mock.patch.object(type(service), "_find_sync_action", return_value=(action, "panel")):
                result = service.execute_sync_action("open-shell-debug", now_ms=1770000040903)

            self.assertEqual(result.status, "unsupported")
            feed = service.list_sync_activity(limit=5)
            self.assertEqual(feed.total_count, 1)
            self.assertEqual(feed.records[0].action_id, "open-shell-debug")
            self.assertEqual(feed.records[0].status, "unsupported")
            self.assertEqual(feed.records[0].level, "danger")
            self.assertEqual(
                feed.records[0].message,
                "unsupported sync action command: open-shell-debug",
            )

    def test_execute_sync_action_can_return_sync_activity_feed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            service.execute_sync_action("show-vault-summary", now_ms=1770000040901)

            result = service.execute_sync_action("sync-activity", now_ms=1770000040902)

            self.assertEqual(result.status, "executed")
            self.assertEqual(result.action.command, "sync-activity")
            self.assertEqual(result.payload.total_count, 1)
            self.assertEqual(len(result.payload.records), 1)
            self.assertEqual(result.payload.records[0].action_id, "show-vault-summary")

            feed = service.list_sync_activity(limit=5)
            self.assertEqual(feed.total_count, 2)
            self.assertEqual(feed.records[-1].action_id, "sync-activity")

    def test_execute_sync_action_records_failed_activity_before_reraising(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            with mock.patch.object(type(service), "pull_and_apply", side_effect=RuntimeError("network down")):
                with self.assertRaisesRegex(RuntimeError, "network down"):
                    service.execute_sync_action("pull", now_ms=1770000040904)

            feed = service.list_sync_activity(limit=5)
            self.assertEqual(feed.total_count, 1)
            self.assertEqual(feed.records[0].action_id, "pull")
            self.assertEqual(feed.records[0].status, "failed")
            self.assertEqual(feed.records[0].level, "danger")
            self.assertEqual(feed.records[0].message, "RuntimeError: network down")

    def test_execute_sync_action_and_snapshot_returns_updated_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            result = service.execute_sync_action_and_snapshot(
                "show-vault-summary",
                now_ms=1770000040905,
                activity_limit=1,
            )

            self.assertEqual(result.execution.status, "executed")
            self.assertEqual(result.execution.action.action_id, "show-vault-summary")
            self.assertEqual(result.snapshot.generated_at_ms, 1770000040905)
            self.assertEqual(result.snapshot.vault_id, "vault-001")
            self.assertEqual(result.snapshot.activity_feed.total_count, 1)
            self.assertEqual(len(result.snapshot.activity_feed.records), 1)
            self.assertEqual(
                result.snapshot.activity_feed.records[0].action_id,
                "show-vault-summary",
            )

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

    def test_load_local_settings_snapshot_returns_defaults_without_settings_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            snapshot = service.load_local_settings_snapshot()

            self.assertEqual(snapshot.schema_version, "v1")
            self.assertEqual(snapshot.source, "default")
            self.assertEqual(snapshot.settings_path, root / ".noteapp" / "settings.json")
            self.assertEqual(snapshot.vault_root, root)
            self.assertEqual(snapshot.vault_id, "vault-001")
            self.assertEqual(snapshot.device_id, "desktop-shanghai")
            self.assertEqual(snapshot.sync.base_url, "https://sync.example.com")
            self.assertFalse(snapshot.sync.bearer_token_configured)
            self.assertEqual(snapshot.appearance.theme, "dark")
            self.assertEqual(snapshot.ai.local_model_status, "not_configured")
            self.assertEqual(snapshot.ai.embedding_status, "not_configured")
            self.assertEqual(snapshot.ai.provider_api, "openai-completions")
            self.assertEqual(snapshot.ai.model_id, "gpt-4o-mini")
            self.assertFalse(snapshot.ai.api_key_configured)
            self.assertIsNone(snapshot.ai.api_key)
            self.assertEqual(snapshot.crypto.schema_version, "crypto-v1")
            self.assertEqual(snapshot.crypto.crypto_scheme, "e2ee-v1")
            self.assertFalse(snapshot.crypto.unlocked)
            self.assertFalse(snapshot.crypto.key_available)

    def test_load_local_settings_snapshot_uses_env_ai_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))

            with mock.patch.dict(
                os.environ,
                {
                    "NOTEAPP_LOCAL_SETTINGS_DEFAULT_AI_PROVIDER_API": "google-generative-ai",
                    "NOTEAPP_LOCAL_SETTINGS_DEFAULT_AI_BASE_URL": "https://ai-default.example.test",
                    "NOTEAPP_LOCAL_SETTINGS_DEFAULT_AI_MODEL": "gemini-test",
                },
                clear=False,
            ):
                snapshot = service.load_local_settings_snapshot()

            self.assertEqual(snapshot.ai.provider_api, "google-generative-ai")
            self.assertEqual(snapshot.ai.base_url, "https://ai-default.example.test")
            self.assertEqual(snapshot.ai.model_id, "gemini-test")

    def test_load_local_settings_snapshot_reads_local_settings_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            settings_path = root / ".noteapp" / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "appearance": {"theme": "light"},
                        "ai": {
                            "local_model_status": "available",
                            "embedding_status": "indexing",
                        },
                    }
                ),
                encoding="utf-8",
            )

            snapshot = service.load_local_settings_snapshot()

            self.assertEqual(snapshot.source, "file")
            self.assertEqual(snapshot.appearance.theme, "light")
            self.assertEqual(snapshot.ai.local_model_status, "available")
            self.assertEqual(snapshot.ai.embedding_status, "indexing")
            self.assertEqual(snapshot.ai.provider_api, "openai-completions")
            self.assertEqual(snapshot.ai.model_id, "gpt-4o-mini")

    def test_load_local_settings_snapshot_defaults_removed_system_theme(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            settings_path = root / ".noteapp" / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "appearance": {"theme": "system"},
                    }
                ),
                encoding="utf-8",
            )

            snapshot = service.load_local_settings_snapshot()

            self.assertEqual(snapshot.source, "file")
            self.assertEqual(snapshot.appearance.theme, "dark")

    def test_write_local_settings_normalizes_and_returns_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            snapshot = service.write_local_settings(
                {
                    "appearance": {"theme": "light"},
                    "ai": {
                        "local_model_status": "disabled",
                        "embedding_status": "ready",
                        "provider_api": "openai-completions",
                        "base_url": "https://ai-settings.example.test/v1",
                        "model_id": "glm-5_codingplan",
                        "api_key": "local-key",
                    },
                }
            )

            self.assertEqual(snapshot.source, "file")
            self.assertEqual(snapshot.appearance.theme, "light")
            self.assertEqual(snapshot.ai.local_model_status, "disabled")
            self.assertEqual(snapshot.ai.embedding_status, "ready")
            self.assertEqual(snapshot.ai.provider_api, "openai-completions")
            self.assertEqual(snapshot.ai.model_id, "glm-5_codingplan")
            self.assertTrue(snapshot.ai.api_key_configured)
            self.assertEqual(snapshot.ai.api_key, "local-key")
            self.assertEqual(
                json.loads((root / ".noteapp" / "settings.json").read_text(encoding="utf-8")),
                {
                    "schema_version": "v1",
                    "appearance": {"theme": "light"},
                    "ai": {
                        "local_model_status": "disabled",
                        "embedding_status": "ready",
                        "provider_api": "openai-completions",
                        "base_url": "https://ai-settings.example.test/v1",
                        "model_id": "glm-5_codingplan",
                        "api_key": "local-key",
                    },
                },
            )

    def test_write_local_settings_rejects_unsupported_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))

            with self.assertRaisesRegex(ValueError, "local settings contains unsupported keys: sync"):
                service.write_local_settings(
                    {
                        "sync": {"base_url": "https://evil.example.com"},
                    }
                )

    def test_write_local_settings_rejects_unsupported_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))

            with self.assertRaisesRegex(ValueError, "appearance.theme is not supported: sepia"):
                service.write_local_settings(
                    {
                        "appearance": {"theme": "sepia"},
                    }
                )

            with self.assertRaisesRegex(ValueError, "appearance.theme is not supported: system"):
                service.write_local_settings(
                    {
                        "appearance": {"theme": "system"},
                    }
                )

    def test_write_local_settings_rejects_unsupported_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))

            with self.assertRaisesRegex(ValueError, "schema_version is not supported: v2"):
                service.write_local_settings(
                    {
                        "schema_version": "v2",
                        "appearance": {"theme": "dark"},
                        "ai": {
                            "local_model_status": "not_configured",
                            "embedding_status": "not_configured",
                        },
                    }
                )

    def test_local_settings_schema_enums_match_desktop_writer(self) -> None:
        schema_path = Path(__file__).resolve().parents[3] / "packages" / "protocol" / "schemas" / "local-settings.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertEqual(
            set(schema["properties"]["appearance"]["properties"]["theme"]["enum"]),
            _LOCAL_SETTINGS_THEMES,
        )
        self.assertEqual(
            set(schema["properties"]["ai"]["properties"]["local_model_status"]["enum"]),
            _LOCAL_SETTINGS_MODEL_STATUSES,
        )
        self.assertEqual(
            set(schema["properties"]["ai"]["properties"]["embedding_status"]["enum"]),
            _LOCAL_SETTINGS_EMBEDDING_STATUSES,
        )
        self.assertEqual(
            set(schema["properties"]["ai"]["properties"]["provider_api"]["enum"]),
            _LOCAL_SETTINGS_AI_PROVIDER_APIS,
        )

    @unittest.skipUnless(is_e2ee_crypto_available(), "PyNaCl is not installed")
    def test_crypto_recovery_package_export_and_import_unlocks_local_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "vault"
            crypto_store_root = Path(tmpdir) / "crypto-store"
            service, _, _, _, _ = self._seed_workspace(
                root,
                blob_crypto_provider=E2EEDesktopBlobCryptoProvider(
                    vault_id="vault-001",
                    vault_key=b"\x0a" * 32,
                ),
            )
            with mock.patch.dict(
                os.environ,
                {
                    "NOTEAPP_ALLOW_INSECURE_CRYPTO_STORE": "true",
                    "NOTEAPP_CRYPTO_STORE_DIR": str(crypto_store_root),
                },
                clear=False,
            ):
                exported = service.export_crypto_recovery_package(
                    recovery_phrase="meeting archive recovery",
                    created_at=1770000040000,
                    memory_kib=8,
                    iterations=1,
                )

                service.import_crypto_recovery_package(
                    exported.recovery_package,
                    recovery_phrase="meeting archive recovery",
                )
                snapshot = service.load_local_settings_snapshot()

        self.assertEqual(exported.schema_version, "e2ee-recovery-export-v1")
        self.assertEqual(exported.recovery_package["schema_version"], "e2ee-recovery-v1")
        self.assertEqual(exported.vault_id, "vault-001")
        self.assertTrue(snapshot.crypto.unlocked)
        self.assertEqual(snapshot.crypto.crypto_scheme, "e2ee-v1")

    @unittest.skipUnless(is_e2ee_crypto_available(), "PyNaCl is not installed")
    def test_crypto_recovery_package_import_rejects_wrong_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(
                Path(tmpdir),
                blob_crypto_provider=E2EEDesktopBlobCryptoProvider(
                    vault_id="vault-001",
                    vault_key=b"\x0b" * 32,
                ),
            )
            exported = service.export_crypto_recovery_package(
                recovery_phrase="correct phrase",
                created_at=1770000040000,
                memory_kib=8,
                iterations=1,
            )

            with self.assertRaisesRegex(ValueError, "did not decrypt"):
                service.import_crypto_recovery_package(
                    exported.recovery_package,
                    recovery_phrase="wrong phrase",
                )

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
            self.assertEqual(result.cleanup.state.remote_head_revision, 9)
            self.assertEqual(result.cleanup.state.last_manifest_summary_status, "stale")
            self.assertIsNone(result.cleanup.state.last_manifest_summary)
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
                self.assertEqual(state.remote_head_revision, 9)
                self.assertFalse(state.commit_in_progress)
                self.assertEqual(state.last_manifest_summary_status, "stale")

    def test_execute_sync_action_reports_submit_conflict_as_blocked_and_requires_pull(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, payload, _ = self._seed_workspace(root, conflict=True)
            (root / "Notes" / "Live.md").write_bytes(payload + b" local change")

            result = service.execute_sync_action("submit-detected-commit", now_ms=1770000040999)

            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.action.action_id, "submit-detected-commit")
            self.assertIn("base_revision_conflict", result.message or "")
            self.assertIn("remote head revision 9", result.message or "")

            summary = service.summarize_vault()
            self.assertTrue(summary.commit_gate.requires_full_pull)
            self.assertFalse(summary.commit_gate.can_submit_commit)
            self.assertIn("requires_full_pull", summary.commit_gate.blocking_reasons)

            feed = service.list_sync_activity(limit=5)
            self.assertEqual(feed.total_count, 1)
            self.assertEqual(feed.records[0].action_id, "submit-detected-commit")
            self.assertEqual(feed.records[0].status, "blocked")
            self.assertEqual(feed.records[0].level, "warning")
            self.assertIn("base_revision_conflict", feed.records[0].message or "")

    def test_submit_conflict_pull_action_reopens_clean_submit_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, api_opener, _, payload, _ = self._seed_workspace(root, conflict=True)
            live_path = root / "Notes" / "Live.md"
            live_path.write_bytes(payload + b" local change")

            conflicted = service.execute_sync_action("submit-detected-commit", now_ms=1770000040999)
            self.assertEqual(conflicted.status, "blocked")
            self.assertTrue(service.summarize_vault().commit_gate.requires_full_pull)

            pulled = service.execute_sync_action("pull", now_ms=1770000041999)

            self.assertEqual(pulled.status, "executed")
            summary = service.summarize_vault()
            self.assertFalse(summary.commit_gate.requires_full_pull)
            self.assertTrue(summary.commit_gate.can_submit_commit)
            self.assertEqual(summary.changes.change_count, 1)
            self.assertEqual(summary.changes.changes[0].kind, "modified")
            self.assertEqual(summary.state.last_applied_revision, 9)
            self.assertEqual(summary.state.last_manifest_summary_status, "valid")
            self.assertEqual(
                service.build_sync_panel_model(now_ms=1770000042000).primary_action.action_id,
                "submit-detected-commit",
            )

            api_opener.conflict = False
            submitted = service.execute_sync_action("submit-detected-commit", now_ms=1770000042999)

            self.assertEqual(submitted.status, "executed")
            self.assertEqual(service.detect_local_changes().change_count, 0)
            self.assertEqual(service.build_sync_panel_model(now_ms=1770000043000).level, "success")
            self.assertTrue(
                any(
                    call[0] == "GET" and call[1].endswith("/vaults/vault-001/manifests/9")
                    for call in api_opener.calls
                )
            )

    def test_execute_pull_action_downloads_and_applies_remote_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            remote_payload = b"# Remote note\n"
            service, api_opener, blob_opener, _, _ = self._seed_workspace(
                root,
                remote_payload=remote_payload,
            )
            remote_hash = "sha256:" + hashlib.sha256(remote_payload).hexdigest()
            remote_blob_id = build_placeholder_blob_id(remote_hash)

            result = service.execute_sync_action("pull", now_ms=1770000041999)

            self.assertEqual(result.status, "executed")
            self.assertEqual((root / "Notes" / "Live.md").read_bytes(), remote_payload)
            self.assertEqual(service.detect_local_changes().change_count, 0)
            filemap = load_filemap(service.workspace.paths.filemap_path)
            self.assertEqual(filemap.files[0].content_hash, remote_hash)
            self.assertEqual(filemap.files[0].last_known_revision, 9)
            self.assertTrue(
                any(
                    call[0] == "POST" and call[1].endswith("/vaults/vault-001/blobs/download-init")
                    for call in api_opener.calls
                )
            )
            self.assertEqual(
                [call[0:2] for call in blob_opener.calls if call[0] == "GET"],
                [("GET", f"https://blob.example.com/download/{remote_blob_id}")],
            )

    def test_execute_pull_action_preserves_dirty_local_file_as_conflict_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            remote_payload = b"# Remote note\n"
            service, _, _, payload, _ = self._seed_workspace(
                root,
                remote_payload=remote_payload,
            )
            live_path = root / "Notes" / "Live.md"
            local_dirty_payload = payload + b" local dirty\n"
            live_path.write_bytes(local_dirty_payload)
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                state = load_vault_state(connection, "vault-001")
                self.assertIsNotNone(state)
                upsert_vault_state(
                    connection,
                    replace(state, last_manifest_summary=None, last_manifest_summary_status="stale"),
                )

            result = service.execute_sync_action("pull", now_ms=1770000041999)
            snapshot = service.load_snapshot()
            conflict_records = [record for record in snapshot.document.files if record.status == "conflict_copy"]

            self.assertEqual(result.status, "executed")
            self.assertEqual(live_path.read_bytes(), remote_payload)
            self.assertTrue(snapshot.state.has_unresolved_conflicts)
            self.assertEqual(len(conflict_records), 1)
            self.assertEqual(conflict_records[0].conflict_source_file_id, "file-live")
            self.assertEqual((root / conflict_records[0].path).read_bytes(), local_dirty_payload)

    def test_pull_conflict_resolve_all_returns_sync_to_clear_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            remote_payload = b"# Remote note\n"
            service, _, _, payload, _ = self._seed_workspace(
                root,
                remote_payload=remote_payload,
            )
            live_path = root / "Notes" / "Live.md"
            live_path.write_bytes(payload + b" local dirty\n")
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                state = load_vault_state(connection, "vault-001")
                self.assertIsNotNone(state)
                upsert_vault_state(
                    connection,
                    replace(state, last_manifest_summary=None, last_manifest_summary_status="stale"),
                )
            service.execute_sync_action("pull", now_ms=1770000041999)
            self.assertGreater(service.build_sync_panel_model(now_ms=1770000042000).conflict_badge_count, 0)

            result = service.execute_sync_action("resolve-conflicts-all", now_ms=1770000042999)
            snapshot = service.build_sync_shell_snapshot(now_ms=1770000043000)

            self.assertEqual(result.status, "executed")
            self.assertFalse(result.payload.state.has_unresolved_conflicts)
            self.assertEqual(snapshot.sync_center.panel.level, "success")
            self.assertEqual(snapshot.sync_center.panel.conflict_badge_count, 0)
            self.assertEqual(snapshot.sync_center.panel.change_badge_count, 0)
            self.assertEqual(service.list_conflicts().conflict_copies, [])
            self.assertEqual(live_path.read_bytes(), remote_payload)

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

    def test_submit_workspace_commit_can_mark_manual_meeting_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, api_opener, _, _, _ = self._seed_workspace(Path(tmpdir))

            result = service.submit_workspace_commit(
                created_at=1770000030200,
                file_ids=["file-live"],
                commit_intent_id="intent-meeting-version-001",
                file_version_directives=[
                    FileVersionCommitDirective(
                        file_id="file-live",
                        source="manual_meeting_checkpoint",
                        version_label="2026-05-15 周会",
                        change_note="补充行动项",
                        is_pinned=True,
                    )
                ],
            )

            self.assertEqual(result.network.commit.status, "committed")
            commit_calls = [
                call
                for call in api_opener.calls
                if call[0] == "POST" and call[1].endswith("/vaults/vault-001/commits")
            ]
            self.assertEqual(len(commit_calls), 1)
            commit_body = commit_calls[0][2]
            self.assertEqual(
                commit_body["file_version_directives"],
                [
                    {
                        "file_id": "file-live",
                        "source": "manual_meeting_checkpoint",
                        "version_label": "2026-05-15 周会",
                        "change_note": "补充行动项",
                        "is_pinned": True,
                    }
                ],
            )

    @unittest.skipUnless(is_e2ee_crypto_available(), "PyNaCl is not installed")
    def test_submit_workspace_commit_with_e2ee_provider_uploads_ciphertext(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = E2EEDesktopBlobCryptoProvider(
                vault_id="vault-001",
                vault_key=b"\x03" * 32,
            )
            wrong_provider = E2EEDesktopBlobCryptoProvider(
                vault_id="vault-001",
                vault_key=b"\x04" * 32,
            )
            service, api_opener, blob_opener, payload, _ = self._seed_workspace(
                Path(tmpdir),
                blob_crypto_provider=provider,
            )
            content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
            blob_id = provider.build_blob_id(content_hash)

            result = service.submit_workspace_commit(
                created_at=1770000030200,
                file_ids=["file-live"],
                commit_intent_id="intent-e2ee-001",
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
                [("PUT", f"https://blob.example.com/upload/{blob_id}")],
            )
            encrypted_payload = blob_opener.calls[0][2]
            self.assertNotEqual(encrypted_payload[:-POLY1305_TAG_BYTES], payload)
            self.assertEqual(len(encrypted_payload), len(payload) + POLY1305_TAG_BYTES)
            self.assertEqual(
                provider.decrypt_payload(encrypted_payload, content_hash=content_hash),
                payload,
            )
            with self.assertRaisesRegex(ValueError, "authentication failed"):
                wrong_provider.decrypt_payload(encrypted_payload, content_hash=content_hash)

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

    def test_load_file_version_content_downloads_and_decrypts_blob(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            old_payload = b"# Live note\n\n- old meeting\n"
            old_hash = "sha256:" + hashlib.sha256(old_payload).hexdigest()
            old_blob_id = build_placeholder_blob_id(old_hash)
            service, api_opener, blob_opener, _, _ = self._seed_workspace(
                Path(tmpdir),
                file_versions={
                    "file-live": [
                        {
                            "version_id": "fv-old",
                            "file_id": "file-live",
                            "path_at_revision": "Notes/Live.md",
                            "revision": 6,
                            "content_hash": old_hash,
                            "blob_id": old_blob_id,
                            "size": len(old_payload),
                            "mtime": 1770000029000,
                            "created_at": 1770000030000,
                            "created_by_device": "desktop-shanghai",
                            "source": "manual_meeting_checkpoint",
                            "version_label": "meeting before edits",
                            "is_pinned": True,
                        }
                    ]
                },
                downloaded_blobs={
                    old_blob_id: build_placeholder_encrypted_blob_payload(old_payload),
                },
            )

            content = service.load_file_version_content(
                file_id="file-live",
                version_id="fv-old",
            )

            self.assertEqual(content.schema_version, "v1")
            self.assertEqual(content.version.version_id, "fv-old")
            self.assertEqual(content.content_hash, old_hash)
            self.assertEqual(content.text, "# Live note\n\n- old meeting\n")
            self.assertEqual(content.encoding, "utf-8")
            self.assertTrue(
                any(
                    call[0] == "POST" and call[1].endswith("/vaults/vault-001/file-versions/list")
                    for call in api_opener.calls
                )
            )
            self.assertEqual(
                [call[0:2] for call in blob_opener.calls if call[0] == "GET"],
                [("GET", f"https://blob.example.com/download/{old_blob_id}")],
            )

    def test_diff_file_version_with_current_returns_unified_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            old_payload = b"# Live note\n\n- old meeting\n"
            old_hash = "sha256:" + hashlib.sha256(old_payload).hexdigest()
            old_blob_id = build_placeholder_blob_id(old_hash)
            service, _, _, _, _ = self._seed_workspace(
                Path(tmpdir),
                file_versions={
                    "file-live": [
                        {
                            "version_id": "fv-old",
                            "file_id": "file-live",
                            "path_at_revision": "Notes/Live.md",
                            "revision": 6,
                            "content_hash": old_hash,
                            "blob_id": old_blob_id,
                            "size": len(old_payload),
                            "mtime": 1770000029000,
                            "created_at": 1770000030000,
                            "created_by_device": "desktop-shanghai",
                            "source": "manual_meeting_checkpoint",
                            "is_pinned": False,
                        }
                    ]
                },
                downloaded_blobs={
                    old_blob_id: build_placeholder_encrypted_blob_payload(old_payload),
                },
            )
            (Path(tmpdir) / "Notes" / "Live.md").write_bytes(b"# Live note\n\n- new meeting\n")

            diff = service.diff_file_version_with_current(
                file_id="file-live",
                version_id="fv-old",
            )

            self.assertFalse(diff.is_binary)
            self.assertIn("--- Notes/Live.md@r6", diff.diff_text)
            self.assertIn("+++ Notes/Live.md@current", diff.diff_text)
            self.assertIn("-- old meeting", diff.diff_text)
            self.assertIn("+- new meeting", diff.diff_text)

    def test_restore_file_version_writes_current_file_and_commits_restore_directive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            old_payload = b"# Restored meeting\n"
            old_hash = "sha256:" + hashlib.sha256(old_payload).hexdigest()
            old_blob_id = build_placeholder_blob_id(old_hash)
            service, api_opener, blob_opener, _, _ = self._seed_workspace(
                root,
                file_versions={
                    "file-live": [
                        {
                            "version_id": "fv-restore",
                            "file_id": "file-live",
                            "path_at_revision": "Archive/Live.md",
                            "revision": 5,
                            "content_hash": old_hash,
                            "blob_id": old_blob_id,
                            "size": len(old_payload),
                            "mtime": 1770000028000,
                            "created_at": 1770000029000,
                            "created_by_device": "desktop-shanghai",
                            "source": "manual_meeting_checkpoint",
                            "is_pinned": False,
                        }
                    ]
                },
                downloaded_blobs={
                    old_blob_id: build_placeholder_encrypted_blob_payload(old_payload),
                },
            )

            result = service.restore_file_version(
                file_id="file-live",
                version_id="fv-restore",
                created_at=1770000031200,
                commit_intent_id="intent-restore-001",
                version_label="restore checkpoint",
                change_note="restore meeting version",
                is_pinned=True,
            )

            self.assertEqual(result.version.version_id, "fv-restore")
            self.assertEqual((root / "Notes" / "Live.md").read_bytes(), old_payload)
            self.assertEqual(result.commit.network.commit.status, "committed")
            commit_calls = [
                call
                for call in api_opener.calls
                if call[0] == "POST" and call[1].endswith("/vaults/vault-001/commits")
            ]
            self.assertEqual(len(commit_calls), 1)
            commit_body = commit_calls[0][2]
            self.assertEqual(commit_body["commit_intent_id"], "intent-restore-001")
            self.assertEqual(commit_body["file_version_directives"][0]["file_id"], "file-live")
            self.assertEqual(commit_body["file_version_directives"][0]["source"], "restore")
            self.assertEqual(commit_body["file_version_directives"][0]["version_label"], "restore checkpoint")
            self.assertEqual(commit_body["file_version_directives"][0]["change_note"], "restore meeting version")
            self.assertTrue(commit_body["file_version_directives"][0]["is_pinned"])
            uploaded_payload = blob_opener.calls[-1][2]
            self.assertEqual(
                decrypt_placeholder_encrypted_blob_payload(
                    uploaded_payload,
                    content_hash=old_hash,
                ),
                old_payload,
            )

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

    def test_build_pull_required_blob_plan_includes_dirty_move_source_blob(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            move_source = root / "Notes" / "Move-old.md"
            move_source.write_bytes(b"# local dirty move\n")
            pull = self._build_pull_result(
                required_blob_ids=[],
                manifest_files=[
                    ManifestFileEntry(
                        file_id="file-move",
                        path="Notes/Move-new.md",
                        type="note",
                        content_hash="sha256:" + hashlib.sha256(b"# canonical move\n").hexdigest(),
                        blob_id="blob-move",
                        size=len(b"# canonical move\n"),
                        mtime=1770000030001,
                    )
                ],
            )
            apply_plan = DesktopPullApplyPlan(
                vault_id="vault-001",
                revision=8,
                writes=[],
                moves=[
                    DesktopPullApplyMoveFile(
                        file_id="file-move",
                        source_path="Notes/Move-old.md",
                        target_path="Notes/Move-new.md",
                        type="note",
                        content_hash="sha256:" + hashlib.sha256(b"# canonical move\n").hexdigest(),
                    )
                ],
                deletes=[],
                blocking_paths=[],
                ops_hash="sha256:ops8",
            )

            plan = service.build_pull_required_blob_plan(
                pull,
                apply_plan=apply_plan,
            )

            self.assertEqual(plan.blob_ids, ["blob-move"])
            self.assertEqual([item.file_id for item in plan.files], ["file-move"])

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
            self.assertFalse(service.workspace.paths.sync_apply_plan_path.exists())

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
            self.assertEqual(
                service._load_pull_apply_plan_file(),
                apply_plan,
            )

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

    def test_apply_staged_pull_plan_reuses_materialized_write_when_staging_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            rewritten_payload = b"# rewritten\n"
            live_path = root / "Notes" / "Live.md"
            live_path.write_bytes(rewritten_payload)

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
                        content_hash="sha256:" + hashlib.sha256(rewritten_payload).hexdigest(),
                    )
                ],
                moves=[],
                deletes=[],
                blocking_paths=[],
                ops_hash="sha256:ops8",
            )
            staged = DesktopPullApplyStagingResult(
                journal=journal,
                written_staging_paths={},
            )

            execution = service.apply_staged_pull_plan(
                plan,
                staged,
                materialized_at=1770000040200,
            )

            self.assertEqual(execution.written_paths, {"file-live": live_path})
            self.assertEqual(live_path.read_bytes(), rewritten_payload)
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                loaded_journal = load_sync_apply_journal(connection, "vault-001")
                self.assertEqual(loaded_journal.phase, "materializing")

    def test_apply_staged_pull_plan_preserves_dirty_overwrite_as_conflict_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, payload, _ = self._seed_workspace(root)
            live_path = root / "Notes" / "Live.md"
            live_path.write_bytes(payload + b"dirty\n")
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
                        previous_path="Notes/Live.md",
                        expected_previous_content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
                    )
                ],
                moves=[],
                deletes=[],
                blocking_paths=[],
                ops_hash="sha256:ops8",
            )
            staged = DesktopPullApplyStagingResult(
                journal=journal,
                written_staging_paths={"file-live": staging_path},
            )

            service.apply_staged_pull_plan(
                plan,
                staged,
                materialized_at=1770000040200,
            )
            snapshot = service.load_snapshot()
            conflict_records = [record for record in snapshot.document.files if record.status == "conflict_copy"]

            self.assertEqual(live_path.read_bytes(), b"# rewritten\n")
            self.assertTrue(snapshot.state.has_unresolved_conflicts)
            self.assertEqual(len(conflict_records), 1)
            self.assertEqual(conflict_records[0].conflict_source_file_id, "file-live")
            self.assertIn("(conflict 2026-02-02", conflict_records[0].path)
            self.assertEqual((root / conflict_records[0].path).read_bytes(), payload + b"dirty\n")

    def test_apply_staged_pull_plan_preserves_dirty_delete_as_conflict_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            delete_path = root / "Notes" / "Delete.md"
            clean_payload = b"# delete-clean\n"
            dirty_payload = b"# delete-dirty\n"
            delete_path.write_bytes(dirty_payload)

            snapshot = service.load_snapshot()
            document = snapshot.document.replace_files(
                [
                    *snapshot.document.files,
                    FileRecord(
                        file_id="file-delete",
                        path="Notes/Delete.md",
                        type="note",
                        status="deleted",
                        updated_at=1770000040150,
                        content_hash="sha256:" + hashlib.sha256(clean_payload).hexdigest(),
                        last_known_revision=8,
                    ),
                ],
                updated_at=1770000040150,
            )
            write_filemap_atomic(service.workspace.paths.filemap_path, document)

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
                writes=[],
                moves=[],
                deletes=[
                    DesktopPullApplyDeleteFile(
                        file_id="file-delete",
                        path="Notes/Delete.md",
                        reason="deleted",
                        expected_content_hash="sha256:" + hashlib.sha256(clean_payload).hexdigest(),
                    )
                ],
                blocking_paths=[],
                ops_hash="sha256:ops8",
            )
            staged = DesktopPullApplyStagingResult(
                journal=journal,
                written_staging_paths={},
            )

            service.apply_staged_pull_plan(
                plan,
                staged,
                materialized_at=1770000040200,
            )
            snapshot = service.load_snapshot()
            conflict_records = [record for record in snapshot.document.files if record.status == "conflict_copy"]

            self.assertFalse(delete_path.exists())
            self.assertTrue(snapshot.state.has_unresolved_conflicts)
            self.assertEqual(len(conflict_records), 1)
            self.assertEqual(conflict_records[0].conflict_source_file_id, "file-delete")
            self.assertIn("(conflict 2026-02-02", conflict_records[0].path)
            self.assertEqual((root / conflict_records[0].path).read_bytes(), dirty_payload)

    def test_preserve_dirty_pull_conflict_copy_reuses_existing_conflict_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, payload, _ = self._seed_workspace(root)
            live_path = root / "Notes" / "Live.md"
            dirty_payload = payload + b"dirty\n"
            live_path.write_bytes(dirty_payload)

            first_path = None
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                first_path = service._preserve_dirty_pull_conflict_copy(
                    connection,
                    source_file_id="file-live",
                    live_path=live_path,
                    original_relative_path="Notes/Live.md",
                    expected_content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
                    materialized_at=1770000040200,
                )
                second_path = service._preserve_dirty_pull_conflict_copy(
                    connection,
                    source_file_id="file-live",
                    live_path=live_path,
                    original_relative_path="Notes/Live.md",
                    expected_content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
                    materialized_at=1770000040200,
                )

            self.assertIsNotNone(first_path)
            self.assertEqual(second_path, first_path)
            snapshot = service.load_snapshot()
            conflict_records = [record for record in snapshot.document.files if record.status == "conflict_copy"]
            self.assertEqual(len(conflict_records), 1)
            self.assertTrue(snapshot.state.has_unresolved_conflicts)
            self.assertEqual((root / conflict_records[0].path).read_bytes(), dirty_payload)

    def test_apply_staged_pull_plan_materializes_blocking_path_swap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            path_a = root / "Notes" / "A.md"
            path_b = root / "Notes" / "B.md"
            path_a.write_bytes(b"# A\n")
            path_b.write_bytes(b"# B\n")

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
                writes=[],
                moves=[
                    DesktopPullApplyMoveFile(
                        file_id="file-a",
                        source_path="Notes/A.md",
                        target_path="Notes/B.md",
                        type="note",
                        content_hash="sha256:" + hashlib.sha256(b"# A\n").hexdigest(),
                    ),
                    DesktopPullApplyMoveFile(
                        file_id="file-b",
                        source_path="Notes/B.md",
                        target_path="Notes/A.md",
                        type="note",
                        content_hash="sha256:" + hashlib.sha256(b"# B\n").hexdigest(),
                    ),
                ],
                deletes=[],
                blocking_paths=["Notes/A.md", "Notes/B.md"],
                ops_hash="sha256:ops8",
            )
            staged = DesktopPullApplyStagingResult(
                journal=journal,
                written_staging_paths={},
            )

            execution = service.apply_staged_pull_plan(
                plan,
                staged,
                materialized_at=1770000040200,
            )

            self.assertEqual(path_a.read_bytes(), b"# B\n")
            self.assertEqual(path_b.read_bytes(), b"# A\n")
            self.assertEqual(
                execution.moved_paths,
                {
                    "file-a": path_b,
                    "file-b": path_a,
                },
            )
            self.assertEqual(execution.deleted_paths, [])
            self.assertEqual(execution.journal.phase, "materializing")

    def test_apply_staged_pull_plan_materializes_dirty_move_from_staged_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            move_source = root / "Notes" / "Move-old.md"
            move_source.write_bytes(b"# local dirty move\n")
            staging_path = root / ".noteapp" / "staging" / "file-move.staging"
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            staging_path.write_bytes(b"# canonical move\n")

            snapshot = service.load_snapshot()
            document = snapshot.document.replace_files(
                [
                    *snapshot.document.files,
                    FileRecord(
                        file_id="file-move",
                        path="Notes/Move-new.md",
                        type="note",
                        status="active",
                        updated_at=1770000040150,
                        content_hash="sha256:" + hashlib.sha256(b"# canonical move\n").hexdigest(),
                        last_known_revision=8,
                    ),
                ],
                updated_at=1770000040150,
            )
            write_filemap_atomic(service.workspace.paths.filemap_path, document)

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
                writes=[],
                moves=[
                    DesktopPullApplyMoveFile(
                        file_id="file-move",
                        source_path="Notes/Move-old.md",
                        target_path="Notes/Move-new.md",
                        type="note",
                        content_hash="sha256:" + hashlib.sha256(b"# canonical move\n").hexdigest(),
                    )
                ],
                deletes=[],
                blocking_paths=[],
                ops_hash="sha256:ops8",
            )
            staged = DesktopPullApplyStagingResult(
                journal=journal,
                written_staging_paths={"file-move": staging_path},
            )

            service.apply_staged_pull_plan(
                plan,
                staged,
                materialized_at=1770000040200,
            )
            snapshot = service.load_snapshot()
            conflict_records = [record for record in snapshot.document.files if record.status == "conflict_copy"]

            self.assertFalse(move_source.exists())
            self.assertEqual((root / "Notes" / "Move-new.md").read_bytes(), b"# canonical move\n")
            self.assertTrue(snapshot.state.has_unresolved_conflicts)
            self.assertEqual(len(conflict_records), 1)
            self.assertEqual(conflict_records[0].conflict_source_file_id, "file-move")
            self.assertEqual((root / conflict_records[0].path).read_bytes(), b"# local dirty move\n")

    def test_apply_staged_pull_plan_reuses_materialized_dirty_move_when_staging_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            move_source = root / "Notes" / "Move-old.md"
            move_source.write_bytes(b"# local dirty move\n")
            move_target = root / "Notes" / "Move-new.md"
            move_target.write_bytes(b"# canonical move\n")

            snapshot = service.load_snapshot()
            document = snapshot.document.replace_files(
                [
                    *snapshot.document.files,
                    FileRecord(
                        file_id="file-move",
                        path="Notes/Move-new.md",
                        type="note",
                        status="active",
                        updated_at=1770000040150,
                        content_hash="sha256:" + hashlib.sha256(b"# canonical move\n").hexdigest(),
                        last_known_revision=8,
                    ),
                ],
                updated_at=1770000040150,
            )
            write_filemap_atomic(service.workspace.paths.filemap_path, document)

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
                writes=[],
                moves=[
                    DesktopPullApplyMoveFile(
                        file_id="file-move",
                        source_path="Notes/Move-old.md",
                        target_path="Notes/Move-new.md",
                        type="note",
                        content_hash="sha256:" + hashlib.sha256(b"# canonical move\n").hexdigest(),
                    )
                ],
                deletes=[],
                blocking_paths=[],
                ops_hash="sha256:ops8",
            )
            staged = DesktopPullApplyStagingResult(
                journal=journal,
                written_staging_paths={},
            )

            execution = service.apply_staged_pull_plan(
                plan,
                staged,
                materialized_at=1770000040200,
            )
            snapshot = service.load_snapshot()
            conflict_records = [record for record in snapshot.document.files if record.status == "conflict_copy"]

            self.assertFalse(move_source.exists())
            self.assertEqual(move_target.read_bytes(), b"# canonical move\n")
            self.assertEqual(execution.moved_paths, {"file-move": move_target})
            self.assertTrue(snapshot.state.has_unresolved_conflicts)
            self.assertEqual(len(conflict_records), 1)
            self.assertEqual(conflict_records[0].conflict_source_file_id, "file-move")
            self.assertEqual((root / conflict_records[0].path).read_bytes(), b"# local dirty move\n")

    def test_apply_staged_pull_plan_reuses_materialized_blocking_dirty_move_when_staging_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            source_path = root / "Notes" / "A.md"
            target_path = root / "Notes" / "B.md"
            source_path.write_bytes(b"# local dirty A\n")
            target_path.write_bytes(b"# A\n")
            snapshot = service.load_snapshot()
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                snapshot.document.replace_files(
                    [
                        *snapshot.document.files,
                        FileRecord(
                            file_id="file-a",
                            path="Notes/A.md",
                            type="note",
                            status="active",
                            updated_at=1770000040150,
                            content_hash="sha256:" + hashlib.sha256(b"# A\n").hexdigest(),
                            last_known_revision=8,
                        ),
                    ],
                    updated_at=1770000040150,
                ),
            )

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
                writes=[],
                moves=[
                    DesktopPullApplyMoveFile(
                        file_id="file-a",
                        source_path="Notes/A.md",
                        target_path="Notes/B.md",
                        type="note",
                        content_hash="sha256:" + hashlib.sha256(b"# A\n").hexdigest(),
                    )
                ],
                deletes=[],
                blocking_paths=["Notes/A.md", "Notes/B.md"],
                ops_hash="sha256:ops8",
            )
            staged = DesktopPullApplyStagingResult(
                journal=journal,
                written_staging_paths={},
            )

            execution = service.apply_staged_pull_plan(
                plan,
                staged,
                materialized_at=1770000040200,
            )
            snapshot = service.load_snapshot()
            conflict_records = [record for record in snapshot.document.files if record.status == "conflict_copy"]

            self.assertFalse(source_path.exists())
            self.assertEqual(target_path.read_bytes(), b"# A\n")
            self.assertEqual(execution.moved_paths, {"file-a": target_path})
            self.assertTrue(snapshot.state.has_unresolved_conflicts)
            self.assertEqual(len(conflict_records), 1)
            self.assertEqual(conflict_records[0].conflict_source_file_id, "file-a")
            self.assertEqual((root / conflict_records[0].path).read_bytes(), b"# local dirty A\n")

    def test_finalize_applied_pull_plan_clears_journal_and_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            staging_path = root / ".noteapp" / "staging" / "file-live.staging"
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            staging_path.write_bytes(b"# rewritten\n")

            journal = SyncApplyJournalRecord(
                vault_id="vault-001",
                journal_id="journal-1",
                target_revision=8,
                target_manifest_hash="sha256:head8",
                phase="materializing",
                ops_hash="sha256:ops8",
                created_at=1770000040100,
                updated_at=1770000040200,
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
                moves=[],
                deletes=[],
                blocking_paths=[],
                ops_hash="sha256:ops8",
            )
            execution = type(
                "Execution",
                (),
                {
                    "journal": journal,
                    "written_paths": {"file-live": root / "Notes" / "Live.md"},
                    "moved_paths": {},
                    "deleted_paths": [],
                },
            )()
            staged = DesktopPullApplyStagingResult(
                journal=journal,
                written_staging_paths={"file-live": staging_path},
            )

            finalized = service.finalize_applied_pull_plan(
                plan,
                execution,
                staged,
                finalized_at=1770000040300,
            )

            self.assertFalse(staging_path.exists())
            self.assertEqual(finalized.removed_staging_paths, [staging_path])
            self.assertIsNone(finalized.removed_plan_path)
            self.assertEqual(finalized.state.last_applied_revision, 8)
            self.assertEqual(finalized.state.acked_revision, 8)
            self.assertEqual(finalized.state.pending_ack_to_server, [8])
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_sync_apply_journal(connection, "vault-001"))

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

    def test_pull_and_apply_nonblocking_orchestrates_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))
            before_snapshot = service.load_snapshot()
            pull = self._build_pull_result(required_blob_ids=[], manifest_files=[])
            plan = DesktopPullApplyPlan(
                vault_id="vault-001",
                revision=8,
                writes=[],
                moves=[],
                deletes=[],
                blocking_paths=[],
                ops_hash="sha256:ops8",
            )
            resolved = DesktopPullRequiredBlobResult(
                pull=pull,
                plan=DesktopPullRequiredBlobPlan(vault_id="vault-001", revision=8, blob_ids=[], files=[]),
                download=None,
                plaintext_by_file_id={},
            )
            staged = DesktopPullApplyStagingResult(journal=None, written_staging_paths={})
            execution = type(
                "Execution",
                (),
                {
                    "journal": None,
                    "written_paths": {},
                    "moved_paths": {},
                    "deleted_paths": [],
                },
            )()
            finalized = type(
                "Finalized",
                (),
                {
                    "state": service.load_snapshot().state,
                    "removed_staging_paths": [],
                    "removed_plan_path": None,
                },
            )()

            with mock.patch.object(type(service), "_pull_and_ack_with_snapshot", return_value=(before_snapshot, pull)):
                with mock.patch.object(type(service), "_build_pull_apply_plan", return_value=plan):
                    with mock.patch.object(type(service), "download_and_decrypt_pull_required_blobs", return_value=resolved):
                        with mock.patch.object(type(service), "stage_pull_required_plaintext_for_apply", return_value=staged):
                            with mock.patch.object(type(service), "apply_staged_pull_plan", return_value=execution):
                                with mock.patch.object(type(service), "finalize_applied_pull_plan", return_value=finalized):
                                    session = service.pull_and_apply_nonblocking(rewritten_at=1770000040100)

            self.assertIs(session.pull, pull)
            self.assertIs(session.plan, plan)
            self.assertIs(session.staged, staged)
            self.assertIs(session.execution, execution)
            self.assertIs(session.finalized, finalized)

    def test_pull_and_apply_orchestrates_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))
            before_snapshot = service.load_snapshot()
            pull = self._build_pull_result(required_blob_ids=[], manifest_files=[])
            plan = DesktopPullApplyPlan(
                vault_id="vault-001",
                revision=8,
                writes=[],
                moves=[],
                deletes=[],
                blocking_paths=["Notes/A.md"],
                ops_hash="sha256:ops8",
            )
            resolved = DesktopPullRequiredBlobResult(
                pull=pull,
                plan=DesktopPullRequiredBlobPlan(vault_id="vault-001", revision=8, blob_ids=[], files=[]),
                download=None,
                plaintext_by_file_id={},
            )
            staged = DesktopPullApplyStagingResult(journal=None, written_staging_paths={})
            execution = type(
                "Execution",
                (),
                {
                    "journal": None,
                    "written_paths": {},
                    "moved_paths": {},
                    "deleted_paths": [],
                },
            )()
            finalized = type(
                "Finalized",
                (),
                {
                    "state": service.load_snapshot().state,
                    "removed_staging_paths": [],
                    "removed_plan_path": None,
                },
            )()

            with mock.patch.object(type(service), "_pull_and_ack_with_snapshot", return_value=(before_snapshot, pull)):
                with mock.patch.object(type(service), "_build_pull_apply_plan", return_value=plan):
                    with mock.patch.object(type(service), "download_and_decrypt_pull_required_blobs", return_value=resolved):
                        with mock.patch.object(type(service), "stage_pull_required_plaintext_for_apply", return_value=staged):
                            with mock.patch.object(type(service), "apply_staged_pull_plan", return_value=execution):
                                with mock.patch.object(type(service), "finalize_applied_pull_plan", return_value=finalized):
                                    session = service.pull_and_apply(rewritten_at=1770000040100)

            self.assertIs(session.pull, pull)
            self.assertIs(session.plan, plan)
            self.assertIs(session.staged, staged)
            self.assertIs(session.execution, execution)
            self.assertIs(session.finalized, finalized)

    def test_resume_pull_apply_recovery_returns_idle_without_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _, _, _, _ = self._seed_workspace(Path(tmpdir))

            recovered = service.resume_pull_apply_recovery(normalized_at=1770000040200)

            self.assertEqual(recovered.mode, "idle")
            self.assertFalse(recovered.requires_full_pull)
            self.assertIsNone(recovered.journal_phase)
            self.assertIsNone(recovered.state)
            self.assertEqual(recovered.removed_staging_paths, [])
            self.assertEqual(recovered.isolated_staging_paths, [])
            self.assertIsNone(recovered.removed_plan_path)

    def test_resume_pull_apply_recovery_isolates_unjournaled_pull_apply_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            staging_path = root / ".noteapp" / "staging" / "leftover.staging"
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            staging_path.write_text("payload", encoding="utf-8")
            service.workspace.paths.sync_apply_plan_path.parent.mkdir(parents=True, exist_ok=True)
            service.workspace.paths.sync_apply_plan_path.write_text("{}", encoding="utf-8")

            recovered = service.resume_pull_apply_recovery(normalized_at=1770000040200)

            self.assertEqual(recovered.mode, "orphaned")
            self.assertTrue(recovered.requires_full_pull)
            self.assertIsNone(recovered.journal_phase)
            self.assertEqual(recovered.state.last_manifest_summary_status, "stale")
            self.assertIsNone(recovered.state.last_manifest_summary)
            self.assertEqual(recovered.removed_staging_paths, [])
            self.assertEqual(len(recovered.isolated_staging_paths), 1)
            self.assertFalse(staging_path.exists())
            self.assertEqual(recovered.isolated_staging_paths[0].parent.name, "staging-orphans")
            self.assertEqual(recovered.removed_plan_path, service.workspace.paths.sync_apply_plan_path)
            self.assertFalse(service.workspace.paths.sync_apply_plan_path.exists())

    def test_resume_pull_apply_recovery_ignores_unjournaled_commit_blob_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            blob_staging_path = root / ".noteapp" / "staging" / "blob-live.blob.staging"
            blob_staging_path.parent.mkdir(parents=True, exist_ok=True)
            blob_staging_path.write_bytes(b"blob")

            recovered = service.resume_pull_apply_recovery(normalized_at=1770000040200)

            self.assertEqual(recovered.mode, "idle")
            self.assertFalse(recovered.requires_full_pull)
            self.assertIsNone(recovered.journal_phase)
            self.assertIsNone(recovered.state)
            self.assertEqual(recovered.removed_staging_paths, [])
            self.assertEqual(recovered.isolated_staging_paths, [])
            self.assertTrue(blob_staging_path.exists())
            self.assertIsNone(recovered.removed_plan_path)

    def test_resume_pull_apply_recovery_finalizes_finalizing_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            staging_path = root / ".noteapp" / "staging" / "file-live.staging"
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            staging_path.write_bytes(b"# rewritten\n")

            with closing(open_database(service.workspace.paths.db_path)) as connection:
                upsert_sync_apply_journal(
                    connection,
                    SyncApplyJournalRecord(
                        vault_id="vault-001",
                        journal_id="journal-1",
                        target_revision=8,
                        target_manifest_hash="sha256:head8",
                        phase="finalizing",
                        ops_hash="sha256:ops8",
                        created_at=1770000040100,
                        updated_at=1770000040200,
                    ),
                )

            recovered = service.resume_pull_apply_recovery(normalized_at=1770000040300)

            self.assertEqual(recovered.mode, "finalized")
            self.assertEqual(recovered.journal_phase, "finalizing")
            self.assertEqual(recovered.state.last_applied_revision, 8)
            self.assertEqual(recovered.state.pending_ack_to_server, [8])
            self.assertEqual(recovered.removed_staging_paths, [staging_path])
            self.assertEqual(recovered.isolated_staging_paths, [])
            self.assertIsNone(recovered.removed_plan_path)
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_sync_apply_journal(connection, "vault-001"))

    def test_resume_pull_apply_recovery_replays_staging_with_persisted_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            path_a = root / "Notes" / "A.md"
            path_b = root / "Notes" / "B.md"
            path_a.write_bytes(b"# A\n")
            path_b.write_bytes(b"# B\n")

            plan = DesktopPullApplyPlan(
                vault_id="vault-001",
                revision=8,
                writes=[],
                moves=[
                    DesktopPullApplyMoveFile(
                        file_id="file-a",
                        source_path="Notes/A.md",
                        target_path="Notes/B.md",
                        type="note",
                        content_hash="sha256:" + hashlib.sha256(b"# A\n").hexdigest(),
                    ),
                    DesktopPullApplyMoveFile(
                        file_id="file-b",
                        source_path="Notes/B.md",
                        target_path="Notes/A.md",
                        type="note",
                        content_hash="sha256:" + hashlib.sha256(b"# B\n").hexdigest(),
                    ),
                ],
                deletes=[],
                blocking_paths=["Notes/A.md", "Notes/B.md"],
                ops_hash="sha256:ops8",
            )
            service._write_pull_apply_plan_file(plan)

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
                        updated_at=1770000040200,
                    ),
                )

            recovered = service.resume_pull_apply_recovery(normalized_at=1770000040300)

            self.assertEqual(recovered.mode, "replayed")
            self.assertEqual(recovered.journal_phase, "staging")
            self.assertEqual(path_a.read_bytes(), b"# B\n")
            self.assertEqual(path_b.read_bytes(), b"# A\n")
            self.assertEqual(recovered.state.last_applied_revision, 8)
            self.assertEqual(recovered.state.pending_ack_to_server, [8])
            self.assertEqual(recovered.removed_staging_paths, [])
            self.assertEqual(recovered.isolated_staging_paths, [])
            self.assertEqual(recovered.removed_plan_path, service.workspace.paths.sync_apply_plan_path)
            self.assertFalse(service.workspace.paths.sync_apply_plan_path.exists())
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_sync_apply_journal(connection, "vault-001"))

    def test_resume_pull_apply_recovery_replays_partial_materialized_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            rewritten_payload = b"# rewritten\n"
            added_payload = b"# added\n"
            rewritten_hash = "sha256:" + hashlib.sha256(rewritten_payload).hexdigest()
            added_hash = "sha256:" + hashlib.sha256(added_payload).hexdigest()
            live_path = root / "Notes" / "Live.md"
            live_path.write_bytes(rewritten_payload)
            added_staging_path = root / ".noteapp" / "staging" / "file-added.staging"
            added_staging_path.parent.mkdir(parents=True, exist_ok=True)
            added_staging_path.write_bytes(added_payload)

            snapshot = service.load_snapshot()
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                snapshot.document.replace_files(
                    [
                        replace(
                            snapshot.document.files[0],
                            updated_at=1770000040250,
                            content_hash=rewritten_hash,
                            meta={
                                "blob_id": "blob-live-rewritten",
                                "size": len(rewritten_payload),
                                "mtime": live_path.stat().st_mtime_ns // 1_000_000,
                                "mime_type": "text/markdown",
                            },
                        ),
                        FileRecord(
                            file_id="file-added",
                            path="Notes/Added.md",
                            type="note",
                            status="active",
                            updated_at=1770000040250,
                            content_hash=added_hash,
                            meta={
                                "blob_id": "blob-added",
                                "size": len(added_payload),
                                "mtime": 1770000040250,
                                "mime_type": "text/markdown",
                            },
                        ),
                    ],
                    updated_at=1770000040250,
                ),
            )
            service._write_pull_apply_plan_file(
                DesktopPullApplyPlan(
                    vault_id="vault-001",
                    revision=8,
                    writes=[
                        DesktopPullApplyWriteFile(
                            file_id="file-live",
                            target_path="Notes/Live.md",
                            staging_path=".noteapp/staging/file-live.staging",
                            type="note",
                            content_hash=rewritten_hash,
                        ),
                        DesktopPullApplyWriteFile(
                            file_id="file-added",
                            target_path="Notes/Added.md",
                            staging_path=".noteapp/staging/file-added.staging",
                            type="note",
                            content_hash=added_hash,
                        ),
                    ],
                    moves=[],
                    deletes=[],
                    blocking_paths=[],
                    ops_hash="sha256:ops8",
                )
            )

            with closing(open_database(service.workspace.paths.db_path)) as connection:
                upsert_sync_apply_journal(
                    connection,
                    SyncApplyJournalRecord(
                        vault_id="vault-001",
                        journal_id="journal-1",
                        target_revision=8,
                        target_manifest_hash="sha256:head8",
                        phase="materializing",
                        ops_hash="sha256:ops8",
                        created_at=1770000040100,
                        updated_at=1770000040200,
                    ),
                )

            recovered = service.resume_pull_apply_recovery(normalized_at=1770000040300)

            self.assertEqual(recovered.mode, "replayed")
            self.assertEqual(recovered.journal_phase, "materializing")
            self.assertEqual((root / "Notes" / "Live.md").read_bytes(), rewritten_payload)
            self.assertEqual((root / "Notes" / "Added.md").read_bytes(), added_payload)
            self.assertEqual(recovered.state.last_applied_revision, 8)
            self.assertEqual(recovered.state.pending_ack_to_server, [8])
            self.assertEqual(recovered.removed_staging_paths, [added_staging_path])
            self.assertEqual(recovered.removed_plan_path, service.workspace.paths.sync_apply_plan_path)
            self.assertFalse(service.workspace.paths.sync_apply_plan_path.exists())
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_sync_apply_journal(connection, "vault-001"))

    def test_resume_pull_apply_recovery_degrades_when_replay_payload_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            plan = DesktopPullApplyPlan(
                vault_id="vault-001",
                revision=8,
                writes=[
                    DesktopPullApplyWriteFile(
                        file_id="file-a",
                        target_path="Notes/A.md",
                        staging_path=".noteapp/staging/file-a.staging",
                        type="note",
                        content_hash="sha256:" + hashlib.sha256(b"# rewritten\n").hexdigest(),
                    )
                ],
                moves=[],
                deletes=[],
                blocking_paths=[],
                ops_hash="sha256:ops8",
            )
            service._write_pull_apply_plan_file(plan)

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
                        updated_at=1770000040200,
                    ),
                )

            recovered = service.resume_pull_apply_recovery(normalized_at=1770000040300)

            self.assertEqual(recovered.mode, "degraded")
            self.assertEqual(recovered.journal_phase, "staging")
            self.assertEqual(recovered.state.last_manifest_summary_status, "stale")
            self.assertEqual(recovered.removed_staging_paths, [])
            self.assertEqual(recovered.isolated_staging_paths, [])
            self.assertEqual(recovered.removed_plan_path, service.workspace.paths.sync_apply_plan_path)
            self.assertFalse(service.workspace.paths.sync_apply_plan_path.exists())
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_sync_apply_journal(connection, "vault-001"))

    def test_resume_pull_apply_recovery_degrades_unmaterialized_workspace_without_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            snapshot = service.load_snapshot()
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                snapshot.document.replace_files(
                    [
                        replace(
                            snapshot.document.files[0],
                            content_hash="sha256:not-materialized",
                        )
                    ],
                    updated_at=1770000040250,
                ),
            )

            with closing(open_database(service.workspace.paths.db_path)) as connection:
                upsert_sync_apply_journal(
                    connection,
                    SyncApplyJournalRecord(
                        vault_id="vault-001",
                        journal_id="journal-1",
                        target_revision=8,
                        target_manifest_hash="sha256:head8",
                        phase="materializing",
                        ops_hash="sha256:ops8",
                        created_at=1770000040100,
                        updated_at=1770000040200,
                    ),
                )

            staging_path = root / ".noteapp" / "staging" / "leftover.staging"
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            staging_path.write_text("payload", encoding="utf-8")

            recovered = service.resume_pull_apply_recovery(normalized_at=1770000040300)

            self.assertEqual(recovered.mode, "degraded")
            self.assertEqual(recovered.journal_phase, "materializing")
            self.assertEqual(recovered.state.last_manifest_summary_status, "stale")
            self.assertIsNone(recovered.state.last_manifest_summary)
            self.assertEqual(recovered.removed_staging_paths, [])
            self.assertEqual(len(recovered.isolated_staging_paths), 1)
            self.assertFalse(staging_path.exists())
            self.assertEqual(recovered.isolated_staging_paths[0].parent.name, "staging-orphans")
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_sync_apply_journal(connection, "vault-001"))

    def test_resume_pull_apply_recovery_finalizes_materialized_workspace_without_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            rewritten_payload = b"# rewritten\n"
            rewritten_hash = "sha256:" + hashlib.sha256(rewritten_payload).hexdigest()
            live_path = root / "Notes" / "Live.md"
            live_path.write_bytes(rewritten_payload)
            snapshot = service.load_snapshot()
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                snapshot.document.replace_files(
                    [
                        replace(
                            snapshot.document.files[0],
                            updated_at=1770000040250,
                            content_hash=rewritten_hash,
                            meta={
                                "blob_id": "blob-live-rewritten",
                                "size": len(rewritten_payload),
                                "mtime": live_path.stat().st_mtime_ns // 1_000_000,
                                "mime_type": "text/markdown",
                            },
                        )
                    ],
                    updated_at=1770000040250,
                ),
            )

            with closing(open_database(service.workspace.paths.db_path)) as connection:
                upsert_sync_apply_journal(
                    connection,
                    SyncApplyJournalRecord(
                        vault_id="vault-001",
                        journal_id="journal-1",
                        target_revision=8,
                        target_manifest_hash="sha256:head8",
                        phase="materializing",
                        ops_hash="sha256:ops8",
                        created_at=1770000040100,
                        updated_at=1770000040200,
                    ),
                )

            staging_path = root / ".noteapp" / "staging" / "leftover.staging"
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            staging_path.write_text("payload", encoding="utf-8")

            recovered = service.resume_pull_apply_recovery(normalized_at=1770000040300)

            self.assertEqual(recovered.mode, "finalized")
            self.assertEqual(recovered.journal_phase, "materializing")
            self.assertEqual(recovered.state.last_applied_revision, 8)
            self.assertEqual(recovered.state.pending_ack_to_server, [8])
            self.assertEqual(recovered.removed_staging_paths, [staging_path])
            self.assertFalse(staging_path.exists())
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_sync_apply_journal(connection, "vault-001"))

    def test_resume_pull_apply_recovery_replays_materialized_workspace_when_write_target_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            rewritten_payload = b"# rewritten\n"
            rewritten_hash = "sha256:" + hashlib.sha256(rewritten_payload).hexdigest()
            live_path = root / "Notes" / "Live.md"
            live_path.write_bytes(rewritten_payload)
            snapshot = service.load_snapshot()
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                snapshot.document.replace_files(
                    [
                        replace(
                            snapshot.document.files[0],
                            updated_at=1770000040250,
                            content_hash=rewritten_hash,
                            meta={
                                "blob_id": "blob-live-rewritten",
                                "size": len(rewritten_payload),
                                "mtime": live_path.stat().st_mtime_ns // 1_000_000,
                                "mime_type": "text/markdown",
                            },
                        )
                    ],
                    updated_at=1770000040250,
                ),
            )
            service._write_pull_apply_plan_file(
                DesktopPullApplyPlan(
                    vault_id="vault-001",
                    revision=8,
                    writes=[
                        DesktopPullApplyWriteFile(
                            file_id="file-live",
                            target_path="Notes/Live.md",
                            staging_path=".noteapp/staging/file-live.staging",
                            type="note",
                            content_hash=rewritten_hash,
                        )
                    ],
                    moves=[],
                    deletes=[],
                    blocking_paths=[],
                    ops_hash="sha256:ops8",
                )
            )

            with closing(open_database(service.workspace.paths.db_path)) as connection:
                upsert_sync_apply_journal(
                    connection,
                    SyncApplyJournalRecord(
                        vault_id="vault-001",
                        journal_id="journal-1",
                        target_revision=8,
                        target_manifest_hash="sha256:head8",
                        phase="materializing",
                        ops_hash="sha256:ops8",
                        created_at=1770000040100,
                        updated_at=1770000040200,
                    ),
                )

            recovered = service.resume_pull_apply_recovery(normalized_at=1770000040300)

            self.assertEqual(recovered.mode, "replayed")
            self.assertEqual(recovered.journal_phase, "materializing")
            self.assertEqual(recovered.state.last_applied_revision, 8)
            self.assertEqual(recovered.state.pending_ack_to_server, [8])
            self.assertEqual(recovered.removed_staging_paths, [])
            self.assertEqual(recovered.removed_plan_path, service.workspace.paths.sync_apply_plan_path)
            self.assertFalse(service.workspace.paths.sync_apply_plan_path.exists())
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_sync_apply_journal(connection, "vault-001"))

    def test_resume_pull_apply_recovery_degrades_preparing_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            with closing(open_database(service.workspace.paths.db_path)) as connection:
                upsert_sync_apply_journal(
                    connection,
                    SyncApplyJournalRecord(
                        vault_id="vault-001",
                        journal_id="journal-1",
                        target_revision=8,
                        target_manifest_hash="sha256:head8",
                        phase="preparing",
                        ops_hash="sha256:ops8",
                        created_at=1770000040100,
                        updated_at=1770000040200,
                    ),
                )

            service.workspace.paths.sync_apply_plan_path.parent.mkdir(parents=True, exist_ok=True)
            service.workspace.paths.sync_apply_plan_path.write_text("{}", encoding="utf-8")
            staging_path = root / ".noteapp" / "staging" / "partial.staging"
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            staging_path.write_text("payload", encoding="utf-8")

            recovered = service.resume_pull_apply_recovery(normalized_at=1770000040300)

            self.assertEqual(recovered.mode, "degraded")
            self.assertEqual(recovered.journal_phase, "preparing")
            self.assertEqual(recovered.state.last_manifest_summary_status, "stale")
            self.assertIsNone(recovered.state.last_manifest_summary)
            self.assertEqual(recovered.removed_staging_paths, [])
            self.assertEqual(len(recovered.isolated_staging_paths), 1)
            self.assertFalse(staging_path.exists())
            self.assertEqual(recovered.isolated_staging_paths[0].parent.name, "staging-orphans")
            self.assertEqual(recovered.removed_plan_path, service.workspace.paths.sync_apply_plan_path)
            self.assertFalse(service.workspace.paths.sync_apply_plan_path.exists())
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_sync_apply_journal(connection, "vault-001"))

    def test_resume_pull_apply_recovery_degrades_staging_without_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

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
                        updated_at=1770000040200,
                    ),
                )

            staging_path = root / ".noteapp" / "staging" / "leftover.staging"
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            staging_path.write_text("payload", encoding="utf-8")

            recovered = service.resume_pull_apply_recovery(normalized_at=1770000040300)

            self.assertEqual(recovered.mode, "degraded")
            self.assertEqual(recovered.journal_phase, "staging")
            self.assertEqual(recovered.state.last_manifest_summary_status, "stale")
            self.assertIsNone(recovered.state.last_manifest_summary)
            self.assertEqual(recovered.removed_staging_paths, [])
            self.assertEqual(len(recovered.isolated_staging_paths), 1)
            self.assertFalse(staging_path.exists())
            self.assertEqual(recovered.isolated_staging_paths[0].parent.name, "staging-orphans")
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_sync_apply_journal(connection, "vault-001"))

    def test_resume_pull_apply_recovery_degrades_staging_with_corrupt_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

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
                        updated_at=1770000040200,
                    ),
                )

            service.workspace.paths.sync_apply_plan_path.parent.mkdir(parents=True, exist_ok=True)
            service.workspace.paths.sync_apply_plan_path.write_text("{bad json", encoding="utf-8")
            staging_path = root / ".noteapp" / "staging" / "leftover.staging"
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            staging_path.write_text("payload", encoding="utf-8")

            recovered = service.resume_pull_apply_recovery(normalized_at=1770000040300)

            self.assertEqual(recovered.mode, "degraded")
            self.assertEqual(recovered.journal_phase, "staging")
            self.assertEqual(recovered.state.last_manifest_summary_status, "stale")
            self.assertEqual(recovered.removed_staging_paths, [])
            self.assertEqual(len(recovered.isolated_staging_paths), 1)
            self.assertEqual(recovered.removed_plan_path, service.workspace.paths.sync_apply_plan_path)
            self.assertFalse(service.workspace.paths.sync_apply_plan_path.exists())
            self.assertFalse(staging_path.exists())
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_sync_apply_journal(connection, "vault-001"))

    def test_resume_pull_apply_recovery_degrades_staging_with_mismatched_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            plan = DesktopPullApplyPlan(
                vault_id="vault-001",
                revision=8,
                writes=[],
                moves=[],
                deletes=[],
                blocking_paths=[],
                ops_hash="sha256:not-ops8",
            )
            service._write_pull_apply_plan_file(plan)

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
                        updated_at=1770000040200,
                    ),
                )

            staging_path = root / ".noteapp" / "staging" / "leftover.staging"
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            staging_path.write_text("payload", encoding="utf-8")

            recovered = service.resume_pull_apply_recovery(normalized_at=1770000040300)

            self.assertEqual(recovered.mode, "degraded")
            self.assertEqual(recovered.journal_phase, "staging")
            self.assertEqual(recovered.state.last_manifest_summary_status, "stale")
            self.assertEqual(recovered.removed_staging_paths, [])
            self.assertEqual(len(recovered.isolated_staging_paths), 1)
            self.assertEqual(recovered.removed_plan_path, service.workspace.paths.sync_apply_plan_path)
            self.assertFalse(service.workspace.paths.sync_apply_plan_path.exists())
            self.assertFalse(staging_path.exists())
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_sync_apply_journal(connection, "vault-001"))

    def test_resume_pull_apply_recovery_ignores_corrupt_plan_during_finalizing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)

            with closing(open_database(service.workspace.paths.db_path)) as connection:
                upsert_sync_apply_journal(
                    connection,
                    SyncApplyJournalRecord(
                        vault_id="vault-001",
                        journal_id="journal-1",
                        target_revision=8,
                        target_manifest_hash="sha256:head8",
                        phase="finalizing",
                        ops_hash="sha256:ops8",
                        created_at=1770000040100,
                        updated_at=1770000040200,
                    ),
                )

            service.workspace.paths.sync_apply_plan_path.parent.mkdir(parents=True, exist_ok=True)
            service.workspace.paths.sync_apply_plan_path.write_text("{bad json", encoding="utf-8")

            recovered = service.resume_pull_apply_recovery(normalized_at=1770000040300)

            self.assertEqual(recovered.mode, "finalized")
            self.assertEqual(recovered.journal_phase, "finalizing")
            self.assertEqual(recovered.state.last_applied_revision, 8)
            self.assertEqual(recovered.state.pending_ack_to_server, [8])
            self.assertEqual(recovered.removed_staging_paths, [])
            self.assertEqual(recovered.isolated_staging_paths, [])
            self.assertEqual(recovered.removed_plan_path, service.workspace.paths.sync_apply_plan_path)
            self.assertFalse(service.workspace.paths.sync_apply_plan_path.exists())
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_sync_apply_journal(connection, "vault-001"))

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

    def test_submit_workspace_commit_promotes_and_rejects_unresolved_conflict_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            snapshot = service.load_snapshot()
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                snapshot.document.replace_files(
                    [
                        *snapshot.document.files,
                        FileRecord(
                            file_id="file-conflict",
                            path="Notes/Live (conflict 2026-04-29 Desktop-Win).md",
                            type="note",
                            status="conflict_copy",
                            updated_at=1770000040200,
                            content_hash="sha256:conflict",
                            conflict_source_file_id="file-live",
                        ),
                    ],
                    updated_at=1770000040200,
                ),
            )

            with self.assertRaisesRegex(
                ValueError,
                "document contains unresolved conflict_copy entries",
            ):
                service.submit_workspace_commit(
                    created_at=1770000040300,
                    file_ids=["file-live"],
                )

            reloaded = service.load_snapshot()
            self.assertTrue(reloaded.state.has_unresolved_conflicts)
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_commit_intent_journal(connection, "vault-001"))

    def test_submit_workspace_commit_promotes_and_rejects_conflict_orphans(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            orphan_path = root / ".noteapp" / "conflict-orphans" / "Live (conflict 2026-04-29 Desktop-Win).md"
            orphan_path.parent.mkdir(parents=True, exist_ok=True)
            orphan_path.write_text("orphan conflict", encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "vault_state is not eligible to start a new commit",
            ):
                service.submit_workspace_commit(
                    created_at=1770000040300,
                    file_ids=["file-live"],
                )

            reloaded = service.load_snapshot()
            self.assertTrue(reloaded.state.has_unresolved_conflicts)
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                self.assertIsNone(load_commit_intent_journal(connection, "vault-001"))

    def test_list_conflicts_reports_conflict_records_and_orphans(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, payload, _ = self._seed_workspace(root)
            conflict_path = root / "Notes" / "Live (conflict 2026-04-29 Desktop-Win).md"
            conflict_path.write_bytes(payload + b" conflict")
            conflict_hash = "sha256:" + hashlib.sha256(conflict_path.read_bytes()).hexdigest()
            orphan_path = root / ".noteapp" / "conflict-orphans" / "Live orphan.md"
            orphan_path.parent.mkdir(parents=True, exist_ok=True)
            orphan_path.write_text("orphan conflict", encoding="utf-8")
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                FileMapDocument(
                    vault_id="vault-001",
                    updated_at=1770000040100,
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
                        ),
                        FileRecord(
                            file_id="file-conflict",
                            path="Notes/Live (conflict 2026-04-29 Desktop-Win).md",
                            type="note",
                            status="conflict_copy",
                            updated_at=1770000040100,
                            content_hash=conflict_hash,
                            conflict_source_file_id="file-live",
                        ),
                    ],
                ),
            )
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                state = load_vault_state(connection, "vault-001")
                self.assertIsNotNone(state)
                upsert_vault_state(connection, replace(state, has_unresolved_conflicts=False))

            status = service.list_conflicts()

            self.assertTrue(status.state.has_unresolved_conflicts)
            self.assertTrue(status.actual_has_unresolved_conflicts)
            self.assertEqual(len(status.conflict_copies), 1)
            self.assertEqual(status.conflict_copies[0].file_id, "file-conflict")
            self.assertTrue(status.conflict_copies[0].exists_on_disk)
            self.assertEqual(status.conflict_copies[0].conflict_source_file_id, "file-live")
            self.assertEqual(
                status.conflict_orphans[0].path,
                ".noteapp/conflict-orphans/Live orphan.md",
            )

    def test_resolve_conflicts_removes_conflict_copy_and_clears_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, payload, _ = self._seed_workspace(root)
            conflict_path = root / "Notes" / "Live (conflict 2026-04-29 Desktop-Win).md"
            conflict_path.write_bytes(payload + b" conflict")
            conflict_hash = "sha256:" + hashlib.sha256(conflict_path.read_bytes()).hexdigest()
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                FileMapDocument(
                    vault_id="vault-001",
                    updated_at=1770000040100,
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
                        ),
                        FileRecord(
                            file_id="file-conflict",
                            path="Notes/Live (conflict 2026-04-29 Desktop-Win).md",
                            type="note",
                            status="conflict_copy",
                            updated_at=1770000040100,
                            content_hash=conflict_hash,
                            conflict_source_file_id="file-live",
                        ),
                    ],
                ),
            )
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                state = load_vault_state(connection, "vault-001")
                self.assertIsNotNone(state)
                upsert_vault_state(connection, replace(state, has_unresolved_conflicts=True))

            result = service.resolve_conflicts(
                resolved_at=1770000040200,
                conflict_file_ids=["file-conflict"],
            )

            self.assertEqual(list(result.removed_conflict_paths), ["file-conflict"])
            self.assertFalse(conflict_path.exists())
            self.assertFalse(result.state.has_unresolved_conflicts)
            snapshot = service.load_snapshot()
            self.assertFalse(snapshot.state.has_unresolved_conflicts)
            self.assertEqual([record.file_id for record in snapshot.document.files], ["file-live"])

    def test_resolve_conflicts_removes_conflict_orphan_and_clears_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, _, _ = self._seed_workspace(root)
            orphan_path = root / ".noteapp" / "conflict-orphans" / "Live (conflict 2026-04-29 Desktop-Win).md"
            orphan_path.parent.mkdir(parents=True, exist_ok=True)
            orphan_path.write_text("orphan conflict", encoding="utf-8")
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                state = load_vault_state(connection, "vault-001")
                self.assertIsNotNone(state)
                upsert_vault_state(connection, replace(state, has_unresolved_conflicts=True))

            result = service.resolve_conflicts(
                resolved_at=1770000040200,
                orphan_relative_paths=[".noteapp/conflict-orphans/Live (conflict 2026-04-29 Desktop-Win).md"],
            )

            self.assertEqual(result.removed_orphan_paths, [orphan_path])
            self.assertFalse(orphan_path.exists())
            self.assertFalse(result.state.has_unresolved_conflicts)
            self.assertFalse(service.load_snapshot().state.has_unresolved_conflicts)

    def test_resolve_conflicts_keeps_state_true_when_other_conflicts_remain(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, payload, _ = self._seed_workspace(root)
            first_conflict_path = root / "Notes" / "Live (conflict 2026-04-29 Desktop-Win).md"
            second_conflict_path = root / "Notes" / "Live (conflict 2026-04-30 Desktop-Win).md"
            first_conflict_path.write_bytes(payload + b" first")
            second_conflict_path.write_bytes(payload + b" second")
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                FileMapDocument(
                    vault_id="vault-001",
                    updated_at=1770000040100,
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
                        ),
                        FileRecord(
                            file_id="file-conflict-a",
                            path="Notes/Live (conflict 2026-04-29 Desktop-Win).md",
                            type="note",
                            status="conflict_copy",
                            updated_at=1770000040100,
                            content_hash="sha256:" + hashlib.sha256(first_conflict_path.read_bytes()).hexdigest(),
                            conflict_source_file_id="file-live",
                        ),
                        FileRecord(
                            file_id="file-conflict-b",
                            path="Notes/Live (conflict 2026-04-30 Desktop-Win).md",
                            type="note",
                            status="conflict_copy",
                            updated_at=1770000040110,
                            content_hash="sha256:" + hashlib.sha256(second_conflict_path.read_bytes()).hexdigest(),
                            conflict_source_file_id="file-live",
                        ),
                    ],
                ),
            )
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                state = load_vault_state(connection, "vault-001")
                self.assertIsNotNone(state)
                upsert_vault_state(connection, replace(state, has_unresolved_conflicts=True))

            result = service.resolve_conflicts(
                resolved_at=1770000040200,
                conflict_file_ids=["file-conflict-a"],
            )

            self.assertTrue(result.state.has_unresolved_conflicts)
            self.assertFalse(first_conflict_path.exists())
            self.assertTrue(second_conflict_path.exists())
            snapshot = service.load_snapshot()
            self.assertTrue(snapshot.state.has_unresolved_conflicts)
            self.assertEqual(
                [record.file_id for record in snapshot.document.files if record.status == "conflict_copy"],
                ["file-conflict-b"],
            )

    def test_resolve_conflicts_can_resolve_all_local_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service, _, _, payload, _ = self._seed_workspace(root)
            conflict_path = root / "Notes" / "Live (conflict 2026-04-29 Desktop-Win).md"
            conflict_path.write_bytes(payload + b" conflict")
            conflict_hash = "sha256:" + hashlib.sha256(conflict_path.read_bytes()).hexdigest()
            orphan_path = root / ".noteapp" / "conflict-orphans" / "Live orphan.md"
            orphan_path.parent.mkdir(parents=True, exist_ok=True)
            orphan_path.write_text("orphan conflict", encoding="utf-8")
            write_filemap_atomic(
                service.workspace.paths.filemap_path,
                FileMapDocument(
                    vault_id="vault-001",
                    updated_at=1770000040100,
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
                        ),
                        FileRecord(
                            file_id="file-conflict",
                            path="Notes/Live (conflict 2026-04-29 Desktop-Win).md",
                            type="note",
                            status="conflict_copy",
                            updated_at=1770000040100,
                            content_hash=conflict_hash,
                            conflict_source_file_id="file-live",
                        ),
                    ],
                ),
            )
            with closing(open_database(service.workspace.paths.db_path)) as connection:
                state = load_vault_state(connection, "vault-001")
                self.assertIsNotNone(state)
                upsert_vault_state(connection, replace(state, has_unresolved_conflicts=True))

            result = service.resolve_conflicts(
                resolved_at=1770000040200,
                resolve_all=True,
            )

            self.assertEqual(list(result.removed_conflict_paths), ["file-conflict"])
            self.assertEqual(result.removed_orphan_paths, [orphan_path])
            self.assertFalse(conflict_path.exists())
            self.assertFalse(orphan_path.exists())
            self.assertFalse(result.state.has_unresolved_conflicts)
            snapshot = service.load_snapshot()
            self.assertFalse(snapshot.state.has_unresolved_conflicts)
            self.assertEqual([record.file_id for record in snapshot.document.files], ["file-live"])

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

    def test_submit_detected_changes_if_needed_commits_pending_tombstones(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service, api_opener, blob_opener, _, _ = self._seed_workspace(Path(tmpdir))
            service.delete_workspace_note("file-live", now_ms=1770000030150)

            result = service.submit_detected_changes_if_needed(
                created_at=1770000030200,
                commit_intent_id="intent-tombstone-001",
            )

            self.assertIsNotNone(result)
            self.assertEqual(result.network.commit.status, "committed")
            self.assertEqual([call[0] for call in api_opener.calls], ["POST"])
            self.assertEqual(api_opener.calls[0][1], "https://sync.example.com/vaults/vault-001/commits")
            self.assertEqual(blob_opener.calls, [])
            self.assertEqual(
                [item.file_id for item in result.prepared.submission.manifest.tombstones],
                ["file-live"],
            )

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
            filemap = load_filemap(service.workspace.paths.filemap_path)
            self.assertEqual(filemap.files[0].content_hash, updated_hash)
            self.assertEqual(
                filemap.files[0].last_known_revision,
                result.network.commit.response.new_revision,
            )
            self.assertEqual(service.detect_local_changes().change_count, 0)

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
            filemap = load_filemap(service.workspace.paths.filemap_path)
            self.assertEqual([record for record in filemap.files if record.status == "active"], [])
            self.assertEqual(service.detect_local_changes().change_count, 0)

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
            filemap = load_filemap(service.workspace.paths.filemap_path)
            active_records = [record for record in filemap.files if record.status == "active"]
            self.assertEqual(len(active_records), 1)
            self.assertEqual(active_records[0].path, "Notes/New.md")
            self.assertEqual(active_records[0].last_known_revision, result.network.commit.response.new_revision)
            self.assertEqual(service.detect_local_changes().change_count, 0)

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
