from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from sync_store import CommitConflict, SyncStore  # noqa: E402


def _manifest(vault_id: str, *, base_revision: int, blob_id: str = "blob-1") -> dict[str, object]:
    return {
        "schema_version": "v1",
        "vault_id": vault_id,
        "revision": 0,
        "base_revision": base_revision,
        "created_by_device": "device-a",
        "created_at": 1770000000000,
        "files": [
            {
                "file_id": "file-1",
                "path": "Notes/hello.md",
                "type": "note",
                "content_hash": "sha256:plain",
                "blob_id": blob_id,
                "size": 5,
                "mtime": 1770000000000,
            }
        ],
        "tombstones": [],
        "summary_hash": "pending",
    }


class SyncStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SyncStore(Path(self.temp_dir.name))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_register_device_and_resolve_token(self) -> None:
        response = self.store.register_device(
            {
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
            }
        )

        self.assertTrue(response["device_id"])
        self.assertTrue(response["access_token"])
        self.assertEqual(
            self.store.device_id_for_token(response["access_token"]),
            response["device_id"],
        )

    def test_blob_capability_upload_check_and_download(self) -> None:
        upload = self.store.init_blob_upload(
            "vault-1",
            {
                "blobs": [
                    {
                        "blob_id": "blob-1",
                        "encrypted_size": 7,
                        "content_hash": "sha256:plain",
                    }
                ]
            },
            request_base_url="http://127.0.0.1:8000/",
            device_id="device-a",
        )
        token = upload["uploads"][0]["upload_url"].rsplit("/", 1)[-1]

        self.store.complete_blob_upload(token, b"payload")
        self.assertEqual(
            self.store.check_blobs("vault-1", {"blob_ids": ["blob-1", "blob-2"]}),
            {
                "existing_blob_ids": ["blob-1"],
                "missing_blob_ids": ["blob-2"],
            },
        )

        download = self.store.init_blob_download(
            "vault-1",
            {"blob_ids": ["blob-1"]},
            request_base_url="http://127.0.0.1:8000/",
            device_id="device-a",
        )
        download_token = download["downloads"][0]["download_url"].rsplit("/", 1)[-1]
        self.assertEqual(self.store.read_blob_download(download_token), b"payload")

    def test_create_commit_advances_head_and_stores_manifest(self) -> None:
        self.store.init_blob_upload(
            "vault-1",
            {
                "blobs": [
                    {
                        "blob_id": "blob-1",
                        "encrypted_size": 7,
                        "content_hash": "sha256:plain",
                    }
                ]
            },
            request_base_url="http://127.0.0.1:8000/",
            device_id="device-a",
        )
        state = self.store._load()
        capability_token = next(iter(state["capabilities"]))
        self.store.complete_blob_upload(capability_token, b"payload")

        response = self.store.create_commit(
            "vault-1",
            {
                "commit_intent_id": "intent-1",
                "base_revision": 0,
                "created_by_device": "device-a",
                "intent_manifest_hash": "sha256:intent",
                "manifest": _manifest("vault-1", base_revision=0),
                "blob_refs": [{"blob_id": "blob-1", "file_id": "file-1"}],
            },
            auth_device_id="device-a",
        )

        self.assertEqual(response["new_revision"], 1)
        self.assertEqual(response["acked_revision_for_device"], 1)
        self.assertEqual(self.store.get_vault_head("vault-1")["head_revision"], 1)
        manifest = self.store.get_manifest("vault-1", 1)
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["revision"], 1)
        self.assertEqual(manifest["base_revision"], 0)
        self.assertEqual(manifest["tombstones"], [])

    def test_create_commit_rejects_stale_base_revision(self) -> None:
        self.test_create_commit_advances_head_and_stores_manifest()

        with self.assertRaises(CommitConflict) as context:
            self.store.create_commit(
                "vault-1",
                {
                    "commit_intent_id": "intent-2",
                    "base_revision": 0,
                    "created_by_device": "device-b",
                    "intent_manifest_hash": "sha256:intent-2",
                    "manifest": _manifest("vault-1", base_revision=0, blob_id="blob-1"),
                    "blob_refs": [{"blob_id": "blob-1", "file_id": "file-1"}],
                },
                auth_device_id="device-b",
            )

        self.assertEqual(context.exception.payload["code"], "base_revision_conflict")
        self.assertEqual(context.exception.payload["current_head_revision"], 1)

    def test_resolve_commit_intent_reports_found_not_found_and_mismatch(self) -> None:
        self.test_create_commit_advances_head_and_stores_manifest()

        found = self.store.resolve_commit_intent(
            "vault-1",
            {
                "commit_intent_id": "intent-1",
                "intent_manifest_hash": "sha256:intent",
            },
        )
        self.assertEqual(found["status"], "found")
        self.assertEqual(found["matched_revision"], 1)

        missing = self.store.resolve_commit_intent(
            "vault-1",
            {
                "commit_intent_id": "intent-missing",
                "intent_manifest_hash": "sha256:intent",
            },
        )
        self.assertEqual(missing["status"], "not_found")

        mismatched = self.store.resolve_commit_intent(
            "vault-1",
            {
                "commit_intent_id": "intent-1",
                "intent_manifest_hash": "sha256:different",
            },
        )
        self.assertEqual(mismatched["status"], "mismatched")


if __name__ == "__main__":
    unittest.main()
