from __future__ import annotations

import argparse
import base64
import json
import sys
from time import sleep as default_sleep
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, TextIO

from vault_core import BlobDownloadSessionResult

from .runner import DesktopSyncRunner
from .scheduler import (
    DesktopSyncCycleScheduleConfig,
    DesktopSyncScheduleConfig,
    DesktopSyncScheduler,
)
from .service import DesktopSyncService, build_desktop_sync_service
from .sync_runtime import DesktopSyncHttpConfig

ServiceBuilder = Callable[[DesktopSyncHttpConfig, Path], DesktopSyncService]


def _resolve_blob_output_path(output_dir: Path, blob_id: str) -> Path:
    if not blob_id or blob_id in {".", ".."}:
        raise ValueError(f"blob_id is not safe for output path: {blob_id!r}")
    if Path(blob_id).name != blob_id or "/" in blob_id or "\\" in blob_id:
        raise ValueError(f"blob_id is not safe for output path: {blob_id!r}")
    return output_dir / f"{blob_id}.blob"


def _write_downloaded_blobs(output_dir: Path, downloaded_blobs: dict[str, bytes]) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written_paths: dict[str, Path] = {}
    for blob_id, payload in downloaded_blobs.items():
        output_path = _resolve_blob_output_path(output_dir, blob_id)
        output_path.write_bytes(payload)
        written_paths[blob_id] = output_path
    return written_paths


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BlobDownloadSessionResult):
        return {
            "init": _to_jsonable(value.init),
            "downloaded_blobs_base64": {
                blob_id: base64.b64encode(payload).decode("ascii")
                for blob_id, payload in value.downloaded_blobs.items()
            },
        }
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def build_cli_service(config: DesktopSyncHttpConfig, vault_root: Path) -> DesktopSyncService:
    return build_desktop_sync_service(config, vault_root)


def _load_base64_payload_map(path: Path) -> dict[str, bytes]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"payload file must contain an object: {path}")

    decoded: dict[str, bytes] = {}
    for file_id, encoded in payload.items():
        if not isinstance(file_id, str) or not file_id:
            raise ValueError(f"payload file contains invalid file_id: {path}")
        if not isinstance(encoded, str):
            raise ValueError(f"payload file values must be base64 strings: {path}")
        decoded[file_id] = base64.b64decode(encoded.encode("ascii"), validate=True)
    return decoded


def _load_payload_dir(file_ids: Sequence[str], path: Path, *, suffix: str) -> dict[str, bytes]:
    decoded: dict[str, bytes] = {}
    for file_id in file_ids:
        payload_path = path / f"{file_id}{suffix}"
        decoded[file_id] = payload_path.read_bytes()
    return decoded


