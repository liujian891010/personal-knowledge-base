import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from vault_core import (
    FileMapDocument,
    FileRecord,
    add_file,
    allocate_conflict_copy_path,
    TombstoneRecord,
    append_tombstone,
    initialize_vault,
    load_filemap,
    load_tombstone_ledger,
    mark_deleted,
    move_conflict_orphan,
    move_staging_orphan,
    recover_filemap,
    register_conflict_copy,
    rename_file,
    sanitize_device_name,
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

    def test_add_file_and_rename_file_update_filemap(self) -> None:
        document = FileMapDocument(vault_id="vault_pkb_001", updated_at=1770000000000, files=[])

        created = add_file(
            document,
            file_id="file_note_a",
            path="Notes/A.md",
            type="note",
            updated_at=1770000010000,
        )
        renamed = rename_file(
            created,
            file_id="file_note_a",
            new_path="Notes/Architecture/A.md",
            updated_at=1770000011000,
        )

        self.assertEqual(created.files[0].status, "active")
        self.assertEqual(renamed.files[0].path, "Notes/Architecture/A.md")
        self.assertEqual(renamed.updated_at, 1770000011000)

    def test_register_conflict_copy_creates_secondary_record(self) -> None:
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000010000,
            files=[
                FileRecord(
                    file_id="file_note_a",
                    path="Notes/A.md",
                    type="note",
                    status="active",
                    updated_at=1770000010000,
                )
            ],
        )

        updated = register_conflict_copy(
            document,
            source_file_id="file_note_a",
            conflict_file_id="file_note_a_conflict",
            conflict_path="Notes/A (conflict 2026-04-29 Desktop-Win).md",
            updated_at=1770000012000,
        )

        self.assertEqual(len(updated.files), 2)
        conflict = [record for record in updated.files if record.status == "conflict_copy"][0]
        self.assertEqual(conflict.conflict_source_file_id, "file_note_a")

    def test_allocate_conflict_copy_path_sanitizes_device_and_avoids_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original = Path(tmpdir) / "Notes" / "A.md"
            original.parent.mkdir(parents=True, exist_ok=True)
            first = original.parent / "A (conflict 2026-04-29 Desktop-Win-01).md"
            first.write_text("existing", encoding="utf-8")

            candidate = allocate_conflict_copy_path(
                original,
                conflict_date=date(2026, 4, 29),
                device_name="Desktop*Win?01",
            )

            self.assertEqual(candidate.name, "A (conflict 2026-04-29 Desktop-Win-01) 2.md")
            self.assertEqual(sanitize_device_name("Desktop*Win?01"), "Desktop-Win-01")

    def test_move_staging_orphan_rehomes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "vault"
            initialize_vault(root, vault_id="vault_pkb_001", now_ms=1770000020000)
            staging_file = root / ".noteapp" / "staging" / "abc.staging"
            staging_file.write_text("payload", encoding="utf-8")

            target = move_staging_orphan(root, staging_file)

            self.assertFalse(staging_file.exists())
            self.assertTrue(target.exists())
            self.assertEqual(target.parent.name, "staging-orphans")

    def test_move_conflict_orphan_rehomes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "vault"
            notes_dir = root / "Notes"
            notes_dir.mkdir(parents=True, exist_ok=True)
            conflict_file = notes_dir / "A (conflict 2026-04-29 Desktop-Win).md"
            conflict_file.write_text("payload", encoding="utf-8")

            target = move_conflict_orphan(root, conflict_file)

            self.assertFalse(conflict_file.exists())
            self.assertTrue(target.exists())
            self.assertEqual(target.parent.name, "conflict-orphans")


if __name__ == "__main__":
    unittest.main()
