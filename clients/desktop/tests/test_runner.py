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

    def submit_workspace_commit(
        self,
        *,
        created_at: int,
        file_ids,
        encrypted_blob_by_file_id,
        commit_intent_id=None,
        cleanup_normalized_at=None,
    ):
        self.calls.append(
            (
                "submit",
                created_at,
                list(file_ids),
                encrypted_blob_by_file_id,
                commit_intent_id,
                cleanup_normalized_at,
            )
        )
        return {
            "step": "submit",
            "created_at": created_at,
            "file_ids": list(file_ids),
            "encrypted_blob_sizes": (
                None
                if encrypted_blob_by_file_id is None
                else {
                    key: len(value) for key, value in encrypted_blob_by_file_id.items()
                }
            ),
            "commit_intent_id": commit_intent_id,
            "cleanup_normalized_at": cleanup_normalized_at,
        }

    def submit_detected_changes(
        self,
        *,
        created_at: int,
        commit_intent_id=None,
        cleanup_normalized_at=None,
    ):
        self.calls.append(
            (
                "submit-detected",
                created_at,
                commit_intent_id,
                cleanup_normalized_at,
            )
        )
        return {
            "step": "submit-detected",
            "created_at": created_at,
            "commit_intent_id": commit_intent_id,
            "cleanup_normalized_at": cleanup_normalized_at,
        }

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

    def test_run_cycle_submits_workspace_commit_before_pull(self) -> None:
        service = FakeService()

        result = DesktopSyncRunner(service).run_cycle(
            init_now_ms=1770000051000,
            recovery_normalized_at=1770000051010,
            submit_created_at=1770000051020,
            submit_file_ids=["file-a"],
            encrypted_blob_by_file_id={"file-a": b"blob-a"},
            commit_intent_id="intent-003",
            cleanup_normalized_at=1770000051021,
            pull_rewritten_at=1770000051030,
        )

        self.assertEqual(
            result.submitted,
            {
                "step": "submit",
                "created_at": 1770000051020,
                "file_ids": ["file-a"],
                "encrypted_blob_sizes": {"file-a": 6},
                "commit_intent_id": "intent-003",
                "cleanup_normalized_at": 1770000051021,
            },
        )
        self.assertEqual(
            service.calls,
            [
                ("init", 1770000051000),
                ("recover", 1770000051010),
                (
                    "submit",
                    1770000051020,
                    ["file-a"],
                    {"file-a": b"blob-a"},
                    "intent-003",
                    1770000051021,
                ),
                ("pull", 1770000051030),
                ("status", None),
            ],
        )

    def test_run_cycle_allows_submit_without_explicit_encrypted_payloads(self) -> None:
        service = FakeService()

        result = DesktopSyncRunner(service).run_cycle(
            init_now_ms=1770000051100,
            recovery_normalized_at=1770000051110,
            submit_created_at=1770000051120,
            submit_file_ids=["file-a"],
            pull_rewritten_at=1770000051130,
        )

        self.assertEqual(result.submitted["file_ids"], ["file-a"])
        self.assertEqual(
            service.calls,
            [
                ("init", 1770000051100),
                ("recover", 1770000051110),
                ("submit", 1770000051120, ["file-a"], None, None, None),
                ("pull", 1770000051130),
                ("status", None),
            ],
        )

    def test_run_cycle_can_submit_detected_changes_before_pull(self) -> None:
        service = FakeService()

        result = DesktopSyncRunner(service).run_cycle(
            init_now_ms=1770000051200,
            recovery_normalized_at=1770000051210,
            submit_created_at=1770000051220,
            submit_detected=True,
            commit_intent_id="intent-detected-001",
            cleanup_normalized_at=1770000051221,
            pull_rewritten_at=1770000051230,
        )

        self.assertEqual(
            result.submitted,
            {
                "step": "submit-detected",
                "created_at": 1770000051220,
                "commit_intent_id": "intent-detected-001",
                "cleanup_normalized_at": 1770000051221,
            },
        )
        self.assertEqual(
            service.calls,
            [
                ("init", 1770000051200),
                ("recover", 1770000051210),
                ("submit-detected", 1770000051220, "intent-detected-001", 1770000051221),
                ("pull", 1770000051230),
                ("status", None),
            ],
        )


if __name__ == "__main__":
    unittest.main()