def _load_encrypted_blob_payloads(
    file_ids: Sequence[str],
    *,
    encrypted_map: Optional[str],
    encrypted_dir: Optional[str],
) -> dict[str, bytes]:
    if encrypted_map:
        return _load_base64_payload_map(Path(encrypted_map))
    if encrypted_dir:
        return _load_payload_dir(
            file_ids,
            Path(encrypted_dir),
            suffix=".blob",
        )
    raise ValueError("encrypted payload source is required")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pkb-desktop-sync")
    parser.add_argument("--vault-root", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--vault-id", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--bearer-token")

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--now-ms", type=int)

    subparsers.add_parser("status")

    pull_parser = subparsers.add_parser("pull")
    pull_parser.add_argument("--rewritten-at", type=int, required=True)

    recover_parser = subparsers.add_parser("recover")
    recover_parser.add_argument("--normalized-at", type=int, required=True)

    sync_once_parser = subparsers.add_parser("sync-once")
    sync_once_parser.add_argument("--normalized-at", type=int, required=True)
    sync_once_parser.add_argument("--rewritten-at", type=int, required=True)
    sync_once_parser.add_argument("--now-ms", type=int)

    sync_loop_parser = subparsers.add_parser("sync-loop")
    sync_loop_parser.add_argument("--iterations", type=int, required=True)
    sync_loop_parser.add_argument("--normalized-at", type=int, required=True)
    sync_loop_parser.add_argument("--rewritten-at", type=int, required=True)
    sync_loop_parser.add_argument("--interval-seconds", type=float, default=0.0)
    sync_loop_parser.add_argument("--step-ms", type=int, default=0)
    sync_loop_parser.add_argument("--now-ms", type=int)
    sync_loop_parser.add_argument("--continue-on-error", action="store_true")

    sync_cycle_parser = subparsers.add_parser("sync-cycle")
    sync_cycle_parser.add_argument("--normalized-at", type=int, required=True)
    sync_cycle_parser.add_argument("--rewritten-at", type=int, required=True)
    sync_cycle_parser.add_argument("--now-ms", type=int)
    sync_cycle_parser.add_argument("--submit-created-at", type=int)
    sync_cycle_parser.add_argument("--file-id", action="append", dest="file_ids")
    sync_cycle_parser.add_argument("--commit-intent-id")
    sync_cycle_parser.add_argument("--cleanup-normalized-at", type=int)
    sync_cycle_parser.add_argument("--encrypted-map")
    sync_cycle_parser.add_argument("--encrypted-dir")

    sync_cycle_loop_parser = subparsers.add_parser("sync-cycle-loop")
    sync_cycle_loop_parser.add_argument("--iterations", type=int, required=True)
    sync_cycle_loop_parser.add_argument("--normalized-at", type=int, required=True)
    sync_cycle_loop_parser.add_argument("--rewritten-at", type=int, required=True)
    sync_cycle_loop_parser.add_argument("--interval-seconds", type=float, default=0.0)
    sync_cycle_loop_parser.add_argument("--step-ms", type=int, default=0)
    sync_cycle_loop_parser.add_argument("--now-ms", type=int)
    sync_cycle_loop_parser.add_argument("--submit-created-at", type=int)
    sync_cycle_loop_parser.add_argument("--file-id", action="append", dest="file_ids")
    sync_cycle_loop_parser.add_argument("--commit-intent-id")
    sync_cycle_loop_parser.add_argument("--cleanup-normalized-at", type=int)
    sync_cycle_loop_parser.add_argument("--encrypted-map")
    sync_cycle_loop_parser.add_argument("--encrypted-dir")
    sync_cycle_loop_parser.add_argument("--continue-on-error", action="store_true")

    download_parser = subparsers.add_parser("download-blobs")
    download_parser.add_argument("--blob-id", action="append", dest="blob_ids", required=True)
    download_parser.add_argument("--output-dir")

    submit_parser = subparsers.add_parser("submit-commit")
    submit_parser.add_argument("--created-at", type=int, required=True)
    submit_parser.add_argument("--commit-intent-id")
    submit_parser.add_argument("--cleanup-normalized-at", type=int)
    submit_parser.add_argument("--content-map", required=True)
    submit_parser.add_argument("--encrypted-map", required=True)

    submit_workspace_parser = subparsers.add_parser("submit-workspace-commit")
    submit_workspace_parser.add_argument("--created-at", type=int, required=True)
    submit_workspace_parser.add_argument("--file-id", action="append", dest="file_ids", required=True)
    submit_workspace_parser.add_argument("--commit-intent-id")
    submit_workspace_parser.add_argument("--cleanup-normalized-at", type=int)
    encrypted_group = submit_workspace_parser.add_mutually_exclusive_group(required=False)
    encrypted_group.add_argument("--encrypted-map")
    encrypted_group.add_argument("--encrypted-dir")
    return parser


