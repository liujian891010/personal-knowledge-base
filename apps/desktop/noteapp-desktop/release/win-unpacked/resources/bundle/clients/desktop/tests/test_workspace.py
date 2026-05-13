from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request

from clients.desktop import DesktopSyncHttpConfig, build_desktop_vault_workspace
from clients.desktop.worker import write_desktop_sync_worker_state
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
    def __init__(
        self,
        *,
        head_revision: int = 8,
        manifest_payload: object | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, object | None, float]] = []
        self.head_revision = head_revision
        self.manifest_payload = (
            {
                "vault_id": "vault-001",
                "revision": head_revision,
                "base_revision": max(head_revision - 1, 0),
                "created_by_device": "desktop-remote",
                "created_at": 1770000025000,
                "summary_hash": f"sha256:head{head_revision}",
                "files": [],
                "tombstones": [],
            }
            if manifest_payload is None
            else manifest_payload
        )

    def __call__(self, request: Request, timeout: float) -> FakeHttpResponse:
        body = None if request.data is None else json.loads(request.data.decode("utf-8"))
        self.calls.append((request.get_method(), request.full_url, body, timeout))

        if request.get_method() == "GET" and request.full_url.endswith("/vaults/vault-001/head"):
            return self._json_response(
                {
                    "vault_id": "vault-001",
                    "head_revision": self.head_revision,
                    "manifest_summary": f"sha256:head{self.head_revision}",
                }
            )
        if request.get_method() == "GET" and request.full_url.endswith(
            f"/vaults/vault-001/manifests/{self.head_revision}"
        ):
            return self._json_response(self.manifest_payload)
        if request.get_method() == "POST" and request.full_url.endswith("/vaults/vault-001/ack"):
            return self._json_response({"max_acked_revision": self.head_revision})
        if request.get_method() == "POST" and request.full_url.endswith("/vaults/vault-001/commits/resolve-intent"):
            return self._json_response(
                {
                    "status": "found",
                    "matched_revision": self.head_revision,
                    "observed_head_revision": self.head_revision,
                    "head_manifest_summary": f"sha256:head{self.head_revision}",
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

    def test_pull_and_ack_reports_required_blob_ids_for_changed_remote_files(self) -> None:
        opener = RoutingUrlopen(
            manifest_payload={
                "vault_id": "vault-001",
                "revision": 8,
                "base_revision": 7,
                "created_by_device": "desktop-remote",
                "created_at": 1770000025000,
                "summary_hash": "sha256:head8",
                "files": [
                    {
                        "file_id": "file-live",
                        "path": "Notes/Live.md",
                        "type": "note",
                        "content_hash": "sha256:new",
                        "blob_id": "blob-live-new",
                        "size": 12,
                        "mtime": 1770000024900,
                    }
                ],
                "tombstones": [],
            }
        )

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

            from vault_core import FileMapDocument, FileRecord, write_filemap_atomic

            write_filemap_atomic(
                workspace.paths.filemap_path,
                FileMapDocument(
                    vault_id="vault-001",
                    updated_at=1770000022100,
                    files=[
                        FileRecord(
                            file_id="file-live",
                            path="Notes/Live.md",
                            type="note",
                            status="active",
                            updated_at=1770000022090,
                            content_hash="sha256:old",
                            last_known_revision=7,
                        )
                    ],
                ),
            )

            result = workspace.pull_and_ack(rewritten_at=1770000023000)

            self.assertEqual(result.pull.reconcile.applied.required_blob_ids, ["blob-live-new"])

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

    def test_load_worker_state_and_health_reads_latest_worker_summary(self) -> None:
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
            workspace.ensure_initialized(now_ms=1770000022000)
            write_desktop_sync_worker_state(
                workspace.paths.worker_state_path,
                type(
                    "FakeResult",
                    (),
                    {
                        "started_at_ms": 1770000023000,
                        "finished_at_ms": 1770000023001,
                        "effective_step_ms": 0,
                        "state_path": workspace.paths.worker_state_path,
                        "config": type(
                            "FakeConfig",
                            (),
                            {
                                "iterations": 1,
                                "interval_seconds": 30.0,
                                "step_ms": None,
                                "continue_on_error": True,
                                "init_now_ms": None,
                                "recovery_normalized_at": None,
                                "submit_created_at": None,
                                "submit_file_ids": None,
                                "commit_intent_id": None,
                                "cleanup_normalized_at": None,
                                "pull_rewritten_at": None,
                                "encrypted_blob_by_file_id": None,
                            },
                        )(),
                        "time_plan": type(
                            "FakePlan",
                            (),
                            {
                                "base_now_ms": 1770000023000,
                                "init_now_ms": 1770000023000,
                                "recovery_normalized_at": 1770000023010,
                                "submit_created_at": None,
                                "cleanup_normalized_at": None,
                                "pull_rewritten_at": 1770000023020,
                            },
                        )(),
                        "loop": type(
                            "FakeLoop",
                            (),
                            {
                                "success_count": 1,
                                "failure_count": 0,
                                "stopped_early": False,
                                "iterations": [],
                            },
                        )(),
                    },
                )(),
            )

            state = workspace.load_worker_state()
            health = workspace.load_worker_health()

            self.assertEqual(state.started_at_ms, 1770000023000)
            self.assertEqual(health.status, "healthy")
            self.assertEqual(health.success_count, 1)

    def test_detect_local_changes_scans_workspace_files(self) -> None:
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
            workspace.ensure_initialized(now_ms=1770000022000)
            note_path = root / "Notes" / "Live.md"
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text("# local\n", encoding="utf-8")

            from vault_core import FileMapDocument, FileRecord, write_filemap_atomic

            write_filemap_atomic(
                workspace.paths.filemap_path,
                FileMapDocument(
                    vault_id="vault-001",
                    updated_at=1770000022100,
                    files=[
                        FileRecord(
                            file_id="file-live",
                            path="Notes/Live.md",
                            type="note",
                            status="active",
                            updated_at=1770000022090,
                            content_hash="sha256:stale",
                        )
                    ],
                ),
            )

            changes = workspace.detect_local_changes()

            self.assertEqual(changes.modified_file_ids, ["file-live"])
            self.assertEqual(changes.missing_file_ids, [])


if __name__ == "__main__":
    unittest.main()
