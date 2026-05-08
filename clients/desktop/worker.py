from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Callable, Optional, Sequence

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
    time_plan: DesktopSyncTimePlan
    effective_step_ms: int
    loop: DesktopSyncCycleLoopResult


@dataclass(frozen=True)
class DesktopSyncWorker:
    runner: DesktopSyncRunner
    sleep: Callable[[float], None]
    now_ms_provider: Callable[[], int]

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
        return DesktopSyncWorkerResult(
            config=config,
            started_at_ms=started_at_ms,
            time_plan=time_plan,
            effective_step_ms=effective_step_ms,
            loop=loop,
        )
