from __future__ import annotations

from dataclasses import dataclass
from time import sleep as default_sleep
from typing import Callable, Optional

from .runner import DesktopSyncRunOnceResult, DesktopSyncRunner


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
