from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from clients.desktop.cli import run_cli
from vault_core import (
    BlobDownloadCapability,
    BlobDownloadInitExecutionResult,
    BlobDownloadInitRequestPayload,
    BlobDownloadInitResponsePayload,
    BlobDownloadSessionResult,
)


@dataclass
class FakeResult:
    kind: str
    value: int


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []
        self.fail_recover_at_calls: set[int] = set()
        self.fail_submit_workspace_at_calls: set[int] = set()
        self.skip_submit_detected_if_needed = False
        self.pull_and_ack_payload = None
        self.download_and_decrypt_pull_payload = None
        self.detect_local_changes_payload = {
            "vault_id": "vault-001",
            "tracked_record_count": 2,
            "change_count": 2,
            "modified_file_ids": ["file-a"],
            "missing_file_ids": ["file-b"],
            "changes": [
                {
                    "kind": "modified",
                    "path": "Notes/A.md",
                    "file_id": "file-a",
                    "file_type": "note",
                    "record_status": "active",
                    "content_hash": "sha256:aaa",
                    "size_bytes": 123,
                    "mtime_ms": 1770000044900,
                },
                {
                    "kind": "missing",
                    "path": "Notes/B.md",
                    "file_id": "file-b",
                    "file_type": "note",
                    "record_status": "active",
                    "content_hash": "sha256:bbb",
                    "size_bytes": None,
                    "mtime_ms": None,
                },
            ],
        }
        self.worker_state_payload = {
            "started_at_ms": 1770000045000,
            "finished_at_ms": 1770000045001,
            "effective_step_ms": 2500,
            "success_count": 2,
            "failure_count": 1,
            "stopped_early": False,
            "state_path": "C:/vault/.noteapp/sync-worker-state.json",
            "latest_failure": {
                "iteration": 1,
                "error_type": "RuntimeError",
                "error_message": "submit failed at call 0",
            },
            "config": {
                "iterations": 2,
                "interval_seconds": 2.5,
                "step_ms": None,
                "continue_on_error": True,
                "init_now_ms": None,
                "recovery_normalized_at": None,
                "submit_created_at": None,
                "submit_file_ids": ["file-a"],
                "commit_intent_id": None,
                "cleanup_normalized_at": None,
                "pull_rewritten_at": None,
                "encrypted_blob_by_file_id": None,
            },
            "time_plan": {
                "base_now_ms": 1770000045000,
                "init_now_ms": 1770000045000,
                "recovery_normalized_at": 1770000045010,
                "submit_created_at": 1770000045020,
                "cleanup_normalized_at": 1770000045021,
                "pull_rewritten_at": 1770000045030,
            },
        }
        self.worker_health_payload = {
            "status": "degraded",
            "started_at_ms": 1770000045000,
            "finished_at_ms": 1770000045001,
            "success_count": 2,
            "failure_count": 1,
            "stopped_early": False,
            "state_path": "C:/vault/.noteapp/sync-worker-state.json",
            "latest_failure": {
                "iteration": 1,
                "error_type": "RuntimeError",
                "error_message": "submit failed at call 0",
            },
        }

    def ensure_initialized(self, *, now_ms=None):
        self.calls.append(("init", now_ms))
        return FakeResult(kind="init", value=now_ms)

    def load_snapshot(self):
        self.calls.append(("status", None))
        return {"kind": "status", "files": 1}

    def detect_local_changes(self):
        self.calls.append(("detect-local-changes", None))
        return self.detect_local_changes_payload

    def load_worker_state(self):
        self.calls.append(("worker-state", None))
        return self.worker_state_payload

    def load_worker_health(self):
        self.calls.append(("worker-health", None))
        return self.worker_health_payload

    def pull_and_ack(self, *, rewritten_at: int):
        self.calls.append(("pull", rewritten_at))
        if self.pull_and_ack_payload is not None:
            return self.pull_and_ack_payload
        return {"kind": "pull", "rewritten_at": rewritten_at}

    def resume_commit_recovery(self, *, normalized_at: int):
        self.calls.append(("recover", normalized_at))
        recover_count = sum(1 for call in self.calls if call[0] == "recover") - 1
        if recover_count in self.fail_recover_at_calls:
            raise RuntimeError(f"recover failed at call {recover_count}")
        return {"kind": "recover", "normalized_at": normalized_at}

    def download_blobs(self, blob_ids):
        self.calls.append(("download-blobs", list(blob_ids)))
        return BlobDownloadSessionResult(
            init=BlobDownloadInitExecutionResult(
                request=BlobDownloadInitRequestPayload(blob_ids=list(blob_ids)),
                response=BlobDownloadInitResponsePayload(
                    downloads=[
                        BlobDownloadCapability(
                            blob_id=blob_id,
                            download_url=f"https://blob.example.com/download/{blob_id}",
                            encrypted_size=3,
                            expires_at="2026-05-08T12:00:00Z",
                        )
                        for blob_id in blob_ids
                    ]
                ),
            ),
            downloaded_blobs={blob_id: b"xyz" for blob_id in blob_ids},
        )

    def download_and_decrypt_pull_required_blobs(self, pull_result):
        self.calls.append(("download-and-decrypt-pull-required-blobs", pull_result))
        if self.download_and_decrypt_pull_payload is not None:
            return self.download_and_decrypt_pull_payload
        return {
            "pull": pull_result,
            "plan": {"blob_ids": [], "files": [], "revision": 0, "vault_id": "vault-001"},
            "download": None,
            "plaintext_by_file_id_base64": {},
        }

    def submit_commit(
        self,
        *,
        created_at: int,
        commit_intent_id=None,
        cleanup_normalized_at=None,
        content_by_file_id,
        encrypted_blob_by_file_id,
    ):
        self.calls.append(
            (
                "submit-commit",
                created_at,
                commit_intent_id,
                cleanup_normalized_at,
                content_by_file_id,
                encrypted_blob_by_file_id,
            )
        )
        return {
            "kind": "submit-commit",
            "created_at": created_at,
            "commit_intent_id": commit_intent_id,
            "content_sizes": {key: len(value) for key, value in content_by_file_id.items()},
            "encrypted_sizes": {key: len(value) for key, value in encrypted_blob_by_file_id.items()},
        }

    def submit_workspace_commit(
        self,
        *,
        created_at: int,
        file_ids,
        commit_intent_id=None,
        cleanup_normalized_at=None,
        encrypted_blob_by_file_id=None,
    ):
        self.calls.append(
            (
                "submit-workspace-commit",
                created_at,
                list(file_ids),
                commit_intent_id,
                cleanup_normalized_at,
                encrypted_blob_by_file_id,
            )
        )
        submit_count = sum(1 for call in self.calls if call[0] == "submit-workspace-commit") - 1
        if submit_count in self.fail_submit_workspace_at_calls:
            raise RuntimeError(f"submit failed at call {submit_count}")
        return {
            "kind": "submit-workspace-commit",
            "created_at": created_at,
            "file_ids": list(file_ids),
            "commit_intent_id": commit_intent_id,
            "encrypted_sizes": (
                None
                if encrypted_blob_by_file_id is None
                else {
                    key: len(value) for key, value in encrypted_blob_by_file_id.items()
                }
            ),
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
                "submit-detected-commit",
                created_at,
                commit_intent_id,
                cleanup_normalized_at,
            )
        )
        return {
            "kind": "submit-detected-commit",
            "created_at": created_at,
            "commit_intent_id": commit_intent_id,
            "cleanup_normalized_at": cleanup_normalized_at,
        }

    def submit_detected_changes_if_needed(
        self,
        *,
        created_at: int,
        commit_intent_id=None,
        cleanup_normalized_at=None,
    ):
        self.calls.append(
            (
                "submit-detected-commit",
                created_at,
                commit_intent_id,
                cleanup_normalized_at,
            )
        )
        if self.skip_submit_detected_if_needed:
            return None
        return {
            "kind": "submit-detected-commit",
            "created_at": created_at,
            "commit_intent_id": commit_intent_id,
            "cleanup_normalized_at": cleanup_normalized_at,
        }


class DesktopCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.created = []
        self.service = FakeService()

    def _builder(self, config, vault_root: Path):
        self.created.append((config, vault_root))
        return self.service

    def _run(self, *argv: str, sleep=None, now_ms_provider=None):
        stdout = io.StringIO()
        exit_code = run_cli(
            argv,
            stdout=stdout,
            service_builder=self._builder,
            sleep=(lambda _: None) if sleep is None else sleep,
            now_ms_provider=(lambda: 1770000040000) if now_ms_provider is None else now_ms_provider,
        )
        return exit_code, json.loads(stdout.getvalue())

    def test_init_command_builds_service_and_returns_json(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "--bearer-token",
            "token-1",
            "init",
            "--now-ms",
            "1770000040000",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, {"kind": "init", "value": 1770000040000})
        config, vault_root = self.created[0]
        self.assertEqual(config.base_url, "https://sync.example.com")
        self.assertEqual(config.vault_id, "vault-001")
        self.assertEqual(config.device_id, "desktop-shanghai")
        self.assertEqual(config.bearer_token, "token-1")
        self.assertEqual(vault_root, Path("C:/vault"))
        self.assertEqual(self.service.calls, [("init", 1770000040000)])

    def test_status_command_routes_to_load_snapshot(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "status",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, {"files": 1, "kind": "status"})
        self.assertEqual(self.service.calls, [("status", None)])

    def test_detect_local_changes_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "detect-local-changes",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["modified_file_ids"], ["file-a"])
        self.assertEqual(payload["missing_file_ids"], ["file-b"])
        self.assertEqual(self.service.calls, [("detect-local-changes", None)])

    def test_worker_state_command_routes_to_load_worker_state(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "worker-state",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["latest_failure"]["iteration"], 1)
        self.assertEqual(self.service.calls, [("worker-state", None)])

    def test_worker_health_command_routes_to_load_worker_health(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "worker-health",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(self.service.calls, [("worker-health", None)])

    def test_pull_command_routes_rewritten_at(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "pull",
            "--rewritten-at",
            "1770000040100",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, {"kind": "pull", "rewritten_at": 1770000040100})
        self.assertEqual(self.service.calls, [("pull", 1770000040100)])

    def test_pull_command_can_download_required_blobs(self) -> None:
        self.service.pull_and_ack_payload = {
            "pull": {
                "reconcile": {
                    "applied": {
                        "required_blob_ids": ["blob-a", "blob-b"],
                    }
                }
            }
        }

        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "pull",
            "--rewritten-at",
            "1770000040100",
            "--download-required-blobs",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "pull": {
                    "pull": {
                        "reconcile": {
                            "applied": {
                                "required_blob_ids": ["blob-a", "blob-b"],
                            }
                        }
                    }
                },
                "download": {
                    "init": {
                        "request": {"blob_ids": ["blob-a", "blob-b"]},
                        "response": {
                            "downloads": [
                                {
                                    "blob_id": "blob-a",
                                    "download_url": "https://blob.example.com/download/blob-a",
                                    "encrypted_size": 3,
                                    "expires_at": "2026-05-08T12:00:00Z",
                                    "headers": None,
                                },
                                {
                                    "blob_id": "blob-b",
                                    "download_url": "https://blob.example.com/download/blob-b",
                                    "encrypted_size": 3,
                                    "expires_at": "2026-05-08T12:00:00Z",
                                    "headers": None,
                                },
                            ]
                        },
                    },
                    "downloaded_blobs_base64": {
                        "blob-a": base64.b64encode(b"xyz").decode("ascii"),
                        "blob-b": base64.b64encode(b"xyz").decode("ascii"),
                    },
                },
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                ("pull", 1770000040100),
                ("download-blobs", ["blob-a", "blob-b"]),
            ],
        )

    def test_pull_command_can_write_downloaded_required_blobs(self) -> None:
        self.service.pull_and_ack_payload = {
            "pull": {
                "reconcile": {
                    "applied": {
                        "required_blob_ids": ["blob-a"],
                    }
                }
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "pull",
                "--rewritten-at",
                "1770000040100",
                "--download-required-blobs",
                "--output-dir",
                tmpdir,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                payload,
                {
                    "pull": {
                        "pull": {
                            "reconcile": {
                                "applied": {
                                    "required_blob_ids": ["blob-a"],
                                }
                            }
                        }
                    },
                    "download": {
                        "init": {
                            "request": {"blob_ids": ["blob-a"]},
                            "response": {
                                "downloads": [
                                    {
                                        "blob_id": "blob-a",
                                        "download_url": "https://blob.example.com/download/blob-a",
                                        "encrypted_size": 3,
                                        "expires_at": "2026-05-08T12:00:00Z",
                                        "headers": None,
                                    }
                                ]
                            },
                        },
                        "written_blob_paths": {
                            "blob-a": str(Path(tmpdir) / "blob-a.blob"),
                        },
                    },
                },
            )
            self.assertEqual((Path(tmpdir) / "blob-a.blob").read_bytes(), b"xyz")

    def test_pull_command_can_download_and_decrypt_required_blobs(self) -> None:
        self.service.pull_and_ack_payload = {
            "pull": {
                "reconcile": {
                    "applied": {
                        "required_blob_ids": ["blob-a"],
                    }
                }
            }
        }
        self.service.download_and_decrypt_pull_payload = {
            "pull": self.service.pull_and_ack_payload,
            "plan": {
                "vault_id": "vault-001",
                "revision": 8,
                "blob_ids": ["blob-a"],
                "files": [
                    {
                        "file_id": "file-a",
                        "path": "Notes/A.md",
                        "type": "note",
                        "blob_id": "blob-a",
                        "content_hash": "sha256:abc",
                    }
                ],
            },
            "download": {
                "init": {
                    "request": {"blob_ids": ["blob-a"]},
                    "response": {
                        "downloads": [
                            {
                                "blob_id": "blob-a",
                                "download_url": "https://blob.example.com/download/blob-a",
                                "encrypted_size": 3,
                                "expires_at": "2026-05-08T12:00:00Z",
                                "headers": None,
                            }
                        ]
                    },
                },
                "downloaded_blobs_base64": {
                    "blob-a": base64.b64encode(b"xyz").decode("ascii"),
                },
            },
            "plaintext_by_file_id_base64": {
                "file-a": base64.b64encode(b"# A\n").decode("ascii"),
            },
        }

        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "pull",
            "--rewritten-at",
            "1770000040100",
            "--download-required-blobs",
            "--decrypt-required-blobs",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, self.service.download_and_decrypt_pull_payload)
        self.assertEqual([call[0] for call in self.service.calls], ["pull", "download-and-decrypt-pull-required-blobs"])

    def test_recover_command_routes_normalized_at(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "recover",
            "--normalized-at",
            "1770000040200",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, {"kind": "recover", "normalized_at": 1770000040200})
        self.assertEqual(self.service.calls, [("recover", 1770000040200)])

    def test_sync_once_command_routes_runner_sequence(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-once",
            "--now-ms",
            "1770000040250",
            "--normalized-at",
            "1770000040260",
            "--rewritten-at",
            "1770000040270",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "final_snapshot": {"files": 1, "kind": "status"},
                "initialized": {"kind": "init", "value": 1770000040250},
                "pull": {"kind": "pull", "rewritten_at": 1770000040270},
                "recovery": {"kind": "recover", "normalized_at": 1770000040260},
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040250),
                ("recover", 1770000040260),
                ("pull", 1770000040270),
                ("status", None),
            ],
        )

    def test_sync_once_command_can_auto_generate_time_arguments(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-once",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040000),
                ("recover", 1770000040010),
                ("pull", 1770000040020),
                ("status", None),
            ],
        )
        self.assertEqual(payload["pull"]["rewritten_at"], 1770000040020)

    def test_sync_loop_command_routes_scheduler_sequence(self) -> None:
        slept: list[float] = []
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-loop",
            "--iterations",
            "3",
            "--now-ms",
            "1770000040400",
            "--normalized-at",
            "1770000040410",
            "--rewritten-at",
            "1770000040420",
            "--step-ms",
            "50",
            "--interval-seconds",
            "1.25",
            sleep=slept.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "config": {
                    "continue_on_error": False,
                    "init_now_ms": 1770000040400,
                    "interval_seconds": 1.25,
                    "iterations": 3,
                    "pull_rewritten_at": 1770000040420,
                    "recovery_normalized_at": 1770000040410,
                    "step_ms": 50,
                },
                "iterations": [
                    {
                        "init_now_ms": 1770000040400,
                        "iteration": 0,
                        "pull_rewritten_at": 1770000040420,
                        "recovery_normalized_at": 1770000040410,
                        "failure": None,
                        "run_once": {
                            "final_snapshot": {"files": 1, "kind": "status"},
                            "initialized": {"kind": "init", "value": 1770000040400},
                            "pull": {"kind": "pull", "rewritten_at": 1770000040420},
                            "recovery": {"kind": "recover", "normalized_at": 1770000040410},
                        },
                    },
                    {
                        "init_now_ms": 1770000040450,
                        "iteration": 1,
                        "pull_rewritten_at": 1770000040470,
                        "recovery_normalized_at": 1770000040460,
                        "failure": None,
                        "run_once": {
                            "final_snapshot": {"files": 1, "kind": "status"},
                            "initialized": {"kind": "init", "value": 1770000040450},
                            "pull": {"kind": "pull", "rewritten_at": 1770000040470},
                            "recovery": {"kind": "recover", "normalized_at": 1770000040460},
                        },
                    },
                    {
                        "init_now_ms": 1770000040500,
                        "iteration": 2,
                        "pull_rewritten_at": 1770000040520,
                        "recovery_normalized_at": 1770000040510,
                        "failure": None,
                        "run_once": {
                            "final_snapshot": {"files": 1, "kind": "status"},
                            "initialized": {"kind": "init", "value": 1770000040500},
                            "pull": {"kind": "pull", "rewritten_at": 1770000040520},
                            "recovery": {"kind": "recover", "normalized_at": 1770000040510},
                        },
                    },
                ],
                "failure_count": 0,
                "stopped_early": False,
                "success_count": 3,
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040400),
                ("recover", 1770000040410),
                ("pull", 1770000040420),
                ("status", None),
                ("init", 1770000040450),
                ("recover", 1770000040460),
                ("pull", 1770000040470),
                ("status", None),
                ("init", 1770000040500),
                ("recover", 1770000040510),
                ("pull", 1770000040520),
                ("status", None),
            ],
        )
        self.assertEqual(slept, [1.25, 1.25])

    def test_sync_loop_command_can_auto_generate_time_arguments(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-loop",
            "--iterations",
            "2",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["config"]["init_now_ms"], 1770000040000)
        self.assertEqual(payload["config"]["recovery_normalized_at"], 1770000040010)
        self.assertEqual(payload["config"]["pull_rewritten_at"], 1770000040020)
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040000),
                ("recover", 1770000040010),
                ("pull", 1770000040020),
                ("status", None),
                ("init", 1770000040000),
                ("recover", 1770000040010),
                ("pull", 1770000040020),
                ("status", None),
            ],
        )

    def test_sync_cycle_command_routes_optional_workspace_submit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            encrypted_dir = Path(tmpdir) / "encrypted"
            encrypted_dir.mkdir(parents=True, exist_ok=True)
            (encrypted_dir / "file-a.blob").write_bytes(b"enc-a")

            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "sync-cycle",
                "--now-ms",
                "1770000040430",
                "--normalized-at",
                "1770000040440",
                "--submit-created-at",
                "1770000040450",
                "--file-id",
                "file-a",
                "--commit-intent-id",
                "intent-010",
                "--cleanup-normalized-at",
                "1770000040451",
                "--encrypted-dir",
                str(encrypted_dir),
                "--rewritten-at",
                "1770000040460",
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "final_snapshot": {"files": 1, "kind": "status"},
                "initialized": {"kind": "init", "value": 1770000040430},
                "pull": {"kind": "pull", "rewritten_at": 1770000040460},
                "recovery": {"kind": "recover", "normalized_at": 1770000040440},
                "submitted": {
                    "kind": "submit-workspace-commit",
                    "created_at": 1770000040450,
                    "file_ids": ["file-a"],
                    "commit_intent_id": "intent-010",
                    "encrypted_sizes": {"file-a": 5},
                },
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040430),
                ("recover", 1770000040440),
                (
                    "submit-workspace-commit",
                    1770000040450,
                    ["file-a"],
                    "intent-010",
                    1770000040451,
                    {"file-a": b"enc-a"},
                ),
                ("pull", 1770000040460),
                ("status", None),
            ],
        )

    def test_sync_cycle_command_can_submit_without_explicit_encrypted_payloads(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-cycle",
            "--now-ms",
            "1770000040530",
            "--normalized-at",
            "1770000040540",
            "--submit-created-at",
            "1770000040550",
            "--file-id",
            "file-a",
            "--rewritten-at",
            "1770000040560",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["submitted"]["encrypted_sizes"], None)
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040530),
                ("recover", 1770000040540),
                ("submit-workspace-commit", 1770000040550, ["file-a"], None, 1770000040551, None),
                ("pull", 1770000040560),
                ("status", None),
            ],
        )

    def test_sync_cycle_command_can_auto_generate_time_arguments(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-cycle",
            "--file-id",
            "file-a",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040000),
                ("recover", 1770000040010),
                ("submit-workspace-commit", 1770000040020, ["file-a"], None, 1770000040021, None),
                ("pull", 1770000040030),
                ("status", None),
            ],
        )
        self.assertEqual(payload["submitted"]["created_at"], 1770000040020)

    def test_sync_cycle_loop_command_routes_scheduler_over_full_cycle(self) -> None:
        slept: list[float] = []
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-cycle-loop",
            "--iterations",
            "2",
            "--now-ms",
            "1770000040600",
            "--normalized-at",
            "1770000040610",
            "--submit-created-at",
            "1770000040620",
            "--file-id",
            "file-a",
            "--cleanup-normalized-at",
            "1770000040621",
            "--rewritten-at",
            "1770000040630",
            "--step-ms",
            "100",
            "--interval-seconds",
            "0.5",
            sleep=slept.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "config": {
                    "cleanup_normalized_at": 1770000040621,
                    "commit_intent_id": None,
                    "continue_on_error": False,
                    "encrypted_blob_by_file_id": None,
                    "init_now_ms": 1770000040600,
                    "interval_seconds": 0.5,
                    "iterations": 2,
                    "pull_rewritten_at": 1770000040630,
                    "recovery_normalized_at": 1770000040610,
                    "step_ms": 100,
                    "submit_created_at": 1770000040620,
                    "submit_detected": False,
                    "submit_file_ids": ["file-a"],
                },
                "iterations": [
                    {
                        "cleanup_normalized_at": 1770000040621,
                        "init_now_ms": 1770000040600,
                        "iteration": 0,
                        "pull_rewritten_at": 1770000040630,
                        "recovery_normalized_at": 1770000040610,
                        "failure": None,
                        "run_cycle": {
                            "final_snapshot": {"files": 1, "kind": "status"},
                            "initialized": {"kind": "init", "value": 1770000040600},
                            "pull": {"kind": "pull", "rewritten_at": 1770000040630},
                            "recovery": {"kind": "recover", "normalized_at": 1770000040610},
                            "submitted": {
                                "kind": "submit-workspace-commit",
                                "created_at": 1770000040620,
                                "commit_intent_id": None,
                                "encrypted_sizes": None,
                                "file_ids": ["file-a"],
                            },
                        },
                        "submit_created_at": 1770000040620,
                    },
                    {
                        "cleanup_normalized_at": 1770000040721,
                        "init_now_ms": 1770000040700,
                        "iteration": 1,
                        "pull_rewritten_at": 1770000040730,
                        "recovery_normalized_at": 1770000040710,
                        "failure": None,
                        "run_cycle": {
                            "final_snapshot": {"files": 1, "kind": "status"},
                            "initialized": {"kind": "init", "value": 1770000040700},
                            "pull": {"kind": "pull", "rewritten_at": 1770000040730},
                            "recovery": {"kind": "recover", "normalized_at": 1770000040710},
                            "submitted": {
                                "kind": "submit-workspace-commit",
                                "created_at": 1770000040720,
                                "commit_intent_id": None,
                                "encrypted_sizes": None,
                                "file_ids": ["file-a"],
                            },
                        },
                        "submit_created_at": 1770000040720,
                    },
                ],
                "failure_count": 0,
                "stopped_early": False,
                "success_count": 2,
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040600),
                ("recover", 1770000040610),
                ("submit-workspace-commit", 1770000040620, ["file-a"], None, 1770000040621, None),
                ("pull", 1770000040630),
                ("status", None),
                ("init", 1770000040700),
                ("recover", 1770000040710),
                ("submit-workspace-commit", 1770000040720, ["file-a"], None, 1770000040721, None),
                ("pull", 1770000040730),
                ("status", None),
            ],
        )
        self.assertEqual(slept, [0.5])

    def test_sync_cycle_command_can_submit_detected_changes(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-cycle",
            "--now-ms",
            "1770000040640",
            "--normalized-at",
            "1770000040650",
            "--submit-created-at",
            "1770000040660",
            "--submit-detected",
            "--commit-intent-id",
            "intent-detected-010",
            "--cleanup-normalized-at",
            "1770000040661",
            "--rewritten-at",
            "1770000040670",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["submitted"]["kind"], "submit-detected-commit")
        self.assertEqual(payload["submitted"]["commit_intent_id"], "intent-detected-010")
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040640),
                ("recover", 1770000040650),
                ("submit-detected-commit", 1770000040660, "intent-detected-010", 1770000040661),
                ("pull", 1770000040670),
                ("status", None),
            ],
        )

    def test_sync_cycle_loop_command_can_submit_detected_changes(self) -> None:
        slept: list[float] = []
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-cycle-loop",
            "--iterations",
            "2",
            "--submit-detected",
            "--interval-seconds",
            "0.25",
            sleep=slept.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["config"]["submit_detected"])
        self.assertEqual(payload["iterations"][0]["run_cycle"]["submitted"]["kind"], "submit-detected-commit")
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040000),
                ("recover", 1770000040010),
                ("submit-detected-commit", 1770000040020, None, 1770000040021),
                ("pull", 1770000040030),
                ("status", None),
                ("init", 1770000040000),
                ("recover", 1770000040010),
                ("submit-detected-commit", 1770000040020, None, 1770000040021),
                ("pull", 1770000040030),
                ("status", None),
            ],
        )
        self.assertEqual(slept, [0.25])

    def test_sync_worker_command_can_submit_detected_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            exit_code, payload = self._run(
                "--vault-root",
                tmpdir,
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "sync-worker",
                "--iterations",
                "1",
                "--submit-detected",
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["config"]["submit_detected"])
            self.assertEqual(payload["loop"]["iterations"][0]["run_cycle"]["submitted"]["kind"], "submit-detected-commit")
            self.assertEqual(
                self.service.calls,
                [
                    ("init", 1770000040000),
                    ("recover", 1770000040010),
                    ("submit-detected-commit", 1770000040020, None, 1770000040021),
                    ("pull", 1770000040030),
                    ("status", None),
                ],
            )

    def test_sync_cycle_loop_command_can_auto_generate_time_arguments(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-cycle-loop",
            "--iterations",
            "2",
            "--file-id",
            "file-a",
            "--step-ms",
            "50",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["config"]["init_now_ms"], 1770000040000)
        self.assertEqual(payload["config"]["recovery_normalized_at"], 1770000040010)
        self.assertEqual(payload["config"]["submit_created_at"], 1770000040020)
        self.assertEqual(payload["config"]["cleanup_normalized_at"], 1770000040021)
        self.assertEqual(payload["config"]["pull_rewritten_at"], 1770000040030)
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040000),
                ("recover", 1770000040010),
                ("submit-workspace-commit", 1770000040020, ["file-a"], None, 1770000040021, None),
                ("pull", 1770000040030),
                ("status", None),
                ("init", 1770000040050),
                ("recover", 1770000040060),
                ("submit-workspace-commit", 1770000040070, ["file-a"], None, 1770000040071, None),
                ("pull", 1770000040080),
                ("status", None),
            ],
        )

    def test_sync_worker_command_wraps_cycle_loop_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            slept: list[float] = []
            exit_code, payload = self._run(
                "--vault-root",
                tmpdir,
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "sync-worker",
                "--iterations",
                "2",
                "--file-id",
                "file-a",
                "--interval-seconds",
                "2.5",
                sleep=slept.append,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["started_at_ms"], 1770000040000)
            self.assertEqual(payload["effective_step_ms"], 2500)
            self.assertTrue(payload["config"]["continue_on_error"])
            self.assertEqual(payload["time_plan"]["submit_created_at"], 1770000040020)
            self.assertEqual(
                payload["state_path"],
                str(Path(tmpdir) / ".noteapp" / "sync-worker-state.json"),
            )
            self.assertEqual(
                self.service.calls,
                [
                    ("init", 1770000040000),
                    ("recover", 1770000040010),
                    ("submit-workspace-commit", 1770000040020, ["file-a"], None, 1770000040021, None),
                    ("pull", 1770000040030),
                    ("status", None),
                    ("init", 1770000042500),
                    ("recover", 1770000042510),
                    ("submit-workspace-commit", 1770000042520, ["file-a"], None, 1770000042521, None),
                    ("pull", 1770000042530),
                    ("status", None),
                ],
            )
            self.assertEqual(slept, [2.5])

    def test_sync_worker_command_writes_worker_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            exit_code, payload = self._run(
                "--vault-root",
                tmpdir,
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "sync-worker",
                "--iterations",
                "1",
                "--file-id",
                "file-a",
            )

            self.assertEqual(exit_code, 0)
            state_path = Path(tmpdir) / ".noteapp" / "sync-worker-state.json"
            written = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(written["started_at_ms"], 1770000040000)
            self.assertEqual(written["success_count"], 1)
            self.assertEqual(written["state_path"], str(state_path))
            self.assertEqual(payload["state_path"], str(state_path))

    def test_sync_worker_command_can_stop_on_error(self) -> None:
        self.service.fail_submit_workspace_at_calls = {0}
        with self.assertRaisesRegex(RuntimeError, "submit failed at call 0"):
            self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "sync-worker",
                "--iterations",
                "2",
                "--file-id",
                "file-a",
                "--stop-on-error",
            )

    def test_sync_loop_command_can_continue_on_error(self) -> None:
        self.service.fail_recover_at_calls = {1}
        slept: list[float] = []
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-loop",
            "--iterations",
            "3",
            "--normalized-at",
            "1770000040800",
            "--rewritten-at",
            "1770000040810",
            "--step-ms",
            "5",
            "--interval-seconds",
            "0.1",
            "--continue-on-error",
            sleep=slept.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["success_count"], 2)
        self.assertEqual(payload["failure_count"], 1)
        self.assertEqual(payload["iterations"][1]["run_once"], None)
        self.assertEqual(payload["iterations"][1]["failure"]["error_type"], "RuntimeError")
        self.assertEqual(slept, [0.1, 0.1])

    def test_sync_cycle_loop_command_can_continue_on_error(self) -> None:
        self.service.fail_submit_workspace_at_calls = {0}
        slept: list[float] = []
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-cycle-loop",
            "--iterations",
            "2",
            "--normalized-at",
            "1770000040900",
            "--submit-created-at",
            "1770000040910",
            "--file-id",
            "file-a",
            "--rewritten-at",
            "1770000040920",
            "--continue-on-error",
            "--interval-seconds",
            "0.2",
            sleep=slept.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["success_count"], 1)
        self.assertEqual(payload["failure_count"], 1)
        self.assertEqual(payload["iterations"][0]["run_cycle"], None)
        self.assertEqual(payload["iterations"][0]["failure"]["error_type"], "RuntimeError")
        self.assertIsNotNone(payload["iterations"][1]["run_cycle"])
        self.assertEqual(slept, [0.2])

    def test_download_blobs_command_routes_blob_ids_and_base64_encodes_payload(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "download-blobs",
            "--blob-id",
            "blob-a",
            "--blob-id",
            "blob-b",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "downloaded_blobs_base64": {
                    "blob-a": "eHl6",
                    "blob-b": "eHl6",
                },
                "init": {
                    "request": {"blob_ids": ["blob-a", "blob-b"]},
                    "response": {
                        "downloads": [
                            {
                                "blob_id": "blob-a",
                                "download_url": "https://blob.example.com/download/blob-a",
                                "encrypted_size": 3,
                                "expires_at": "2026-05-08T12:00:00Z",
                                "headers": None,
                            },
                            {
                                "blob_id": "blob-b",
                                "download_url": "https://blob.example.com/download/blob-b",
                                "encrypted_size": 3,
                                "expires_at": "2026-05-08T12:00:00Z",
                                "headers": None,
                            },
                        ]
                    },
                },
            },
        )
        self.assertEqual(self.service.calls, [("download-blobs", ["blob-a", "blob-b"])])

    def test_download_blobs_command_writes_blob_files_when_output_dir_is_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "downloaded"
            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "download-blobs",
                "--blob-id",
                "blob-a",
                "--blob-id",
                "blob-b",
                "--output-dir",
                str(output_dir),
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                payload,
                {
                    "init": {
                        "request": {"blob_ids": ["blob-a", "blob-b"]},
                        "response": {
                            "downloads": [
                                {
                                    "blob_id": "blob-a",
                                    "download_url": "https://blob.example.com/download/blob-a",
                                    "encrypted_size": 3,
                                    "expires_at": "2026-05-08T12:00:00Z",
                                    "headers": None,
                                },
                                {
                                    "blob_id": "blob-b",
                                    "download_url": "https://blob.example.com/download/blob-b",
                                    "encrypted_size": 3,
                                    "expires_at": "2026-05-08T12:00:00Z",
                                    "headers": None,
                                },
                            ]
                        },
                    },
                    "written_blob_paths": {
                        "blob-a": str(output_dir / "blob-a.blob"),
                        "blob-b": str(output_dir / "blob-b.blob"),
                    },
                },
            )
            self.assertEqual((output_dir / "blob-a.blob").read_bytes(), b"xyz")
            self.assertEqual((output_dir / "blob-b.blob").read_bytes(), b"xyz")

        self.assertEqual(self.service.calls, [("download-blobs", ["blob-a", "blob-b"])])

    def test_submit_commit_command_decodes_payload_files_and_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            content_map = Path(tmpdir) / "content.json"
            encrypted_map = Path(tmpdir) / "encrypted.json"
            content_map.write_text(
                json.dumps(
                    {
                        "file-a": base64.b64encode(b"hello").decode("ascii"),
                    }
                ),
                encoding="utf-8",
            )
            encrypted_map.write_text(
                json.dumps(
                    {
                        "file-a": base64.b64encode(b"encrypted-payload").decode("ascii"),
                    }
                ),
                encoding="utf-8",
            )

            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "submit-commit",
                "--created-at",
                "1770000040300",
                "--commit-intent-id",
                "intent-001",
                "--cleanup-normalized-at",
                "1770000040301",
                "--content-map",
                str(content_map),
                "--encrypted-map",
                str(encrypted_map),
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "kind": "submit-commit",
                "created_at": 1770000040300,
                "commit_intent_id": "intent-001",
                "content_sizes": {"file-a": 5},
                "encrypted_sizes": {"file-a": 17},
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                (
                    "submit-commit",
                    1770000040300,
                    "intent-001",
                    1770000040301,
                    {"file-a": b"hello"},
                    {"file-a": b"encrypted-payload"},
                )
            ],
        )

    def test_submit_workspace_commit_command_routes_selected_file_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            encrypted_map = Path(tmpdir) / "encrypted.json"
            encrypted_map.write_text(
                json.dumps(
                    {
                        "file-a": base64.b64encode(b"encrypted-a").decode("ascii"),
                        "file-b": base64.b64encode(b"encrypted-bb").decode("ascii"),
                    }
                ),
                encoding="utf-8",
            )

            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "submit-workspace-commit",
                "--created-at",
                "1770000040500",
                "--commit-intent-id",
                "intent-002",
                "--cleanup-normalized-at",
                "1770000040501",
                "--file-id",
                "file-a",
                "--file-id",
                "file-b",
                "--encrypted-map",
                str(encrypted_map),
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "kind": "submit-workspace-commit",
                "created_at": 1770000040500,
                "file_ids": ["file-a", "file-b"],
                "commit_intent_id": "intent-002",
                "encrypted_sizes": {"file-a": 11, "file-b": 12},
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                (
                    "submit-workspace-commit",
                    1770000040500,
                    ["file-a", "file-b"],
                    "intent-002",
                    1770000040501,
                    {"file-a": b"encrypted-a", "file-b": b"encrypted-bb"},
                )
            ],
        )

    def test_submit_workspace_commit_command_loads_blob_dir_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            encrypted_dir = Path(tmpdir) / "encrypted"
            encrypted_dir.mkdir(parents=True, exist_ok=True)
            (encrypted_dir / "file-a.blob").write_bytes(b"enc-a")
            (encrypted_dir / "file-b.blob").write_bytes(b"enc-bb")

            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "submit-workspace-commit",
                "--created-at",
                "1770000040600",
                "--file-id",
                "file-a",
                "--file-id",
                "file-b",
                "--encrypted-dir",
                str(encrypted_dir),
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "kind": "submit-workspace-commit",
                "created_at": 1770000040600,
                "file_ids": ["file-a", "file-b"],
                "commit_intent_id": None,
                "encrypted_sizes": {"file-a": 5, "file-b": 6},
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                (
                    "submit-workspace-commit",
                    1770000040600,
                    ["file-a", "file-b"],
                    None,
                    None,
                    {"file-a": b"enc-a", "file-b": b"enc-bb"},
                )
            ],
        )

    def test_submit_workspace_commit_command_allows_auto_generated_encrypted_payloads(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "submit-workspace-commit",
            "--created-at",
            "1770000040700",
            "--file-id",
            "file-a",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "kind": "submit-workspace-commit",
                "created_at": 1770000040700,
                "file_ids": ["file-a"],
                "commit_intent_id": None,
                "encrypted_sizes": None,
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                (
                    "submit-workspace-commit",
                    1770000040700,
                    ["file-a"],
                    None,
                    None,
                    None,
                )
            ],
        )

    def test_submit_detected_commit_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "submit-detected-commit",
            "--created-at",
            "1770000040800",
            "--commit-intent-id",
            "intent-003",
            "--cleanup-normalized-at",
            "1770000040801",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "kind": "submit-detected-commit",
                "created_at": 1770000040800,
                "commit_intent_id": "intent-003",
                "cleanup_normalized_at": 1770000040801,
            },
        )
        self.assertEqual(
            self.service.calls,
            [("submit-detected-commit", 1770000040800, "intent-003", 1770000040801)],
        )

    def test_submit_detected_commit_command_reports_skipped_when_no_local_changes(self) -> None:
        self.service.skip_submit_detected_if_needed = True

        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "submit-detected-commit",
            "--created-at",
            "1770000040810",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, {"reason": "no_local_changes", "status": "skipped"})
        self.assertEqual(
            self.service.calls,
            [("submit-detected-commit", 1770000040810, None, None)],
        )


if __name__ == "__main__":
    unittest.main()
