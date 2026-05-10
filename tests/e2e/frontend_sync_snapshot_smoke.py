from __future__ import annotations

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
FRONTEND_ROOT = ROOT / "apps" / "frontend" / "noteapp-web"
VAULT_CORE_SRC = ROOT / "packages" / "vault-core" / "src"
PORT = int(os.environ.get("NOTEAPP_FRONTEND_SNAPSHOT_SMOKE_PORT", "8092"))
BRIDGE_PORT = int(os.environ.get("NOTEAPP_FRONTEND_SNAPSHOT_BRIDGE_PORT", "3192"))
BASE_URL = f"http://127.0.0.1:{PORT}"
BRIDGE_URL = f"http://127.0.0.1:{BRIDGE_PORT}"
VAULT_ID = "vault-frontend-snapshot-smoke"


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


def request_bridge_json(
    path: str,
    *,
    method: str = "GET",
    headers: Optional[dict[str, str]] = None,
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(f"{BRIDGE_URL}{path}", headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw.decode("utf-8")) if raw else {}


def wait_for_bridge() -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            status, payload = request_bridge_json("/health")
            if status == 200 and payload.get("ok") is True:
                return
        except OSError:
            pass
        time.sleep(0.25)
    raise RuntimeError("sync bridge did not become ready")


def register_device() -> tuple[str, str]:
    status, payload = request_json(
        "POST",
        "/devices/register",
        {
            "device_name": "Frontend Snapshot Smoke",
            "platform": "desktop",
            "protocol_version": "v1",
        },
    )
    assert status == 200, payload
    return payload["device_id"], payload["access_token"]


def run_checked(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            "command failed with exit code "
            f"{result.returncode}: {' '.join(command)}\n{result.stdout}"
        )


def assert_bridge_rejects_remote_host(env: dict[str, str]) -> None:
    result = subprocess.run(
        ["node", "scripts/sync-shell-bridge.mjs"],
        cwd=FRONTEND_ROOT,
        env={
            **env,
            "NOTEAPP_SYNC_BRIDGE_HOST": "0.0.0.0",
        },
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=10,
    )
    assert result.returncode != 0, result.stdout
    assert "Refusing to bind sync bridge to non-loopback host" in result.stdout, result.stdout


def main() -> int:
    with tempfile.TemporaryDirectory() as data_dir, tempfile.TemporaryDirectory() as work_dir:
        server_env = dict(os.environ)
        server_env["NOTEAPP_SERVER_DATA_DIR"] = data_dir
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
            env=server_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            wait_for_server()
            device_id, token = register_device()
            vault_root = Path(work_dir) / "vault"
            output_path = Path(work_dir) / "live-sync-shell.json"
            pythonpath = os.pathsep.join([str(VAULT_CORE_SRC), str(ROOT)])
            cli_env = {
                **os.environ,
                "PYTHONPATH": pythonpath,
            }

            run_checked(
                [
                    sys.executable,
                    "-m",
                    "clients.desktop.cli",
                    "--vault-root",
                    str(vault_root),
                    "--base-url",
                    BASE_URL,
                    "--vault-id",
                    VAULT_ID,
                    "--device-id",
                    device_id,
                    "--bearer-token",
                    token,
                    "init",
                    "--now-ms",
                    "1770002000000",
                ],
                cwd=ROOT,
                env=cli_env,
            )

            snapshot_env = {
                **os.environ,
                "PYTHON": sys.executable,
                "NOTEAPP_VAULT_ROOT": str(vault_root),
                "NOTEAPP_SYNC_BASE_URL": BASE_URL,
                "NOTEAPP_VAULT_ID": VAULT_ID,
                "NOTEAPP_DEVICE_ID": device_id,
                "NOTEAPP_BEARER_TOKEN": token,
                "NOTEAPP_SYNC_NOW_MS": "1770002000100",
                "NOTEAPP_ACTIVITY_LIMIT": "5",
                "NOTEAPP_SYNC_SNAPSHOT_OUTPUT": str(output_path),
            }
            run_checked(
                ["node", "scripts/write-live-sync-shell.mjs"],
                cwd=FRONTEND_ROOT,
                env=snapshot_env,
            )

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            assert payload["generated_at_ms"] == 1770002000100, payload
            assert payload["vault_id"] == VAULT_ID, payload
            assert payload["device_id"] == device_id, payload
            assert payload["sync_center"]["panel"]["level"] in {"success", "info", "warning", "danger"}, payload

            bridge_env = {
                **snapshot_env,
                "NOTEAPP_SYNC_BRIDGE_PORT": str(BRIDGE_PORT),
            }
            assert_bridge_rejects_remote_host(bridge_env)
            bridge = subprocess.Popen(
                ["node", "scripts/sync-shell-bridge.mjs"],
                cwd=FRONTEND_ROOT,
                env=bridge_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                wait_for_bridge()
                status, allowed_origin = request_bridge_json(
                    "/health",
                    headers={"Origin": "http://127.0.0.1:3000"},
                )
                assert status == 200, allowed_origin
                assert allowed_origin["allowedOrigin"] == "http://127.0.0.1:3000", allowed_origin
                status, rejected_origin = request_bridge_json(
                    "/health",
                    headers={"Origin": "http://evil.example"},
                )
                assert status == 403, rejected_origin
                status, bridge_payload = request_bridge_json("/api/sync/snapshot")
                assert status == 200, bridge_payload
                assert bridge_payload["generated_at_ms"] == 1770002000100, bridge_payload
                assert bridge_payload["vault_id"] == VAULT_ID, bridge_payload
                assert bridge_payload["device_id"] == device_id, bridge_payload
                status, action_payload = request_bridge_json(
                    "/api/sync/actions/show-vault-summary",
                    method="POST",
                )
                assert status == 200, action_payload
                assert action_payload["vault_id"] == VAULT_ID, action_payload
                assert action_payload["device_id"] == device_id, action_payload
                assert "snapshot" not in action_payload, action_payload
                status, missing_action = request_bridge_json(
                    "/api/sync/actions/not-a-real-action",
                    method="POST",
                )
                assert status == 404, missing_action
                assert missing_action["code"] == "sync_action_not_found", missing_action
                assert "sync action not found" in missing_action["message"], missing_action
            finally:
                bridge.terminate()
                try:
                    bridge.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    bridge.kill()

            print(
                json.dumps(
                    {
                        "ok": True,
                        "snapshot": str(output_path),
                        "vault_id": payload["vault_id"],
                        "device_id": payload["device_id"],
                        "level": payload["sync_center"]["panel"]["level"],
                        "bridge_url": BRIDGE_URL,
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
