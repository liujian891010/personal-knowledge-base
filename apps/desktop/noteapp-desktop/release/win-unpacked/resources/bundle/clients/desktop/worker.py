from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Callable, Optional

from .runner import DesktopSyncRunner
from .scheduler import (
    DesktopSyncCycleLoopResult,
    DesktopSyncCycleScheduleConfig,
    DesktopSyncScheduler,
)
from .timing import DesktopSyncTimePlan, resolve_desktop_sync_time_plan
from .worker_state import (
    DesktopSyncWorkerConfig,
    DesktopSyncWorkerFailureRecord,
    DesktopSyncWorkerHealth,
    DesktopSyncWorkerStateRecord,
    build_desktop_sync_worker_health,
    build_desktop_sync_worker_state_record,
    load_desktop_sync_worker_state,
    write_desktop_sync_worker_state,
)


def _default_worker_step_ms(interval_seconds: float, iterations: int) -> int:
    if iterations < 2:
        return 0
    return max(1, int(ceil(interval_seconds * 1000)))


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
class DesktopSyncWorker:
    runner: DesktopSyncRunner
    sleep: Callable[[float], None]
    now_ms_provider: Callable[[], int]
    state_path: Optional[Path] = None

    def run(self, config: DesktopSyncWorkerConfig) -> DesktopSyncWorkerResult:
        started_at_ms = self.now_ms_provider()
        submit_requested = any(
            (
                config.submit_created_at is not None,
                bool(config.submit_file_ids),
                config.submit_detected,
                config.commit_intent_id is not None,
                config.cleanup_normalized_at is not None,
                config.encrypted_blob_by_file_id is not None,
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
                submit_detected=config.submit_detected,
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
