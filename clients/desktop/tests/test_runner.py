from __future__ import annotations

import unittest

from clients.desktop.runner import DesktopSyncRunner


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def ensure_initialized(self, *, now_ms=None):
        self.calls.append(("init", now_ms))
        return {"step": "init", "now_ms": now_ms}

    def resume_commit_recovery(self, *, normalized_at: int):
        self.calls.append(("recover", normalized_at))
        return {"step": "recover", "normalized_at": normalized_at}

    def pull_and_ack(self, *, rewritten_at: int):
        self.calls.append(("pull", rewritten_at))
        return {"step": "pull", "rewritten_at": rewritten_at}

    def load_snapshot(self):
        self.calls.append(("status", None))
        return {"step": "status"}


class DesktopSyncRunnerTests(unittest.TestCase):
    def test_run_once_executes_recovery_then_pull_then_final_snapshot(self) -> None:
        service = FakeService()

        result = DesktopSyncRunner(service).run_once(
            init_now_ms=1770000050000,
            recovery_normalized_at=1770000050100,
            pull_rewritten_at=1770000050200,
        )

        self.assertEqual(
            result.initialized,
            {"step": "init", "now_ms": 1770000050000},
        )
        self.assertEqual(
            result.recovery,
            {"step": "recover", "normalized_at": 1770000050100},
        )
        self.assertEqual(
            result.pull,
            {"step": "pull", "rewritten_at": 1770000050200},
        )
        self.assertEqual(result.final_snapshot, {"step": "status"})
        self.assertEqual(
            service.calls,
            [
                ("init", 1770000050000),
                ("recover", 1770000050100),
                ("pull", 1770000050200),
                ("status", None),
            ],
        )


if __name__ == "__main__":
    unittest.main()
