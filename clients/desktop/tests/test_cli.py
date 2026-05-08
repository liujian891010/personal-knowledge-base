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

    def ensure_initialized(self, *, now_ms=None):
        self.calls.append(("init", now_ms))
        return FakeResult(kind="init", value=now_ms)

    def load_snapshot(self):
        self.calls.append(("status", None))
        return {"kind": "status", "files": 1}

    def pull_and_ack(self, *, rewritten_at: int):
        self.calls.append(("pull", rewritten_at))
        return {"kind": "pull", "rewritten_at": rewritten_at}

    def resume_commit_recovery(self, *, normalized_at: int):
        self.calls.append(("recover", normalized_at))
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


class DesktopCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.created = []
        self.service = FakeService()

    def _builder(self, config, vault_root: Path):
        self.created.append((config, vault_root))
        return self.service

    def _run(self, *argv: str, sleep=None):
        stdout = io.StringIO()
        exit_code = run_cli(
            argv,
            stdout=stdout,
            service_builder=self._builder,
            sleep=(lambda _: None) if sleep is None else sleep,
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
                        "run_once": {
                            "final_snapshot": {"files": 1, "kind": "status"},
                            "initialized": {"kind": "init", "value": 1770000040500},
                            "pull": {"kind": "pull", "rewritten_at": 1770000040520},
                            "recovery": {"kind": "recover", "normalized_at": 1770000040510},
                        },
                    },
                ],
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


if __name__ == "__main__":
    unittest.main()
