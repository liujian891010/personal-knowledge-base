import json
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from vault_core import (
    FileMapDocument,
    FileRecord,
    add_file,
    allocate_conflict_copy_path,
    bootstrap_database,
    clear_commit_intent_journal,
    clear_sync_apply_journal,
    CommitIntentJournalRecord,
    TombstoneRecord,
    append_tombstone,
    initialize_vault,
    load_commit_intent_journal,
    list_file_index,
    load_filemap,
    load_sync_apply_journal,
    load_tombstone_ledger,
    load_vault_state,
    normalize_commit_journal_for_recovery,
    mark_deleted,
    move_conflict_orphan,
    move_staging_orphan,
    open_database,
    normalize_legacy_acknowledged_commit_intent,
    recover_prepared_commit_cleanup,
    recover_submitted_commit_match,
    recover_submitted_commit_miss,
    recover_sync_apply_finalizing_state,
    recover_filemap,
    replace_active_wiki_task,
    register_conflict_copy,
    rename_file,
    select_pending_tombstones_for_commit,
    sanitize_device_name,
    should_block_new_commit,
    SyncApplyJournalRecord,
    upsert_commit_intent_journal,
    upsert_file_index_entry,
    upsert_sync_apply_journal,
    upsert_vault_state,
    VaultStateRecord,
    WikiTaskRecord,
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

    def test_bootstrap_database_creates_core_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)

                table_names = {
                    row["name"]
                    for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")
                }
                self.assertIn("vault_state", table_names)
                self.assertIn("file_index", table_names)
                self.assertIn("wiki_tasks", table_names)
                self.assertIn("idx_wiki_tasks_active_path", table_names)
                self.assertTrue("search_index" in table_names)

    def test_vault_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)

                record = VaultStateRecord(
                    vault_id="vault_pkb_001",
                    last_applied_revision=7,
                    remote_head_revision=8,
                    acked_revision=6,
                    pending_ack_to_server=[7],
                    commit_in_progress=False,
                    last_manifest_summary="sha256:abc",
                    last_manifest_summary_status="valid",
                    local_delete_sequence=19,
                    has_unresolved_conflicts=True,
                )
                upsert_vault_state(connection, record)
                loaded = load_vault_state(connection, "vault_pkb_001")

                self.assertEqual(loaded, record)

    def test_file_index_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)

                record = FileRecord(
                    file_id="file_note_a",
                    path="Notes/A.md",
                    type="note",
                    status="active",
                    updated_at=1770000030000,
                    content_hash="sha256:abc",
                    last_known_revision=7,
                )
                upsert_file_index_entry(
                    connection,
                    vault_id="vault_pkb_001",
                    record=record,
                    local_mtime=1770000029000,
                    size=1024,
                )
                rows = list_file_index(connection, "vault_pkb_001")

                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["path"], "Notes/A.md")
                self.assertEqual(rows[0]["size"], 1024)

    def test_replace_active_wiki_task_supersedes_previous_active_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)

                first = WikiTaskRecord(
                    task_id="task_1",
                    target_wiki_path=".ai/wiki/LLM Wiki.md",
                    task_base_page_hash="sha256:page1",
                    task_base_revision=7,
                    task_sources_hash="sha256:sources1",
                    status="pending",
                    created_at=1770000031000,
                    updated_at=1770000031000,
                )
                second = WikiTaskRecord(
                    task_id="task_2",
                    target_wiki_path=".ai/wiki/LLM Wiki.md",
                    task_base_page_hash="sha256:page2",
                    task_base_revision=8,
                    task_sources_hash="sha256:sources2",
                    status="running",
                    created_at=1770000032000,
                    updated_at=1770000032000,
                )

                replace_active_wiki_task(connection, first)
                replace_active_wiki_task(connection, second)

                rows = list(
                    connection.execute(
                        "SELECT task_id, status FROM wiki_tasks WHERE target_wiki_path = ? ORDER BY created_at",
                        (".ai/wiki/LLM Wiki.md",),
                    ).fetchall()
                )
                self.assertEqual(
                    [(row["task_id"], row["status"]) for row in rows],
                    [("task_1", "superseded"), ("task_2", "running")],
                )

    def test_sync_apply_journal_round_trip_and_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)

                record = SyncApplyJournalRecord(
                    vault_id="vault_pkb_001",
                    journal_id="journal_1",
                    target_revision=8,
                    target_manifest_hash="sha256:manifest8",
                    phase="materializing",
                    ops_hash="sha256:ops8",
                    created_at=1770000040000,
                    updated_at=1770000041000,
                )
                upsert_sync_apply_journal(connection, record)
                self.assertEqual(load_sync_apply_journal(connection, "vault_pkb_001"), record)

                clear_sync_apply_journal(connection, "vault_pkb_001")
                self.assertIsNone(load_sync_apply_journal(connection, "vault_pkb_001"))

    def test_commit_intent_journal_round_trip_and_legacy_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)

                legacy = CommitIntentJournalRecord(
                    vault_id="vault_pkb_001",
                    commit_intent_id="intent_1",
                    intent_manifest_hash="sha256:intent1",
                    base_revision=7,
                    created_by_device="desktop-shanghai",
                    status="acknowledged",
                    intent_delete_seq_upper_bound=None,
                    created_at=1770000042000,
                    updated_at=1770000042000,
                )
                upsert_commit_intent_journal(connection, legacy)

                normalized = normalize_legacy_acknowledged_commit_intent(
                    connection,
                    "vault_pkb_001",
                    normalized_at=1770000043000,
                )
                loaded = load_commit_intent_journal(connection, "vault_pkb_001")

                self.assertEqual(normalized.status, "submitted")
                self.assertEqual(loaded.status, "submitted")
                self.assertIsNone(loaded.intent_delete_seq_upper_bound)

                clear_commit_intent_journal(connection, "vault_pkb_001")
                self.assertIsNone(load_commit_intent_journal(connection, "vault_pkb_001"))

    def test_recover_sync_apply_finalizing_state_repairs_ack_and_clears_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                upsert_vault_state(
                    connection,
                    VaultStateRecord(
                        vault_id="vault_pkb_001",
                        last_applied_revision=7,
                        remote_head_revision=8,
                        acked_revision=6,
                        pending_ack_to_server=[5, 7],
                        commit_in_progress=False,
                        last_manifest_summary="sha256:old",
                        last_manifest_summary_status="valid",
                        local_delete_sequence=19,
                    ),
                )
                upsert_sync_apply_journal(
                    connection,
                    SyncApplyJournalRecord(
                        vault_id="vault_pkb_001",
                        journal_id="journal_1",
                        target_revision=8,
                        target_manifest_hash="sha256:new",
                        phase="finalizing",
                        created_at=1770000050000,
                        updated_at=1770000051000,
                    ),
                )

                recovered = recover_sync_apply_finalizing_state(connection, "vault_pkb_001")

                self.assertEqual(recovered.last_applied_revision, 8)
                self.assertEqual(recovered.acked_revision, 8)
                self.assertEqual(recovered.pending_ack_to_server, [5, 7, 8])
                self.assertEqual(recovered.last_manifest_summary, "sha256:new")
                self.assertIsNone(load_sync_apply_journal(connection, "vault_pkb_001"))

    def test_recover_prepared_commit_cleanup_releases_commit_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                upsert_vault_state(
                    connection,
                    VaultStateRecord(
                        vault_id="vault_pkb_001",
                        last_applied_revision=7,
                        remote_head_revision=7,
                        acked_revision=7,
                        pending_ack_to_server=[],
                        commit_in_progress=True,
                        last_manifest_summary="sha256:head7",
                        last_manifest_summary_status="valid",
                        local_delete_sequence=19,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_1",
                        intent_manifest_hash="sha256:intent1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="prepared",
                        intent_delete_seq_upper_bound=19,
                        created_at=1770000052000,
                        updated_at=1770000052000,
                    ),
                )

                recovered = recover_prepared_commit_cleanup(connection, "vault_pkb_001")

                self.assertFalse(recovered.commit_in_progress)
                self.assertIsNone(load_commit_intent_journal(connection, "vault_pkb_001"))

    def test_recover_submitted_commit_match_with_manifest_404_marks_summary_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                upsert_vault_state(
                    connection,
                    VaultStateRecord(
                        vault_id="vault_pkb_001",
                        last_applied_revision=7,
                        remote_head_revision=7,
                        acked_revision=7,
                        pending_ack_to_server=[4, 6],
                        commit_in_progress=True,
                        last_manifest_summary="sha256:head7",
                        last_manifest_summary_status="valid",
                        local_delete_sequence=19,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_1",
                        intent_manifest_hash="sha256:intent1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="acknowledged",
                        intent_delete_seq_upper_bound=None,
                        created_at=1770000053000,
                        updated_at=1770000053000,
                    ),
                )

                recovered = recover_submitted_commit_match(
                    connection,
                    "vault_pkb_001",
                    matched_revision=8,
                    observed_head_revision=10,
                    matched_manifest_summary=None,
                    normalized_at=1770000054000,
                )

                self.assertEqual(recovered.last_applied_revision, 8)
                self.assertEqual(recovered.remote_head_revision, 10)
                self.assertEqual(recovered.acked_revision, 8)
                self.assertEqual(recovered.pending_ack_to_server, [4, 6])
                self.assertEqual(recovered.last_manifest_summary_status, "stale")
                self.assertIsNone(recovered.last_manifest_summary)
                self.assertIsNone(load_commit_intent_journal(connection, "vault_pkb_001"))

    def test_recover_submitted_commit_miss_only_releases_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                initial = VaultStateRecord(
                    vault_id="vault_pkb_001",
                    last_applied_revision=7,
                    remote_head_revision=8,
                    acked_revision=7,
                    pending_ack_to_server=[7],
                    commit_in_progress=True,
                    last_manifest_summary="sha256:head7",
                    last_manifest_summary_status="valid",
                    local_delete_sequence=19,
                )
                upsert_vault_state(connection, initial)
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_1",
                        intent_manifest_hash="sha256:intent1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=19,
                        created_at=1770000055000,
                        updated_at=1770000055000,
                    ),
                )

                recovered = recover_submitted_commit_miss(
                    connection,
                    "vault_pkb_001",
                    normalized_at=1770000056000,
                )

                self.assertFalse(recovered.commit_in_progress)
                self.assertEqual(recovered.pending_ack_to_server, [7])
                self.assertEqual(recovered.last_manifest_summary, "sha256:head7")
                self.assertIsNone(load_commit_intent_journal(connection, "vault_pkb_001"))

    def test_recovery_helpers_block_commit_on_stale_summary_and_active_journal(self) -> None:
        state = VaultStateRecord(
            vault_id="vault_pkb_001",
            last_applied_revision=8,
            remote_head_revision=10,
            acked_revision=8,
            pending_ack_to_server=[],
            commit_in_progress=False,
            last_manifest_summary=None,
            last_manifest_summary_status="stale",
            local_delete_sequence=19,
        )

        self.assertTrue(should_block_new_commit(state))
        self.assertTrue(should_block_new_commit(state, has_active_commit_journal=True))

    def test_select_pending_tombstones_supports_seq_and_legacy_time_modes(self) -> None:
        tombstones = [
            TombstoneRecord(
                file_id="file_a",
                deleted_revision=None,
                deleted_at=100,
                local_delete_seq=1,
            ),
            TombstoneRecord(
                file_id="file_b",
                deleted_revision=None,
                deleted_at=300,
                local_delete_seq=5,
            ),
            TombstoneRecord(
                file_id="file_remote",
                deleted_revision=8,
                deleted_at=200,
                local_delete_seq=0,
            ),
        ]
        seq_journal = CommitIntentJournalRecord(
            vault_id="vault_pkb_001",
            commit_intent_id="intent_seq",
            intent_manifest_hash="sha256:intent_seq",
            base_revision=7,
            created_by_device="desktop-shanghai",
            status="submitted",
            intent_delete_seq_upper_bound=2,
            created_at=250,
            updated_at=250,
        )
        legacy_journal = CommitIntentJournalRecord(
            vault_id="vault_pkb_001",
            commit_intent_id="intent_legacy",
            intent_manifest_hash="sha256:intent_legacy",
            base_revision=7,
            created_by_device="desktop-shanghai",
            status="acknowledged",
            intent_delete_seq_upper_bound=None,
            created_at=250,
            updated_at=250,
        )

        self.assertEqual(
            [item.file_id for item in select_pending_tombstones_for_commit(tombstones, seq_journal)],
            ["file_a"],
        )
        self.assertEqual(
            [item.file_id for item in select_pending_tombstones_for_commit(tombstones, legacy_journal)],
            ["file_a"],
        )
        normalized = normalize_commit_journal_for_recovery(legacy_journal, normalized_at=260)
        self.assertEqual(normalized.status, "submitted")


if __name__ == "__main__":
    unittest.main()
