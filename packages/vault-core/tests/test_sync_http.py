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
    CapabilityBlobDownloader,
    CapabilityBlobUploader,
    JsonHttpSyncTransport,
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
