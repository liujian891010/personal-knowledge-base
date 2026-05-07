import json
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from vault_core import (
    EMPTY_VAULT_FINAL_MANIFEST_SUMMARY,
    AppliedManifestResult,
    FileMapDocument,
    FileRecord,
    ManifestFileEntry,
    ManifestRecord,
    add_file,
    apply_manifest_reconciled_state,
    apply_manifest_summary_stale,
    apply_pulled_manifest,
    allocate_conflict_copy_path,
    build_initial_vault_state,
    bootstrap_database,
    clear_commit_intent_journal,
    clear_sync_apply_journal,
    canonical_manifest_payload,
    CommitIntentJournalRecord,
    compute_intent_manifest_hash,
    compute_manifest_summary_hash,
    converge_manifest_state,
    TombstoneRecord,
    append_tombstone,
    initialize_vault,
    initialize_vault_state,
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
    merge_manifest_tombstones,
    open_database,
    normalize_legacy_acknowledged_commit_intent,
    recover_prepared_commit_cleanup,
    recover_submitted_commit_from_manifest,
    recover_submitted_commit_match,
    recover_submitted_commit_miss,
    recover_sync_apply_finalizing_state,
    recover_filemap,
    recover_filemap_rewrite_convergence,
    persist_manifest_convergence,
    requires_full_pull,
    replace_active_wiki_task,
    register_conflict_copy,
    rename_file,
    select_reclaimable_tombstones,
    select_pending_tombstones_for_commit,
    sanitize_device_name,
    serialize_manifest_canonical,
    should_block_new_commit,
    SyncApplyJournalRecord,
    upsert_commit_intent_journal,
    upsert_file_index_entry,
    upsert_sync_apply_journal,
    upsert_vault_state,
    VaultStateRecord,
    WikiTaskRecord,
    with_computed_manifest_summary,
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

    def test_load_tombstone_ledger_accepts_remote_seq_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "tombstone-ledger.jsonl"
            ledger_path.write_text(
                json.dumps(
                    {
                        "file_id": "file_remote",
                        "last_known_path": "Notes/Remote.md",
                        "deleted_revision": 8,
                        "deleted_at": 1770000007000,
                        "local_delete_seq": 0,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            loaded = load_tombstone_ledger(ledger_path)

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].local_delete_seq, 0)

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

    def test_manifest_summary_hash_tracks_head_state_not_transport_metadata(self) -> None:
        base = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=7,
            base_revision=6,
            created_by_device="desktop-shanghai",
            created_at=1770000013000,
            summary_hash="placeholder",
            files=[
                ManifestFileEntry(
                    file_id="file_note_a",
                    path="Notes/A.md",
                    type="note",
                    content_hash="sha256:a",
                    blob_id="blob_a",
                    size=128,
                    mtime=1770000012000,
                )
            ],
            tombstones=[
                TombstoneRecord(
                    file_id="file_deleted_b",
                    deleted_revision=7,
                    deleted_at=1770000011000,
                    local_delete_seq=4,
                    last_known_path="Notes/B.md",
                )
            ],
        )
        transport_variant = ManifestRecord(
            vault_id="vault_other",
            revision=7,
            base_revision=3,
            created_by_device="mobile-hangzhou",
            created_at=1880000013000,
            summary_hash="placeholder",
            files=list(base.files),
            tombstones=list(base.tombstones),
        )
        next_revision = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=8,
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=1770000014000,
            summary_hash="placeholder",
            files=list(base.files),
            tombstones=list(base.tombstones),
        )

        self.assertEqual(compute_manifest_summary_hash(base), compute_manifest_summary_hash(transport_variant))
        self.assertNotEqual(compute_manifest_summary_hash(base), compute_manifest_summary_hash(next_revision))

    def test_intent_manifest_hash_omits_revision_and_null_deleted_revision(self) -> None:
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=7,
            base_revision=6,
            created_by_device="desktop-shanghai",
            created_at=1770000015000,
            summary_hash="placeholder",
            files=[
                ManifestFileEntry(
                    file_id="file_note_a",
                    path="Notes/A.md",
                    type="note",
                    content_hash="sha256:a",
                    blob_id="blob_a",
                    size=128,
                    mtime=1770000012000,
                )
            ],
            tombstones=[
                TombstoneRecord(
                    file_id="file_local_delete",
                    deleted_revision=None,
                    deleted_at=1770000011000,
                    local_delete_seq=4,
                    last_known_path="Notes/B.md",
                )
            ],
        )
        same_intent_new_revision = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=99,
            base_revision=6,
            created_by_device="desktop-shanghai",
            created_at=1770000015000,
            summary_hash="placeholder",
            files=list(manifest.files),
            tombstones=list(manifest.tombstones),
        )

        canonical_payload = canonical_manifest_payload(manifest, purpose="intent")
        canonical_bytes = serialize_manifest_canonical(manifest, purpose="intent")

        self.assertEqual(compute_intent_manifest_hash(manifest), compute_intent_manifest_hash(same_intent_new_revision))
        self.assertNotIn("revision", canonical_payload)
        self.assertNotIn("deleted_revision", canonical_payload["tombstones"][0])
        self.assertNotIn(b"deleted_revision", canonical_bytes)

    def test_empty_vault_final_manifest_summary_constant_is_stable(self) -> None:
        manifest = ManifestRecord(
            vault_id="vault_any",
            revision=0,
            base_revision=0,
            created_by_device="device_any",
            created_at=999,
            files=[],
            tombstones=[],
            summary_hash="placeholder",
        )

        self.assertEqual(compute_manifest_summary_hash(manifest), EMPTY_VAULT_FINAL_MANIFEST_SUMMARY)

    def test_with_computed_manifest_summary_replaces_placeholder(self) -> None:
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=7,
            base_revision=6,
            created_by_device="desktop-shanghai",
            created_at=1770000016000,
            files=[],
            tombstones=[],
            summary_hash="placeholder",
        )

        updated = with_computed_manifest_summary(manifest)

        self.assertEqual(updated.summary_hash, compute_manifest_summary_hash(manifest))
        self.assertNotEqual(updated.summary_hash, "placeholder")

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

    def test_build_initial_vault_state_uses_empty_vault_summary_gate(self) -> None:
        record = build_initial_vault_state("vault_pkb_001")

        self.assertEqual(record.last_applied_revision, 0)
        self.assertEqual(record.remote_head_revision, 0)
        self.assertEqual(record.acked_revision, 0)
        self.assertEqual(record.last_manifest_summary, EMPTY_VAULT_FINAL_MANIFEST_SUMMARY)
        self.assertFalse(should_block_new_commit(record))
        self.assertFalse(requires_full_pull(record))
        self.assertFalse(requires_full_pull(record, observed_head_revision=0))
        self.assertTrue(requires_full_pull(record, observed_head_revision=1))

    def test_initialize_vault_state_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)

                created = initialize_vault_state(
                    connection,
                    "vault_pkb_001",
                    meta={"source": "bootstrap"},
                )
                loaded = load_vault_state(connection, "vault_pkb_001")
                second = initialize_vault_state(connection, "vault_pkb_001")

                self.assertEqual(created.last_manifest_summary, EMPTY_VAULT_FINAL_MANIFEST_SUMMARY)
                self.assertEqual(loaded, created)
                self.assertEqual(second, created)

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

    def test_recover_submitted_commit_from_manifest_computes_final_summary(self) -> None:
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
                        status="submitted",
                        intent_delete_seq_upper_bound=19,
                        created_at=1770000053000,
                        updated_at=1770000053000,
                    ),
                )
                matched_manifest = ManifestRecord(
                    vault_id="vault_pkb_001",
                    revision=8,
                    base_revision=7,
                    created_by_device="desktop-shanghai",
                    created_at=1770000053500,
                    summary_hash="placeholder",
                    files=[
                        ManifestFileEntry(
                            file_id="file_note_a",
                            path="Notes/A.md",
                            type="note",
                            content_hash="sha256:a",
                            blob_id="blob_a",
                            size=128,
                            mtime=1770000052000,
                        )
                    ],
                    tombstones=[],
                )

                recovered = recover_submitted_commit_from_manifest(
                    connection,
                    "vault_pkb_001",
                    matched_manifest=matched_manifest,
                    matched_revision=8,
                    observed_head_revision=10,
                    normalized_at=1770000054000,
                )

                self.assertEqual(recovered.last_applied_revision, 8)
                self.assertEqual(recovered.remote_head_revision, 10)
                self.assertEqual(recovered.acked_revision, 8)
                self.assertEqual(recovered.last_manifest_summary, compute_manifest_summary_hash(matched_manifest))
                self.assertEqual(recovered.last_manifest_summary_status, "valid")
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

    def test_apply_manifest_summary_stale_marks_state_for_full_pull(self) -> None:
        state = VaultStateRecord(
            vault_id="vault_pkb_001",
            last_applied_revision=8,
            remote_head_revision=10,
            acked_revision=8,
            pending_ack_to_server=[8],
            commit_in_progress=False,
            last_manifest_summary="sha256:head8",
            last_manifest_summary_status="valid",
            local_delete_sequence=19,
        )

        stale = apply_manifest_summary_stale(state)

        self.assertIsNone(stale.last_manifest_summary)
        self.assertEqual(stale.last_manifest_summary_status, "stale")
        self.assertTrue(should_block_new_commit(stale))
        self.assertTrue(requires_full_pull(stale))
        self.assertTrue(requires_full_pull(stale, observed_head_revision=8))

    def test_apply_manifest_reconciled_state_updates_summary_and_ack_idempotently(self) -> None:
        state = VaultStateRecord(
            vault_id="vault_pkb_001",
            last_applied_revision=7,
            remote_head_revision=8,
            acked_revision=6,
            pending_ack_to_server=[5, 7],
            commit_in_progress=False,
            last_manifest_summary="sha256:head7",
            last_manifest_summary_status="valid",
            local_delete_sequence=19,
        )

        updated = apply_manifest_reconciled_state(
            state,
            target_revision=8,
            manifest_summary="sha256:head8",
        )

        self.assertEqual(updated.last_applied_revision, 8)
        self.assertEqual(updated.remote_head_revision, 8)
        self.assertEqual(updated.acked_revision, 8)
        self.assertEqual(updated.pending_ack_to_server, [5, 7, 8])
        self.assertEqual(updated.last_manifest_summary, "sha256:head8")
        self.assertEqual(updated.last_manifest_summary_status, "valid")

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

    def test_merge_manifest_tombstones_preserves_local_seq_and_adds_remote_seq_zero(self) -> None:
        local = [
            TombstoneRecord(
                file_id="file_local_delete",
                deleted_revision=None,
                deleted_at=100,
                local_delete_seq=9,
                last_known_path="Notes/Local Delete.md",
                deleted_by_device="desktop-shanghai",
            )
        ]
        remote = [
            TombstoneRecord(
                file_id="file_local_delete",
                deleted_revision=12,
                deleted_at=120,
                local_delete_seq=0,
                last_known_path="Notes/Local Delete.md",
                deleted_by_device="desktop-shanghai",
            ),
            TombstoneRecord(
                file_id="file_remote_delete",
                deleted_revision=11,
                deleted_at=110,
                local_delete_seq=0,
                last_known_path="Notes/Remote Delete.md",
                deleted_by_device="laptop-beijing",
            ),
        ]

        merged = {item.file_id: item for item in merge_manifest_tombstones(local, remote)}

        self.assertEqual(merged["file_local_delete"].deleted_revision, 12)
        self.assertEqual(merged["file_local_delete"].local_delete_seq, 9)
        self.assertEqual(merged["file_remote_delete"].local_delete_seq, 0)

    def test_select_reclaimable_tombstones_only_collects_committed_missing_entries(self) -> None:
        local_tombstones = [
            TombstoneRecord(
                file_id="file_collect",
                deleted_revision=7,
                deleted_at=100,
                local_delete_seq=1,
            ),
            TombstoneRecord(
                file_id="file_pending_local",
                deleted_revision=None,
                deleted_at=101,
                local_delete_seq=2,
            ),
            TombstoneRecord(
                file_id="file_future_revision",
                deleted_revision=12,
                deleted_at=102,
                local_delete_seq=3,
            ),
        ]
        manifest_tombstones = [
            TombstoneRecord(
                file_id="file_still_remote",
                deleted_revision=8,
                deleted_at=103,
                local_delete_seq=0,
            )
        ]

        reclaimable = select_reclaimable_tombstones(
            local_tombstones,
            manifest_tombstones,
            target_revision=8,
        )

        self.assertEqual([item.file_id for item in reclaimable], ["file_collect"])

    def test_converge_manifest_state_rebuilds_filemap_and_preserves_conflict_copy(self) -> None:
        current = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=90,
            files=[
                FileRecord(
                    file_id="file_note_architecture",
                    path="Notes/Old Sync Design.md",
                    type="note",
                    status="active",
                    updated_at=80,
                    content_hash="sha256:old",
                    last_known_revision=6,
                ),
                FileRecord(
                    file_id="file_conflict_copy",
                    path="Notes/Architecture/Sync Design (conflict 2026-04-29 Desktop-Win).md",
                    type="note",
                    status="conflict_copy",
                    updated_at=85,
                    content_hash="sha256:conflict",
                    conflict_source_file_id="file_note_architecture",
                ),
            ],
        )
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=8,
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=200,
            summary_hash="sha256:manifest8",
            files=[
                ManifestFileEntry(
                    file_id="file_note_architecture",
                    path="Notes/Architecture/Sync Design.md",
                    type="note",
                    content_hash="sha256:new",
                    blob_id="blob_sync_design",
                    size=1024,
                    mtime=180,
                )
            ],
            tombstones=[
                TombstoneRecord(
                    file_id="file_remote_deleted",
                    deleted_revision=8,
                    deleted_at=150,
                    local_delete_seq=0,
                    last_known_path="Notes/Archive/Legacy Plan.md",
                    deleted_by_device="laptop-beijing",
                )
            ],
        )

        converged = converge_manifest_state(
            current,
            manifest,
            local_tombstones=[],
            rewritten_at=220,
        )

        records = {item.file_id: item for item in converged.filemap.files}
        self.assertEqual(records["file_note_architecture"].path, "Notes/Architecture/Sync Design.md")
        self.assertEqual(records["file_note_architecture"].last_known_revision, 8)
        self.assertEqual(records["file_remote_deleted"].status, "deleted")
        self.assertEqual(records["file_remote_deleted"].last_known_revision, 8)
        self.assertEqual(records["file_conflict_copy"].status, "conflict_copy")
        self.assertEqual(converged.tombstones[0].local_delete_seq, 0)

    def test_converge_manifest_state_keeps_null_revision_tombstones_until_commit_ack(self) -> None:
        current = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=90,
            files=[
                FileRecord(
                    file_id="file_pending_delete",
                    path="Notes/Pending Delete.md",
                    type="note",
                    status="deleted",
                    updated_at=89,
                    last_known_revision=None,
                )
            ],
        )
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=8,
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=200,
            summary_hash="sha256:manifest8",
            files=[],
            tombstones=[],
        )
        local_tombstones = [
            TombstoneRecord(
                file_id="file_pending_delete",
                deleted_revision=None,
                deleted_at=150,
                local_delete_seq=12,
                last_known_path="Notes/Pending Delete.md",
                deleted_by_device="desktop-shanghai",
            )
        ]

        converged = converge_manifest_state(
            current,
            manifest,
            local_tombstones=local_tombstones,
            rewritten_at=220,
        )

        self.assertEqual([item.file_id for item in converged.reclaimed_tombstones], [])
        self.assertEqual([item.file_id for item in converged.tombstones], ["file_pending_delete"])
        self.assertEqual(
            [item.file_id for item in converged.filemap.files if item.status == "deleted"],
            ["file_pending_delete"],
        )

    def test_persist_manifest_convergence_writes_filemap_and_compacted_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            filemap_path = root / "filemap.json"
            ledger_path = root / "tombstone-ledger.jsonl"
            current = FileMapDocument(
                vault_id="vault_pkb_001",
                updated_at=90,
                files=[
                    FileRecord(
                        file_id="file_live",
                        path="Notes/Live Old.md",
                        type="note",
                        status="active",
                        updated_at=80,
                        content_hash="sha256:old",
                        last_known_revision=6,
                    ),
                    FileRecord(
                        file_id="file_pending_delete",
                        path="Notes/Pending Delete.md",
                        type="note",
                        status="deleted",
                        updated_at=79,
                        last_known_revision=None,
                    ),
                ],
            )
            manifest = ManifestRecord(
                vault_id="vault_pkb_001",
                revision=8,
                base_revision=7,
                created_by_device="desktop-shanghai",
                created_at=200,
                summary_hash="sha256:manifest8",
                files=[
                    ManifestFileEntry(
                        file_id="file_live",
                        path="Notes/Live.md",
                        type="note",
                        content_hash="sha256:new",
                        blob_id="blob_live",
                        size=128,
                        mtime=180,
                    )
                ],
                tombstones=[
                    TombstoneRecord(
                        file_id="file_remote_deleted",
                        deleted_revision=8,
                        deleted_at=150,
                        local_delete_seq=0,
                        last_known_path="Notes/Remote Deleted.md",
                    )
                ],
            )
            local_tombstones = [
                TombstoneRecord(
                    file_id="file_gc_candidate",
                    deleted_revision=7,
                    deleted_at=120,
                    local_delete_seq=4,
                    last_known_path="Notes/GC Candidate.md",
                ),
                TombstoneRecord(
                    file_id="file_pending_delete",
                    deleted_revision=None,
                    deleted_at=121,
                    local_delete_seq=5,
                    last_known_path="Notes/Pending Delete.md",
                ),
            ]

            persisted = persist_manifest_convergence(
                filemap_path,
                ledger_path,
                current,
                manifest,
                local_tombstones=local_tombstones,
                rewritten_at=220,
            )

            self.assertEqual([item.file_id for item in persisted.reclaimed_tombstones], ["file_gc_candidate"])
            self.assertEqual(
                [item.file_id for item in load_tombstone_ledger(ledger_path)],
                ["file_remote_deleted", "file_pending_delete"],
            )
            loaded_filemap = load_filemap(filemap_path)
            self.assertEqual(
                [item.file_id for item in loaded_filemap.sorted_files()],
                ["file_live", "file_pending_delete", "file_remote_deleted"],
            )

    def test_recover_filemap_rewrite_convergence_promotes_valid_tmp_before_rewriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            filemap_path = root / "filemap.json"
            ledger_path = root / "tombstone-ledger.jsonl"
            stale = FileMapDocument(vault_id="vault_pkb_001", updated_at=100, files=[])
            write_filemap_atomic(filemap_path, stale)
            promoted = FileMapDocument(
                vault_id="vault_pkb_001",
                updated_at=220,
                files=[
                    FileRecord(
                        file_id="file_live",
                        path="Notes/Live.md",
                        type="note",
                        status="active",
                        updated_at=220,
                        content_hash="sha256:new",
                        last_known_revision=8,
                    )
                ],
            )
            (root / "filemap.json.tmp").write_text(
                json.dumps(promoted.to_dict(), ensure_ascii=False),
                encoding="utf-8",
            )
            manifest = ManifestRecord(
                vault_id="vault_pkb_001",
                revision=8,
                base_revision=7,
                created_by_device="desktop-shanghai",
                created_at=200,
                summary_hash="sha256:manifest8",
                files=[
                    ManifestFileEntry(
                        file_id="file_live",
                        path="Notes/Live.md",
                        type="note",
                        content_hash="sha256:new",
                        blob_id="blob_live",
                        size=128,
                        mtime=180,
                    )
                ],
                tombstones=[],
            )

            recovered = recover_filemap_rewrite_convergence(
                filemap_path,
                ledger_path,
                stale,
                manifest,
                local_tombstones=[],
                rewritten_at=220,
            )

            self.assertEqual(recovered.filemap, promoted)
            self.assertFalse((root / "filemap.json.tmp").exists())
            self.assertEqual(load_tombstone_ledger(ledger_path), [])

    def test_recover_filemap_rewrite_convergence_rebuilds_when_tmp_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            filemap_path = root / "filemap.json"
            ledger_path = root / "tombstone-ledger.jsonl"
            current = FileMapDocument(
                vault_id="vault_pkb_001",
                updated_at=90,
                files=[
                    FileRecord(
                        file_id="file_live",
                        path="Notes/Live Old.md",
                        type="note",
                        status="active",
                        updated_at=80,
                        content_hash="sha256:old",
                        last_known_revision=6,
                    )
                ],
            )
            write_filemap_atomic(filemap_path, current)
            (root / "filemap.json.tmp").write_text("{broken", encoding="utf-8")
            manifest = ManifestRecord(
                vault_id="vault_pkb_001",
                revision=8,
                base_revision=7,
                created_by_device="desktop-shanghai",
                created_at=200,
                summary_hash="sha256:manifest8",
                files=[
                    ManifestFileEntry(
                        file_id="file_live",
                        path="Notes/Live.md",
                        type="note",
                        content_hash="sha256:new",
                        blob_id="blob_live",
                        size=128,
                        mtime=180,
                    )
                ],
                tombstones=[
                    TombstoneRecord(
                        file_id="file_remote_deleted",
                        deleted_revision=8,
                        deleted_at=150,
                        local_delete_seq=0,
                        last_known_path="Notes/Remote Deleted.md",
                    )
                ],
            )

            recovered = recover_filemap_rewrite_convergence(
                filemap_path,
                ledger_path,
                current,
                manifest,
                local_tombstones=[],
                rewritten_at=220,
            )

            self.assertEqual(
                [item.file_id for item in recovered.filemap.sorted_files()],
                ["file_live", "file_remote_deleted"],
            )
            self.assertEqual(
                [item.file_id for item in load_filemap(filemap_path).sorted_files()],
                ["file_live", "file_remote_deleted"],
            )
            self.assertEqual(
                [item.file_id for item in load_tombstone_ledger(ledger_path)],
                ["file_remote_deleted"],
            )

    def test_apply_pulled_manifest_persists_convergence_and_updates_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            filemap_path = root / "filemap.json"
            ledger_path = root / "tombstone-ledger.jsonl"
            db_path = root / "state.sqlite3"
            current = FileMapDocument(
                vault_id="vault_pkb_001",
                updated_at=90,
                files=[
                    FileRecord(
                        file_id="file_live",
                        path="Notes/Live Old.md",
                        type="note",
                        status="active",
                        updated_at=80,
                        content_hash="sha256:old",
                        last_known_revision=6,
                    ),
                    FileRecord(
                        file_id="file_conflict_copy",
                        path="Notes/Live (conflict 2026-04-29 Desktop-Win).md",
                        type="note",
                        status="conflict_copy",
                        updated_at=81,
                        content_hash="sha256:conflict",
                        conflict_source_file_id="file_live",
                    ),
                ],
            )
            manifest = ManifestRecord(
                vault_id="vault_pkb_001",
                revision=8,
                base_revision=7,
                created_by_device="desktop-shanghai",
                created_at=200,
                summary_hash="placeholder",
                files=[
                    ManifestFileEntry(
                        file_id="file_live",
                        path="Notes/Live.md",
                        type="note",
                        content_hash="sha256:new",
                        blob_id="blob_live",
                        size=128,
                        mtime=180,
                    )
                ],
                tombstones=[
                    TombstoneRecord(
                        file_id="file_remote_deleted",
                        deleted_revision=8,
                        deleted_at=150,
                        local_delete_seq=0,
                        last_known_path="Notes/Remote Deleted.md",
                    )
                ],
            )
            local_tombstones = [
                TombstoneRecord(
                    file_id="file_pending_delete",
                    deleted_revision=None,
                    deleted_at=121,
                    local_delete_seq=5,
                    last_known_path="Notes/Pending Delete.md",
                )
            ]

            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                initialize_vault_state(connection, "vault_pkb_001")

                applied = apply_pulled_manifest(
                    connection,
                    filemap_path=filemap_path,
                    ledger_path=ledger_path,
                    current_document=current,
                    manifest=manifest,
                    local_tombstones=local_tombstones,
                    rewritten_at=220,
                )

                self.assertIsInstance(applied, AppliedManifestResult)
                self.assertEqual(applied.state.last_applied_revision, 8)
                self.assertEqual(applied.state.remote_head_revision, 8)
                self.assertEqual(applied.state.acked_revision, 8)
                self.assertEqual(applied.state.pending_ack_to_server, [8])
                self.assertEqual(
                    applied.state.last_manifest_summary,
                    compute_manifest_summary_hash(manifest),
                )
                self.assertEqual(
                    [item.file_id for item in load_filemap(filemap_path).sorted_files()],
                    ["file_conflict_copy", "file_live", "file_pending_delete", "file_remote_deleted"],
                )
                self.assertEqual(
                    [item.file_id for item in load_tombstone_ledger(ledger_path)],
                    ["file_remote_deleted", "file_pending_delete"],
                )


if __name__ == "__main__":
    unittest.main()
