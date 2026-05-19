from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = ROOT / "apps" / "backend" / "noteapp-server"
PORT = int(os.environ.get("NOTEAPP_PRODUCTION_SMOKE_PORT", "8092"))


def request_json(
    base_url: str,
    method: str,
    path: str,
    payload: Optional[dict[str, Any]] = None,
    *,
    token: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw.decode("utf-8")) if raw else {}


def request_bytes(method: str, url: str, payload: Optional[bytes] = None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=payload, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def wait_for_server(base_url: str) -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            status, payload = request_json(base_url, "GET", "/health")
            if status == 200 and payload == {"ok": True}:
                return
        except OSError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"server did not become ready: {base_url}")


class RunningServer:
    def __init__(self, port: int, *, blob_storage: str = "filesystem") -> None:
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.blob_storage = blob_storage
        self.temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self.process: subprocess.Popen[str] | None = None

    def __enter__(self) -> str:
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        env = dict(os.environ)
        env["NOTEAPP_SERVER_DATA_DIR"] = str(data_dir)
        env["NOTEAPP_SERVER_STORAGE"] = "sqlite"
        env["NOTEAPP_SERVER_SQLITE_PATH"] = str(data_dir / "noteapp-server.sqlite3")
        env["NOTEAPP_SERVER_BLOB_STORAGE"] = self.blob_storage
        env["NOTEAPP_SERVER_LOG_LEVEL"] = "WARNING"
        if self.blob_storage == "s3":
            env["NOTEAPP_SERVER_OBJECT_ENDPOINT"] = "http://127.0.0.1:1"
            env["NOTEAPP_SERVER_OBJECT_BUCKET"] = "noteapp-smoke"
            env["NOTEAPP_SERVER_OBJECT_REGION"] = "us-east-1"
            env["NOTEAPP_SERVER_OBJECT_ACCESS_KEY_ID"] = "smoke"
            env["NOTEAPP_SERVER_OBJECT_SECRET_ACCESS_KEY"] = "smoke"
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            cwd=SERVER_ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            text=True,
        )
        wait_for_server(self.base_url)
        return self.base_url

    def __exit__(self, *_: object) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.temp_dir is not None:
            self.temp_dir.cleanup()


def register_device(base_url: str) -> dict[str, Any]:
    status, payload = request_json(
        base_url,
        "POST",
        "/devices/register",
        {
            "device_name": "Production Smoke Desktop",
            "platform": "desktop",
            "protocol_version": "v1",
        },
    )
    assert status == 200, payload
    return payload


def manifest(vault_id: str, *, base_revision: int, blob_id: str, file_id: str, path: str) -> dict[str, Any]:
    return {
        "schema_version": "v1",
        "vault_id": vault_id,
        "revision": 0,
        "base_revision": base_revision,
        "created_by_device": "smoke-device",
        "created_at": 1770000000000,
        "files": [
            {
                "file_id": file_id,
                "path": path,
                "type": "note",
                "content_hash": "sha256:plain-smoke",
                "blob_id": blob_id,
                "size": 21,
                "mtime": 1770000000000,
            }
        ],
        "tombstones": [],
        "summary_hash": "pending",
    }


def upload_blob(base_url: str, token: str, vault_id: str, blob_id: str, payload: bytes) -> None:
    status, upload_init = request_json(
        base_url,
        "POST",
        f"/vaults/{vault_id}/blobs/upload-init",
        {
            "blobs": [
                {
                    "blob_id": blob_id,
                    "encrypted_size": len(payload),
                    "content_hash": "sha256:plain-smoke",
                }
            ]
        },
        token=token,
    )
    assert status == 200, upload_init
    status, body = request_bytes("PUT", upload_init["uploads"][0]["upload_url"], payload)
    assert status == 204, body


def commit_payload(vault_id: str, *, name: str, blob_id: str) -> dict[str, Any]:
    return {
        "commit_intent_id": f"intent-{name}",
        "base_revision": 0,
        "created_by_device": f"device-{name}",
        "intent_manifest_hash": f"sha256:intent-{name}",
        "manifest": manifest(
            vault_id,
            base_revision=0,
            blob_id=blob_id,
            file_id=f"file-{name}",
            path=f"Notes/{name}.md",
        ),
        "blob_refs": [{"blob_id": blob_id, "file_id": f"file-{name}"}],
    }


