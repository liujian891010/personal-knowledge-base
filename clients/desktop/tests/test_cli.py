from __future__ import annotations

import io
import json
import unittest
from dataclasses import dataclass
from pathlib import Path

from clients.desktop.cli import run_cli


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


class DesktopCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.created = []
        self.service = FakeService()

    def _builder(self, config, vault_root: Path):
        self.created.append((config, vault_root))
        return self.service

    def _run(self, *argv: str):
        stdout = io.StringIO()
        exit_code = run_cli(argv, stdout=stdout, service_builder=self._builder)
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


if __name__ == "__main__":
    unittest.main()
