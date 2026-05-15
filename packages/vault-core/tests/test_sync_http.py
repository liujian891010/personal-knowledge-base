from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request

from vault_core import (
    BlobDownloadCapability,
    BlobUploadCapability,
    BlobUploadPlanEntry,
    BlobDownloadRange,
    CapabilityBlobDownloader,
    CapabilityBlobUploader,
    JsonHttpSyncTransport,
    ResumableBlobDownloadCapability,
    ResumableCapabilityBlobDownloader,
    ResumableBlobUploadCapability,
    ResumableBlobUploadChunkState,
    ResumableCapabilityBlobUploader,
    SyncHttpJsonResponse,
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


class RecordingUrlopen:
    def __init__(self, responses: list[FakeHttpResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[Request, float]] = []

    def __call__(self, request: Request, timeout: float) -> FakeHttpResponse:
        self.calls.append((request, timeout))
        if not self.responses:
            raise AssertionError("no fake HTTP response configured")
        return self.responses.pop(0)


class SyncHttpTests(unittest.TestCase):
    def _headers(self, request: Request) -> dict[str, str]:
        return {key.lower(): value for key, value in request.header_items()}

    def test_json_http_sync_transport_posts_json_with_bearer_auth(self) -> None:
        opener = RecordingUrlopen(
            [
                FakeHttpResponse(
                    status_code=200,
                    body=json.dumps(
                        {
                            "existing_blob_ids": ["blob_a"],
                            "missing_blob_ids": [],
                        }
                    ).encode("utf-8"),
                )
            ]
        )
        transport = JsonHttpSyncTransport(
            base_url="https://sync.example.test/api",
            bearer_token="token_123",
            timeout_seconds=12.5,
            opener=opener,
        )

        response = transport.post_blob_check(
            "vault_pkb_001",
            {"blob_ids": ["blob_a"]},
        )

        self.assertEqual(
            response,
            SyncHttpJsonResponse(
                status_code=200,
                payload={
                    "existing_blob_ids": ["blob_a"],
                    "missing_blob_ids": [],
                },
            ),
        )
        request, timeout = opener.calls[0]
        self.assertEqual(timeout, 12.5)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            request.full_url,
            "https://sync.example.test/api/vaults/vault_pkb_001/blobs/check",
        )
        self.assertEqual(self._headers(request)["authorization"], "Bearer token_123")
        self.assertEqual(json.loads(request.data.decode("utf-8")), {"blob_ids": ["blob_a"]})

    def test_json_http_sync_transport_gets_head_without_body(self) -> None:
        opener = RecordingUrlopen(
            [
                FakeHttpResponse(
                    status_code=200,
                    body=json.dumps(
                        {
                            "vault_id": "vault_pkb_001",
                            "head_revision": 8,
                            "manifest_summary": "sha256:head8",
                        }
                    ).encode("utf-8"),
                )
            ]
        )
        transport = JsonHttpSyncTransport(
            base_url="https://sync.example.test",
            opener=opener,
        )

        response = transport.get_vault_head("vault_pkb_001")

        self.assertEqual(response.status_code, 200)
        request, _ = opener.calls[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(request.full_url, "https://sync.example.test/vaults/vault_pkb_001/head")

    def test_json_http_sync_transport_calls_device_endpoints(self) -> None:
        opener = RecordingUrlopen(
            [
                FakeHttpResponse(status_code=200, body=json.dumps({"devices": []}).encode("utf-8")),
                FakeHttpResponse(status_code=200, body=json.dumps({"ok": True}).encode("utf-8")),
                FakeHttpResponse(status_code=200, body=json.dumps({"reclaimed_count": 0}).encode("utf-8")),
                FakeHttpResponse(status_code=204, body=b""),
            ]
        )
        transport = JsonHttpSyncTransport(
            base_url="https://sync.example.test",
            opener=opener,
        )

        transport.get_vault_devices("vault_pkb_001")
        transport.post_vault_device_heartbeat("vault_pkb_001")
        transport.post_tombstone_gc("vault_pkb_001", {"min_retention_ms": 0})
        transport.delete_device("dev_phone")

        list_request, _ = opener.calls[0]
        heartbeat_request, _ = opener.calls[1]
        tombstone_gc_request, _ = opener.calls[2]
        delete_request, _ = opener.calls[3]
        self.assertEqual(list_request.get_method(), "GET")
        self.assertIsNone(list_request.data)
        self.assertEqual(list_request.full_url, "https://sync.example.test/vaults/vault_pkb_001/devices")
        self.assertEqual(heartbeat_request.get_method(), "POST")
        self.assertIsNone(heartbeat_request.data)
        self.assertEqual(
            heartbeat_request.full_url,
            "https://sync.example.test/vaults/vault_pkb_001/devices/heartbeat",
        )
        self.assertEqual(tombstone_gc_request.get_method(), "POST")
        self.assertEqual(
            tombstone_gc_request.full_url,
            "https://sync.example.test/vaults/vault_pkb_001/tombstones/gc",
        )
        self.assertEqual(json.loads(tombstone_gc_request.data.decode("utf-8")), {"min_retention_ms": 0})
        self.assertEqual(delete_request.get_method(), "DELETE")
        self.assertEqual(delete_request.full_url, "https://sync.example.test/devices/dev_phone")

    def test_json_http_sync_transport_posts_file_version_endpoints(self) -> None:
        opener = RecordingUrlopen(
            [
                FakeHttpResponse(status_code=200, body=json.dumps({"versions": []}).encode("utf-8")),
                FakeHttpResponse(status_code=200, body=json.dumps({"version": {"version_id": "fv_a"}}).encode("utf-8")),
            ]
        )
        transport = JsonHttpSyncTransport(
            base_url="https://sync.example.test",
            opener=opener,
        )

        transport.post_file_versions_list("vault_pkb_001", {"file_id": "file_a", "limit": 20})
        transport.patch_file_version("vault_pkb_001", "fv_a", {"is_pinned": True})

        self.assertEqual(
            [call[0].full_url for call in opener.calls],
            [
                "https://sync.example.test/vaults/vault_pkb_001/file-versions/list",
                "https://sync.example.test/vaults/vault_pkb_001/file-versions/fv_a",
            ],
        )
        self.assertEqual(opener.calls[1][0].get_method(), "PATCH")

    def test_json_http_sync_transport_posts_resumable_upload_endpoints(self) -> None:
        opener = RecordingUrlopen(
            [
                FakeHttpResponse(status_code=200, body=json.dumps({"uploads": []}).encode("utf-8")),
                FakeHttpResponse(status_code=200, body=json.dumps({"uploads": []}).encode("utf-8")),
                FakeHttpResponse(status_code=200, body=json.dumps({"downloads": []}).encode("utf-8")),
            ]
        )
        transport = JsonHttpSyncTransport(
            base_url="https://sync.example.test",
            opener=opener,
        )

        transport.post_resumable_blob_upload_init("vault_pkb_001", {"blobs": []})
        transport.post_resumable_blob_upload_complete("vault_pkb_001", {"uploads": []})
        transport.post_resumable_blob_download_init("vault_pkb_001", {"blobs": []})

        self.assertEqual(
            [call[0].full_url for call in opener.calls],
            [
                "https://sync.example.test/vaults/vault_pkb_001/blobs/resumable-upload-init",
                "https://sync.example.test/vaults/vault_pkb_001/blobs/resumable-upload-complete",
                "https://sync.example.test/vaults/vault_pkb_001/blobs/resumable-download-init",
            ],
        )

    def test_capability_blob_uploader_puts_staging_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            staging_path = Path(tmpdir) / "blob_a.blob.staging"
            staging_path.write_bytes(b"encrypted-payload")
            opener = RecordingUrlopen([FakeHttpResponse(status_code=200, body=b"")])
            uploader = CapabilityBlobUploader(opener=opener)

            uploader.upload_blob(
                BlobUploadPlanEntry(
                    blob_id="blob_a",
                    content_hash="sha256:a",
                    encrypted_size=len(b"encrypted-payload"),
                    blob_staging_path=staging_path,
                    file_ids=["file_a"],
                ),
                BlobUploadCapability(
                    blob_id="blob_a",
                    upload_url="https://blob.example.test/upload/blob_a",
                    expires_at="2026-05-08T12:00:00Z",
                    headers={"x-upload-token": "token_a"},
                ),
            )

        request, _ = opener.calls[0]
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(request.full_url, "https://blob.example.test/upload/blob_a")
        self.assertEqual(request.data, b"encrypted-payload")
        self.assertEqual(self._headers(request)["x-upload-token"], "token_a")

    def test_resumable_capability_blob_uploader_puts_missing_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            staging_path = Path(tmpdir) / "blob_a.blob.staging"
            staging_path.write_bytes(b"0123456789")
            opener = RecordingUrlopen(
                [
                    FakeHttpResponse(status_code=204, body=b""),
                    FakeHttpResponse(status_code=204, body=b""),
                ]
            )
            uploader = ResumableCapabilityBlobUploader(opener=opener)

            uploaded_chunk_ids = uploader.upload_blob_chunks(
                BlobUploadPlanEntry(
                    blob_id="blob_a",
                    content_hash="sha256:a",
                    encrypted_size=10,
                    blob_staging_path=staging_path,
                    file_ids=["file_a"],
                ),
                ResumableBlobUploadCapability(
                    blob_id="blob_a",
                    session_id="session_a",
                    upload_url="https://blob.example.test/resumable/session_a",
                    expires_at="2026-05-08T12:00:00Z",
                    chunk_size=4,
                    encrypted_size=10,
                    uploaded_chunks=[
                        ResumableBlobUploadChunkState(
                            chunk_id="chunk-already",
                            offset=0,
                            size=4,
                            status="uploaded",
                        )
                    ],
                    missing_chunks=[
                        ResumableBlobUploadChunkState(
                            chunk_id="chunk-b",
                            offset=4,
                            size=4,
                            status="missing",
                        ),
                        ResumableBlobUploadChunkState(
                            chunk_id="chunk-c",
                            offset=8,
                            size=2,
                            status="missing",
                        ),
                    ],
                    headers={"x-upload-token": "token_a"},
                ),
            )

        self.assertEqual(uploaded_chunk_ids, ["chunk-already", "chunk-b", "chunk-c"])
        first_request, _ = opener.calls[0]
        second_request, _ = opener.calls[1]
        self.assertEqual(first_request.data, b"4567")
        self.assertEqual(second_request.data, b"89")
        self.assertEqual(self._headers(first_request)["x-noteapp-chunk-id"], "chunk-b")
        self.assertEqual(self._headers(first_request)["x-noteapp-chunk-offset"], "4")
        self.assertEqual(self._headers(first_request)["x-noteapp-chunk-size"], "4")
        self.assertEqual(self._headers(first_request)["x-upload-token"], "token_a")

    def test_resumable_capability_blob_downloader_gets_ranges(self) -> None:
        opener = RecordingUrlopen(
            [
                FakeHttpResponse(status_code=200, body=b"0123"),
                FakeHttpResponse(status_code=200, body=b"456789"),
            ]
        )
        downloader = ResumableCapabilityBlobDownloader(opener=opener)

        downloaded = downloader.download_blob_ranges(
            ResumableBlobDownloadCapability(
                blob_id="blob_a",
                download_url="https://blob.example.test/ranged/blob_a",
                encrypted_size=10,
                expires_at="2026-05-08T12:00:00Z",
                ranges=[
                    BlobDownloadRange(offset=0, size=4),
                    BlobDownloadRange(offset=4, size=6),
                ],
                headers={"x-download-token": "token_a"},
            )
        )

        self.assertEqual([item.offset for item in downloaded], [0, 4])
        self.assertEqual([item.payload for item in downloaded], [b"0123", b"456789"])
        first_request, _ = opener.calls[0]
        self.assertEqual(first_request.get_method(), "GET")
        self.assertEqual(self._headers(first_request)["x-noteapp-range-offset"], "0")
        self.assertEqual(self._headers(first_request)["x-noteapp-range-size"], "4")
        self.assertEqual(self._headers(first_request)["x-download-token"], "token_a")

    def test_capability_blob_downloader_reads_payload_and_validates_size(self) -> None:
        opener = RecordingUrlopen([FakeHttpResponse(status_code=200, body=b"encrypted-payload")])
        downloader = CapabilityBlobDownloader(opener=opener)

        payload = downloader.download_blob(
            BlobDownloadCapability(
                blob_id="blob_a",
                download_url="https://blob.example.test/download/blob_a",
                encrypted_size=len(b"encrypted-payload"),
                expires_at="2026-05-08T12:00:00Z",
                headers={"x-download-token": "token_a"},
            )
        )

        self.assertEqual(payload, b"encrypted-payload")
        request, _ = opener.calls[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(self._headers(request)["x-download-token"], "token_a")

        mismatch = CapabilityBlobDownloader(
            opener=RecordingUrlopen([FakeHttpResponse(status_code=200, body=b"short")])
        )
        with self.assertRaisesRegex(ValueError, "size does not match"):
            mismatch.download_blob(
                BlobDownloadCapability(
                    blob_id="blob_a",
                    download_url="https://blob.example.test/download/blob_a",
                    encrypted_size=12,
                    expires_at="2026-05-08T12:00:00Z",
                )
            )


if __name__ == "__main__":
    unittest.main()
