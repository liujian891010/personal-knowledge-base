from __future__ import annotations

import unittest

from clients.desktop.runner import DesktopSyncCycleResult, DesktopSyncRunOnceResult
from clients.desktop.scheduler import (
    DesktopSyncCycleScheduleConfig,
    DesktopSyncScheduleConfig,
    DesktopSyncScheduler,
)


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[int | None, int, int]] = []
        self.cycle_calls: list[tuple[int | None, int, int | None, list[str] | None, dict[str, bytes] | None, str | None, int | None, int]] = []

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

    def run_cycle(
        self,
        *,
        init_now_ms=None,
        recovery_normalized_at: int,
        submit_created_at=None,
        submit_file_ids=None,
        encrypted_blob_by_file_id=None,
        commit_intent_id=None,
        cleanup_normalized_at=None,
        pull_rewritten_at: int,
    ) -> DesktopSyncCycleResult:
        resolved_file_ids = None if submit_file_ids is None else list(submit_file_ids)
        self.cycle_calls.append(
            (
                init_now_ms,
                recovery_normalized_at,
                submit_created_at,
                resolved_file_ids,
                encrypted_blob_by_file_id,
                commit_intent_id,
                cleanup_normalized_at,
                pull_rewritten_at,
            )
        )
        return DesktopSyncCycleResult(
            initialized={"step": "init", "now_ms": init_now_ms},
            recovery={"step": "recover", "normalized_at": recovery_normalized_at},
            submitted=(
                None
                if submit_created_at is None
                else {
                    "step": "submit",
                    "created_at": submit_created_at,
                    "file_ids": resolved_file_ids,
                }
            ),
            pull={"step": "pull", "rewritten_at": pull_rewritten_at},
            final_snapshot={"step": "status", "iteration": len(self.cycle_calls) - 1},
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

    def test_run_cycle_loop_repeats_runner_cycle_with_stepped_submit_timestamps(self) -> None:
        runner = FakeRunner()
        slept: list[float] = []

        result = DesktopSyncScheduler(runner, sleep=slept.append).run_cycle_loop(
            DesktopSyncCycleScheduleConfig(
                iterations=2,
                init_now_ms=1770000062000,
                recovery_normalized_at=1770000062100,
                submit_created_at=1770000062150,
                submit_file_ids=["file-a"],
                encrypted_blob_by_file_id={"file-a": b"enc-a"},
                commit_intent_id="intent-100",
                cleanup_normalized_at=1770000062151,
                pull_rewritten_at=1770000062200,
                step_ms=40,
                interval_seconds=2.0,
            )
        )

        self.assertEqual(
            runner.cycle_calls,
            [
                (
                    1770000062000,
                    1770000062100,
                    1770000062150,
                    ["file-a"],
                    {"file-a": b"enc-a"},
                    "intent-100",
                    1770000062151,
                    1770000062200,
                ),
                (
                    1770000062040,
                    1770000062140,
                    1770000062190,
                    ["file-a"],
                    {"file-a": b"enc-a"},
                    "intent-100",
                    1770000062191,
                    1770000062240,
                ),
            ],
        )
        self.assertEqual(slept, [2.0])
        self.assertEqual(result.iterations[1].run_cycle.submitted["created_at"], 1770000062190)

    def test_cycle_schedule_config_rejects_incomplete_submit_config(self) -> None:
        with self.assertRaisesRegex(ValueError, "submit_file_ids"):
            DesktopSyncCycleScheduleConfig(
                iterations=1,
                recovery_normalized_at=1,
                submit_created_at=2,
                pull_rewritten_at=3,
            )

        with self.assertRaisesRegex(ValueError, "submit_created_at"):
            DesktopSyncCycleScheduleConfig(
                iterations=1,
                recovery_normalized_at=1,
                submit_file_ids=["file-a"],
                pull_rewritten_at=3,
            )


if __name__ == "__main__":
    unittest.main()
