from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request

from clients.desktop import DesktopSyncHttpConfig, build_desktop_vault_workspace
from vault_core import (
    CommitIntentJournalRecord,
    VaultStateRecord,
    load_vault_state,
    open_database,
    upsert_commit_intent_journal,
    upsert_vault_state,
)


@dataclass
class FakeHttpResponse:
    status_code: int
    body: bytes

    def read(self) -> bytes:
        return self.body

    def getcode(self) -> int:
        return self.status_code

    def close(self) -> None:
        return None


class RoutingUrlopen:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object | None, float]] = []

    def __call__(self, request: Request, timeout: float) -> FakeHttpResponse:
        body = None if request.data is None else json.loads(request.data.decode("utf-8"))
        self.calls.append((request.get_method(), request.full_url, body, timeout))

        if request.get_method() == "GET" and request.full_url.endswith("/vaults/vault-001/head"):
            return self._json_response(
                {
                    "vault_id": "vault-001",
                    "head_revision": 8,
                    "manifest_summary": "sha256:head8",
                }
            )
        if request.get_method() == "GET" and request.full_url.endswith("/vaults/vault-001/manifests/8"):
            return self._json_response(
                {
                    "vault_id": "vault-001",
                    "revision": 8,
                    "base_revision": 0,
                    "created_by_device": "desktop-remote",
                    "created_at": 1770000025000,
                    "summary_hash": "sha256:head8",
                    "files": [],
                    "tombstones": [],
                }
            )
        if request.get_method() == "POST" and request.full_url.endswith("/vaults/vault-001/ack"):
            return self._json_response({"max_acked_revision": 8})
        if request.get_method() == "POST" and request.full_url.endswith("/vaults/vault-001/commits/resolve-intent"):
            return self._json_response(
                {
                    "status": "found",
                    "matched_revision": 8,
                    "observed_head_revision": 8,
                    "head_manifest_summary": "sha256:head8",
                }
            )
        raise AssertionError(f"unexpected request: {request.get_method()} {request.full_url}")

    def _json_response(self, payload: dict[str, object]) -> FakeHttpResponse:
        return FakeHttpResponse(
            status_code=200,
            body=json.dumps(payload).encode("utf-8"),
        )


class DesktopVaultWorkspaceTests(unittest.TestCase):
    def test_ensure_initialized_bootstraps_noteapp_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = build_desktop_vault_workspace(
                DesktopSyncHttpConfig(
                    base_url="https://sync.example.com",
                    vault_id="vault-001",
                    device_id="desktop-shanghai",
                ),
                root,
            )

            snapshot = workspace.ensure_initialized(now_ms=1770000022000)

            self.assertEqual(workspace.paths.filemap_path, root / ".noteapp" / "filemap.json")
            self.assertEqual(workspace.paths.ledger_path, root / ".noteapp" / "tombstone-ledger.jsonl")
            self.assertEqual(workspace.paths.db_path, root / ".noteapp" / "state.sqlite3")
            self.assertEqual(workspace.paths.worker_state_path, root / ".noteapp" / "sync-worker-state.json")
            self.assertTrue(workspace.paths.filemap_path.exists())
            self.assertTrue(workspace.paths.ledger_path.exists())
            self.assertTrue(workspace.paths.db_path.exists())
            self.assertEqual(snapshot.document.vault_id, "vault-001")
            self.assertEqual(snapshot.state.vault_id, "vault-001")
            self.assertEqual(snapshot.state.meta, {"device_id": "desktop-shanghai"})
            self.assertEqual(snapshot.tombstones, [])

    def test_pull_and_ack_uses_workspace_paths_and_persists_reconciled_state(self) -> None:
        opener = RoutingUrlopen()

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = build_desktop_vault_workspace(
                DesktopSyncHttpConfig(
                    base_url="https://sync.example.com",
                    vault_id="vault-001",
                    device_id="desktop-shanghai",
                ),
                root,
                api_opener=opener,
            )
            workspace.ensure_initialized(now_ms=1770000022000)

            result = workspace.pull_and_ack(rewritten_at=1770000023000)
            reloaded = workspace.load_snapshot()

            self.assertEqual(result.pull.reconcile.state.last_applied_revision, 8)
            self.assertIsNotNone(result.ack)
            self.assertEqual(result.ack.response.max_acked_revision, 8)
            self.assertEqual(reloaded.state.last_applied_revision, 8)
            self.assertEqual(
                [call[0:2] for call in opener.calls],
                [
                    ("GET", "https://sync.example.com/vaults/vault-001/head"),
                    ("GET", "https://sync.example.com/vaults/vault-001/manifests/8"),
                    ("POST", "https://sync.example.com/vaults/vault-001/ack"),
                ],
            )

    def test_resume_commit_recovery_runs_remote_confirmation_against_workspace_state(self) -> None:
        opener = RoutingUrlopen()

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = build_desktop_vault_workspace(
                DesktopSyncHttpConfig(
                    base_url="https://sync.example.com",
                    vault_id="vault-001",
                    device_id="desktop-shanghai",
                ),
                root,
                api_opener=opener,
            )
            workspace.ensure_initialized(now_ms=1770000022000)

            with closing(open_database(workspace.paths.db_path)) as connection:
                current = load_vault_state(connection, "vault-001")
                self.assertIsNotNone(current)
                upsert_vault_state(
                    connection,
                    VaultStateRecord(
                        vault_id="vault-001",
                        last_applied_revision=7,
                        remote_head_revision=7,
                        acked_revision=7,
                        pending_ack_to_server=[],
                        commit_in_progress=True,
                        last_manifest_summary="sha256:head7",
                        last_manifest_summary_status="valid",
                        local_delete_sequence=0,
                        meta=current.meta,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault-001",
                        commit_intent_id="intent-001",
                        intent_manifest_hash="sha256:intent-001",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=None,
                        created_at=1770000022500,
                        updated_at=1770000022500,
                    ),
                )

            result = workspace.resume_commit_recovery(normalized_at=1770000024000)
            reloaded = workspace.load_snapshot()

            self.assertEqual(result.mode, "submitted_confirmation")
            self.assertIsNotNone(result.submitted)
            self.assertEqual(result.submitted.recovery.state.last_applied_revision, 8)
            self.assertEqual(reloaded.state.last_applied_revision, 8)
            self.assertEqual(
                [call[0:2] for call in opener.calls],
                [
                    ("POST", "https://sync.example.com/vaults/vault-001/commits/resolve-intent"),
                    ("GET", "https://sync.example.com/vaults/vault-001/manifests/8"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
