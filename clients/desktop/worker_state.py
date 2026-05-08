from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .timing import DesktopSyncTimePlan


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"byte_length": len(value)}
    if hasattr(value, "__dataclass_fields__"):
        return {key: _to_jsonable(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        payload = {
            key: _to_jsonable(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
        if payload:
            return payload
    object_payload = {}
    for key in dir(value):
        if key.startswith("_"):
            continue
        try:
            item = getattr(value, key)
        except Exception:
            continue
        if callable(item):
            continue
        object_payload[key] = _to_jsonable(item)
    if object_payload:
        return object_payload
    return value


def _write_text_atomic(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    try:
        temp_path.write_text(payload, encoding="utf-8")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


@dataclass(frozen=True)
class DesktopSyncWorkerConfig:
    iterations: int
    interval_seconds: float = 30.0
    step_ms: Optional[int] = None
    continue_on_error: bool = True
    init_now_ms: Optional[int] = None
    recovery_normalized_at: Optional[int] = None
    submit_created_at: Optional[int] = None
    submit_file_ids: Optional[list[str]] = None
    commit_intent_id: Optional[str] = None
    cleanup_normalized_at: Optional[int] = None
    pull_rewritten_at: Optional[int] = None
    encrypted_blob_by_file_id: Optional[dict[str, bytes]] = None


@dataclass(frozen=True)
class DesktopSyncWorkerFailureRecord:
    iteration: int
    error_type: str
    error_message: str


@dataclass(frozen=True)
class DesktopSyncWorkerStateRecord:
    started_at_ms: int
    finished_at_ms: int
    effective_step_ms: int
    success_count: int
    failure_count: int
    stopped_early: bool
    state_path: Optional[str]
    latest_failure: Optional[DesktopSyncWorkerFailureRecord]
    config: DesktopSyncWorkerConfig
    time_plan: DesktopSyncTimePlan


@dataclass(frozen=True)
class DesktopSyncWorkerHealth:
    status: str
    started_at_ms: int
    finished_at_ms: int
    success_count: int
    failure_count: int
    stopped_early: bool
    latest_failure: Optional[DesktopSyncWorkerFailureRecord]
    state_path: Optional[str]


def _find_latest_failure(result: Any) -> Optional[DesktopSyncWorkerFailureRecord]:
    for item in reversed(result.loop.iterations):
        if item.failure is not None:
            return DesktopSyncWorkerFailureRecord(
                iteration=item.iteration,
                error_type=item.failure.error_type,
                error_message=item.failure.error_message,
            )
    return None


def build_desktop_sync_worker_state_record(result: Any) -> DesktopSyncWorkerStateRecord:
    return DesktopSyncWorkerStateRecord(
        started_at_ms=result.started_at_ms,
        finished_at_ms=result.finished_at_ms,
        effective_step_ms=result.effective_step_ms,
        success_count=result.loop.success_count,
        failure_count=result.loop.failure_count,
        stopped_early=result.loop.stopped_early,
        state_path=None if result.state_path is None else str(result.state_path),
        latest_failure=_find_latest_failure(result),
        config=result.config,
        time_plan=result.time_plan,
    )


def write_desktop_sync_worker_state(
    path: Path,
    result: Any,
) -> DesktopSyncWorkerStateRecord:
    record = build_desktop_sync_worker_state_record(result)
    _write_text_atomic(
        path,
        json.dumps(_to_jsonable(record), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return record


def load_desktop_sync_worker_state(path: Path) -> DesktopSyncWorkerStateRecord:
    payload = json.loads(path.read_text(encoding="utf-8"))
    latest_failure_payload = payload.get("latest_failure")
    latest_failure = None
    if latest_failure_payload is not None:
        latest_failure = DesktopSyncWorkerFailureRecord(
            iteration=latest_failure_payload["iteration"],
            error_type=latest_failure_payload["error_type"],
            error_message=latest_failure_payload["error_message"],
        )
    return DesktopSyncWorkerStateRecord(
        started_at_ms=payload["started_at_ms"],
        finished_at_ms=payload["finished_at_ms"],
        effective_step_ms=payload["effective_step_ms"],
        success_count=payload["success_count"],
        failure_count=payload["failure_count"],
        stopped_early=payload["stopped_early"],
        state_path=payload.get("state_path"),
        latest_failure=latest_failure,
        config=DesktopSyncWorkerConfig(**payload["config"]),
        time_plan=DesktopSyncTimePlan(**payload["time_plan"]),
    )


def build_desktop_sync_worker_health(
    state: DesktopSyncWorkerStateRecord,
) -> DesktopSyncWorkerHealth:
    if state.failure_count == 0:
        status = "healthy"
    elif state.success_count > 0:
        status = "degraded"
    else:
        status = "failing"
    return DesktopSyncWorkerHealth(
        status=status,
        started_at_ms=state.started_at_ms,
        finished_at_ms=state.finished_at_ms,
        success_count=state.success_count,
        failure_count=state.failure_count,
        stopped_early=state.stopped_early,
        latest_failure=state.latest_failure,
        state_path=state.state_path,
    )
