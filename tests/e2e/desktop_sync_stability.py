from __future__ import annotations

import argparse
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
VAULT_CORE_SRC = ROOT / "packages" / "vault-core" / "src"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(VAULT_CORE_SRC))

from clients.desktop.service import build_desktop_sync_service  # noqa: E402
from clients.desktop.sync_runtime import DesktopSyncHttpConfig  # noqa: E402


BASE_TIME_MS = 1770006000000


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
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw.decode("utf-8")) if raw else {}


def wait_for_server(base_url: str) -> None:
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            status, payload = request_json(base_url, "GET", "/health")
            if status == 200 and payload == {"ok": True}:
                return
        except OSError:
            pass
        time.sleep(0.25)
    raise RuntimeError("server did not become ready")


def start_server(*, data_dir: Path, port: int, storage: str) -> subprocess.Popen[str]:
    env = dict(os.environ)
    env["NOTEAPP_SERVER_DATA_DIR"] = str(data_dir)
    env["NOTEAPP_SERVER_LOG_LEVEL"] = "WARNING"
    env["NOTEAPP_SERVER_STORAGE"] = storage
    if storage == "sqlite":
        env["NOTEAPP_SERVER_SQLITE_PATH"] = str(data_dir / "noteapp-server.sqlite3")
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=SERVER_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        text=True,
    )


