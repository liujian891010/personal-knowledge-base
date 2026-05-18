from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

import sys

from fastapi.testclient import TestClient

SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

import main as server_main  # noqa: E402
from sync_store import SyncStore  # noqa: E402


class NoteappServerMainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_store = server_main.store
        server_main.store = SyncStore(Path(self.temp_dir.name))
        self.client = TestClient(server_main.app)

    def tearDown(self) -> None:
        server_main.store = self.original_store
        self.temp_dir.cleanup()

    def _register_device(self) -> dict[str, str]:
        response = self.client.post(
            "/devices/register",
            json={
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["device_id"])
        self.assertTrue(payload["access_token"])
        return payload

    def test_delete_device_revokes_protected_endpoints_and_download_capabilities(self) -> None:
        registered = self._register_device()
        headers = {"Authorization": f"Bearer {registered['access_token']}"}

        upload_init = self.client.post(
            "/vaults/vault-1/blobs/upload-init",
            json={
                "blobs": [
                    {
                        "blob_id": "blob-1",
                        "encrypted_size": 7,
                        "content_hash": "sha256:plain",
                    }
                ]
            },
            headers=headers,
        )
        self.assertEqual(upload_init.status_code, 200)
        upload_url = upload_init.json()["uploads"][0]["upload_url"]
        self.assertEqual(self.client.put(urlparse(upload_url).path, content=b"payload").status_code, 204)

        download_init = self.client.post(
            "/vaults/vault-1/blobs/download-init",
            json={"blob_ids": ["blob-1"]},
            headers=headers,
        )
        self.assertEqual(download_init.status_code, 200)
        stale_download_url = download_init.json()["downloads"][0]["download_url"]

        deleted = self.client.delete(f"/devices/{registered['device_id']}", headers=headers)
        self.assertEqual(deleted.status_code, 204)

        blocked_requests = [
            ("GET", "/vaults/vault-1/head", None),
            ("GET", "/vaults/vault-1/manifests/1", None),
            ("POST", "/vaults/vault-1/blobs/check", {}),
            ("POST", "/vaults/vault-1/blobs/upload-init", {}),
            ("POST", "/vaults/vault-1/blobs/download-init", {}),
            ("POST", "/vaults/vault-1/blobs/resumable-download-init", {}),
            ("POST", "/vaults/vault-1/commits", {}),
            ("POST", "/vaults/vault-1/ack", {}),
            ("POST", "/vaults/vault-1/tombstones/gc", {}),
        ]
        for method, path, body in blocked_requests:
            request_kwargs = {"headers": headers}
            if body is not None:
                request_kwargs["json"] = body
            response = self.client.request(method, path, **request_kwargs)
            self.assertEqual(response.status_code, 403, path)
            self.assertEqual(response.json()["code"], "device_revoked")

        stale_download = self.client.get(urlparse(stale_download_url).path)
        self.assertEqual(stale_download.status_code, 404)
        self.assertEqual(stale_download.json()["code"], "capability_not_found")


if __name__ == "__main__":
    unittest.main()