def run_cli(
    argv: Optional[Sequence[str]] = None,
    *,
    stdout: TextIO,
    service_builder: ServiceBuilder = build_cli_service,
    sleep: Callable[[float], None] = default_sleep,
) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)

    config = DesktopSyncHttpConfig(
        base_url=args.base_url,
        vault_id=args.vault_id,
        device_id=args.device_id,
        bearer_token=args.bearer_token,
    )
    service = service_builder(config, Path(args.vault_root))

    if args.command == "init":
        result = service.ensure_initialized(now_ms=args.now_ms)
    elif args.command == "status":
        result = service.load_snapshot()
    elif args.command == "pull":
        result = service.pull_and_ack(rewritten_at=args.rewritten_at)
    elif args.command == "recover":
        result = service.resume_commit_recovery(normalized_at=args.normalized_at)
    elif args.command == "sync-once":
        result = DesktopSyncRunner(service).run_once(
            init_now_ms=args.now_ms,
            recovery_normalized_at=args.normalized_at,
            pull_rewritten_at=args.rewritten_at,
        )
    elif args.command == "sync-loop":
        result = DesktopSyncScheduler(
            DesktopSyncRunner(service),
            sleep=sleep,
        ).run_loop(
            DesktopSyncScheduleConfig(
                iterations=args.iterations,
                init_now_ms=args.now_ms,
                recovery_normalized_at=args.normalized_at,
                pull_rewritten_at=args.rewritten_at,
                interval_seconds=args.interval_seconds,
                step_ms=args.step_ms,
                continue_on_error=args.continue_on_error,
            )
        )
    elif args.command == "sync-cycle":
        should_submit = any(
            value is not None
            for value in (
                args.submit_created_at,
                args.file_ids,
                args.commit_intent_id,
                args.cleanup_normalized_at,
                args.encrypted_map,
                args.encrypted_dir,
            )
        )
        encrypted_blob_by_file_id = None
        if should_submit:
            if args.submit_created_at is None:
                raise ValueError("sync-cycle submit step requires --submit-created-at")
            if not args.file_ids:
                raise ValueError("sync-cycle submit step requires at least one --file-id")
            if args.encrypted_map or args.encrypted_dir:
                encrypted_blob_by_file_id = _load_encrypted_blob_payloads(
                    args.file_ids,
                    encrypted_map=args.encrypted_map,
                    encrypted_dir=args.encrypted_dir,
                )
        result = DesktopSyncRunner(service).run_cycle(
            init_now_ms=args.now_ms,
            recovery_normalized_at=args.normalized_at,
            pull_rewritten_at=args.rewritten_at,
            submit_created_at=args.submit_created_at,
            submit_file_ids=args.file_ids,
            encrypted_blob_by_file_id=encrypted_blob_by_file_id,
            commit_intent_id=args.commit_intent_id,
            cleanup_normalized_at=args.cleanup_normalized_at,
        )
    elif args.command == "sync-cycle-loop":
        should_submit = any(
            value is not None
            for value in (
                args.submit_created_at,
                args.file_ids,
                args.commit_intent_id,
                args.cleanup_normalized_at,
                args.encrypted_map,
                args.encrypted_dir,
            )
        )
        encrypted_blob_by_file_id = None
        if should_submit:
            if args.submit_created_at is None:
                raise ValueError("sync-cycle-loop submit step requires --submit-created-at")
            if not args.file_ids:
                raise ValueError("sync-cycle-loop submit step requires at least one --file-id")
            if args.encrypted_map or args.encrypted_dir:
                encrypted_blob_by_file_id = _load_encrypted_blob_payloads(
                    args.file_ids,
                    encrypted_map=args.encrypted_map,
                    encrypted_dir=args.encrypted_dir,
                )
        result = DesktopSyncScheduler(
            DesktopSyncRunner(service),
            sleep=sleep,
        ).run_cycle_loop(
            DesktopSyncCycleScheduleConfig(
                iterations=args.iterations,
                init_now_ms=args.now_ms,
                recovery_normalized_at=args.normalized_at,
                submit_created_at=args.submit_created_at,
                submit_file_ids=args.file_ids,
                encrypted_blob_by_file_id=encrypted_blob_by_file_id,
                commit_intent_id=args.commit_intent_id,
                cleanup_normalized_at=args.cleanup_normalized_at,
                pull_rewritten_at=args.rewritten_at,
                interval_seconds=args.interval_seconds,
                step_ms=args.step_ms,
                continue_on_error=args.continue_on_error,
            )
        )
    elif args.command == "download-blobs":
        result = service.download_blobs(args.blob_ids)
        if args.output_dir:
            output_dir = Path(args.output_dir)
            result = {
                "init": _to_jsonable(result.init),
                "written_blob_paths": _to_jsonable(
                    _write_downloaded_blobs(output_dir, result.downloaded_blobs)
                ),
            }
    elif args.command == "submit-commit":
        result = service.submit_commit(
            created_at=args.created_at,
            commit_intent_id=args.commit_intent_id,
            cleanup_normalized_at=args.cleanup_normalized_at,
            content_by_file_id=_load_base64_payload_map(Path(args.content_map)),
            encrypted_blob_by_file_id=_load_base64_payload_map(Path(args.encrypted_map)),
        )
    elif args.command == "submit-workspace-commit":
        result = service.submit_workspace_commit(
            created_at=args.created_at,
            file_ids=args.file_ids,
            commit_intent_id=args.commit_intent_id,
            cleanup_normalized_at=args.cleanup_normalized_at,
            encrypted_blob_by_file_id=(
                _load_encrypted_blob_payloads(
                    args.file_ids,
                    encrypted_map=args.encrypted_map,
                    encrypted_dir=args.encrypted_dir,
                )
                if args.encrypted_map or args.encrypted_dir
                else None
            ),
        )
    else:
        raise ValueError(f"unsupported command: {args.command}")

    stdout.write(json.dumps(_to_jsonable(result), ensure_ascii=False, indent=2, sort_keys=True))
    stdout.write("\n")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    return run_cli(argv, stdout=sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
