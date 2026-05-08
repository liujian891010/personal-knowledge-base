from __future__ import annotations

from dataclasses import dataclass
from time import sleep as default_sleep
from typing import Callable, Iterable, Mapping, Optional

from .runner import DesktopSyncCycleResult, DesktopSyncRunOnceResult, DesktopSyncRunner


@dataclass(frozen=True)
class DesktopSyncScheduleConfig:
    iterations: int
    recovery_normalized_at: int
    pull_rewritten_at: int
    interval_seconds: float = 0.0
    init_now_ms: Optional[int] = None
    step_ms: int = 0

    def __post_init__(self) -> None:
        if self.iterations < 1:
            raise ValueError("iterations must be >= 1")
        if self.interval_seconds < 0:
            raise ValueError("interval_seconds must be >= 0")
        if self.step_ms < 0:
            raise ValueError("step_ms must be >= 0")


@dataclass(frozen=True)
class DesktopSyncIterationResult:
    iteration: int
    init_now_ms: Optional[int]
    recovery_normalized_at: int
    pull_rewritten_at: int
    run_once: DesktopSyncRunOnceResult


@dataclass(frozen=True)
class DesktopSyncLoopResult:
    config: DesktopSyncScheduleConfig
    iterations: list[DesktopSyncIterationResult]


@dataclass(frozen=True)
class DesktopSyncCycleScheduleConfig:
    iterations: int
    recovery_normalized_at: int
    pull_rewritten_at: int
    interval_seconds: float = 0.0
    init_now_ms: Optional[int] = None
    submit_created_at: Optional[int] = None
    submit_file_ids: Optional[list[str]] = None
    encrypted_blob_by_file_id: Optional[dict[str, bytes]] = None
    commit_intent_id: Optional[str] = None
    cleanup_normalized_at: Optional[int] = None
    step_ms: int = 0

    def __post_init__(self) -> None:
        if self.iterations < 1:
            raise ValueError("iterations must be >= 1")
        if self.interval_seconds < 0:
            raise ValueError("interval_seconds must be >= 0")
        if self.step_ms < 0:
            raise ValueError("step_ms must be >= 0")
        if self.submit_created_at is None:
            if self.submit_file_ids:
                raise ValueError("submit_file_ids require submit_created_at")
            if self.encrypted_blob_by_file_id is not None:
                raise ValueError("encrypted_blob_by_file_id requires submit_created_at")
            if self.commit_intent_id is not None:
                raise ValueError("commit_intent_id requires submit_created_at")
            if self.cleanup_normalized_at is not None:
                raise ValueError("cleanup_normalized_at requires submit_created_at")
        elif not self.submit_file_ids:
            raise ValueError("submit_created_at requires submit_file_ids")


@dataclass(frozen=True)
class DesktopSyncCycleIterationResult:
    iteration: int
    init_now_ms: Optional[int]
    recovery_normalized_at: int
    submit_created_at: Optional[int]
    pull_rewritten_at: int
    cleanup_normalized_at: Optional[int]
    run_cycle: DesktopSyncCycleResult


@dataclass(frozen=True)
class DesktopSyncCycleLoopResult:
    config: DesktopSyncCycleScheduleConfig
    iterations: list[DesktopSyncCycleIterationResult]


@dataclass(frozen=True)
class DesktopSyncScheduler:
    runner: DesktopSyncRunner
    sleep: Callable[[float], None] = default_sleep

    def run_loop(self, config: DesktopSyncScheduleConfig) -> DesktopSyncLoopResult:
        iterations: list[DesktopSyncIterationResult] = []

        for iteration in range(config.iterations):
            offset_ms = iteration * config.step_ms
            init_now_ms = (
                None if config.init_now_ms is None else config.init_now_ms + offset_ms
            )
            recovery_normalized_at = config.recovery_normalized_at + offset_ms
            pull_rewritten_at = config.pull_rewritten_at + offset_ms
            run_once = self.runner.run_once(
                init_now_ms=init_now_ms,
                recovery_normalized_at=recovery_normalized_at,
                pull_rewritten_at=pull_rewritten_at,
            )
            iterations.append(
                DesktopSyncIterationResult(
                    iteration=iteration,
                    init_now_ms=init_now_ms,
                    recovery_normalized_at=recovery_normalized_at,
                    pull_rewritten_at=pull_rewritten_at,
                    run_once=run_once,
                )
            )
            if iteration + 1 < config.iterations and config.interval_seconds > 0:
                self.sleep(config.interval_seconds)

        return DesktopSyncLoopResult(
            config=config,
            iterations=iterations,
        )

    def run_cycle_loop(self, config: DesktopSyncCycleScheduleConfig) -> DesktopSyncCycleLoopResult:
        iterations: list[DesktopSyncCycleIterationResult] = []

        for iteration in range(config.iterations):
            offset_ms = iteration * config.step_ms
            init_now_ms = None if config.init_now_ms is None else config.init_now_ms + offset_ms
            recovery_normalized_at = config.recovery_normalized_at + offset_ms
            submit_created_at = (
                None if config.submit_created_at is None else config.submit_created_at + offset_ms
            )
            pull_rewritten_at = config.pull_rewritten_at + offset_ms
            cleanup_normalized_at = (
                None
                if config.cleanup_normalized_at is None
                else config.cleanup_normalized_at + offset_ms
            )
            run_cycle = self.runner.run_cycle(
                init_now_ms=init_now_ms,
                recovery_normalized_at=recovery_normalized_at,
                submit_created_at=submit_created_at,
                submit_file_ids=config.submit_file_ids,
                encrypted_blob_by_file_id=config.encrypted_blob_by_file_id,
                commit_intent_id=config.commit_intent_id,
                cleanup_normalized_at=cleanup_normalized_at,
                pull_rewritten_at=pull_rewritten_at,
            )
            iterations.append(
                DesktopSyncCycleIterationResult(
                    iteration=iteration,
                    init_now_ms=init_now_ms,
                    recovery_normalized_at=recovery_normalized_at,
                    submit_created_at=submit_created_at,
                    pull_rewritten_at=pull_rewritten_at,
                    cleanup_normalized_at=cleanup_normalized_at,
                    run_cycle=run_cycle,
                )
            )
            if iteration + 1 < config.iterations and config.interval_seconds > 0:
                self.sleep(config.interval_seconds)

        return DesktopSyncCycleLoopResult(
            config=config,
            iterations=iterations,
        )
