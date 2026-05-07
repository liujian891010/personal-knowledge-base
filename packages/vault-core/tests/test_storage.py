import json
import tempfile
import unittest
from pathlib import Path

from vault_core import (
    FileMapDocument,
    FileRecord,
    TombstoneRecord,
    append_tombstone,
    initialize_vault,
    load_filemap,
    load_tombstone_ledger,
    mark_deleted,
    recover_filemap,
    write_filemap_atomic,
)


class VaultCoreStorageTests(unittest.TestCase):
    def test_initialize_vault_creates_required_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "demo-vault"
            document = initialize_vault(root, vault_id="vault_pkb_001", now_ms=1770000000000)

            expected_dirs = [
                ".noteapp",
                ".noteapp/drafts",
                ".noteapp/staging",
                ".noteapp/staging-orphans",
                ".noteapp/conflict-orphans",
                ".ai/raw",
                ".ai/wiki",
                "Notes",
                "Attachments",
            ]
            for relative_dir in expected_dirs:
                self.assertTrue((root / relative_dir).is_dir(), relative_dir)

            vaultinfo = json.loads((root / ".vaultinfo").read_text(encoding="utf-8"))
            self.assertEqual(vaultinfo["vault_id"], "vault_pkb_001")
            self.assertEqual(document.vault_id, "vault_pkb_001")

            filemap = load_filemap(root / ".noteapp" / "filemap.json")
            self.assertEqual(filemap.vault_id, "vault_pkb_001")
            self.assertEqual(filemap.files, [])
            self.assertTrue((root / ".noteapp" / "tombstone-ledger.jsonl").exists())

    def test_write_filemap_round_trip_is_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            filemap_path = Path(tmpdir) / "filemap.json"
            document = FileMapDocument(
                vault_id="vault_pkb_001",
                updated_at=1770000001000,
                files=[
                    FileRecord(
                        file_id="file_b",
                        path="Notes/B.md",
                        type="note",
                        status="active",
                        updated_at=1770000000900,
                    ),
                    FileRecord(
                        file_id="file_a",
                        path="Notes/A.md",
                        type="note",
                        status="active",
                        updated_at=1770000000800,
                    ),
                ],
            )

            write_filemap_atomic(filemap_path, document)
            loaded = load_filemap(filemap_path)

            self.assertEqual([item.path for item in loaded.sorted_files()], ["Notes/A.md", "Notes/B.md"])
            self.assertFalse((Path(tmpdir) / "filemap.json.tmp").exists())

    def test_recover_filemap_promotes_valid_tmp_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            filemap_path = Path(tmpdir) / "filemap.json"
            tmp_path = Path(tmpdir) / "filemap.json.tmp"
            tmp_path.write_text(
                json.dumps(
                    {
                        "schema_version": "v1",
                        "vault_id": "vault_pkb_001",
                        "updated_at": 1770000002000,
                        "files": [],
                    }
                ),
                encoding="utf-8",
            )

            recovered = recover_filemap(filemap_path)

            self.assertTrue(recovered)
            self.assertTrue(filemap_path.exists())
            self.assertFalse(tmp_path.exists())
            self.assertEqual(load_filemap(filemap_path).vault_id, "vault_pkb_001")

    def test_conflict_copy_requires_source_file_id(self) -> None:
        with self.assertRaises(ValueError):
            FileRecord(
                file_id="file_conflict",
                path="Notes/A (conflict).md",
                type="note",
                status="conflict_copy",
                updated_at=1770000003000,
            )

    def test_append_and_load_tombstone_ledger_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "tombstone-ledger.jsonl"
            record = TombstoneRecord(
                file_id="file_note_architecture",
                last_known_path="Notes/Architecture/Sync Design.md",
                deleted_revision=7,
                deleted_at=1770000004000,
                local_delete_seq=18,
                deleted_by_device="desktop-shanghai",
            )

            append_tombstone(ledger_path, record)
            loaded = load_tombstone_ledger(ledger_path)

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].local_delete_seq, 18)
            self.assertEqual(loaded[0].deleted_by_device, "desktop-shanghai")

    def test_legacy_tombstone_ledger_backfills_missing_local_delete_seq(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "tombstone-ledger.jsonl"
            ledger_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "file_id": "file_a",
                                "last_known_path": "Notes/A.md",
                                "deleted_revision": 4,
                                "deleted_at": 1770000005000,
                            }
                        ),
                        json.dumps(
                            {
                                "file_id": "file_b",
                                "last_known_path": "Notes/B.md",
                                "deleted_revision": 5,
                                "deleted_at": 1770000006000,
                                "local_delete_seq": 8,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            loaded = load_tombstone_ledger(ledger_path)

            self.assertEqual([item.local_delete_seq for item in loaded], [1, 8])
            repaired = ledger_path.read_text(encoding="utf-8")
            self.assertIn('"local_delete_seq": 1', repaired)

    def test_mark_deleted_updates_filemap_and_returns_tombstone(self) -> None:
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000007000,
            files=[
                FileRecord(
                    file_id="file_note_architecture",
                    path="Notes/Architecture/Sync Design.md",
                    type="note",
                    status="active",
                    updated_at=1770000006900,
                    last_known_revision=6,
                )
            ],
        )

        updated_document, tombstone = mark_deleted(
            document,
            file_id="file_note_architecture",
            deleted_at=1770000008000,
            local_delete_seq=19,
            deleted_revision=7,
            deleted_by_device="desktop-shanghai",
        )

        self.assertEqual(updated_document.updated_at, 1770000008000)
        self.assertEqual(updated_document.files[0].status, "deleted")
        self.assertEqual(updated_document.files[0].last_known_revision, 7)
        self.assertEqual(tombstone.local_delete_seq, 19)
        self.assertEqual(tombstone.last_known_path, "Notes/Architecture/Sync Design.md")


if __name__ == "__main__":
    unittest.main()
