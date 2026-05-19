from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch

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
        server_main.metrics.reset()
        server_main.store = SyncStore(Path(self.temp_dir.name))
        self.client = TestClient(server_main.app)

    def tearDown(self) -> None:
        server_main.store = self.original_store
        self.temp_dir.cleanup()

    def test_create_store_from_env_uses_sqlite_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            db_path = data_dir / "noteapp-server.sqlite3"
            with patch.dict(
                server_main.os.environ,
                {
                    "NOTEAPP_SERVER_DATA_DIR": str(data_dir),
                    "NOTEAPP_SERVER_STORAGE": "sqlite",
                    "NOTEAPP_SERVER_SQLITE_PATH": str(db_path),
                    "NOTEAPP_SERVER_BLOB_STORAGE": "local",
                },
            ):
                created = server_main.create_store_from_env()
                registered = created.register_device(
                    {
                        "device_name": "Desktop",
                        "platform": "desktop",
                        "protocol_version": "v1",
                    }
                )

            self.assertTrue(db_path.exists())
            self.assertFalse((data_dir / "sync-state.json").exists())
            self.assertEqual(created.device_id_for_token(registered["access_token"]), registered["device_id"])
            diagnostics = created.diagnostics()
            self.assertTrue(diagnostics["ok"])
            self.assertEqual(diagnostics["repository"]["type"], "sqlite")
            self.assertEqual(diagnostics["repository"]["applied_schema_version"], 2)

    def test_production_env_requires_sqlite_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                server_main.os.environ,
                {
                    "NOTEAPP_SERVER_ENV": "production",
                    "NOTEAPP_SERVER_DATA_DIR": str(Path(temp_dir) / "data"),
                    "NOTEAPP_SERVER_STORAGE": "json",
                    "NOTEAPP_SERVER_BLOB_STORAGE": "local",
                },
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "requires NOTEAPP_SERVER_STORAGE=sqlite"):
                    server_main.create_store_from_env()

    def test_production_env_requires_explicit_database_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                server_main.os.environ,
                {
                    "NOTEAPP_SERVER_ENV": "production",
                    "NOTEAPP_SERVER_DATA_DIR": str(Path(temp_dir) / "data"),
                    "NOTEAPP_SERVER_STORAGE": "sqlite",
                    "NOTEAPP_SERVER_BLOB_STORAGE": "local",
                },
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "requires NOTEAPP_SERVER_DATABASE_URL"):
                    server_main.create_store_from_env()

    def test_production_env_accepts_explicit_sqlite_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            db_path = Path(temp_dir) / "managed" / "noteapp-server.sqlite3"
            with patch.dict(
                server_main.os.environ,
                {
                    "NOTEAPP_SERVER_ENV": "production",
                    "NOTEAPP_SERVER_DATA_DIR": str(data_dir),
                    "NOTEAPP_SERVER_STORAGE": "sqlite",
                    "NOTEAPP_SERVER_SQLITE_PATH": str(db_path),
                    "NOTEAPP_SERVER_BLOB_STORAGE": "local",
                },
                clear=True,
            ):
                created = server_main.create_store_from_env()

            self.assertTrue(db_path.exists())
            self.assertEqual(created.diagnostics()["repository"]["type"], "sqlite")

    def test_health_dependencies_reports_runtime_components(self) -> None:
        health = self.client.get("/health", headers={"x-request-id": "req-health"})
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"ok": True})
        self.assertEqual(health.headers["x-request-id"], "req-health")

        details = self.client.get("/health/dependencies")
        self.assertEqual(details.status_code, 200)
        payload = details.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["process"]["ok"])
        self.assertEqual(payload["repository"]["type"], "json")
        self.assertTrue(payload["blob_store"]["ok"])
        self.assertTrue(payload["state"]["ok"])

    def test_request_metrics_include_errors_and_request_id(self) -> None:
        before = server_main.metrics.snapshot()["requests_total"]

        response = self.client.get("/vaults/vault-1/head", headers={"x-request-id": "req-missing-auth"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["x-request-id"], "req-missing-auth")
        self.assertEqual(response.headers["x-noteapp-error-code"], "missing_authorization")
        metrics = self.client.get("/metrics").json()
        self.assertGreaterEqual(metrics["requests_total"], before + 1)
        self.assertGreaterEqual(metrics["errors_total"], 1)
        self.assertGreater(metrics["error_rate"], 0)

    def _register_device(self, **overrides: object) -> dict[str, str]:
        payload = {
            "device_name": "Desktop",
            "platform": "desktop",
            "protocol_version": "v1",
        }
        payload.update(overrides)
        response = self.client.post(
            "/devices/register",
            json=payload,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["device_id"])
        self.assertTrue(payload["user_id"])
        self.assertTrue(payload["access_token"])
        self.assertTrue(payload["refresh_token"])
        return payload

    def test_login_creates_authorized_session(self) -> None:
        response = self.client.post(
            "/auth/login",
            json={
                "account_key": "user@example.test",
                "display_name": "Example User",
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["user_id"])
        self.assertTrue(payload["device_id"])
        self.assertTrue(payload["access_token"])
        self.assertTrue(payload["refresh_token"])

        head = self.client.get(
            "/vaults/vault-1/head",
            headers={"Authorization": f"Bearer {payload['access_token']}"},
        )
        self.assertEqual(head.status_code, 200)

    def test_password_register_and_login_flow(self) -> None:
        registered = self.client.post(
            "/auth/register",
            json={
                "account_key": "secure@example.test",
                "password": "correct horse battery staple",
                "display_name": "Secure User",
                "device_name": "Desktop",
                "platform": "desktop",
                "protocol_version": "v1",
            },
        )
        self.assertEqual(registered.status_code, 200)
        payload = registered.json()
        self.assertTrue(payload["access_token"])
        state = server_main.store._load()
        user = state["users"][payload["user_id"]]
        self.assertIn("password_auth", user)
        self.assertNotIn("password", user)

        passwordless = self.client.post(
            "/devices/register",
            json={
                "account_key": "secure@example.test",
                "device_name": "Phone",
                "platform": "mobile",
            },
        )
        self.assertEqual(passwordless.status_code, 401)
        self.assertEqual(passwordless.json()["code"], "password_required")

        wrong_password = self.client.post(
            "/auth/login",
            json={
                "account_key": "secure@example.test",
                "password": "wrong horse battery",
                "device_name": "Desktop",
                "platform": "desktop",
            },
        )
        self.assertEqual(wrong_password.status_code, 401)
        self.assertEqual(wrong_password.json()["code"], "invalid_credentials")

        logged_in = self.client.post(
            "/auth/login",
            json={
                "account_key": "secure@example.test",
                "password": "correct horse battery staple",
                "device_name": "Laptop",
                "platform": "desktop",
            },
        )
        self.assertEqual(logged_in.status_code, 200)
        login_payload = logged_in.json()
        self.assertEqual(login_payload["user_id"], payload["user_id"])
        self.assertNotEqual(login_payload["device_id"], payload["device_id"])

        head = self.client.get(
            "/vaults/vault-1/head",
            headers={"Authorization": f"Bearer {login_payload['access_token']}"},
        )
        self.assertEqual(head.status_code, 200)

    def test_password_register_rejects_duplicate_account(self) -> None:
        body = {
            "account_key": "secure@example.test",
            "password": "correct horse battery staple",
            "device_name": "Desktop",
            "platform": "desktop",
        }
        self.assertEqual(self.client.post("/auth/register", json=body).status_code, 200)

        duplicate = self.client.post("/auth/register", json=body)

        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["code"], "account_exists")

    def test_vault_endpoints_reject_missing_authorization(self) -> None:
        blocked_requests = [
            ("GET", "/vaults/vault-1/head", None),
            ("GET", "/vaults/vault-1/manifests/1", None),
            ("POST", "/vaults/vault-1/blobs/check", {}),
            ("POST", "/vaults/vault-1/blobs/upload-init", {}),
            ("POST", "/vaults/vault-1/blobs/download-init", {}),
            ("POST", "/vaults/vault-1/blobs/resumable-upload-init", {}),
            ("POST", "/vaults/vault-1/blobs/resumable-upload-complete", {}),
            ("POST", "/vaults/vault-1/blobs/resumable-download-init", {}),
            ("POST", "/vaults/vault-1/commits", {}),
            ("POST", "/vaults/vault-1/commits/resolve-intent", {}),
            ("POST", "/vaults/vault-1/ack", {}),
            ("POST", "/vaults/vault-1/file-versions/list", {}),
            ("PATCH", "/vaults/vault-1/file-versions/fv-1", {}),
            ("GET", "/vaults/vault-1/devices", None),
            ("POST", "/vaults/vault-1/devices/heartbeat", {}),
            ("POST", "/vaults/vault-1/tombstones/gc", {}),
        ]
        for method, path, body in blocked_requests:
            request_kwargs = {}
            if body is not None:
                request_kwargs["json"] = body
            response = self.client.request(method, path, **request_kwargs)
            self.assertEqual(response.status_code, 401, path)
            self.assertEqual(response.json()["code"], "missing_authorization")

    def test_refresh_rotates_access_and_refresh_tokens(self) -> None:
        registered = self._register_device()

        refreshed = self.client.post(
            "/auth/refresh",
            json={"refresh_token": registered["refresh_token"]},
        )
        self.assertEqual(refreshed.status_code, 200)
        payload = refreshed.json()
        self.assertEqual(payload["user_id"], registered["user_id"])
        self.assertEqual(payload["device_id"], registered["device_id"])
        self.assertNotEqual(payload["access_token"], registered["access_token"])
        self.assertNotEqual(payload["refresh_token"], registered["refresh_token"])

        old_access = self.client.get(
            "/vaults/vault-1/head",
            headers={"Authorization": f"Bearer {registered['access_token']}"},
        )
        self.assertEqual(old_access.status_code, 401)
        self.assertEqual(old_access.json()["code"], "invalid_access_token")

        old_refresh = self.client.post(
            "/auth/refresh",
            json={"refresh_token": registered["refresh_token"]},
        )
        self.assertEqual(old_refresh.status_code, 401)
        self.assertEqual(old_refresh.json()["code"], "invalid_refresh_token")

        new_access = self.client.get(
            "/vaults/vault-1/head",
            headers={"Authorization": f"Bearer {payload['access_token']}"},
        )
        self.assertEqual(new_access.status_code, 200)

    def test_logout_revokes_current_session(self) -> None:
        registered = self._register_device()
        headers = {"Authorization": f"Bearer {registered['access_token']}"}

        logged_out = self.client.post("/auth/logout", headers=headers)
        self.assertEqual(logged_out.status_code, 204)

        blocked = self.client.get("/vaults/vault-1/head", headers=headers)
        self.assertEqual(blocked.status_code, 401)
        self.assertEqual(blocked.json()["code"], "invalid_access_token")

        refresh = self.client.post("/auth/refresh", json={"refresh_token": registered["refresh_token"]})
        self.assertEqual(refresh.status_code, 401)
        self.assertEqual(refresh.json()["code"], "invalid_refresh_token")

    def test_delete_device_rejects_cross_account_actor(self) -> None:
        actor = self._register_device(account_key="alice@example.test")
        target = self._register_device(account_key="bob@example.test")

        forbidden = self.client.delete(
            f"/devices/{target['device_id']}",
            headers={"Authorization": f"Bearer {actor['access_token']}"},
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(forbidden.json()["code"], "device_forbidden")

        target_head = self.client.get(
            "/vaults/vault-1/head",
            headers={"Authorization": f"Bearer {target['access_token']}"},
        )
        self.assertEqual(target_head.status_code, 200)

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