def stop_server(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def register_device(base_url: str, name: str) -> tuple[str, str]:
    status, payload = request_json(
        base_url,
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


def build_service(base_url: str, vault_id: str, vault_root: Path, device_id: str, token: str):
    config = DesktopSyncHttpConfig(
        base_url=base_url,
        vault_id=vault_id,
        device_id=device_id,
        bearer_token=token,
        request_timeout_seconds=15.0,
        blob_timeout_seconds=15.0,
    )
    return build_desktop_sync_service(
        config,
        vault_root,
        file_id_builder=lambda path: "file-" + path.replace("/", "-").replace("\\", "-"),
        allow_placeholder_crypto=True,
    )


def write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8", newline="\n")


def stability_payload(iteration: int) -> str:
    digest = hashlib.sha256(f"noteai-stability-{iteration}".encode("utf-8")).hexdigest()
    return "\n".join(
        [
            "# Stability note",
            "",
            f"iteration: {iteration}",
            f"sha256: {digest}",
            "",
        ]
    )


def assert_head(base_url: str, vault_id: str, token: str, expected_revision: int) -> dict[str, Any]:
    status, head = request_json(base_url, "GET", f"/vaults/{vault_id}/head", token=token)
    assert status == 200, head
    assert head["head_revision"] == expected_revision, head
    return head


def assert_file_not_active_at_head(
    base_url: str,
    vault_id: str,
    token: str,
    file_id: str,
    *,
    minimum_revision: int,
) -> dict[str, Any]:
    status, head = request_json(base_url, "GET", f"/vaults/{vault_id}/head", token=token)
    assert status == 200, head
    head_revision = head["head_revision"]
    assert head_revision >= minimum_revision, head
    status, manifest = request_json(base_url, "GET", f"/vaults/{vault_id}/manifests/{head_revision}", token=token)
    assert status == 200, manifest
    active_file_ids = {item.get("file_id") for item in manifest.get("files", [])}
    assert file_id not in active_file_ids, manifest
    return head


def run_continuous_sync(
    *,
    base_url: str,
    work_root: Path,
    iterations: int,
    progress_every: int,
) -> dict[str, Any]:
    vault_id = "vault-stability-continuous"
    device_a, token_a = register_device(base_url, "Stability A")
    device_b, token_b = register_device(base_url, "Stability B")
    vault_a = work_root / "continuous-a"
    vault_b = work_root / "continuous-b"
    service_a = build_service(base_url, vault_id, vault_a, device_a, token_a)
    service_b = build_service(base_url, vault_id, vault_b, device_b, token_b)

    service_a.ensure_initialized(now_ms=BASE_TIME_MS)
    service_b.ensure_initialized(now_ms=BASE_TIME_MS + 1)

    note_a = vault_a / "Notes" / "stability.md"
    note_b = vault_b / "Notes" / "stability.md"
    started = time.perf_counter()
    last_payload = ""

    for iteration in range(1, iterations + 1):
        last_payload = stability_payload(iteration)
        write_text(note_a, last_payload)
        submitted = service_a.submit_detected_changes_if_needed(
            created_at=BASE_TIME_MS + iteration * 10,
            commit_intent_id=f"intent-stability-{iteration:04d}",
        )
        assert submitted is not None, f"iteration {iteration} produced no commit"
        assert submitted.network.commit.status == "committed", submitted.network.commit.status
        response = submitted.network.commit.response
        assert response is not None
        assert response.new_revision == iteration, response

        pulled = service_b.pull_and_apply(rewritten_at=BASE_TIME_MS + iteration * 10 + 1)
        assert pulled.pull.pull.head.head_revision == iteration
        assert note_b.read_text(encoding="utf-8") == last_payload

        if progress_every > 0 and iteration % progress_every == 0:
            print(json.dumps({"event": "progress", "scenario": "continuous", "iteration": iteration}), flush=True)

    elapsed_seconds = round(time.perf_counter() - started, 3)
    head = assert_head(base_url, vault_id, token_a, iterations)
    assert note_b.read_text(encoding="utf-8") == last_payload
    assert service_a.detect_local_changes().change_count == 0
    assert service_b.detect_local_changes().change_count == 0

    return {
        "ok": True,
        "vault_id": vault_id,
        "iterations": iterations,
        "head_revision": head["head_revision"],
        "elapsed_seconds": elapsed_seconds,
        "final_sha256": hashlib.sha256(last_payload.encode("utf-8")).hexdigest(),
        "devices": [device_a, device_b],
    }


def run_long_offline_delete_regression(*, base_url: str, work_root: Path) -> dict[str, Any]:
    vault_id = "vault-stability-offline-delete"
    device_a, token_a = register_device(base_url, "Online A")
    device_b, token_b = register_device(base_url, "Online B")
    device_c, token_c = register_device(base_url, "Offline C")
    vault_a = work_root / "offline-a"
    vault_b = work_root / "offline-b"
    vault_c = work_root / "offline-c"
    service_a = build_service(base_url, vault_id, vault_a, device_a, token_a)
    service_b = build_service(base_url, vault_id, vault_b, device_b, token_b)
    service_c = build_service(base_url, vault_id, vault_c, device_c, token_c)

    service_a.ensure_initialized(now_ms=BASE_TIME_MS + 100000)
    service_b.ensure_initialized(now_ms=BASE_TIME_MS + 100001)
    service_c.ensure_initialized(now_ms=BASE_TIME_MS + 100002)

    relative_note = Path("Notes") / "offline-delete.md"
    file_id = "file-Notes-offline-delete.md"
    note_a = vault_a / relative_note
    note_b = vault_b / relative_note
    note_c = vault_c / relative_note

    initial_payload = "# Offline delete\n\ncreated before old device goes offline\n"
    write_text(note_a, initial_payload)
    created = service_a.submit_detected_changes_if_needed(
        created_at=BASE_TIME_MS + 100100,
        commit_intent_id="intent-offline-create",
    )
    assert created is not None
    assert created.network.commit.status == "committed"
    assert created.network.commit.response is not None
    assert created.network.commit.response.new_revision == 1

    pulled_b = service_b.pull_and_apply(rewritten_at=BASE_TIME_MS + 100200)
    pulled_c = service_c.pull_and_apply(rewritten_at=BASE_TIME_MS + 100300)
    assert pulled_b.pull.pull.head.head_revision == 1
    assert pulled_c.pull.pull.head.head_revision == 1
    assert note_b.read_text(encoding="utf-8") == initial_payload
    assert note_c.read_text(encoding="utf-8") == initial_payload

    thirty_days_ms = 30 * 24 * 60 * 60 * 1000
    service_a.delete_workspace_note(file_id, now_ms=BASE_TIME_MS + thirty_days_ms)
    deleted = service_a.submit_detected_changes_if_needed(
        created_at=BASE_TIME_MS + thirty_days_ms + 100,
        commit_intent_id="intent-offline-delete",
    )
    assert deleted is not None
    assert deleted.network.commit.status == "committed"
    assert deleted.network.commit.response is not None
    assert deleted.network.commit.response.new_revision == 2
    assert_head(base_url, vault_id, token_a, 2)

    pulled_b_delete = service_b.pull_and_apply(rewritten_at=BASE_TIME_MS + thirty_days_ms + 200)
    assert pulled_b_delete.pull.pull.head.head_revision == 2
    assert not note_b.exists()

    stale_submit = service_c.submit_detected_changes_if_needed(
        created_at=BASE_TIME_MS + thirty_days_ms + 300,
        commit_intent_id="intent-offline-stale-submit",
    )
    assert stale_submit is None
    assert_head(base_url, vault_id, token_a, 2)

    pulled_c_delete = service_c.pull_and_apply(rewritten_at=BASE_TIME_MS + thirty_days_ms + 400)
    assert pulled_c_delete.pull.pull.head.head_revision == 2
    assert not note_c.exists()
    assert service_c.detect_local_changes().change_count == 0

    post_pull_submit = service_c.submit_detected_changes_if_needed(
        created_at=BASE_TIME_MS + thirty_days_ms + 500,
        commit_intent_id="intent-offline-post-pull-submit",
    )
    assert post_pull_submit is None
    head = assert_file_not_active_at_head(
        base_url,
        vault_id,
        token_a,
        file_id,
        minimum_revision=2,
    )

    return {
        "ok": True,
        "vault_id": vault_id,
        "head_revision": head["head_revision"],
        "deleted_file_id": file_id,
        "offline_gap_days": 30,
        "stale_submit_created_commit": False,
        "canonical_path_exists_after_reconnect": note_c.exists(),
        "devices": [device_a, device_b, device_c],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run NoteAI desktop sync stability regressions.")
    parser.add_argument(
        "--iterations",
        type=int,
        default=int(os.environ.get("NOTEAPP_STABILITY_ITERATIONS", "1000")),
        help="Number of continuous single-file sync iterations.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("NOTEAPP_DESKTOP_STABILITY_PORT", "8092")),
        help="Local sync server port.",
    )
    parser.add_argument(
        "--storage",
        choices=["json", "sqlite"],
        default=os.environ.get("NOTEAPP_STABILITY_STORAGE", "sqlite"),
        help="Server storage profile.",
    )
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--skip-continuous", action="store_true")
    parser.add_argument("--skip-long-offline", action="store_true")
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args()


def run_with_temporary_server(
    *,
    port: int,
    storage: str,
    runner: Any,
) -> dict[str, Any]:
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as data_dir, tempfile.TemporaryDirectory() as work_dir:
        process = start_server(data_dir=Path(data_dir), port=port, storage=storage)
        try:
            wait_for_server(base_url)
            return runner(base_url, Path(work_dir))
        finally:
            stop_server(process)


def main() -> int:
    args = parse_args()
    if args.iterations < 1 and not args.skip_continuous:
        raise ValueError("--iterations must be >= 1")

    base_url = f"http://127.0.0.1:{args.port}"
    report: dict[str, Any] = {
        "ok": True,
        "storage": args.storage,
        "base_url": base_url,
        "started_at_ms": int(time.time() * 1000),
        "continuous": None,
        "long_offline_delete": None,
    }
    if not args.skip_continuous:
        report["continuous"] = run_with_temporary_server(
            port=args.port,
            storage=args.storage,
            runner=lambda scenario_base_url, scenario_work_root: run_continuous_sync(
                base_url=scenario_base_url,
                work_root=scenario_work_root,
                iterations=args.iterations,
                progress_every=args.progress_every,
            ),
        )
    if not args.skip_long_offline:
        report["long_offline_delete"] = run_with_temporary_server(
            port=args.port,
            storage=args.storage,
            runner=lambda scenario_base_url, scenario_work_root: run_long_offline_delete_regression(
                base_url=scenario_base_url,
                work_root=scenario_work_root,
            ),
        )
    report["finished_at_ms"] = int(time.time() * 1000)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
