from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from math import ceil
from typing import Any, Callable, Optional, Sequence

from .runner import DesktopSyncRunner
from .scheduler import (
    DesktopSyncCycleLoopResult,
    DesktopSyncCycleScheduleConfig,
    DesktopSyncScheduler,
)
from .timing import DesktopSyncTimePlan, resolve_desktop_sync_time_plan


def _default_worker_step_ms(interval_seconds: float, iterations: int) -> int:
    if iterations < 2:
        return 0
    return max(1, int(ceil(interval_seconds * 1000)))


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: _to_jsonable(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
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
class DesktopSyncWorkerResult:
    config: DesktopSyncWorkerConfig
    started_at_ms: int
    finished_at_ms: int
    time_plan: DesktopSyncTimePlan
    effective_step_ms: int
    loop: DesktopSyncCycleLoopResult
    state_path: Optional[Path] = None


@dataclass(frozen=True)
class DesktopSyncWorkerStateRecord:
    started_at_ms: int
    finished_at_ms: int
    effective_step_ms: int
    success_count: int
    failure_count: int
    stopped_early: bool
    state_path: Optional[str]
    config: DesktopSyncWorkerConfig
    time_plan: DesktopSyncTimePlan


def build_desktop_sync_worker_state_record(
    result: DesktopSyncWorkerResult,
) -> DesktopSyncWorkerStateRecord:
    return DesktopSyncWorkerStateRecord(
        started_at_ms=result.started_at_ms,
        finished_at_ms=result.finished_at_ms,
        effective_step_ms=result.effective_step_ms,
        success_count=result.loop.success_count,
        failure_count=result.loop.failure_count,
        stopped_early=result.loop.stopped_early,
        state_path=None if result.state_path is None else str(result.state_path),
        config=result.config,
        time_plan=result.time_plan,
    )


def write_desktop_sync_worker_state(
    path: Path,
    result: DesktopSyncWorkerResult,
) -> DesktopSyncWorkerStateRecord:
    record = build_desktop_sync_worker_state_record(result)
    _write_text_atomic(
        path,
        json.dumps(_to_jsonable(record), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return record


@dataclass(frozen=True)
class DesktopSyncWorker:
    runner: DesktopSyncRunner
    sleep: Callable[[float], None]
    now_ms_provider: Callable[[], int]
    state_path: Optional[Path] = None

    def run(self, config: DesktopSyncWorkerConfig) -> DesktopSyncWorkerResult:
        started_at_ms = self.now_ms_provider()
        submit_requested = any(
            value is not None
            for value in (
                config.submit_created_at,
                config.submit_file_ids,
                config.commit_intent_id,
                config.cleanup_normalized_at,
                config.encrypted_blob_by_file_id,
            )
        )
        time_plan = resolve_desktop_sync_time_plan(
            base_now_ms=started_at_ms,
            init_now_ms=config.init_now_ms,
            recovery_normalized_at=config.recovery_normalized_at,
            submit_created_at=config.submit_created_at,
            cleanup_normalized_at=config.cleanup_normalized_at,
            pull_rewritten_at=config.pull_rewritten_at,
            submit_requested=submit_requested,
        )
        effective_step_ms = (
            _default_worker_step_ms(config.interval_seconds, config.iterations)
            if config.step_ms is None
            else config.step_ms
        )
        loop = DesktopSyncScheduler(
            self.runner,
            sleep=self.sleep,
        ).run_cycle_loop(
            DesktopSyncCycleScheduleConfig(
                iterations=config.iterations,
                init_now_ms=time_plan.init_now_ms,
                recovery_normalized_at=time_plan.recovery_normalized_at,
                submit_created_at=time_plan.submit_created_at,
                submit_file_ids=config.submit_file_ids,
                encrypted_blob_by_file_id=config.encrypted_blob_by_file_id,
                commit_intent_id=config.commit_intent_id,
                cleanup_normalized_at=time_plan.cleanup_normalized_at,
                pull_rewritten_at=time_plan.pull_rewritten_at,
                interval_seconds=config.interval_seconds,
                step_ms=effective_step_ms,
                continue_on_error=config.continue_on_error,
            )
        )
        result = DesktopSyncWorkerResult(
            config=config,
            started_at_ms=started_at_ms,
            finished_at_ms=self.now_ms_provider(),
            time_plan=time_plan,
            effective_step_ms=effective_step_ms,
            loop=loop,
            state_path=self.state_path,
        )
        if self.state_path is not None:
            write_desktop_sync_worker_state(self.state_path, result)
        return result
