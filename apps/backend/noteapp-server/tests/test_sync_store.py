from __future__ import annotations

import hashlib
import sqlite3
import threading
import tempfile
import unittest
from pathlib import Path

import sys

SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))
sys.path.insert(0, str(SERVER_ROOT / "scripts"))

import backup_sqlite  # noqa: E402
import restore_sqlite  # noqa: E402
from blob_storage import BlobStorageError, FileSystemBlobStore  # noqa: E402
from sync_store import BlobCapabilityError, CommitConflict, SyncStore, SyncStoreError  # noqa: E402
from sync_repository import SQLiteStateRepository  # noqa: E402


def _manifest(
    vault_id: str,
    *,
    base_revision: int,
    blob_id: str = "blob-1",
    content_hash: str = "sha256:plain",
    path: str = "Notes/hello.md",
    created_at: int = 1770000000000,
    created_by_device: str = "device-a",
) -> dict[str, object]:
    return {
        "schema_version": "v1",
        "vault_id": vault_id,
        "revision": 0,
        "base_revision": base_revision,
        "created_by_device": created_by_device,
        "created_at": created_at,
        "files": [
            {
                "file_id": "file-1",
                "path": path,
                "type": "note",
                "content_hash": content_hash,
                "blob_id": blob_id,
                "size": 5,
                "mtime": created_at,
            }
        ],
        "tombstones": [],
        "summary_hash": "pending",
    }


def _deleted_manifest(
    vault_id: str,
    *,
    base_revision: int,
    deleted_at: int = 1770000000000,
    created_at: int = 1770000000000,
    created_by_device: str = "device-a",
) -> dict[str, object]:
    return {
        "schema_version": "v1",
        "vault_id": vault_id,
        "revision": 0,
        "base_revision": base_revision,
        "created_by_device": created_by_device,
        "created_at": created_at,
        "files": [],
        "tombstones": [
            {
                "file_id": "file-1",
                "deleted_revision": None,
                "deleted_at": deleted_at,
                "local_delete_seq": 1,
                "last_known_path": "Notes/hello.md",
                "deleted_by_device": created_by_device,
            }
        ],
        "summary_hash": "pending",
    }


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class UnavailableBlobStore:
    backend_name = "s3"

    def put_object(self, object_key: str, payload: bytes) -> None:
        raise BlobStorageError(503, "object_storage_unavailable", "Object storage is temporarily unavailable.")

    def read_object(self, object_key: str) -> bytes:
        raise BlobStorageError(503, "object_storage_unavailable", "Object storage is temporarily unavailable.")

    def read_range(self, object_key: str, *, offset: int, size: int) -> bytes:
        raise BlobStorageError(503, "object_storage_unavailable", "Object storage is temporarily unavailable.")


class SyncStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SyncStore(Path(self.temp_dir.name))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_register_device_and_resolve_token(self) -> None:
        response = self.store.register_device(
            {
                "account_key": "user@example.test",
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
            }
        )

        self.assertTrue(response["user_id"])
        self.assertTrue(response["device_id"])
        self.assertTrue(response["access_token"])
        self.assertTrue(response["refresh_token"])
        self.assertTrue(response["expires_at"])
        self.assertTrue(response["refresh_expires_at"])
        self.assertEqual(
            self.store.device_id_for_token(response["access_token"]),
            response["device_id"],
        )
        state = self.store._load()
        self.assertEqual(state["devices"][response["device_id"]]["user_id"], response["user_id"])
        self.assertEqual(len(state["users"]), 1)

    def test_password_account_requires_password_login_and_hashes_secret(self) -> None:
        registered = self.store.register_password_account(
            {
                "account_key": "secure@example.test",
                "password": "correct horse battery staple",
                "display_name": "Secure User",
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
            },
            now_ms=1_000,
        )

        state = self.store._load()
        user = state["users"][registered["user_id"]]
        self.assertEqual(user["auth_methods"], ["password"])
        self.assertIn("password_auth", user)
        self.assertNotIn("password", user)
        self.assertNotEqual(user["password_auth"]["hash"], "correct horse battery staple")

        with self.assertRaises(SyncStoreError) as password_required:
            self.store.register_device(
                {
                    "account_key": "secure@example.test",
                    "device_name": "Phone",
                    "platform": "mobile",
                }
            )
        self.assertEqual(password_required.exception.code, "password_required")

        with self.assertRaises(SyncStoreError) as invalid:
            self.store.login_password_account(
                {
                    "account_key": "secure@example.test",
                    "password": "wrong horse battery",
                    "device_name": "Desktop",
                    "platform": "desktop",
                }
            )
        self.assertEqual(invalid.exception.code, "invalid_credentials")

        logged_in = self.store.login_password_account(
            {
                "account_key": "secure@example.test",
                "password": "correct horse battery staple",
                "device_name": "Laptop",
                "platform": "desktop",
            },
            now_ms=2_000,
        )
        self.assertEqual(logged_in["user_id"], registered["user_id"])
        self.assertNotEqual(logged_in["device_id"], registered["device_id"])
        self.assertEqual(self.store.device_id_for_token(logged_in["access_token"], now_ms=2_100), logged_in["device_id"])

    def test_password_account_rejects_duplicate_and_short_password(self) -> None:
        with self.assertRaises(SyncStoreError) as short_password:
            self.store.register_password_account(
                {
                    "account_key": "short@example.test",
                    "password": "too-short",
                    "device_name": "Desktop",
                    "platform": "desktop",
                }
            )
        self.assertEqual(short_password.exception.code, "password_too_short")

        self.store.register_password_account(
            {
                "account_key": "secure@example.test",
                "password": "correct horse battery staple",
                "device_name": "Desktop",
                "platform": "desktop",
            }
        )
        with self.assertRaises(SyncStoreError) as duplicate:
            self.store.register_password_account(
                {
                    "account_key": "secure@example.test",
                    "password": "another correct battery staple",
                    "device_name": "Desktop",
                    "platform": "desktop",
                }
            )
        self.assertEqual(duplicate.exception.code, "account_exists")

    def test_diagnostics_reports_repository_blob_store_and_state_counts(self) -> None:
        self.store.register_device(
            {
                "account_key": "user@example.test",
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
            }
        )

        diagnostics = self.store.diagnostics()

        self.assertTrue(diagnostics["ok"])
        self.assertEqual(diagnostics["repository"]["type"], "json")
        self.assertTrue(diagnostics["blob_store"]["ok"])
        self.assertEqual(diagnostics["state"]["user_count"], 1)
        self.assertEqual(diagnostics["state"]["device_count"], 1)

    def test_access_token_expiry_and_refresh_rotation(self) -> None:
        registered = self.store.register_device(
            {
                "account_key": "user@example.test",
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
                "access_token_ttl_ms": 100,
                "refresh_token_ttl_ms": 10_000,
            },
            now_ms=1_000,
        )

        self.assertEqual(
            self.store.device_id_for_token(registered["access_token"], now_ms=1_050),
            registered["device_id"],
        )
        self.assertIsNone(self.store.device_id_for_token(registered["access_token"], now_ms=1_101))
        with self.assertRaises(SyncStoreError) as expired:
            self.store.authorize_access_token(registered["access_token"], now_ms=1_101)
        self.assertEqual(expired.exception.code, "access_token_expired")

        refreshed = self.store.refresh_session(
            {
                "refresh_token": registered["refresh_token"],
                "access_token_ttl_ms": 200,
                "refresh_token_ttl_ms": 10_000,
            },
            now_ms=1_200,
        )

        self.assertEqual(refreshed["user_id"], registered["user_id"])
        self.assertEqual(refreshed["device_id"], registered["device_id"])
        self.assertNotEqual(refreshed["access_token"], registered["access_token"])
        self.assertNotEqual(refreshed["refresh_token"], registered["refresh_token"])
        self.assertIsNone(self.store.device_id_for_token(registered["access_token"], now_ms=1_250))
        with self.assertRaises(SyncStoreError) as old_refresh:
            self.store.refresh_session({"refresh_token": registered["refresh_token"]}, now_ms=1_250)
        self.assertEqual(old_refresh.exception.code, "invalid_refresh_token")
        self.assertEqual(
            self.store.device_id_for_token(refreshed["access_token"], now_ms=1_300),
            registered["device_id"],
        )

    def test_expired_refresh_token_is_rejected(self) -> None:
        registered = self.store.register_device(
            {
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
                "refresh_token_ttl_ms": 100,
            },
            now_ms=1_000,
        )

        with self.assertRaises(SyncStoreError) as context:
            self.store.refresh_session({"refresh_token": registered["refresh_token"]}, now_ms=1_101)
        self.assertEqual(context.exception.code, "refresh_token_expired")

    def test_sqlite_repository_persists_revision_manifest_and_blob_metadata(self) -> None:
        db_path = Path(self.temp_dir.name) / "noteapp-server.sqlite3"
        store = SyncStore(Path(self.temp_dir.name), repository=SQLiteStateRepository(db_path))
        registered = store.register_device(
            {
                "account_key": "user@example.test",
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
            }
        )
        upload = store.init_blob_upload(
            "vault-sqlite",
            {
                "blobs": [
                    {
                        "blob_id": "blob-sqlite",
                        "encrypted_size": 7,
                        "content_hash": "sha256:plain",
                    }
                ]
            },
            request_base_url="http://127.0.0.1:8000/",
            device_id=registered["device_id"],
        )
        upload_token = upload["uploads"][0]["upload_url"].rsplit("/", 1)[-1]
        store.complete_blob_upload(upload_token, b"payload")
        commit = store.create_commit(
            "vault-sqlite",
            {
                "commit_intent_id": "intent-sqlite",
                "base_revision": 0,
                "created_by_device": registered["device_id"],
                "intent_manifest_hash": "sha256:intent-sqlite",
                "manifest": _manifest(
                    "vault-sqlite",
                    base_revision=0,
                    blob_id="blob-sqlite",
                    content_hash="sha256:plain",
                    created_by_device=registered["device_id"],
                ),
                "blob_refs": [{"blob_id": "blob-sqlite", "file_id": "file-1"}],
            },
            auth_device_id=registered["device_id"],
        )
        self.assertEqual(commit["new_revision"], 1)

        restarted = SyncStore(Path(self.temp_dir.name), repository=SQLiteStateRepository(db_path))
        self.assertEqual(restarted.device_id_for_token(registered["access_token"]), registered["device_id"])
        self.assertEqual(restarted.get_vault_head("vault-sqlite")["head_revision"], 1)
        self.assertEqual(restarted.get_manifest("vault-sqlite", 1)["files"][0]["blob_id"], "blob-sqlite")
        self.assertEqual(restarted.check_blobs("vault-sqlite", {"blob_ids": ["blob-sqlite"]})["existing_blob_ids"], ["blob-sqlite"])
        self.assertFalse((Path(self.temp_dir.name) / "sync-state.json").exists())

        connection = sqlite3.connect(db_path)
        try:
            self.assertEqual(
                connection.execute("SELECT head_revision FROM vaults WHERE vault_id = ?", ("vault-sqlite",)).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM manifests WHERE vault_id = ?", ("vault-sqlite",)).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT encrypted_size FROM blobs WHERE blob_id = ?", ("blob-sqlite",)).fetchone()[0],
                7,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 1").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 2").fetchone()[0],
                1,
            )
            projected_blob = connection.execute(
                "SELECT object_key, status FROM blobs WHERE blob_id = ?",
                ("blob-sqlite",),
            ).fetchone()
            self.assertTrue(projected_blob[0].startswith("vaults/vault-sqlite/blobs/"))
            self.assertEqual(projected_blob[1], "available")
        finally:
            connection.close()

    def test_sqlite_backup_and_restore_scripts_round_trip_state(self) -> None:
        db_path = Path(self.temp_dir.name) / "source.sqlite3"
        store = SyncStore(Path(self.temp_dir.name) / "source", repository=SQLiteStateRepository(db_path))
        registered = store.register_device(
            {
                "account_key": "user@example.test",
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
            }
        )
        backup_path = Path(self.temp_dir.name) / "backup.sqlite3"
        restored_path = Path(self.temp_dir.name) / "restored.sqlite3"

        backup_sqlite.backup_sqlite(db_path, backup_path)
        restore_sqlite.restore_sqlite(backup_path, restored_path)

        restored = SyncStore(Path(self.temp_dir.name) / "restored", repository=SQLiteStateRepository(restored_path))
        self.assertEqual(restored.device_id_for_token(registered["access_token"]), registered["device_id"])
        self.assertTrue(restored.diagnostics()["ok"])

    def test_sqlite_repository_imports_json_state(self) -> None:
        json_dir = Path(self.temp_dir.name) / "json"
        json_store = SyncStore(json_dir)
        registered = json_store.register_device(
            {
                "account_key": "user@example.test",
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
            }
        )
        json_store.get_vault_head("vault-import")

        db_path = Path(self.temp_dir.name) / "imported.sqlite3"
        SQLiteStateRepository(db_path).import_json_file(json_dir / "sync-state.json")

        imported = SyncStore(Path(self.temp_dir.name) / "sqlite", repository=SQLiteStateRepository(db_path))
        self.assertEqual(imported.device_id_for_token(registered["access_token"]), registered["device_id"])
        self.assertEqual(imported.get_vault_head("vault-import")["head_revision"], 0)
        connection = sqlite3.connect(db_path)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM devices WHERE device_id = ?", (registered["device_id"],)).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_sqlite_repository_serializes_concurrent_cas_commits(self) -> None:
        db_path = Path(self.temp_dir.name) / "cas.sqlite3"
        data_dir = Path(self.temp_dir.name) / "server"
        seed_store = SyncStore(data_dir, repository=SQLiteStateRepository(db_path))
        upload = seed_store.init_blob_upload(
            "vault-cas",
            {
                "blobs": [
                    {
                        "blob_id": "blob-cas",
                        "encrypted_size": 7,
                        "content_hash": "sha256:plain",
                    }
                ]
            },
            request_base_url="http://127.0.0.1:8000/",
            device_id="device-seed",
        )
        seed_store.complete_blob_upload(upload["uploads"][0]["upload_url"].rsplit("/", 1)[-1], b"payload")

        barrier = threading.Barrier(2)
        results: list[dict[str, object]] = []
        result_lock = threading.Lock()

        def worker(name: str) -> None:
            store = SyncStore(data_dir, repository=SQLiteStateRepository(db_path))
            payload = {
                "commit_intent_id": f"intent-{name}",
                "base_revision": 0,
                "created_by_device": f"device-{name}",
                "intent_manifest_hash": f"sha256:intent-{name}",
                "manifest": _manifest(
                    "vault-cas",
                    base_revision=0,
                    blob_id="blob-cas",
                    content_hash="sha256:plain",
                    path=f"Notes/{name}.md",
                    created_by_device=f"device-{name}",
                ),
                "blob_refs": [{"blob_id": "blob-cas", "file_id": f"file-{name}"}],
            }
            barrier.wait(timeout=5)
            try:
                committed = store.create_commit("vault-cas", payload, auth_device_id=f"device-{name}")
                outcome = {"name": name, "status": "committed", "revision": committed["new_revision"]}
            except CommitConflict as conflict:
                outcome = {"name": name, "status": "conflict", "code": conflict.payload["code"]}
            with result_lock:
                results.append(outcome)

        threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())

        self.assertEqual(sorted(result["status"] for result in results), ["committed", "conflict"])
        self.assertEqual([result["revision"] for result in results if result["status"] == "committed"], [1])
        self.assertEqual([result["code"] for result in results if result["status"] == "conflict"], ["base_revision_conflict"])
        final_store = SyncStore(data_dir, repository=SQLiteStateRepository(db_path))
        self.assertEqual(final_store.get_vault_head("vault-cas")["head_revision"], 1)

    def test_list_vault_devices_and_heartbeat_report_ack_and_inactive_state(self) -> None:
        desktop = self.store.register_device(
            {
                "device_name": "Desktop",
                "platform": "desktop",
                "app_version": "1.0.43",
                "protocol_version": "v1",
            }
        )
        mobile = self.store.register_device(
            {
                "device_name": "Phone",
                "platform": "mobile",
                "protocol_version": "v1",
            }
        )

        self.store.get_vault_head("vault-1")
        state = self.store._load()
        vault = state["vaults"]["vault-1"]
        vault["head_revision"] = 8
        vault["acks"] = {
            desktop["device_id"]: 8,
            mobile["device_id"]: 3,
        }
        state["devices"][desktop["device_id"]]["last_seen_at_ms"] = 1770000000000
        state["devices"][mobile["device_id"]]["last_seen_at_ms"] = 1769000000000
        self.store._save(state)

        listed = self.store.list_vault_devices(
            "vault-1",
            current_device_id=desktop["device_id"],
            now_ms=1770000005000,
            inactive_after_ms=60_000,
        )

        self.assertEqual(listed["head_revision"], 8)
        self.assertEqual(listed["inactive_after_ms"], 60_000)
        self.assertEqual([item["device_id"] for item in listed["devices"]], [desktop["device_id"], mobile["device_id"]])
        self.assertTrue(listed["devices"][0]["is_current_device"])
        self.assertFalse(listed["devices"][0]["is_inactive_candidate"])
        self.assertEqual(listed["devices"][0]["acked_revision"], 8)
        self.assertEqual(listed["devices"][0]["app_version"], "1.0.43")
        self.assertFalse(listed["devices"][0]["is_revoked"])
        self.assertFalse("access_token" in listed["devices"][0])
        self.assertTrue(listed["devices"][1]["is_inactive_candidate"])
        self.assertEqual(listed["devices"][1]["acked_revision"], 3)

        heartbeat = self.store.heartbeat_device(
            "vault-1",
            device_id=mobile["device_id"],
            now_ms=1770000006000,
        )
        self.assertEqual(heartbeat["last_seen_at_ms"], 1770000006000)
        self.assertEqual(heartbeat["acked_revision"], 3)
        self.assertEqual(heartbeat["head_revision"], 8)

        refreshed = self.store.list_vault_devices(
            "vault-1",
            current_device_id=desktop["device_id"],
            now_ms=1770000006000,
            inactive_after_ms=60_000,
        )
        refreshed_mobile = next(item for item in refreshed["devices"] if item["device_id"] == mobile["device_id"])
        self.assertFalse(refreshed_mobile["is_inactive_candidate"])

    def test_list_vault_devices_omits_registered_devices_not_joined_to_vault(self) -> None:
        desktop = self.store.register_device(
            {
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
            }
        )
        phone = self.store.register_device(
            {
                "device_name": "Phone",
                "platform": "mobile",
                "protocol_version": "v1",
            }
        )
        self.store.get_vault_head("vault-1")
        state = self.store._load()
        state["vaults"]["vault-1"]["acks"] = {desktop["device_id"]: 0}
        self.store._save(state)

        listed = self.store.list_vault_devices(
            "vault-1",
            current_device_id=desktop["device_id"],
            now_ms=1770000005000,
        )

        self.assertEqual([item["device_id"] for item in listed["devices"]], [desktop["device_id"]])
        self.assertNotIn(phone["device_id"], [item["device_id"] for item in listed["devices"]])

    def test_device_enters_active_set_after_first_commit_or_ack(self) -> None:
        desktop = self.store.register_device(
            {
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
            }
        )
        phone = self.store.register_device(
            {
                "device_name": "Phone",
                "platform": "mobile",
                "protocol_version": "v1",
            }
        )
        desktop_id = desktop["device_id"]
        phone_id = phone["device_id"]

        self.store.get_vault_head("vault-1")
        state = self.store._load()
        state["vaults"]["vault-1"]["acks"] = {}
        self.store._save(state)

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
            device_id=desktop_id,
        )
        state = self.store._load()
        capability_token = next(iter(state["capabilities"]))
        self.store.complete_blob_upload(capability_token, b"payload")

        before_commit = self.store.list_vault_devices(
            "vault-1",
            current_device_id=desktop_id,
            now_ms=1770000005000,
        )
        self.assertEqual(before_commit["devices"], [])

        committed = self.store.create_commit(
            "vault-1",
            {
                "commit_intent_id": "intent-join-1",
                "base_revision": 0,
                "created_by_device": desktop_id,
                "intent_manifest_hash": "sha256:intent-join-1",
                "manifest": _manifest("vault-1", base_revision=0, created_by_device=desktop_id),
                "blob_refs": [{"blob_id": "blob-1", "file_id": "file-1"}],
            },
            auth_device_id=desktop_id,
        )
        self.assertEqual(committed["acked_revision_for_device"], 1)

        after_commit = self.store.list_vault_devices(
            "vault-1",
            current_device_id=desktop_id,
            now_ms=1770000006000,
        )
        self.assertEqual([item["device_id"] for item in after_commit["devices"]], [desktop_id])
        self.assertEqual(after_commit["devices"][0]["acked_revision"], 1)

        acked = self.store.ack_revisions("vault-1", {"revisions": [1]}, device_id=phone_id)
        self.assertEqual(acked["max_acked_revision"], 1)

        after_ack = self.store.list_vault_devices(
            "vault-1",
            current_device_id=desktop_id,
            now_ms=1770000007000,
        )
        self.assertEqual([item["device_id"] for item in after_ack["devices"]], [desktop_id, phone_id])
        self.assertEqual(after_ack["devices"][1]["acked_revision"], 1)

        self.store.create_commit(
            "vault-1",
            {
                "commit_intent_id": "intent-join-2",
                "base_revision": 1,
                "created_by_device": desktop_id,
                "intent_manifest_hash": "sha256:intent-join-2",
                "manifest": _deleted_manifest(
                    "vault-1",
                    base_revision=1,
                    deleted_at=1,
                    created_at=1770000008000,
                    created_by_device=desktop_id,
                ),
                "blob_refs": [],
            },
            auth_device_id=desktop_id,
        )

        gc_result = self.store.run_tombstone_gc(
            "vault-1",
            now_ms=1770000009000,
            min_retention_ms=0,
        )
        self.assertCountEqual(gc_result["active_device_ids"], [desktop_id, phone_id])
        self.assertEqual(gc_result["blocked_tombstones"][0]["blocked_by_devices"][0]["device_id"], phone_id)

    def test_delete_device_revokes_token_and_device_capabilities(self) -> None:
        registered = self.store.register_device(
            {
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
            }
        )
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
            device_id=registered["device_id"],
        )
        capability_token = upload["uploads"][0]["upload_url"].rsplit("/", 1)[-1]

        self.assertTrue(self.store.delete_device(registered["device_id"]))

        self.assertIsNone(self.store.device_id_for_token(registered["access_token"]))
        with self.assertRaises(SyncStoreError) as refresh_context:
            self.store.refresh_session({"refresh_token": registered["refresh_token"]})
        self.assertEqual(refresh_context.exception.code, "invalid_refresh_token")
        with self.assertRaises(BlobCapabilityError) as context:
            self.store.complete_blob_upload(capability_token, b"payload")
        self.assertEqual(context.exception.code, "capability_not_found")

    def test_delete_device_revokes_unconsumed_download_capability(self) -> None:
        registered = self.store.register_device(
            {
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
            }
        )
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
            device_id=registered["device_id"],
        )
        upload_token = upload["uploads"][0]["upload_url"].rsplit("/", 1)[-1]
        self.store.complete_blob_upload(upload_token, b"payload")
        download = self.store.init_blob_download(
            "vault-1",
            {"blob_ids": ["blob-1"]},
            request_base_url="http://127.0.0.1:8000/",
            device_id=registered["device_id"],
        )
        download_token = download["downloads"][0]["download_url"].rsplit("/", 1)[-1]

        self.assertTrue(self.store.delete_device(registered["device_id"]))

        with self.assertRaises(BlobCapabilityError) as context:
            self.store.read_blob_download(download_token)
        self.assertEqual(context.exception.code, "capability_not_found")

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

    def test_filesystem_object_storage_uses_service_generated_object_key(self) -> None:
        object_dir = Path(self.temp_dir.name) / "object-store"
        store = SyncStore(
            Path(self.temp_dir.name) / "server",
            blob_store=FileSystemBlobStore(object_dir, backend_name="filesystem"),
        )
        payload = b"payload"
        upload = store.init_blob_upload(
            "vault-1",
            {
                "blobs": [
                    {
                        "blob_id": "blob-object",
                        "encrypted_size": len(payload),
                        "content_hash": "sha256:plain",
                        "encrypted_sha256": _sha256(payload),
                    }
                ]
            },
            request_base_url="http://127.0.0.1:8000/",
            device_id="device-a",
        )
        upload_token = upload["uploads"][0]["upload_url"].rsplit("/", 1)[-1]

        store.complete_blob_upload(upload_token, payload)

        state = store._load()
        blob = state["blobs"]["blob-object"]
        self.assertEqual(blob["storage_backend"], "filesystem")
        self.assertEqual(blob["status"], "available")
        self.assertEqual(blob["encrypted_sha256"], _sha256(payload))
        self.assertTrue(blob["object_key"].startswith("vaults/vault-1/blobs/"))
        self.assertTrue((object_dir / blob["object_key"]).exists())

        download = store.init_blob_download(
            "vault-1",
            {"blob_ids": ["blob-object"]},
            request_base_url="http://127.0.0.1:8000/",
            device_id="device-a",
        )
        download_token = download["downloads"][0]["download_url"].rsplit("/", 1)[-1]
        self.assertEqual(store.read_blob_download(download_token), payload)

    def test_object_storage_unavailable_is_explainable_and_keeps_upload_capability(self) -> None:
        store = SyncStore(Path(self.temp_dir.name) / "server", blob_store=UnavailableBlobStore())
        upload = store.init_blob_upload(
            "vault-1",
            {
                "blobs": [
                    {
                        "blob_id": "blob-unavailable",
                        "encrypted_size": 7,
                        "content_hash": "sha256:plain",
                    }
                ]
            },
            request_base_url="http://127.0.0.1:8000/",
            device_id="device-a",
        )
        upload_token = upload["uploads"][0]["upload_url"].rsplit("/", 1)[-1]

        with self.assertRaises(BlobCapabilityError) as context:
            store.complete_blob_upload(upload_token, b"payload")

        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(context.exception.code, "object_storage_unavailable")
        self.assertIn(upload_token, store._load()["capabilities"])

    def test_resumable_blob_upload_tracks_chunks_and_completes_blob(self) -> None:
        payload = b"encrypted-payload"
        init = self.store.init_resumable_blob_upload(
            "vault-1",
            {
                "blobs": [
                    {
                        "blob_id": "blob-resumable-1",
                        "encrypted_size": len(payload),
                        "content_hash": "sha256:plain",
                        "encrypted_sha256": _sha256(payload),
                        "chunk_size": 5,
                    }
                ]
            },
            request_base_url="http://127.0.0.1:8000/",
            device_id="device-a",
        )
        upload = init["uploads"][0]
        self.assertEqual(upload["uploaded_chunks"], [])
        self.assertEqual(len(upload["missing_chunks"]), 4)

        first_chunk = upload["missing_chunks"][0]
        second_chunk = upload["missing_chunks"][1]
        session_id = upload["session_id"]
        self.store.put_resumable_blob_chunk(
            session_id,
            chunk_id=first_chunk["chunk_id"],
            offset=first_chunk["offset"],
            size=first_chunk["size"],
            payload=payload[first_chunk["offset"] : first_chunk["offset"] + first_chunk["size"]],
        )
        self.store.put_resumable_blob_chunk(
            session_id,
            chunk_id=second_chunk["chunk_id"],
            offset=second_chunk["offset"],
            size=second_chunk["size"],
            payload=payload[second_chunk["offset"] : second_chunk["offset"] + second_chunk["size"]],
        )

        resumed = self.store.init_resumable_blob_upload(
            "vault-1",
            {
                "blobs": [
                    {
                        "blob_id": "blob-resumable-1",
                        "encrypted_size": len(payload),
                        "content_hash": "sha256:plain",
                        "encrypted_sha256": _sha256(payload),
                        "chunk_size": 5,
                    }
                ]
            },
            request_base_url="http://127.0.0.1:8000/",
            device_id="device-a",
        )
        resumed_upload = resumed["uploads"][0]
        self.assertEqual(resumed_upload["session_id"], session_id)
        self.assertEqual([chunk["status"] for chunk in resumed_upload["uploaded_chunks"]], ["uploaded", "uploaded"])
        self.assertEqual(len(resumed_upload["missing_chunks"]), 2)

        incomplete = self.store.complete_resumable_blob_upload(
            "vault-1",
            {
                "uploads": [
                    {
                        "blob_id": "blob-resumable-1",
                        "session_id": session_id,
                        "encrypted_size": len(payload),
                        "encrypted_sha256": _sha256(payload),
                        "uploaded_chunk_ids": [chunk["chunk_id"] for chunk in resumed_upload["uploaded_chunks"]],
                    }
                ]
            },
        )
        self.assertEqual(incomplete["uploads"][0]["status"], "incomplete")
        self.assertEqual(len(incomplete["uploads"][0]["missing_chunks"]), 2)
        self.assertEqual(
            self.store.check_blobs("vault-1", {"blob_ids": ["blob-resumable-1"]})["missing_blob_ids"],
            ["blob-resumable-1"],
        )

        all_chunks = resumed_upload["uploaded_chunks"] + resumed_upload["missing_chunks"]
        for chunk in resumed_upload["missing_chunks"]:
            self.store.put_resumable_blob_chunk(
                session_id,
                chunk_id=chunk["chunk_id"],
                offset=chunk["offset"],
                size=chunk["size"],
                payload=payload[chunk["offset"] : chunk["offset"] + chunk["size"]],
            )

        accepted = self.store.complete_resumable_blob_upload(
            "vault-1",
            {
                "uploads": [
                    {
                        "blob_id": "blob-resumable-1",
                        "session_id": session_id,
                        "encrypted_size": len(payload),
                        "encrypted_sha256": _sha256(payload),
                        "uploaded_chunk_ids": [chunk["chunk_id"] for chunk in all_chunks],
                    }
                ]
            },
        )
        self.assertEqual(accepted["uploads"][0]["status"], "accepted")
        self.assertEqual(
            self.store.check_blobs("vault-1", {"blob_ids": ["blob-resumable-1"]})["existing_blob_ids"],
            ["blob-resumable-1"],
        )
        download = self.store.init_blob_download(
            "vault-1",
            {"blob_ids": ["blob-resumable-1"]},
            request_base_url="http://127.0.0.1:8000/",
            device_id="device-a",
        )
        download_token = download["downloads"][0]["download_url"].rsplit("/", 1)[-1]
        self.assertEqual(self.store.read_blob_download(download_token), payload)

    def test_resumable_blob_upload_rejects_wrong_chunk_metadata_and_hash(self) -> None:
        payload = b"encrypted-payload"
        init = self.store.init_resumable_blob_upload(
            "vault-1",
            {
                "blobs": [
                    {
                        "blob_id": "blob-resumable-2",
                        "encrypted_size": len(payload),
                        "content_hash": "sha256:plain",
                        "encrypted_sha256": "sha256:wrong",
                        "chunk_size": len(payload),
                    }
                ]
            },
            request_base_url="http://127.0.0.1:8000/",
            device_id="device-a",
        )
        upload = init["uploads"][0]
        chunk = upload["missing_chunks"][0]

        with self.assertRaises(BlobCapabilityError) as context:
            self.store.put_resumable_blob_chunk(
                upload["session_id"],
                chunk_id=chunk["chunk_id"],
                offset=chunk["offset"] + 1,
                size=chunk["size"],
                payload=payload,
            )
        self.assertEqual(context.exception.code, "chunk_range_mismatch")

        self.store.put_resumable_blob_chunk(
            upload["session_id"],
            chunk_id=chunk["chunk_id"],
            offset=chunk["offset"],
            size=chunk["size"],
            payload=payload,
        )
        with self.assertRaisesRegex(ValueError, "encrypted_sha256"):
            self.store.complete_resumable_blob_upload(
                "vault-1",
                {
                    "uploads": [
                        {
                            "blob_id": "blob-resumable-2",
                            "session_id": upload["session_id"],
                            "encrypted_size": len(payload),
                            "encrypted_sha256": "sha256:wrong",
                            "uploaded_chunk_ids": [chunk["chunk_id"]],
                        }
                    ]
                },
            )

    def test_resumable_blob_download_returns_authorized_ranges(self) -> None:
        payload = b"encrypted-payload"
        upload = self.store.init_blob_upload(
            "vault-1",
            {
                "blobs": [
                    {
                        "blob_id": "blob-ranged-download",
                        "encrypted_size": len(payload),
                        "content_hash": "sha256:plain",
                    }
                ]
            },
            request_base_url="http://127.0.0.1:8000/",
            device_id="device-a",
        )
        upload_token = upload["uploads"][0]["upload_url"].rsplit("/", 1)[-1]
        self.store.complete_blob_upload(upload_token, payload)

        download = self.store.init_resumable_blob_download(
            "vault-1",
            {
                "blobs": [
                    {
                        "blob_id": "blob-ranged-download",
                        "ranges": [
                            {"offset": 0, "size": 5},
                            {"offset": 5, "size": len(payload) - 5},
                        ],
                    }
                ]
            },
            request_base_url="http://127.0.0.1:8000/",
            device_id="device-a",
        )
        capability = download["downloads"][0]
        token = capability["download_url"].rsplit("/", 1)[-1]

        self.assertEqual(capability["encrypted_size"], len(payload))
        self.assertEqual(capability["ranges"], [{"offset": 0, "size": 5}, {"offset": 5, "size": len(payload) - 5}])
        self.assertEqual(
            self.store.read_resumable_blob_download(token, offset=0, size=5),
            payload[:5],
        )
        self.assertEqual(
            self.store.read_resumable_blob_download(token, offset=5, size=len(payload) - 5),
            payload[5:],
        )

        with self.assertRaises(BlobCapabilityError) as context:
            self.store.read_resumable_blob_download(token, offset=1, size=len(payload))
        self.assertEqual(context.exception.code, "range_not_authorized")

    def test_resumable_blob_download_rejects_invalid_range_request(self) -> None:
        payload = b"encrypted-payload"
        upload = self.store.init_blob_upload(
            "vault-1",
            {
                "blobs": [
                    {
                        "blob_id": "blob-ranged-download-invalid",
                        "encrypted_size": len(payload),
                        "content_hash": "sha256:plain",
                    }
                ]
            },
            request_base_url="http://127.0.0.1:8000/",
            device_id="device-a",
        )
        upload_token = upload["uploads"][0]["upload_url"].rsplit("/", 1)[-1]
        self.store.complete_blob_upload(upload_token, payload)

        with self.assertRaisesRegex(ValueError, "exceeds"):
            self.store.init_resumable_blob_download(
                "vault-1",
                {
                    "blobs": [
                        {
                            "blob_id": "blob-ranged-download-invalid",
                            "ranges": [{"offset": len(payload), "size": 1}],
                        }
                    ]
                },
                request_base_url="http://127.0.0.1:8000/",
                device_id="device-a",
            )

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

    def test_tombstone_gc_waits_for_all_joined_active_devices_ack(self) -> None:
        desktop = self.store.register_device(
            {
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
            }
        )
        phone = self.store.register_device(
            {
                "device_name": "Phone",
                "platform": "mobile",
                "protocol_version": "v1",
            }
        )
        unjoined = self.store.register_device(
            {
                "device_name": "Unjoined",
                "platform": "desktop",
                "protocol_version": "v1",
            }
        )
        desktop_id = desktop["device_id"]
        phone_id = phone["device_id"]

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
            device_id=desktop_id,
        )
        state = self.store._load()
        capability_token = next(iter(state["capabilities"]))
        self.store.complete_blob_upload(capability_token, b"payload")

        self.store.create_commit(
            "vault-1",
            {
                "commit_intent_id": "intent-gc-file",
                "base_revision": 0,
                "created_by_device": desktop_id,
                "intent_manifest_hash": "sha256:intent-gc-file",
                "manifest": _manifest("vault-1", base_revision=0, created_by_device=desktop_id),
                "blob_refs": [{"blob_id": "blob-1", "file_id": "file-1"}],
            },
            auth_device_id=desktop_id,
        )
        self.store.ack_revisions("vault-1", {"revisions": [1]}, device_id=phone_id)

        deleted = self.store.create_commit(
            "vault-1",
            {
                "commit_intent_id": "intent-gc-delete",
                "base_revision": 1,
                "created_by_device": desktop_id,
                "intent_manifest_hash": "sha256:intent-gc-delete",
                "manifest": _deleted_manifest(
                    "vault-1",
                    base_revision=1,
                    deleted_at=1,
                    created_at=1770000010000,
                    created_by_device=desktop_id,
                ),
                "blob_refs": [],
            },
            auth_device_id=desktop_id,
        )
        self.assertEqual(deleted["new_revision"], 2)

        blocked = self.store.run_tombstone_gc(
            "vault-1",
            now_ms=1770000020000,
            min_retention_ms=0,
        )

        self.assertIsNone(blocked["new_revision"])
        self.assertEqual(blocked["head_revision"], 2)
        self.assertEqual(blocked["reclaimed_count"], 0)
        self.assertCountEqual(blocked["active_device_ids"], [desktop_id, phone_id])
        self.assertNotIn(unjoined["device_id"], blocked["active_device_ids"])
        self.assertEqual(blocked["blocked_tombstones"][0]["reason"], "waiting_for_ack")
        self.assertEqual(blocked["blocked_tombstones"][0]["blocked_by_devices"][0]["device_id"], phone_id)

        acked = self.store.ack_revisions("vault-1", {"revisions": [2]}, device_id=phone_id)
        self.assertEqual(acked["max_acked_revision"], 2)
        self.assertEqual(self.store.get_vault_head("vault-1")["head_revision"], 3)
        gc_manifest = self.store.get_manifest("vault-1", 3)
        self.assertEqual(gc_manifest["base_revision"], 2)
        self.assertEqual(gc_manifest["tombstones"], [])
        state = self.store._load()
        runs = state["vaults"]["vault-1"]["tombstone_gc"]["runs"]
        self.assertEqual(runs[-1]["reason"], "reclaimed")
        self.assertEqual(runs[-1]["reclaimed_tombstones"][0]["file_id"], "file-1")

    def test_delete_device_triggers_tombstone_gc_after_blocker_removed(self) -> None:
        desktop = self.store.register_device(
            {
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
            }
        )
        phone = self.store.register_device(
            {
                "device_name": "Phone",
                "platform": "mobile",
                "protocol_version": "v1",
            }
        )
        desktop_id = desktop["device_id"]
        phone_id = phone["device_id"]

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
            device_id=desktop_id,
        )
        state = self.store._load()
        capability_token = next(iter(state["capabilities"]))
        self.store.complete_blob_upload(capability_token, b"payload")
        self.store.create_commit(
            "vault-1",
            {
                "commit_intent_id": "intent-gc-remove-file",
                "base_revision": 0,
                "created_by_device": desktop_id,
                "intent_manifest_hash": "sha256:intent-gc-remove-file",
                "manifest": _manifest("vault-1", base_revision=0, created_by_device=desktop_id),
                "blob_refs": [{"blob_id": "blob-1", "file_id": "file-1"}],
            },
            auth_device_id=desktop_id,
        )
        self.store.ack_revisions("vault-1", {"revisions": [1]}, device_id=phone_id)
        self.store.create_commit(
            "vault-1",
            {
                "commit_intent_id": "intent-gc-remove-delete",
                "base_revision": 1,
                "created_by_device": desktop_id,
                "intent_manifest_hash": "sha256:intent-gc-remove-delete",
                "manifest": _deleted_manifest(
                    "vault-1",
                    base_revision=1,
                    deleted_at=1,
                    created_at=1770000010000,
                    created_by_device=desktop_id,
                ),
                "blob_refs": [],
            },
            auth_device_id=desktop_id,
        )

        self.assertEqual(self.store.get_vault_head("vault-1")["head_revision"], 2)
        self.assertTrue(self.store.delete_device(phone_id))

        self.assertEqual(self.store.get_vault_head("vault-1")["head_revision"], 3)
        gc_manifest = self.store.get_manifest("vault-1", 3)
        self.assertEqual(gc_manifest["tombstones"], [])
        listed = self.store.list_vault_devices(
            "vault-1",
            current_device_id=desktop_id,
            now_ms=1770000020000,
        )
        self.assertEqual([item["device_id"] for item in listed["devices"]], [desktop_id, phone_id])
        self.assertTrue(next(item for item in listed["devices"] if item["device_id"] == phone_id)["is_revoked"])

    def test_create_commit_writes_file_versions_by_stable_file_id(self) -> None:
        self.store.init_blob_upload(
            "vault-1",
            {
                "blobs": [
                    {
                        "blob_id": "blob-meeting-v1",
                        "encrypted_size": 7,
                        "content_hash": "sha256:meeting-v1",
                    },
                    {
                        "blob_id": "blob-meeting-v2",
                        "encrypted_size": 7,
                        "content_hash": "sha256:meeting-v2",
                    },
                    {
                        "blob_id": "blob-meeting-v3",
                        "encrypted_size": 7,
                        "content_hash": "sha256:meeting-v3",
                    },
                ]
            },
            request_base_url="http://127.0.0.1:8000/",
            device_id="device-a",
        )
        state = self.store._load()
        for token in list(state["capabilities"]):
            self.store.complete_blob_upload(token, b"payload")

        first = self.store.create_commit(
            "vault-1",
            {
                "commit_intent_id": "intent-meeting-1",
                "base_revision": 0,
                "created_by_device": "device-a",
                "intent_manifest_hash": "sha256:intent-meeting-1",
                "manifest": _manifest(
                    "vault-1",
                    base_revision=0,
                    blob_id="blob-meeting-v1",
                    content_hash="sha256:meeting-v1",
                    path="Meetings/XXX.md",
                    created_at=1770000000000,
                ),
                "blob_refs": [{"blob_id": "blob-meeting-v1", "file_id": "file-1"}],
            },
            auth_device_id="device-a",
        )
        self.assertEqual(first["new_revision"], 1)

        second = self.store.create_commit(
            "vault-1",
            {
                "commit_intent_id": "intent-meeting-2",
                "base_revision": 1,
                "created_by_device": "device-a",
                "intent_manifest_hash": "sha256:intent-meeting-2",
                "manifest": _manifest(
                    "vault-1",
                    base_revision=1,
                    blob_id="blob-meeting-v2",
                    content_hash="sha256:meeting-v2",
                    path="Meetings/XXX.md",
                    created_at=1770000010000,
                ),
                "blob_refs": [{"blob_id": "blob-meeting-v2", "file_id": "file-1"}],
                "file_version_directives": [
                    {
                        "file_id": "file-1",
                        "source": "manual_meeting_checkpoint",
                        "version_label": "2026-05-15 周会",
                        "change_note": "会后确认版",
                        "is_pinned": True,
                    }
                ],
            },
            auth_device_id="device-a",
        )
        self.assertEqual(second["new_revision"], 2)

        moved = self.store.create_commit(
            "vault-1",
            {
                "commit_intent_id": "intent-meeting-3",
                "base_revision": 2,
                "created_by_device": "device-a",
                "intent_manifest_hash": "sha256:intent-meeting-3",
                "manifest": _manifest(
                    "vault-1",
                    base_revision=2,
                    blob_id="blob-meeting-v3",
                    content_hash="sha256:meeting-v3",
                    path="Archive/Meetings/XXX.md",
                    created_at=1770000020000,
                ),
                "blob_refs": [{"blob_id": "blob-meeting-v3", "file_id": "file-1"}],
            },
            auth_device_id="device-a",
        )
        self.assertEqual(moved["new_revision"], 3)

        listed = self.store.list_file_versions("vault-1", {"file_id": "file-1", "limit": 10})
        self.assertEqual([item["revision"] for item in listed["versions"]], [3, 2, 1])
        self.assertEqual([item["file_id"] for item in listed["versions"]], ["file-1", "file-1", "file-1"])
        self.assertEqual(listed["versions"][0]["path_at_revision"], "Archive/Meetings/XXX.md")
        self.assertEqual(listed["versions"][1]["source"], "manual_meeting_checkpoint")
        self.assertEqual(listed["versions"][1]["version_label"], "2026-05-15 周会")
        self.assertTrue(listed["versions"][1]["is_pinned"])

        updated = self.store.update_file_version(
            "vault-1",
            listed["versions"][1]["version_id"],
            {
                "version_label": "客户会议复盘",
                "change_note": "补充行动项",
                "is_pinned": False,
            },
        )
        self.assertEqual(updated["version"]["version_label"], "客户会议复盘")
        self.assertEqual(updated["version"]["change_note"], "补充行动项")
        self.assertFalse(updated["version"]["is_pinned"])

    def test_create_commit_does_not_version_unchanged_blob_ref_without_directive(self) -> None:
        self.test_create_commit_advances_head_and_stores_manifest()

        self.store.create_commit(
            "vault-1",
            {
                "commit_intent_id": "intent-unchanged",
                "base_revision": 1,
                "created_by_device": "device-a",
                "intent_manifest_hash": "sha256:intent-unchanged",
                "manifest": _manifest("vault-1", base_revision=1, blob_id="blob-1"),
                "blob_refs": [{"blob_id": "blob-1", "file_id": "file-1"}],
            },
            auth_device_id="device-a",
        )

        listed = self.store.list_file_versions("vault-1", {"file_id": "file-1", "limit": 10})
        self.assertEqual([item["revision"] for item in listed["versions"]], [1])

    def test_file_version_retention_keeps_pinned_versions_when_pruning_latest(self) -> None:
        blobs = [
            {
                "blob_id": f"blob-retention-{index}",
                "encrypted_size": 7,
                "content_hash": f"sha256:retention-{index}",
            }
            for index in range(1, 5)
        ]
        self.store.init_blob_upload(
            "vault-1",
            {"blobs": blobs},
            request_base_url="http://127.0.0.1:8000/",
            device_id="device-a",
        )
        state = self.store._load()
        for token in list(state["capabilities"]):
            self.store.complete_blob_upload(token, b"payload")

        state = self.store._load()
        vault = state["vaults"].setdefault(
            "vault-1",
            {
                "head_revision": 0,
                "manifest_summary": None,
                "manifests": {},
                "commits": {},
                "acks": {},
                "file_versions": {
                    "records": {},
                    "by_file_id": {},
                    "retention_policy": {"keep_latest": 1, "keep_pinned": True},
                },
            },
        )
        vault["file_versions"]["retention_policy"] = {"keep_latest": 1, "keep_pinned": True}
        self.store._save(state)

        for index in range(1, 5):
            payload: dict[str, object] = {
                "commit_intent_id": f"intent-retention-{index}",
                "base_revision": index - 1,
                "created_by_device": "device-a",
                "intent_manifest_hash": f"sha256:intent-retention-{index}",
                "manifest": _manifest(
                    "vault-1",
                    base_revision=index - 1,
                    blob_id=f"blob-retention-{index}",
                    content_hash=f"sha256:retention-{index}",
                    path="Meetings/XXX.md",
                    created_at=1770000100000 + index,
                ),
                "blob_refs": [{"blob_id": f"blob-retention-{index}", "file_id": "file-1"}],
            }
            if index == 2:
                payload["file_version_directives"] = [
                    {
                        "file_id": "file-1",
                        "source": "manual_meeting_checkpoint",
                        "version_label": "Pinned meeting",
                        "is_pinned": True,
                    }
                ]
            self.store.create_commit("vault-1", payload, auth_device_id="device-a")

        listed = self.store.list_file_versions("vault-1", {"file_id": "file-1", "limit": 10})
        self.assertEqual([item["revision"] for item in listed["versions"]], [4, 2])
        self.assertFalse(listed["versions"][0]["is_pinned"])
        self.assertTrue(listed["versions"][1]["is_pinned"])
        self.assertEqual(listed["retention_policy"], {"keep_latest": 1, "keep_pinned": True})

        unpinned_only = self.store.list_file_versions(
            "vault-1",
            {"file_id": "file-1", "limit": 10, "include_pinned": False},
        )
        self.assertEqual([item["revision"] for item in unpinned_only["versions"]], [4])

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
