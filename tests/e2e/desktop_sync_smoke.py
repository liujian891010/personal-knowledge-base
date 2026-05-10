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
VAULT_CORE_SRC = ROOT / "packages" / "vault-core" / "src"
PORT = int(os.environ.get("NOTEAPP_DESKTOP_SMOKE_PORT", "8091"))
BASE_URL = f"http://127.0.0.1:{PORT}"
VAULT_ID = "vault-desktop-smoke"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(VAULT_CORE_SRC))

from clients.desktop.service import build_desktop_sync_service  # noqa: E402
from clients.desktop.sync_runtime import DesktopSyncHttpConfig  # noqa: E402


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


def register_device(name: str) -> tuple[str, str]:
    status, payload = request_json(
        "POST",
        "/devices/register",
        {
            "device_name": name,
            "platform": "desktop",
            "protocol_version": "v1",
        },
    )
    assert status == 200, payload
    return payload["device_id"], payload["access_token"]


def build_service(vault_root: Path, device_id: str, token: str):
    config = DesktopSyncHttpConfig(
        base_url=BASE_URL,
        vault_id=VAULT_ID,
        device_id=device_id,
        bearer_token=token,
        request_timeout_seconds=10.0,
        blob_timeout_seconds=10.0,
    )
    return build_desktop_sync_service(
        config,
        vault_root,
        file_id_builder=lambda path: "file-" + path.replace("/", "-").replace("\\", "-"),
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as data_dir, tempfile.TemporaryDirectory() as work_dir:
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

            device_a, token_a = register_device("Desktop A")
            device_b, token_b = register_device("Desktop B")
            root = Path(work_dir)
            vault_a = root / "vault-a"
            vault_b = root / "vault-b"
            service_a = build_service(vault_a, device_a, token_a)
            service_b = build_service(vault_b, device_b, token_b)

            service_a.ensure_initialized(now_ms=1770001000000)
            service_b.ensure_initialized(now_ms=1770001000001)

            first_payload = "# Smoke note\n\nfrom desktop A\n"
            (vault_a / "Notes" / "smoke.md").write_text(first_payload, encoding="utf-8", newline="\n")
            submitted_a = service_a.submit_detected_changes_if_needed(
                created_at=1770001000100,
                commit_intent_id="intent-desktop-a-1",
            )
            assert submitted_a is not None
            assert submitted_a.network.commit.status == "committed"

            pulled_b = service_b.pull_and_apply(rewritten_at=1770001000200)
            assert pulled_b.pull.pull.head.head_revision == 1
            assert (vault_b / "Notes" / "smoke.md").read_text(encoding="utf-8") == first_payload

            second_payload = "# Smoke note\n\nfrom desktop B\n"
            (vault_b / "Notes" / "smoke.md").write_text(second_payload, encoding="utf-8", newline="\n")
            submitted_b = service_b.submit_detected_changes_if_needed(
                created_at=1770001000300,
                commit_intent_id="intent-desktop-b-1",
            )
            assert submitted_b is not None
            assert submitted_b.network.commit.status == "committed"

            pulled_a = service_a.pull_and_apply(rewritten_at=1770001000400)
            assert pulled_a.pull.pull.head.head_revision == 2
            assert (vault_a / "Notes" / "smoke.md").read_text(encoding="utf-8") == second_payload

            status, head = request_json("GET", f"/vaults/{VAULT_ID}/head", token=token_a)
            assert status == 200, head
            assert head["head_revision"] == 2

            print(
                json.dumps(
                    {
                        "ok": True,
                        "vault_id": VAULT_ID,
                        "head": head,
                        "devices": [device_a, device_b],
                        "vault_a_note": (vault_a / "Notes" / "smoke.md").read_text(encoding="utf-8"),
                        "vault_b_note": (vault_b / "Notes" / "smoke.md").read_text(encoding="utf-8"),
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
