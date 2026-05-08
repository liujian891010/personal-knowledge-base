from __future__ import annotations

import unittest

from clients.desktop.runner import DesktopSyncRunOnceResult
from clients.desktop.scheduler import (
    DesktopSyncScheduleConfig,
    DesktopSyncScheduler,
)


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[int | None, int, int]] = []

    def run_once(
        self,
        *,
        init_now_ms=None,
        recovery_normalized_at: int,
        pull_rewritten_at: int,
    ) -> DesktopSyncRunOnceResult:
        self.calls.append((init_now_ms, recovery_normalized_at, pull_rewritten_at))
        return DesktopSyncRunOnceResult(
            initialized={"step": "init", "now_ms": init_now_ms},
            recovery={"step": "recover", "normalized_at": recovery_normalized_at},
            pull={"step": "pull", "rewritten_at": pull_rewritten_at},
            final_snapshot={"step": "status", "iteration": len(self.calls) - 1},
        )


class DesktopSyncSchedulerTests(unittest.TestCase):
    def test_run_loop_repeats_runner_with_stepped_timestamps(self) -> None:
        runner = FakeRunner()
        slept: list[float] = []

        result = DesktopSyncScheduler(runner, sleep=slept.append).run_loop(
            DesktopSyncScheduleConfig(
                iterations=3,
                init_now_ms=1770000060000,
                recovery_normalized_at=1770000060100,
                pull_rewritten_at=1770000060200,
                step_ms=25,
                interval_seconds=1.5,
            )
        )

        self.assertEqual(
            runner.calls,
            [
                (1770000060000, 1770000060100, 1770000060200),
                (1770000060025, 1770000060125, 1770000060225),
                (1770000060050, 1770000060150, 1770000060250),
            ],
        )
        self.assertEqual(slept, [1.5, 1.5])
        self.assertEqual([item.iteration for item in result.iterations], [0, 1, 2])
        self.assertEqual(
            result.iterations[1].run_once.pull,
            {"step": "pull", "rewritten_at": 1770000060225},
        )

    def test_run_loop_allows_missing_init_now_ms_without_sleep(self) -> None:
        runner = FakeRunner()
        slept: list[float] = []

        result = DesktopSyncScheduler(runner, sleep=slept.append).run_loop(
            DesktopSyncScheduleConfig(
                iterations=2,
                recovery_normalized_at=1770000061100,
                pull_rewritten_at=1770000061200,
            )
        )

        self.assertEqual(
            runner.calls,
            [
                (None, 1770000061100, 1770000061200),
                (None, 1770000061100, 1770000061200),
            ],
        )
        self.assertEqual(slept, [])
        self.assertEqual(result.config.iterations, 2)

    def test_schedule_config_rejects_invalid_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "iterations"):
            DesktopSyncScheduleConfig(
                iterations=0,
                recovery_normalized_at=1,
                pull_rewritten_at=2,
            )

        with self.assertRaisesRegex(ValueError, "interval_seconds"):
            DesktopSyncScheduleConfig(
                iterations=1,
                recovery_normalized_at=1,
                pull_rewritten_at=2,
                interval_seconds=-0.1,
            )

        with self.assertRaisesRegex(ValueError, "step_ms"):
            DesktopSyncScheduleConfig(
                iterations=1,
                recovery_normalized_at=1,
                pull_rewritten_at=2,
                step_ms=-1,
            )


if __name__ == "__main__":
    unittest.main()