def run_authorized_sqlite_suite(base_url: str) -> dict[str, Any]:
    vault_id = "vault-production-smoke"
    status, unauthorized = request_json(base_url, "GET", f"/vaults/{vault_id}/head")
    assert status == 401, unauthorized
    assert unauthorized["code"] == "missing_authorization"

    registered = register_device(base_url)
    token = registered["access_token"]

    status, health = request_json(base_url, "GET", "/health/dependencies")
    assert status == 200, health
    assert health["repository"]["type"] == "sqlite"
    assert health["repository"]["applied_schema_version"] == 2

    invalid_manifest = manifest(vault_id, base_revision=0, blob_id="missing", file_id="file-invalid", path="Notes/invalid.md")
    invalid_manifest["vault_id"] = "wrong-vault"
    status, invalid = request_json(
        base_url,
        "POST",
        f"/vaults/{vault_id}/commits",
        {
            "commit_intent_id": "intent-invalid",
            "base_revision": 0,
            "created_by_device": "smoke-device",
            "intent_manifest_hash": "sha256:intent-invalid",
            "manifest": invalid_manifest,
            "blob_refs": [],
        },
        token=token,
    )
    assert status == 400, invalid
    assert invalid["code"] == "invalid_manifest"

    blob_id = "blob-cas"
    payload = b"encrypted-smoke-bytes"
    upload_blob(base_url, token, vault_id, blob_id, payload)

    barrier = threading.Barrier(2)
    result_lock = threading.Lock()
    results: list[tuple[int, dict[str, Any]]] = []

    def worker(name: str) -> None:
        barrier.wait(timeout=5)
        result = request_json(
            base_url,
            "POST",
            f"/vaults/{vault_id}/commits",
            commit_payload(vault_id, name=name, blob_id=blob_id),
            token=token,
        )
        with result_lock:
            results.append(result)

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert sorted(status_code for status_code, _ in results) == [200, 409], results
    assert [payload["new_revision"] for status_code, payload in results if status_code == 200] == [1]
    assert [payload["code"] for status_code, payload in results if status_code == 409] == ["base_revision_conflict"]

    status, download_init = request_json(
        base_url,
        "POST",
        f"/vaults/{vault_id}/blobs/download-init",
        {"blob_ids": [blob_id]},
        token=token,
    )
    assert status == 200, download_init
    stale_download_url = download_init["downloads"][0]["download_url"]

    status, deleted = request_json(base_url, "DELETE", f"/devices/{registered['device_id']}", token=token)
    assert status == 204, deleted
    status, revoked = request_json(base_url, "GET", f"/vaults/{vault_id}/head", token=token)
    assert status == 403, revoked
    assert revoked["code"] == "device_revoked"
    status, stale_body = request_bytes("GET", stale_download_url)
    assert status in {403, 404}, stale_body

    status, metrics = request_json(base_url, "GET", "/metrics")
    assert status == 200, metrics
    assert metrics["commit_conflicts_total"] >= 1
    assert metrics["errors_total"] >= 3
    return {
        "head_revision": 1,
        "commit_conflicts_total": metrics["commit_conflicts_total"],
        "errors_total": metrics["errors_total"],
    }


def run_object_storage_failure_suite(base_url: str) -> dict[str, Any]:
    registered = register_device(base_url)
    token = registered["access_token"]
    status, upload_init = request_json(
        base_url,
        "POST",
        "/vaults/vault-object-failure/blobs/upload-init",
        {
            "blobs": [
                {
                    "blob_id": "blob-object-failure",
                    "encrypted_size": 7,
                    "content_hash": "sha256:plain-smoke",
                }
            ]
        },
        token=token,
    )
    assert status == 200, upload_init
    status, body = request_bytes("PUT", upload_init["uploads"][0]["upload_url"], b"payload")
    error_payload = json.loads(body.decode("utf-8"))
    assert status == 503, error_payload
    assert error_payload["code"] == "object_storage_unavailable"
    status, metrics = request_json(base_url, "GET", "/metrics")
    assert status == 200, metrics
    assert metrics["object_storage_failures_total"] >= 1
    return {
        "object_storage_error_code": error_payload["code"],
        "object_storage_failures_total": metrics["object_storage_failures_total"],
    }


def main() -> int:
    with RunningServer(PORT, blob_storage="filesystem") as base_url:
        sqlite_result = run_authorized_sqlite_suite(base_url)
    with RunningServer(PORT + 1, blob_storage="s3") as base_url:
        object_result = run_object_storage_failure_suite(base_url)

    print(
        json.dumps(
            {
                "ok": True,
                **sqlite_result,
                **object_result,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
