from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from clients.desktop.runner import DesktopSyncCycleResult
from clients.desktop.worker import (
    DesktopSyncWorker,
    DesktopSyncWorkerConfig,
    build_desktop_sync_worker_health,
    build_desktop_sync_worker_state_record,
    load_desktop_sync_worker_state,
)


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[int | None, int, int | None, list[str] | None, bool, dict[str, bytes] | None, str | None, int | None, int]] = []

    def run_cycle(
        self,
        *,
        init_now_ms=None,
        recovery_normalized_at: int,
        submit_created_at=None,
        submit_file_ids=None,
        submit_detected=False,
        encrypted_blob_by_file_id=None,
        commit_intent_id=None,
        cleanup_normalized_at=None,
        pull_rewritten_at: int,
    ) -> DesktopSyncCycleResult:
        resolved_file_ids = None if submit_file_ids is None else list(submit_file_ids)
        self.calls.append(
            (
                init_now_ms,
                recovery_normalized_at,
                submit_created_at,
                resolved_file_ids,
                submit_detected,
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
                else {"step": "submit", "created_at": submit_created_at}
            ),
            pull={"step": "pull", "rewritten_at": pull_rewritten_at},
            final_snapshot={"step": "status", "iteration": len(self.calls) - 1},
        )


class DesktopSyncWorkerTests(unittest.TestCase):
    def test_run_auto_plans_time_and_step_from_interval(self) -> None:
        runner = FakeRunner()
        slept: list[float] = []

        result = DesktopSyncWorker(
            runner,
            sleep=slept.append,
            now_ms_provider=lambda: 1770000070000,
        ).run(
            DesktopSyncWorkerConfig(
                iterations=2,
                interval_seconds=2.5,
                submit_file_ids=["file-a"],
            )
        )

        self.assertEqual(result.started_at_ms, 1770000070000)
        self.assertEqual(result.effective_step_ms, 2500)
        self.assertEqual(result.time_plan.recovery_normalized_at, 1770000070010)
        self.assertEqual(result.time_plan.submit_created_at, 1770000070020)
        self.assertEqual(result.time_plan.cleanup_normalized_at, 1770000070021)
        self.assertEqual(result.time_plan.pull_rewritten_at, 1770000070030)
        self.assertEqual(
            runner.calls,
            [
                (
                    1770000070000,
                    1770000070010,
                    1770000070020,
                    ["file-a"],
                    False,
                    None,
                    None,
                    1770000070021,
                    1770000070030,
                ),
                (
                    1770000072500,
                    1770000072510,
                    1770000072520,
                    ["file-a"],
                    False,
                    None,
                    None,
                    1770000072521,
                    1770000072530,
                ),
            ],
        )
        self.assertEqual(slept, [2.5])
        self.assertTrue(result.loop.config.continue_on_error)
        self.assertEqual(result.finished_at_ms, 1770000070000)

    def test_run_preserves_explicit_step_and_stop_on_error(self) -> None:
        runner = FakeRunner()

        result = DesktopSyncWorker(
            runner,
            sleep=lambda _: None,
            now_ms_provider=lambda: 1770000071000,
        ).run(
            DesktopSyncWorkerConfig(
                iterations=1,
                interval_seconds=5.0,
                step_ms=99,
                continue_on_error=False,
                recovery_normalized_at=1770000071111,
                pull_rewritten_at=1770000071222,
            )
        )

        self.assertEqual(result.effective_step_ms, 99)
        self.assertFalse(result.loop.config.continue_on_error)
        self.assertEqual(runner.calls[0][1], 1770000071111)
        self.assertEqual(runner.calls[0][8], 1770000071222)

    def test_run_can_schedule_detected_submit_mode(self) -> None:
        runner = FakeRunner()

        result = DesktopSyncWorker(
            runner,
            sleep=lambda _: None,
            now_ms_provider=lambda: 1770000071500,
        ).run(
            DesktopSyncWorkerConfig(
                iterations=1,
                submit_detected=True,
                commit_intent_id="intent-detected-300",
            )
        )

        self.assertTrue(result.loop.config.submit_detected)
        self.assertEqual(
            runner.calls,
            [
                (
                    1770000071500,
                    1770000071510,
                    1770000071520,
                    None,
                    True,
                    None,
                    "intent-detected-300",
                    1770000071521,
                    1770000071530,
                )
            ],
        )

    def test_run_writes_state_file_when_state_path_is_provided(self) -> None:
        runner = FakeRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / ".noteapp" / "sync-worker-state.json"
            result = DesktopSyncWorker(
                runner,
                sleep=lambda _: None,
                now_ms_provider=lambda: 1770000072000,
                state_path=state_path,
            ).run(
                DesktopSyncWorkerConfig(
                    iterations=1,
                    submit_file_ids=["file-a"],
                )
            )

            written = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(written["started_at_ms"], 1770000072000)
            self.assertEqual(written["finished_at_ms"], 1770000072000)
            self.assertEqual(written["effective_step_ms"], 0)
            self.assertEqual(written["success_count"], 1)
            self.assertEqual(written["failure_count"], 0)
            self.assertEqual(written["state_path"], str(state_path))
            self.assertEqual(written["latest_failure"], None)
            self.assertEqual(result.state_path, state_path)

    def test_build_state_record_projects_worker_result_summary(self) -> None:
        runner = FakeRunner()

        result = DesktopSyncWorker(
            runner,
            sleep=lambda _: None,
            now_ms_provider=lambda: 1770000073000,
        ).run(
            DesktopSyncWorkerConfig(
                iterations=1,
                submit_file_ids=["file-a"],
            )
        )

        record = build_desktop_sync_worker_state_record(result)
        self.assertEqual(record.started_at_ms, 1770000073000)
        self.assertEqual(record.finished_at_ms, 1770000073000)
        self.assertEqual(record.success_count, 1)
        self.assertEqual(record.failure_count, 0)
        self.assertIsNone(record.latest_failure)

    def test_load_state_and_build_health_from_written_file(self) -> None:
        runner = FakeRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / ".noteapp" / "sync-worker-state.json"
            DesktopSyncWorker(
                runner,
                sleep=lambda _: None,
                now_ms_provider=lambda: 1770000074000,
                state_path=state_path,
            ).run(
                DesktopSyncWorkerConfig(
                    iterations=1,
                    submit_file_ids=["file-a"],
                )
            )

            state = load_desktop_sync_worker_state(state_path)
            health = build_desktop_sync_worker_health(state)

            self.assertEqual(state.started_at_ms, 1770000074000)
            self.assertEqual(health.status, "healthy")
            self.assertEqual(health.failure_count, 0)
            self.assertIsNone(health.latest_failure)


if __name__ == "__main__":
    unittest.main()
