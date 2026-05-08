from __future__ import annotations

import argparse
import base64
import json
import sys
from time import time
from time import sleep as default_sleep
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, TextIO

from vault_core import BlobDownloadSessionResult
from vault_core.constants import FILEMAP_FILENAME, NOTEAPP_DIRNAME

from .runner import DesktopSyncRunner
from .service import DesktopPullRequiredBlobResult
from .scheduler import (
    DesktopSyncCycleScheduleConfig,
    DesktopSyncScheduleConfig,
    DesktopSyncScheduler,
)
from .service import DesktopSyncService, build_desktop_sync_service, inspect_vault_package
from .sync_runtime import DesktopSyncHttpConfig
from .timing import resolve_desktop_sync_time_plan
from .workspace import DesktopVaultPaths
from .worker import DesktopSyncWorker, DesktopSyncWorkerConfig

ServiceBuilder = Callable[[DesktopSyncHttpConfig, Path], DesktopSyncService]
NowMsProvider = Callable[[], int]


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


def _extract_required_blob_ids_from_pull_result(result: Any) -> list[str]:
    payload = _to_jsonable(result)
    if not isinstance(payload, dict):
        return []
    pull_payload = payload.get("pull")
    if not isinstance(pull_payload, dict):
        return []
    reconcile_payload = pull_payload.get("reconcile")
    if not isinstance(reconcile_payload, dict):
        return []
    applied_payload = reconcile_payload.get("applied")
    if not isinstance(applied_payload, dict):
        return []
    required_blob_ids = applied_payload.get("required_blob_ids")
    if not isinstance(required_blob_ids, list):
        return []
    return [blob_id for blob_id in required_blob_ids if isinstance(blob_id, str) and blob_id]


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BlobDownloadSessionResult):
        return {
            "init": _to_jsonable(value.init),
            "downloaded_blobs_base64": {
                blob_id: base64.b64encode(payload).decode("ascii")
                for blob_id, payload in value.downloaded_blobs.items()
            },
        }
    if isinstance(value, DesktopPullRequiredBlobResult):
        return {
            "pull": _to_jsonable(value.pull),
            "plan": _to_jsonable(value.plan),
            "download": None if value.download is None else _to_jsonable(value.download),
            "plaintext_by_file_id_base64": {
                file_id: base64.b64encode(payload).decode("ascii")
                for file_id, payload in value.plaintext_by_file_id.items()
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


def _infer_local_vault_id(vault_root: Path) -> str:
    filemap_path = vault_root / NOTEAPP_DIRNAME / FILEMAP_FILENAME
    payload = json.loads(filemap_path.read_text(encoding="utf-8"))
    vault_id = payload.get("vault_id")
    if not isinstance(vault_id, str) or not vault_id:
        raise ValueError(f"vault_id not found in local filemap: {filemap_path}")
    return vault_id


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pkb-desktop-sync")
    parser.add_argument("--vault-root", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--vault-id")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--bearer-token")

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--now-ms", type=int)

    subparsers.add_parser("status")
    subparsers.add_parser("detect-local-changes")
    subparsers.add_parser("worker-state")
    subparsers.add_parser("worker-health")
    subparsers.add_parser("vault-summary")
    subparsers.add_parser("sync-panel")
    inspect_vault_parser = subparsers.add_parser("inspect-vault-package")
    inspect_vault_parser.add_argument("--input-package", required=True)
    subparsers.add_parser("list-conflicts")
    export_vault_parser = subparsers.add_parser("export-vault")
    export_vault_parser.add_argument("--output-package", required=True)
    export_vault_parser.add_argument("--include-ai-raw", action="store_true")
    import_vault_parser = subparsers.add_parser("import-vault")
    import_vault_parser.add_argument("--input-package", required=True)
    resolve_conflicts_parser = subparsers.add_parser("resolve-conflicts")
    resolve_conflicts_parser.add_argument("--resolved-at", type=int, required=True)
    resolve_conflicts_parser.add_argument("--all", action="store_true", dest="resolve_all")
    resolve_conflicts_parser.add_argument("--file-id", action="append", dest="file_ids")
    resolve_conflicts_parser.add_argument("--orphan-path", action="append", dest="orphan_paths")

    pull_parser = subparsers.add_parser("pull")
    pull_parser.add_argument("--rewritten-at", type=int, required=True)
    pull_parser.add_argument("--plan-apply", action="store_true")
    pull_parser.add_argument("--apply", action="store_true")
    pull_parser.add_argument("--apply-nonblocking", action="store_true")
    pull_parser.add_argument("--download-required-blobs", action="store_true")
    pull_parser.add_argument("--decrypt-required-blobs", action="store_true")
    pull_parser.add_argument("--output-dir")
    pull_parser.add_argument("--plaintext-output-dir")
    pull_parser.add_argument("--stage-required-blobs", action="store_true")

    recover_parser = subparsers.add_parser("recover")
    recover_parser.add_argument("--normalized-at", type=int, required=True)

    recover_pull_parser = subparsers.add_parser("recover-pull-apply")
    recover_pull_parser.add_argument("--normalized-at", type=int, required=True)

    sync_once_parser = subparsers.add_parser("sync-once")
    sync_once_parser.add_argument("--normalized-at", type=int)
    sync_once_parser.add_argument("--rewritten-at", type=int)
    sync_once_parser.add_argument("--now-ms", type=int)

    sync_loop_parser = subparsers.add_parser("sync-loop")
    sync_loop_parser.add_argument("--iterations", type=int, required=True)
    sync_loop_parser.add_argument("--normalized-at", type=int)
    sync_loop_parser.add_argument("--rewritten-at", type=int)
    sync_loop_parser.add_argument("--interval-seconds", type=float, default=0.0)
    sync_loop_parser.add_argument("--step-ms", type=int, default=0)
    sync_loop_parser.add_argument("--now-ms", type=int)
    sync_loop_parser.add_argument("--continue-on-error", action="store_true")

    sync_cycle_parser = subparsers.add_parser("sync-cycle")
    sync_cycle_parser.add_argument("--normalized-at", type=int)
    sync_cycle_parser.add_argument("--rewritten-at", type=int)
    sync_cycle_parser.add_argument("--now-ms", type=int)
    sync_cycle_parser.add_argument("--submit-created-at", type=int)
    sync_cycle_parser.add_argument("--file-id", action="append", dest="file_ids")
    sync_cycle_parser.add_argument("--submit-detected", action="store_true")
    sync_cycle_parser.add_argument("--commit-intent-id")
    sync_cycle_parser.add_argument("--cleanup-normalized-at", type=int)
    sync_cycle_parser.add_argument("--encrypted-map")
    sync_cycle_parser.add_argument("--encrypted-dir")

    sync_cycle_loop_parser = subparsers.add_parser("sync-cycle-loop")
    sync_cycle_loop_parser.add_argument("--iterations", type=int, required=True)
    sync_cycle_loop_parser.add_argument("--normalized-at", type=int)
    sync_cycle_loop_parser.add_argument("--rewritten-at", type=int)
    sync_cycle_loop_parser.add_argument("--interval-seconds", type=float, default=0.0)
    sync_cycle_loop_parser.add_argument("--step-ms", type=int, default=0)
    sync_cycle_loop_parser.add_argument("--now-ms", type=int)
    sync_cycle_loop_parser.add_argument("--submit-created-at", type=int)
    sync_cycle_loop_parser.add_argument("--file-id", action="append", dest="file_ids")
    sync_cycle_loop_parser.add_argument("--submit-detected", action="store_true")
    sync_cycle_loop_parser.add_argument("--commit-intent-id")
    sync_cycle_loop_parser.add_argument("--cleanup-normalized-at", type=int)
    sync_cycle_loop_parser.add_argument("--encrypted-map")
    sync_cycle_loop_parser.add_argument("--encrypted-dir")
    sync_cycle_loop_parser.add_argument("--continue-on-error", action="store_true")

    sync_worker_parser = subparsers.add_parser("sync-worker")
    sync_worker_parser.add_argument("--iterations", type=int, required=True)
    sync_worker_parser.add_argument("--interval-seconds", type=float, default=30.0)
    sync_worker_parser.add_argument("--step-ms", type=int)
    sync_worker_parser.add_argument("--now-ms", type=int)
    sync_worker_parser.add_argument("--normalized-at", type=int)
    sync_worker_parser.add_argument("--submit-created-at", type=int)
    sync_worker_parser.add_argument("--file-id", action="append", dest="file_ids")
    sync_worker_parser.add_argument("--submit-detected", action="store_true")
    sync_worker_parser.add_argument("--commit-intent-id")
    sync_worker_parser.add_argument("--cleanup-normalized-at", type=int)
    sync_worker_parser.add_argument("--rewritten-at", type=int)
    sync_worker_parser.add_argument("--encrypted-map")
    sync_worker_parser.add_argument("--encrypted-dir")
    sync_worker_parser.add_argument("--stop-on-error", action="store_true")

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

    submit_detected_parser = subparsers.add_parser("submit-detected-commit")
    submit_detected_parser.add_argument("--created-at", type=int, required=True)
    submit_detected_parser.add_argument("--commit-intent-id")
    submit_detected_parser.add_argument("--cleanup-normalized-at", type=int)
    return parser


def run_cli(
    argv: Optional[Sequence[str]] = None,
    *,
    stdout: TextIO,
    service_builder: ServiceBuilder = build_cli_service,
    sleep: Callable[[float], None] = default_sleep,
    now_ms_provider: Optional[NowMsProvider] = None,
) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    resolved_now_ms_provider = (
        (lambda: int(time() * 1000))
        if now_ms_provider is None
        else now_ms_provider
    )

    vault_root = Path(args.vault_root)
    if args.command == "inspect-vault-package":
        result = inspect_vault_package(Path(args.input_package))
        stdout.write(json.dumps(_to_jsonable(result), ensure_ascii=False, indent=2))
        stdout.write("\n")
        return 0

    resolved_vault_id = args.vault_id
    if not resolved_vault_id:
        if args.command == "import-vault":
            resolved_vault_id = inspect_vault_package(Path(args.input_package)).vault_id
        elif args.command == "export-vault":
            resolved_vault_id = _infer_local_vault_id(vault_root)
        else:
            raise ValueError(f"--vault-id is required for {args.command}")

    config = DesktopSyncHttpConfig(
        base_url=args.base_url,
        vault_id=resolved_vault_id,
        device_id=args.device_id,
        bearer_token=args.bearer_token,
    )
    service = service_builder(config, vault_root)

    if args.command == "init":
        result = service.ensure_initialized(now_ms=args.now_ms)
    elif args.command == "status":
        result = service.load_snapshot()
    elif args.command == "detect-local-changes":
        result = service.detect_local_changes()
    elif args.command == "worker-state":
        result = service.load_worker_state()
    elif args.command == "worker-health":
        result = service.load_worker_health()
    elif args.command == "vault-summary":
        result = service.summarize_vault()
    elif args.command == "sync-panel":
        result = service.build_sync_panel_model()
    elif args.command == "export-vault":
        result = service.export_vault_package(
            Path(args.output_package),
            include_ai_raw=args.include_ai_raw,
        )
    elif args.command == "import-vault":
        result = service.import_vault_package(Path(args.input_package))
    elif args.command == "list-conflicts":
        result = service.list_conflicts()
    elif args.command == "resolve-conflicts":
        result = service.resolve_conflicts(
            resolved_at=args.resolved_at,
            conflict_file_ids=args.file_ids,
            orphan_relative_paths=args.orphan_paths,
            resolve_all=args.resolve_all,
        )
    elif args.command == "pull":
        if sum(
            1
            for enabled in (args.plan_apply, args.apply, args.apply_nonblocking)
            if enabled
        ) > 1:
            raise ValueError("pull apply modes are mutually exclusive")
        if args.plan_apply and (
            args.apply_nonblocking
            or args.apply
            or args.download_required_blobs
            or args.decrypt_required_blobs
            or args.output_dir
            or args.plaintext_output_dir
            or args.stage_required_blobs
        ):
            raise ValueError("pull --plan-apply cannot be combined with blob download or materialization flags")
        if args.apply and (
            args.download_required_blobs
            or args.decrypt_required_blobs
            or args.output_dir
            or args.plaintext_output_dir
            or args.stage_required_blobs
        ):
            raise ValueError("pull --apply cannot be combined with blob download or materialization flags")
        if args.apply_nonblocking and (
            args.download_required_blobs
            or args.decrypt_required_blobs
            or args.output_dir
            or args.plaintext_output_dir
            or args.stage_required_blobs
        ):
            raise ValueError("pull --apply-nonblocking cannot be combined with blob download or materialization flags")
        if args.decrypt_required_blobs and not args.download_required_blobs:
            raise ValueError("pull --decrypt-required-blobs requires --download-required-blobs")
        if args.output_dir and args.decrypt_required_blobs:
            raise ValueError("pull --output-dir cannot be combined with --decrypt-required-blobs")
        if args.output_dir and not args.download_required_blobs:
            raise ValueError("pull --output-dir requires --download-required-blobs")
        if args.plaintext_output_dir and not args.decrypt_required_blobs:
            raise ValueError("pull --plaintext-output-dir requires --decrypt-required-blobs")
        if args.stage_required_blobs and not args.decrypt_required_blobs:
            raise ValueError("pull --stage-required-blobs requires --decrypt-required-blobs")
        if args.plaintext_output_dir and args.stage_required_blobs:
            raise ValueError("pull --plaintext-output-dir cannot be combined with --stage-required-blobs")
        result = (
            service.pull_and_plan_apply(rewritten_at=args.rewritten_at)
            if args.plan_apply
            else (
                service.pull_and_apply(rewritten_at=args.rewritten_at)
                if args.apply
                else (
                    service.pull_and_apply_nonblocking(rewritten_at=args.rewritten_at)
                    if args.apply_nonblocking
                    else service.pull_and_ack(rewritten_at=args.rewritten_at)
                )
            )
        )
        if args.download_required_blobs:
            if args.decrypt_required_blobs:
                decrypted_result = service.download_and_decrypt_pull_required_blobs(result)
                if args.stage_required_blobs:
                    result = {
                        "pull": _to_jsonable(decrypted_result.pull),
                        "plan": _to_jsonable(decrypted_result.plan),
                        "download": (
                            None
                            if decrypted_result.download is None
                            else _to_jsonable(decrypted_result.download)
                        ),
                        "apply_staging": _to_jsonable(
                            service.stage_pull_required_plaintext_for_apply(
                                decrypted_result,
                                started_at=args.rewritten_at,
                            )
                        ),
                    }
                elif args.plaintext_output_dir:
                    result = {
                        "pull": _to_jsonable(decrypted_result.pull),
                        "plan": _to_jsonable(decrypted_result.plan),
                        "download": (
                            None
                            if decrypted_result.download is None
                            else _to_jsonable(decrypted_result.download)
                        ),
                        "written_plaintext_paths": _to_jsonable(
                            service.materialize_pull_required_plaintext(
                                decrypted_result,
                                Path(args.plaintext_output_dir),
                            )
                        ),
                    }
                else:
                    result = decrypted_result
            else:
                required_blob_ids = _extract_required_blob_ids_from_pull_result(result)
                download_result = None
                if required_blob_ids:
                    download_result = service.download_blobs(required_blob_ids)
                if args.output_dir and download_result is not None:
                    output_dir = Path(args.output_dir)
                    result = {
                        "pull": result,
                        "download": {
                            "init": _to_jsonable(download_result.init),
                            "written_blob_paths": _to_jsonable(
                                _write_downloaded_blobs(output_dir, download_result.downloaded_blobs)
                            ),
                        },
                    }
                else:
                    result = {
                        "pull": result,
                        "download": download_result,
                    }
    elif args.command == "recover":
        result = service.resume_commit_recovery(normalized_at=args.normalized_at)
    elif args.command == "recover-pull-apply":
        result = service.resume_pull_apply_recovery(normalized_at=args.normalized_at)
    elif args.command == "sync-once":
        time_plan = resolve_desktop_sync_time_plan(
            base_now_ms=resolved_now_ms_provider(),
            init_now_ms=args.now_ms,
            recovery_normalized_at=args.normalized_at,
            pull_rewritten_at=args.rewritten_at,
        )
        result = DesktopSyncRunner(service).run_once(
            init_now_ms=time_plan.init_now_ms,
            recovery_normalized_at=time_plan.recovery_normalized_at,
            pull_rewritten_at=time_plan.pull_rewritten_at,
        )
    elif args.command == "sync-loop":
        time_plan = resolve_desktop_sync_time_plan(
            base_now_ms=resolved_now_ms_provider(),
            init_now_ms=args.now_ms,
            recovery_normalized_at=args.normalized_at,
            pull_rewritten_at=args.rewritten_at,
        )
        result = DesktopSyncScheduler(
            DesktopSyncRunner(service),
            sleep=sleep,
        ).run_loop(
            DesktopSyncScheduleConfig(
                iterations=args.iterations,
                init_now_ms=time_plan.init_now_ms,
                recovery_normalized_at=time_plan.recovery_normalized_at,
                pull_rewritten_at=time_plan.pull_rewritten_at,
                interval_seconds=args.interval_seconds,
                step_ms=args.step_ms,
                continue_on_error=args.continue_on_error,
            )
        )
    elif args.command == "sync-cycle":
        should_submit = any(
            (
                args.submit_created_at is not None,
                bool(args.file_ids),
                args.submit_detected,
                args.commit_intent_id is not None,
                args.cleanup_normalized_at is not None,
                args.encrypted_map is not None,
                args.encrypted_dir is not None,
            )
        )
        encrypted_blob_by_file_id = None
        if should_submit:
            if args.submit_detected:
                if args.file_ids:
                    raise ValueError("sync-cycle --submit-detected cannot be combined with --file-id")
                if args.encrypted_map or args.encrypted_dir:
                    raise ValueError(
                        "sync-cycle --submit-detected cannot be combined with encrypted payload inputs"
                    )
            else:
                if not args.file_ids:
                    raise ValueError("sync-cycle submit step requires at least one --file-id")
                if args.encrypted_map or args.encrypted_dir:
                    encrypted_blob_by_file_id = _load_encrypted_blob_payloads(
                        args.file_ids,
                        encrypted_map=args.encrypted_map,
                        encrypted_dir=args.encrypted_dir,
                    )
        time_plan = resolve_desktop_sync_time_plan(
            base_now_ms=resolved_now_ms_provider(),
            init_now_ms=args.now_ms,
            recovery_normalized_at=args.normalized_at,
            submit_created_at=args.submit_created_at,
            cleanup_normalized_at=args.cleanup_normalized_at,
            pull_rewritten_at=args.rewritten_at,
            submit_requested=should_submit,
        )
        result = DesktopSyncRunner(service).run_cycle(
            init_now_ms=time_plan.init_now_ms,
            recovery_normalized_at=time_plan.recovery_normalized_at,
            pull_rewritten_at=time_plan.pull_rewritten_at,
            submit_created_at=time_plan.submit_created_at,
            submit_file_ids=args.file_ids,
            submit_detected=args.submit_detected,
            encrypted_blob_by_file_id=encrypted_blob_by_file_id,
            commit_intent_id=args.commit_intent_id,
            cleanup_normalized_at=time_plan.cleanup_normalized_at,
        )
    elif args.command == "sync-cycle-loop":
        should_submit = any(
            (
                args.submit_created_at is not None,
                bool(args.file_ids),
                args.submit_detected,
                args.commit_intent_id is not None,
                args.cleanup_normalized_at is not None,
                args.encrypted_map is not None,
                args.encrypted_dir is not None,
            )
        )
        encrypted_blob_by_file_id = None
        if should_submit:
            if args.submit_detected:
                if args.file_ids:
                    raise ValueError(
                        "sync-cycle-loop --submit-detected cannot be combined with --file-id"
                    )
                if args.encrypted_map or args.encrypted_dir:
                    raise ValueError(
                        "sync-cycle-loop --submit-detected cannot be combined with encrypted payload inputs"
                    )
            else:
                if not args.file_ids:
                    raise ValueError("sync-cycle-loop submit step requires at least one --file-id")
                if args.encrypted_map or args.encrypted_dir:
                    encrypted_blob_by_file_id = _load_encrypted_blob_payloads(
                        args.file_ids,
                        encrypted_map=args.encrypted_map,
                        encrypted_dir=args.encrypted_dir,
                    )
        time_plan = resolve_desktop_sync_time_plan(
            base_now_ms=resolved_now_ms_provider(),
            init_now_ms=args.now_ms,
            recovery_normalized_at=args.normalized_at,
            submit_created_at=args.submit_created_at,
            cleanup_normalized_at=args.cleanup_normalized_at,
            pull_rewritten_at=args.rewritten_at,
            submit_requested=should_submit,
        )
        result = DesktopSyncScheduler(
            DesktopSyncRunner(service),
            sleep=sleep,
        ).run_cycle_loop(
            DesktopSyncCycleScheduleConfig(
                iterations=args.iterations,
                init_now_ms=time_plan.init_now_ms,
                recovery_normalized_at=time_plan.recovery_normalized_at,
                submit_created_at=time_plan.submit_created_at,
                submit_file_ids=args.file_ids,
                submit_detected=args.submit_detected,
                encrypted_blob_by_file_id=encrypted_blob_by_file_id,
                commit_intent_id=args.commit_intent_id,
                cleanup_normalized_at=time_plan.cleanup_normalized_at,
                pull_rewritten_at=time_plan.pull_rewritten_at,
                interval_seconds=args.interval_seconds,
                step_ms=args.step_ms,
                continue_on_error=args.continue_on_error,
            )
        )
    elif args.command == "sync-worker":
        encrypted_blob_by_file_id = None
        if args.file_ids:
            if args.encrypted_map or args.encrypted_dir:
                encrypted_blob_by_file_id = _load_encrypted_blob_payloads(
                    args.file_ids,
                    encrypted_map=args.encrypted_map,
                    encrypted_dir=args.encrypted_dir,
                )
        elif args.encrypted_map or args.encrypted_dir:
            raise ValueError("sync-worker encrypted payloads require at least one --file-id")
        if args.submit_detected:
            if args.file_ids:
                raise ValueError("sync-worker --submit-detected cannot be combined with --file-id")
            if args.encrypted_map or args.encrypted_dir:
                raise ValueError(
                    "sync-worker --submit-detected cannot be combined with encrypted payload inputs"
                )
        result = DesktopSyncWorker(
            DesktopSyncRunner(service),
            sleep=sleep,
            now_ms_provider=resolved_now_ms_provider,
            state_path=DesktopVaultPaths.from_root(vault_root).worker_state_path,
        ).run(
            DesktopSyncWorkerConfig(
                iterations=args.iterations,
                interval_seconds=args.interval_seconds,
                step_ms=args.step_ms,
                continue_on_error=not args.stop_on_error,
                init_now_ms=args.now_ms,
                recovery_normalized_at=args.normalized_at,
                submit_created_at=args.submit_created_at,
                submit_file_ids=args.file_ids,
                submit_detected=args.submit_detected,
                commit_intent_id=args.commit_intent_id,
                cleanup_normalized_at=args.cleanup_normalized_at,
                pull_rewritten_at=args.rewritten_at,
                encrypted_blob_by_file_id=encrypted_blob_by_file_id,
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
    elif args.command == "submit-detected-commit":
        result = service.submit_detected_changes_if_needed(
            created_at=args.created_at,
            commit_intent_id=args.commit_intent_id,
            cleanup_normalized_at=args.cleanup_normalized_at,
        )
        if result is None:
            result = {
                "status": "skipped",
                "reason": "no_local_changes",
            }
    else:
        raise ValueError(f"unsupported command: {args.command}")

    stdout.write(json.dumps(_to_jsonable(result), ensure_ascii=False, indent=2, sort_keys=True))
    stdout.write("\n")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    return run_cli(argv, stdout=sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
