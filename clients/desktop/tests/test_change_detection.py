from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clients.desktop.change_detection import (
    build_tracked_change_commit_plan,
    detect_local_workspace_changes,
)
from clients.desktop.crypto import build_placeholder_blob_id
from vault_core import FileMapDocument, FileRecord


class DesktopChangeDetectionTests(unittest.TestCase):
    def test_detect_local_workspace_changes_reports_modified_missing_and_untracked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Notes").mkdir(parents=True, exist_ok=True)
            (root / "Assets").mkdir(parents=True, exist_ok=True)
            (root / ".noteapp").mkdir(parents=True, exist_ok=True)

            modified_path = root / "Notes" / "Tracked.md"
            modified_path.write_text("# changed\n", encoding="utf-8")
            (root / "Assets" / "Loose.bin").write_bytes(b"loose")
            (root / ".noteapp" / "ignored.txt").write_text("ignore", encoding="utf-8")

            document = FileMapDocument(
                vault_id="vault-001",
                updated_at=1770000050000,
                files=[
                    FileRecord(
                        file_id="file-tracked",
                        path="Notes/Tracked.md",
                        type="note",
                        status="active",
                        updated_at=1770000049000,
                        content_hash="sha256:stale",
                    ),
                    FileRecord(
                        file_id="file-missing",
                        path="Notes/Missing.md",
                        type="note",
                        status="active",
                        updated_at=1770000049001,
                        content_hash="sha256:missing",
                    ),
                ],
            )

            result = detect_local_workspace_changes(root, document)

            self.assertEqual(result.vault_id, "vault-001")
            self.assertEqual(result.tracked_record_count, 2)
            self.assertEqual(result.change_count, 3)
            self.assertEqual(result.modified_file_ids, ["file-tracked"])
            self.assertEqual(result.missing_file_ids, ["file-missing"])
            self.assertEqual([item.kind for item in result.changes], ["untracked", "missing", "modified"])
            self.assertEqual(result.changes[0].path, "Assets/Loose.bin")
            self.assertEqual(result.changes[0].file_type, "attachment")
            self.assertEqual(result.changes[1].path, "Notes/Missing.md")
            self.assertEqual(result.changes[2].file_id, "file-tracked")

    def test_detect_local_workspace_changes_ignores_deleted_records_and_noteapp_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Notes").mkdir(parents=True, exist_ok=True)
            (root / ".noteapp").mkdir(parents=True, exist_ok=True)
            (root / "Notes" / "Tracked.md").write_bytes(b"# same\n")
            (root / ".noteapp" / "state.sqlite3").write_bytes(b"sqlite")

            document = FileMapDocument(
                vault_id="vault-001",
                updated_at=1770000050100,
                files=[
                    FileRecord(
                        file_id="file-tracked",
                        path="Notes/Tracked.md",
                        type="note",
                        status="active",
                        updated_at=1770000050000,
                        content_hash="sha256:295fe4d30a587f0b17c69bf58cd848104fff29d57a8d275d491139dcb4d39bf6",
                    ),
                    FileRecord(
                        file_id="file-deleted",
                        path="Notes/Deleted.md",
                        type="note",
                        status="deleted",
                        updated_at=1770000050001,
                        content_hash="sha256:deleted",
                    ),
                ],
            )

            result = detect_local_workspace_changes(root, document)

            self.assertEqual(result.change_count, 0)
            self.assertEqual(result.modified_file_ids, [])
            self.assertEqual(result.missing_file_ids, [])

    def test_build_tracked_change_commit_plan_projects_updated_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Notes").mkdir(parents=True, exist_ok=True)
            payload = b"# changed\n"
            (root / "Notes" / "Tracked.md").write_bytes(payload)

            document = FileMapDocument(
                vault_id="vault-001",
                updated_at=1770000050200,
                files=[
                    FileRecord(
                        file_id="file-tracked",
                        path="Notes/Tracked.md",
                        type="note",
                        status="active",
                        updated_at=1770000050000,
                        content_hash="sha256:stale",
                        meta={
                            "blob_id": "blob-old",
                            "size": 1,
                            "mtime": 1770000050000,
                            "mime_type": "text/markdown",
                        },
                    )
                ],
            )

            plan = build_tracked_change_commit_plan(root, document)

            self.assertEqual(plan.change_set.modified_file_ids, ["file-tracked"])
            self.assertEqual(plan.content_by_file_id, {"file-tracked": payload})
            self.assertEqual(plan.document.files[0].meta["mime_type"], "text/markdown")
            self.assertEqual(
                plan.document.files[0].meta["blob_id"],
                build_placeholder_blob_id(plan.document.files[0].content_hash),
            )

    def test_build_tracked_change_commit_plan_rejects_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Notes").mkdir(parents=True, exist_ok=True)
            (root / "Notes" / "Tracked.md").write_bytes(b"# tracked\n")
            (root / "Notes" / "Loose.md").write_bytes(b"# loose\n")

            document = FileMapDocument(
                vault_id="vault-001",
                updated_at=1770000050300,
                files=[
                    FileRecord(
                        file_id="file-tracked",
                        path="Notes/Tracked.md",
                        type="note",
                        status="active",
                        updated_at=1770000050000,
                        content_hash="sha256:stale",
                    )
                ],
            )

            with self.assertRaisesRegex(ValueError, "unsupported items"):
                build_tracked_change_commit_plan(root, document)

    def test_build_tracked_change_commit_plan_marks_missing_tracked_file_deleted(self) -> None:
        document = FileMapDocument(
            vault_id="vault-001",
            updated_at=1770000050400,
            files=[
                FileRecord(
                    file_id="file-missing",
                    path="Notes/Missing.md",
                    type="note",
                    status="active",
                    updated_at=1770000050000,
                    content_hash="sha256:stale",
                )
            ],
        )

        plan = build_tracked_change_commit_plan(
            Path("C:/vault"),
            document,
            current_local_delete_sequence=4,
            deleted_by_device="desktop-shanghai",
        )

        self.assertEqual(plan.change_set.missing_file_ids, ["file-missing"])
        self.assertEqual(plan.document.files[0].status, "deleted")
        self.assertEqual(plan.local_delete_sequence, 5)
        self.assertEqual(plan.tombstones[0].file_id, "file-missing")


if __name__ == "__main__":
    unittest.main()
