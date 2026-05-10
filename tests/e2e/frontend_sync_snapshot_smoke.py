from __future__ import annotations

import hashlib
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

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(VAULT_CORE_SRC))

from vault_core import FileMapDocument, FileRecord, write_filemap_atomic  # noqa: E402
from clients.desktop.workspace import DesktopVaultPaths  # noqa: E402


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
    payload: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
) -> tuple[int, dict[str, Any]]:
    body = None
    resolved_headers = headers or {}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        resolved_headers = {**resolved_headers, "Content-Type": "application/json"}
    request = urllib.request.Request(
        f"{BRIDGE_URL}{path}",
        data=body,
        headers=resolved_headers,
        method=method,
    )
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
            settings_output_path = Path(work_dir) / "local-settings-snapshot.json"
            workspace_files_output_path = Path(work_dir) / "workspace-files.json"
            workspace_root_output_path = Path(work_dir) / "workspace-root.json"
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
                    f"--bearer-token={token}",
                    "init",
                    "--now-ms",
                    "1770002000000",
                ],
                cwd=ROOT,
                env=cli_env,
            )

            note_path = vault_root / "Notes" / "Bridge Smoke.md"
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text("# Bridge Smoke\n\nworkspace files\n", encoding="utf-8", newline="\n")
            note_payload = note_path.read_bytes()
            note_mtime_ms = note_path.stat().st_mtime_ns // 1_000_000
            write_filemap_atomic(
                DesktopVaultPaths.from_root(vault_root).filemap_path,
                FileMapDocument(
                    vault_id=VAULT_ID,
                    updated_at=1770002000050,
                    files=[
                        FileRecord(
                            file_id="file-bridge-smoke",
                            path="Notes/Bridge Smoke.md",
                            type="note",
                            status="active",
                            updated_at=note_mtime_ms,
                            content_hash="sha256:" + hashlib.sha256(note_payload).hexdigest(),
                            last_known_revision=0,
                            meta={
                                "size": len(note_payload),
                                "mtime": note_mtime_ms,
                                "mime_type": "text/markdown",
                            },
                        )
                    ],
                ),
            )

            settings_path = vault_root / ".noteapp" / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "appearance": {"theme": "light"},
                        "ai": {
                            "local_model_status": "available",
                            "embedding_status": "indexing",
                        },
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
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
                "NOTEAPP_SETTINGS_SNAPSHOT_OUTPUT": str(settings_output_path),
                "NOTEAPP_WORKSPACE_FILES_OUTPUT": str(workspace_files_output_path),
                "NOTEAPP_WORKSPACE_ROOT_OUTPUT": str(workspace_root_output_path),
            }
            run_checked(
                ["node", "scripts/write-live-sync-shell.mjs"],
                cwd=FRONTEND_ROOT,
                env=snapshot_env,
            )
            run_checked(
                ["node", "scripts/write-local-settings-snapshot.mjs"],
                cwd=FRONTEND_ROOT,
                env=snapshot_env,
            )
            run_checked(
                ["node", "scripts/write-workspace-files.mjs"],
                cwd=FRONTEND_ROOT,
                env=snapshot_env,
            )

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            assert payload["generated_at_ms"] == 1770002000100, payload
            assert payload["vault_id"] == VAULT_ID, payload
            assert payload["device_id"] == device_id, payload
            assert payload["sync_center"]["panel"]["level"] in {"success", "info", "warning", "danger"}, payload
            settings_payload = json.loads(settings_output_path.read_text(encoding="utf-8"))
            assert settings_payload["schema_version"] == "v1", settings_payload
            assert settings_payload["source"] == "file", settings_payload
            assert settings_payload["settings_path"] == str(settings_path), settings_payload
            assert settings_payload["appearance"]["theme"] == "light", settings_payload
            assert settings_payload["ai"]["local_model_status"] == "available", settings_payload
            assert settings_payload["ai"]["embedding_status"] == "indexing", settings_payload
            assert settings_payload["sync"]["base_url"] == BASE_URL, settings_payload
            assert settings_payload["sync"]["bearer_token_configured"] is True, settings_payload
            workspace_payload = json.loads(workspace_files_output_path.read_text(encoding="utf-8"))
            assert workspace_payload["schema_version"] == "v1", workspace_payload
            assert workspace_payload["vault_id"] == VAULT_ID, workspace_payload
            assert workspace_payload["device_id"] == device_id, workspace_payload
            assert workspace_payload["total_count"] == 1, workspace_payload
            assert workspace_payload["active_count"] == 1, workspace_payload
            assert workspace_payload["missing_count"] == 0, workspace_payload
            assert workspace_payload["files"][0]["path"] == "Notes/Bridge Smoke.md", workspace_payload
            assert workspace_payload["files"][0]["exists_on_disk"] is True, workspace_payload

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
                assert allowed_origin["host"] == "127.0.0.1", allowed_origin
                assert allowed_origin["port"] == BRIDGE_PORT, allowed_origin
                assert allowed_origin["allowRemoteHost"] is False, allowed_origin
                assert allowed_origin["settingsSnapshotPath"] == str(settings_output_path), allowed_origin
                assert allowed_origin["workspaceFilesPath"] == str(workspace_files_output_path), allowed_origin
                assert allowed_origin["workspaceRootPath"] == str(workspace_root_output_path), allowed_origin
                assert allowed_origin["vaultRoot"] == str(vault_root), allowed_origin
                status, rejected_origin = request_bridge_json(
                    "/health",
                    headers={"Origin": "http://evil.example"},
                )
                assert status == 403, rejected_origin
                status, root_payload = request_bridge_json("/api/workspace/root")
                assert status == 200, root_payload
                assert root_payload["vault_root"] == str(vault_root), root_payload
                assert root_payload["exists"] is True, root_payload
                assert root_payload["initialized"] is True, root_payload
                switched_vault_root = Path(work_dir) / "switched-vault"
                switched_vault_root.mkdir()
                status, switched_settings = request_bridge_json(
                    "/api/workspace/root",
                    method="POST",
                    payload={"vault_root": str(switched_vault_root)},
                )
                assert status == 200, switched_settings
                assert switched_settings["vault_root"] == str(switched_vault_root), switched_settings
                assert (switched_vault_root / ".noteapp" / "filemap.json").exists(), switched_settings
                assert json.loads(workspace_root_output_path.read_text(encoding="utf-8"))["vault_root"] == str(
                    switched_vault_root
                )
                status, switched_root_payload = request_bridge_json("/api/workspace/root")
                assert status == 200, switched_root_payload
                assert switched_root_payload["vault_root"] == str(switched_vault_root), switched_root_payload
                status, _ = request_bridge_json(
                    "/api/workspace/root",
                    method="POST",
                    payload={"vault_root": str(vault_root)},
                )
                assert status == 200
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
                status, settings_bridge_payload = request_bridge_json("/api/settings/snapshot")
                assert status == 200, settings_bridge_payload
                assert settings_bridge_payload["source"] == "file", settings_bridge_payload
                assert settings_bridge_payload["vault_id"] == VAULT_ID, settings_bridge_payload
                assert settings_bridge_payload["device_id"] == device_id, settings_bridge_payload
                assert settings_bridge_payload["appearance"]["theme"] == "light", settings_bridge_payload
                status, written_settings_payload = request_bridge_json(
                    "/api/settings/snapshot",
                    method="POST",
                    payload={
                        "schema_version": "v1",
                        "appearance": {"theme": "system"},
                        "ai": {
                            "local_model_status": "disabled",
                            "embedding_status": "ready",
                        },
                    },
                )
                assert status == 200, written_settings_payload
                assert written_settings_payload["source"] == "file", written_settings_payload
                assert written_settings_payload["appearance"]["theme"] == "system", written_settings_payload
                assert written_settings_payload["ai"]["local_model_status"] == "disabled", written_settings_payload
                assert written_settings_payload["ai"]["embedding_status"] == "ready", written_settings_payload
                assert json.loads(settings_path.read_text(encoding="utf-8")) == {
                    "schema_version": "v1",
                    "appearance": {"theme": "system"},
                    "ai": {
                        "local_model_status": "disabled",
                        "embedding_status": "ready",
                    },
                }
                status, live_settings_payload = request_bridge_json("/api/settings/live")
                assert status == 200, live_settings_payload
                assert live_settings_payload == written_settings_payload, live_settings_payload
                status, workspace_bridge_payload = request_bridge_json("/api/workspace/files")
                assert status == 200, workspace_bridge_payload
                assert workspace_bridge_payload["vault_id"] == VAULT_ID, workspace_bridge_payload
                assert workspace_bridge_payload["device_id"] == device_id, workspace_bridge_payload
                assert workspace_bridge_payload["files"][0]["path"] == "Notes/Bridge Smoke.md", workspace_bridge_payload
                status, file_content_payload = request_bridge_json(
                    "/api/workspace/files/file-bridge-smoke/content"
                )
                assert status == 200, file_content_payload
                assert file_content_payload["file_id"] == "file-bridge-smoke", file_content_payload
                assert file_content_payload["path"] == "Notes/Bridge Smoke.md", file_content_payload
                assert file_content_payload["encoding"] == "utf-8", file_content_payload
                assert "# Bridge Smoke" in file_content_payload["text"], file_content_payload
                status, put_preflight = request_bridge_json(
                    "/api/workspace/files/file-bridge-smoke/content",
                    method="OPTIONS",
                    headers={
                        "Origin": "http://127.0.0.1:3000",
                        "Access-Control-Request-Method": "PUT",
                    },
                )
                assert status == 204, put_preflight
                status, written_content_payload = request_bridge_json(
                    "/api/workspace/files/file-bridge-smoke/content",
                    method="PUT",
                    payload={
                        "text": "# Bridge Smoke\n\nedited through bridge\n",
                    },
                )
                assert status == 200, written_content_payload
                assert written_content_payload["file_id"] == "file-bridge-smoke", written_content_payload
                assert "edited through bridge" in written_content_payload["text"], written_content_payload
                assert note_path.read_text(encoding="utf-8") == "# Bridge Smoke\n\nedited through bridge\n"
                status, live_workspace_payload = request_bridge_json("/api/workspace/live")
                assert status == 200, live_workspace_payload
                assert live_workspace_payload["files"][0]["path"] == "Notes/Bridge Smoke.md", live_workspace_payload
                status, live_sync_payload = request_bridge_json("/api/sync/live")
                assert status == 200, live_sync_payload
                assert live_sync_payload["sync_center"]["panel"]["change_badge_count"] == 1, live_sync_payload
                local_change_cards = [
                    card
                    for card in live_sync_payload["sync_center"]["cards"]
                    if card["card_id"] == "local-changes"
                ]
                assert len(local_change_cards) == 1, live_sync_payload
                assert local_change_cards[0]["badge_count"] == 1, local_change_cards
                assert {
                    action["action_id"] for action in local_change_cards[0]["actions"]
                } == {"detect-local-changes", "submit-detected-commit"}, local_change_cards
                status, detected_payload = request_bridge_json(
                    "/api/sync/actions/detect-local-changes",
                    method="POST",
                )
                assert status == 200, detected_payload
                assert detected_payload["sync_center"]["panel"]["change_badge_count"] == 1, detected_payload
                assert any(
                    record["action_id"] == "detect-local-changes"
                    and record["status"] == "executed"
                    for record in detected_payload["activity_feed"]["records"]
                ), detected_payload
                status, submitted_payload = request_bridge_json(
                    "/api/sync/actions/submit-detected-commit",
                    method="POST",
                )
                assert status == 200, submitted_payload
                assert submitted_payload["sync_center"]["panel"]["level"] == "success", submitted_payload
                assert submitted_payload["sync_center"]["panel"]["headline"] == "Vault is in sync", submitted_payload
                assert submitted_payload["sync_center"]["panel"]["change_badge_count"] == 0, submitted_payload
                assert not [
                    card
                    for card in submitted_payload["sync_center"]["cards"]
                    if card["card_id"] == "local-changes"
                ], submitted_payload
                assert "snapshot" not in submitted_payload, submitted_payload
                assert any(
                    record["action_id"] == "submit-detected-commit"
                    and record["status"] == "executed"
                    for record in submitted_payload["activity_feed"]["records"]
                ), submitted_payload
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
                        "settings_source": settings_payload["source"],
                        "workspace_files": workspace_payload["total_count"],
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
