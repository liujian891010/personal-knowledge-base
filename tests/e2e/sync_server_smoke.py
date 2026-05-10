from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = ROOT / "apps" / "backend" / "noteapp-server"
PORT = int(os.environ.get("NOTEAPP_SMOKE_PORT", "8090"))
BASE_URL = f"http://127.0.0.1:{PORT}"


def request_json(
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
        f"{BASE_URL}{path}",
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
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, response.read()


def wait_for_server() -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            status, payload = request_json("GET", "/health")
            if status == 200 and payload == {"ok": True}:
                return
        except OSError:
            pass
        time.sleep(0.25)
    raise RuntimeError("server did not become ready")


def manifest(vault_id: str, blob_id: str, content_hash: str, size: int) -> dict[str, Any]:
    return {
        "schema_version": "v1",
        "vault_id": vault_id,
        "revision": 0,
        "base_revision": 0,
        "created_by_device": "smoke-device",
        "created_at": 1770000000000,
        "files": [
            {
                "file_id": "file-smoke",
                "path": "Notes/smoke.md",
                "type": "note",
                "content_hash": content_hash,
                "blob_id": blob_id,
                "size": size,
                "mtime": 1770000000000,
            }
        ],
        "tombstones": [],
        "summary_hash": "pending",
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as data_dir:
        env = dict(os.environ)
        env["NOTEAPP_SERVER_DATA_DIR"] = data_dir
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(PORT),
            ],
            cwd=SERVER_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            wait_for_server()

            status, registered = request_json(
                "POST",
                "/devices/register",
                {
                    "device_name": "Smoke Desktop",
                    "platform": "desktop",
                    "protocol_version": "v1",
                },
            )
            assert status == 200, registered
            token = registered["access_token"]

            payload = b"encrypted-smoke-payload"
            blob_id = "blob-smoke"
            content_hash = "sha256:plain-smoke"
            status, upload_init = request_json(
                "POST",
                "/vaults/vault-smoke/blobs/upload-init",
                {
                    "blobs": [
                        {
                            "blob_id": blob_id,
                            "encrypted_size": len(payload),
                            "content_hash": content_hash,
                        }
                    ]
                },
                token=token,
            )
            assert status == 200, upload_init
            upload_url = upload_init["uploads"][0]["upload_url"]
            status, _ = request_bytes("PUT", upload_url, payload)
            assert status == 204

            status, commit = request_json(
                "POST",
                "/vaults/vault-smoke/commits",
                {
                    "commit_intent_id": "intent-smoke",
                    "base_revision": 0,
                    "created_by_device": "smoke-device",
                    "intent_manifest_hash": "sha256:intent-smoke",
                    "manifest": manifest("vault-smoke", blob_id, content_hash, len(payload)),
                    "blob_refs": [{"blob_id": blob_id, "file_id": "file-smoke"}],
                },
                token=token,
            )
            assert status == 200, commit
            assert commit["new_revision"] == 1

            status, head = request_json("GET", "/vaults/vault-smoke/head", token=token)
            assert status == 200, head
            assert head["head_revision"] == 1

            status, remote_manifest = request_json("GET", "/vaults/vault-smoke/manifests/1", token=token)
            assert status == 200, remote_manifest
            assert remote_manifest["revision"] == 1
            assert remote_manifest["summary_hash"] == head["manifest_summary"]

            status, download_init = request_json(
                "POST",
                "/vaults/vault-smoke/blobs/download-init",
                {"blob_ids": [blob_id]},
                token=token,
            )
            assert status == 200, download_init
            status, downloaded = request_bytes("GET", download_init["downloads"][0]["download_url"])
            assert status == 200
            assert downloaded == payload

            status, unauthenticated_delete = request_json("DELETE", f"/devices/{registered['device_id']}")
            assert status == 401, unauthenticated_delete
            status, deleted = request_json("DELETE", f"/devices/{registered['device_id']}", token=token)
            assert status == 204, deleted
            status, revoked = request_json("GET", "/vaults/vault-smoke/head", token=token)
            assert status == 403, revoked

            print(
                json.dumps(
                    {
                        "ok": True,
                        "head": head,
                        "blob_base64": base64.b64encode(downloaded).decode("ascii"),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
