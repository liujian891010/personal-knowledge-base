import hashlib
import json
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from vault_core import (
    EMPTY_VAULT_FINAL_MANIFEST_SUMMARY,
    AppliedManifestResult,
    BlobCheckRequest,
    BlobCheckResult,
    BlobStagingMaterializationResult,
    BlobUploadPlan,
    BlobUploadPlanEntry,
    CommitNetworkPlan,
    CommitSnapshotEntry,
    CommitSnapshotTable,
    CommitRecoveryExecutionResult,
    CommitFinalizeCleanupResult,
    CommitSubmissionBundle,
    ContentSnapshotMaterializationResult,
    FileMapDocument,
    FileRecord,
    MaterializedContentSnapshotFile,
    ManifestFileEntry,
    ManifestRecord,
    MaterializedBlobStagingFile,
    CreateCommitBlobRef,
    CreateCommitRequestPayload,
    ContentSnapshotPlan,
    SnapshotDriftAbortResult,
    ReconcileResult,
    ReconcilePlan,
    RevisionMetadata,
    SubmittedConfirmationExecutionResult,
    SubmittedRecoveryResult,
    SubmittedConfirmationPlan,
    SubmittedConfirmationRemoteState,
    SubmittedConfirmationResolution,
    add_file,
    apply_commit_success_state,
    apply_committed_tombstones,
    apply_commit_submitted_state,
    apply_orphaned_commit_lock_recovery,
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
    abort_drifted_commit_snapshot,
    build_blob_check_request,
    build_blob_upload_plan,
    build_commit_manifest,
    build_commit_manifest_from_snapshot_plan,
    build_commit_network_plan,
    build_commit_snapshot_table,
    build_content_snapshot_plan,
    build_create_commit_request_payload,
    cleanup_commit_staging_artifacts,
    cleanup_failed_commit_submission,
    CommitRecoveryPlan,
    assert_content_snapshot_plan_matches,
    detect_content_snapshot_drift,
    execute_pull_reconcile,
    execute_submitted_commit_confirmation,
    execute_submitted_commit_confirmation_remote_state,
    find_matching_revision_metadata,
    finalize_commit_submission,
    finalize_commit_submission_cleanup,
    finalize_commit_manifest,
    finalize_manifest_revision,
    initialize_vault,
    initialize_vault_state,
    isolate_staging_orphans,
    load_commit_intent_journal,
    list_file_index,
    load_filemap,
    load_sync_apply_journal,
    load_tombstone_ledger,
    load_vault_state,
    materialize_blob_staging_plan,
    materialize_content_snapshot_plan,
    normalize_commit_journal_for_recovery,
    mark_deleted,
    move_conflict_orphan,
    move_staging_orphan,
    merge_manifest_tombstones,
    open_database,
    normalize_legacy_acknowledged_commit_intent,
    plan_commit_recovery,
    plan_submitted_confirmation,
    recover_prepared_commit_cleanup,
    recover_submitted_commit_flow,
    recover_submitted_commit_from_manifest,
    recover_submitted_commit_match,
    recover_submitted_commit_miss,
    recover_sync_apply_finalizing_state,
    recover_filemap,
    recover_filemap_rewrite_convergence,
    recover_local_commit_state,
    resolve_blob_check_result,
    resolve_submitted_confirmation,
    resume_commit_recovery,
    persist_manifest_convergence,
    plan_pull_reconcile,
    prepare_frozen_commit_intent,
    prepare_commit_submission,
    prepare_commit_intent,
    requires_full_pull,
    recover_orphaned_commit_lock,
    recover_orphaned_commit_session,
    replace_active_wiki_task,
    register_conflict_copy,
    rename_file,
    select_reclaimable_tombstones,
    select_pending_tombstones_for_commit,
    sanitize_device_name,
    serialize_manifest_canonical,
    should_block_new_commit,
    submit_prepared_commit,
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

    def test_finalize_manifest_revision_recomputes_summary_for_committed_revision(self) -> None:
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=0,
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=1770000017000,
            summary_hash="pending",
            files=[],
            tombstones=[],
        )

        finalized = finalize_manifest_revision(manifest, revision=8)

        self.assertEqual(finalized.revision, 8)
        self.assertEqual(finalized.summary_hash, compute_manifest_summary_hash(finalized))
        self.assertNotEqual(finalized.summary_hash, "pending")

    def test_build_commit_manifest_exports_active_entries_and_tombstones(self) -> None:
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018000,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000017900,
                    content_hash="sha256:live",
                    meta={
                        "blob_id": "blob_live",
                        "size": 128,
                        "mtime": 1770000017800,
                        "mime_type": "text/markdown",
                    },
                ),
                FileRecord(
                    file_id="file_deleted",
                    path="Notes/Deleted.md",
                    type="note",
                    status="deleted",
                    updated_at=1770000017850,
                ),
                FileRecord(
                    file_id="file_conflict",
                    path="Notes/Live (conflict).md",
                    type="note",
                    status="conflict_copy",
                    updated_at=1770000017860,
                    content_hash="sha256:conflict",
                    conflict_source_file_id="file_live",
                    meta={
                        "blob_id": "blob_conflict",
                        "size": 64,
                        "mtime": 1770000017850,
                    },
                ),
            ],
        )
        tombstones = [
            TombstoneRecord(
                file_id="file_deleted",
                deleted_revision=None,
                deleted_at=1770000017810,
                local_delete_seq=3,
                last_known_path="Notes/Deleted.md",
            )
        ]

        manifest = build_commit_manifest(
            document,
            tombstones=tombstones,
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=1770000018001,
        )

        self.assertEqual(manifest.revision, 0)
        self.assertEqual(manifest.summary_hash, "pending")
        self.assertEqual([item.file_id for item in manifest.files], ["file_live"])
        self.assertEqual(manifest.files[0].blob_id, "blob_live")
        self.assertEqual(manifest.files[0].mime_type, "text/markdown")
        self.assertEqual([item.file_id for item in manifest.tombstones], ["file_deleted"])

    def test_build_commit_manifest_rejects_missing_active_file_metadata(self) -> None:
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018100,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018000,
                    content_hash="sha256:live",
                    meta={
                        "blob_id": "blob_live",
                        "mtime": 1770000017900,
                    },
                )
            ],
        )

        with self.assertRaisesRegex(ValueError, "missing size"):
            build_commit_manifest(
                document,
                tombstones=[],
                base_revision=7,
                created_by_device="desktop-shanghai",
                created_at=1770000018101,
            )

    def test_build_commit_manifest_rejects_deleted_entries_without_tombstones(self) -> None:
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018200,
            files=[
                FileRecord(
                    file_id="file_deleted",
                    path="Notes/Deleted.md",
                    type="note",
                    status="deleted",
                    updated_at=1770000018190,
                )
            ],
        )

        with self.assertRaisesRegex(ValueError, "missing tombstones"):
            build_commit_manifest(
                document,
                tombstones=[],
                base_revision=7,
                created_by_device="desktop-shanghai",
                created_at=1770000018201,
            )

    def test_build_commit_manifest_rejects_active_path_collisions_after_nfc_normalization(self) -> None:
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018250,
            files=[
                FileRecord(
                    file_id="file_a",
                    path="Notes/Cafe\u0301.md",
                    type="note",
                    status="active",
                    updated_at=1770000018240,
                    content_hash="sha256:a",
                    meta={
                        "blob_id": "blob_a",
                        "size": 1,
                        "mtime": 1770000018230,
                    },
                ),
                FileRecord(
                    file_id="file_b",
                    path="Notes/Caf\u00e9.md",
                    type="note",
                    status="active",
                    updated_at=1770000018241,
                    content_hash="sha256:b",
                    meta={
                        "blob_id": "blob_b",
                        "size": 1,
                        "mtime": 1770000018231,
                    },
                ),
            ],
        )

        with self.assertRaisesRegex(ValueError, "collide after NFC normalization"):
            build_commit_manifest(
                document,
                tombstones=[],
                base_revision=7,
                created_by_device="desktop-shanghai",
                created_at=1770000018251,
            )

    def test_build_commit_manifest_rejects_conflict_copy_path_overlap_with_active(self) -> None:
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018260,
            files=[
                FileRecord(
                    file_id="file_conflict",
                    path="Notes/Caf\u00e9.md",
                    type="note",
                    status="conflict_copy",
                    updated_at=1770000018258,
                    content_hash="sha256:conflict",
                    conflict_source_file_id="file_live",
                ),
                FileRecord(
                    file_id="file_live",
                    path="Notes/Cafe\u0301.md",
                    type="note",
                    status="active",
                    updated_at=1770000018259,
                    content_hash="sha256:live",
                    meta={
                        "blob_id": "blob_live",
                        "size": 1,
                        "mtime": 1770000018250,
                    },
                ),
            ],
        )

        with self.assertRaisesRegex(ValueError, "conflict copy path overlaps"):
            build_commit_manifest(
                document,
                tombstones=[],
                base_revision=7,
                created_by_device="desktop-shanghai",
                created_at=1770000018261,
            )

    def test_apply_commit_submitted_state_sets_commit_in_progress(self) -> None:
        state = VaultStateRecord(
            vault_id="vault_pkb_001",
            last_applied_revision=7,
            remote_head_revision=7,
            acked_revision=7,
            pending_ack_to_server=[7],
            commit_in_progress=False,
            last_manifest_summary="sha256:head7",
            last_manifest_summary_status="valid",
            local_delete_sequence=5,
        )

        updated = apply_commit_submitted_state(state)

        self.assertTrue(updated.commit_in_progress)
        self.assertEqual(updated.pending_ack_to_server, [7])
        self.assertEqual(updated.last_manifest_summary, state.last_manifest_summary)

    def test_apply_commit_success_state_advances_revision_without_touching_pending_ack(self) -> None:
        state = VaultStateRecord(
            vault_id="vault_pkb_001",
            last_applied_revision=7,
            remote_head_revision=7,
            acked_revision=7,
            pending_ack_to_server=[4, 6],
            commit_in_progress=True,
            last_manifest_summary="sha256:head7",
            last_manifest_summary_status="valid",
            local_delete_sequence=5,
        )

        updated = apply_commit_success_state(
            state,
            committed_revision=8,
            manifest_summary="sha256:head8",
        )

        self.assertEqual(updated.last_applied_revision, 8)
        self.assertEqual(updated.remote_head_revision, 8)
        self.assertEqual(updated.acked_revision, 8)
        self.assertEqual(updated.pending_ack_to_server, [4, 6])
        self.assertFalse(updated.commit_in_progress)
        self.assertEqual(updated.last_manifest_summary, "sha256:head8")

    def test_prepare_commit_intent_persists_prepared_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with closing(open_database(Path(tmpdir) / "state.sqlite3")) as connection:
                bootstrap_database(connection)
                state = VaultStateRecord(
                    vault_id="vault_pkb_001",
                    last_applied_revision=7,
                    remote_head_revision=7,
                    acked_revision=7,
                    pending_ack_to_server=[],
                    commit_in_progress=False,
                    last_manifest_summary="sha256:head7",
                    last_manifest_summary_status="valid",
                    local_delete_sequence=2,
                )
                upsert_vault_state(connection, state)

                bundle = prepare_commit_intent(
                    connection,
                    state=state,
                    commit_intent_id="intent_prepared",
                    created_by_device="desktop-shanghai",
                    created_at=1770000018300,
                )

                stored_journal = load_commit_intent_journal(connection, "vault_pkb_001")
                self.assertEqual(bundle.journal.status, "prepared")
                self.assertEqual(bundle.journal.intent_manifest_hash, "pending")
                self.assertTrue(bundle.state.commit_in_progress)
                self.assertIsNotNone(stored_journal)
                self.assertEqual(stored_journal, bundle.journal)

    def test_build_content_snapshot_plan_freezes_active_files_into_staging_paths(self) -> None:
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018300,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018290,
                    content_hash="sha256:live",
                    meta={
                        "blob_id": "blob_live",
                        "size": 128,
                        "mtime": 1770000018280,
                    },
                ),
                FileRecord(
                    file_id="file_conflict",
                    path="Notes/Live (conflict).md",
                    type="note",
                    status="conflict_copy",
                    updated_at=1770000018291,
                    conflict_source_file_id="file_live",
                ),
                FileRecord(
                    file_id="file_deleted",
                    path="Notes/Deleted.md",
                    type="note",
                    status="deleted",
                    updated_at=1770000018292,
                ),
            ],
        )

        plan = build_content_snapshot_plan(
            document,
            base_revision=7,
            created_at=1770000018301,
        )

        self.assertEqual(
            plan,
            ContentSnapshotPlan(
                vault_id="vault_pkb_001",
                base_revision=7,
                created_at=1770000018301,
                files=plan.files,
            ),
        )
        self.assertEqual(len(plan.files), 1)
        self.assertEqual(plan.files[0].file_id, "file_live")
        self.assertEqual(plan.files[0].snapshot_path, ".noteapp/staging/file_live.snapshot.plain")
        self.assertEqual(plan.files[0].blob_staging_path, ".noteapp/staging/blob_live.blob.staging")
        self.assertEqual(plan.files[0].size, 128)
        self.assertEqual(plan.files[0].mtime, 1770000018280)
        self.assertIsNone(plan.files[0].mime_type)
        self.assertEqual(
            plan.files[0].source_version_token,
            "mtime:1770000018280:size:128:hash:sha256:live",
        )

    def test_detect_content_snapshot_drift_flags_changed_or_missing_files(self) -> None:
        baseline = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018400,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018390,
                    content_hash="sha256:live",
                    meta={
                        "blob_id": "blob_live",
                        "size": 128,
                        "mtime": 1770000018380,
                    },
                )
            ],
        )
        drifted = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018401,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Renamed.md",
                    type="note",
                    status="active",
                    updated_at=1770000018391,
                    content_hash="sha256:new",
                    meta={
                        "blob_id": "blob_live_next",
                        "size": 256,
                        "mtime": 1770000018381,
                    },
                )
            ],
        )

        plan = build_content_snapshot_plan(
            baseline,
            base_revision=7,
            created_at=1770000018402,
        )

        self.assertEqual(detect_content_snapshot_drift(plan, drifted), ["file_live"])
        with self.assertRaisesRegex(ValueError, "content snapshot drift detected: file_live"):
            assert_content_snapshot_plan_matches(plan, drifted)

    def test_prepare_frozen_commit_intent_returns_snapshot_plan_with_prepared_journal(self) -> None:
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018500,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018490,
                    content_hash="sha256:live",
                    meta={
                        "blob_id": "blob_live",
                        "size": 128,
                        "mtime": 1770000018480,
                    },
                )
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with closing(open_database(Path(tmpdir) / "state.sqlite3")) as connection:
                bootstrap_database(connection)
                state = VaultStateRecord(
                    vault_id="vault_pkb_001",
                    last_applied_revision=7,
                    remote_head_revision=7,
                    acked_revision=7,
                    pending_ack_to_server=[],
                    commit_in_progress=False,
                    last_manifest_summary="sha256:head7",
                    last_manifest_summary_status="valid",
                    local_delete_sequence=2,
                )
                upsert_vault_state(connection, state)

                bundle = prepare_frozen_commit_intent(
                    connection,
                    state=state,
                    document=document,
                    commit_intent_id="intent_prepared",
                    created_by_device="desktop-shanghai",
                    created_at=1770000018501,
                )

                self.assertEqual(bundle.journal.status, "prepared")
                self.assertTrue(bundle.state.commit_in_progress)
                self.assertEqual(bundle.snapshot_plan.base_revision, 7)
                self.assertEqual([item.file_id for item in bundle.snapshot_plan.files], ["file_live"])

    def test_build_commit_manifest_from_snapshot_plan_uses_frozen_metadata(self) -> None:
        baseline = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018520,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018510,
                    content_hash="sha256:live",
                    meta={
                        "blob_id": "blob_live",
                        "size": 128,
                        "mtime": 1770000018500,
                        "mime_type": "text/markdown",
                    },
                )
            ],
        )
        current = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018521,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018511,
                    content_hash="sha256:live",
                    meta={
                        "blob_id": "blob_live_new",
                        "size": 256,
                        "mtime": 1770000018500,
                        "mime_type": "text/x-markdown",
                    },
                )
            ],
        )
        plan = build_content_snapshot_plan(
            baseline,
            base_revision=7,
            created_at=1770000018522,
        )

        manifest = build_commit_manifest_from_snapshot_plan(
            current,
            snapshot_plan=plan,
            tombstones=[],
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=1770000018522,
        )

        self.assertEqual(manifest.files[0].blob_id, "blob_live")
        self.assertEqual(manifest.files[0].size, 128)
        self.assertEqual(manifest.files[0].mime_type, "text/markdown")

    def test_materialize_content_snapshot_plan_writes_snapshot_plain_files(self) -> None:
        payload = b"# frozen snapshot\n"
        content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018550,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018540,
                    content_hash=content_hash,
                    meta={
                        "blob_id": "blob_live",
                        "size": len(payload),
                        "mtime": 1770000018530,
                    },
                )
            ],
        )
        plan = build_content_snapshot_plan(
            document,
            base_revision=7,
            created_at=1770000018551,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            materialized = materialize_content_snapshot_plan(
                root,
                plan=plan,
                document=document,
                content_by_file_id={
                    "file_live": payload,
                    "file_unused": b"ignore me",
                },
            )

            snapshot_path = root / ".noteapp" / "staging" / "file_live.snapshot.plain"
            self.assertEqual(
                materialized,
                ContentSnapshotMaterializationResult(
                    snapshot_plan=plan,
                    files=[
                        MaterializedContentSnapshotFile(
                            file_id="file_live",
                            snapshot_path=snapshot_path.resolve(),
                            content_hash=content_hash,
                            size_bytes=len(payload),
                        )
                    ],
                ),
            )
            self.assertEqual(snapshot_path.read_bytes(), payload)
            self.assertFalse((root / ".noteapp" / "staging" / "file_unused.snapshot.plain").exists())

    def test_materialize_content_snapshot_plan_rejects_drift_before_writing(self) -> None:
        payload = b"# frozen snapshot\n"
        baseline = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018560,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018550,
                    content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
                    meta={
                        "blob_id": "blob_live",
                        "size": len(payload),
                        "mtime": 1770000018540,
                    },
                )
            ],
        )
        drifted = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018561,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Renamed.md",
                    type="note",
                    status="active",
                    updated_at=1770000018551,
                    content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
                    meta={
                        "blob_id": "blob_live",
                        "size": len(payload),
                        "mtime": 1770000018540,
                    },
                )
            ],
        )
        plan = build_content_snapshot_plan(
            baseline,
            base_revision=7,
            created_at=1770000018562,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with self.assertRaisesRegex(ValueError, "content snapshot drift detected: file_live"):
                materialize_content_snapshot_plan(
                    root,
                    plan=plan,
                    document=drifted,
                    content_by_file_id={"file_live": payload},
                )

            self.assertFalse((root / ".noteapp" / "staging" / "file_live.snapshot.plain").exists())

    def test_materialize_content_snapshot_plan_rejects_content_hash_mismatch(self) -> None:
        payload = b"# frozen snapshot\n"
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018570,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018560,
                    content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
                    meta={
                        "blob_id": "blob_live",
                        "size": len(payload),
                        "mtime": 1770000018550,
                    },
                )
            ],
        )
        plan = build_content_snapshot_plan(
            document,
            base_revision=7,
            created_at=1770000018571,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with self.assertRaisesRegex(ValueError, "snapshot content hash mismatch for file_id file_live"):
                materialize_content_snapshot_plan(
                    root,
                    plan=plan,
                    document=document,
                    content_by_file_id={"file_live": b"# changed\n"},
                )

            self.assertFalse((root / ".noteapp" / "staging" / "file_live.snapshot.plain").exists())

    def test_materialize_content_snapshot_plan_rejects_content_size_mismatch(self) -> None:
        payload = b"# frozen snapshot\n"
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018572,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018562,
                    content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
                    meta={
                        "blob_id": "blob_live",
                        "size": len(payload) + 1,
                        "mtime": 1770000018552,
                    },
                )
            ],
        )
        plan = build_content_snapshot_plan(
            document,
            base_revision=7,
            created_at=1770000018573,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with self.assertRaisesRegex(ValueError, "snapshot content size mismatch for file_id file_live"):
                materialize_content_snapshot_plan(
                    root,
                    plan=plan,
                    document=document,
                    content_by_file_id={"file_live": payload},
                )

            self.assertFalse((root / ".noteapp" / "staging" / "file_live.snapshot.plain").exists())

    def test_materialize_content_snapshot_plan_cleans_partial_writes_on_failure(self) -> None:
        first_payload = b"# first\n"
        second_payload = b"# second\n"
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018580,
            files=[
                FileRecord(
                    file_id="file_first",
                    path="Notes/First.md",
                    type="note",
                    status="active",
                    updated_at=1770000018570,
                    content_hash="sha256:" + hashlib.sha256(first_payload).hexdigest(),
                    meta={
                        "blob_id": "blob_first",
                        "size": len(first_payload),
                        "mtime": 1770000018560,
                    },
                ),
                FileRecord(
                    file_id="file_second",
                    path="Notes/Second.md",
                    type="note",
                    status="active",
                    updated_at=1770000018571,
                    content_hash="sha256:" + hashlib.sha256(second_payload).hexdigest(),
                    meta={
                        "blob_id": "blob_second",
                        "size": len(second_payload),
                        "mtime": 1770000018561,
                    },
                ),
            ],
        )
        plan = build_content_snapshot_plan(
            document,
            base_revision=7,
            created_at=1770000018581,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with self.assertRaisesRegex(ValueError, "snapshot content hash mismatch for file_id file_second"):
                materialize_content_snapshot_plan(
                    root,
                    plan=plan,
                    document=document,
                    content_by_file_id={
                        "file_first": first_payload,
                        "file_second": b"# drifted second\n",
                    },
                )

            self.assertFalse((root / ".noteapp" / "staging" / "file_first.snapshot.plain").exists())
            self.assertFalse((root / ".noteapp" / "staging" / "file_second.snapshot.plain").exists())

    def test_materialize_blob_staging_plan_writes_encrypted_blob_files(self) -> None:
        payload = b"# frozen snapshot\n"
        encrypted_payload = b"x" * (len(payload) + 16)
        content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018585,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018575,
                    content_hash=content_hash,
                    meta={
                        "blob_id": "blob_live",
                        "size": len(payload),
                        "mtime": 1770000018565,
                    },
                )
            ],
        )
        plan = build_content_snapshot_plan(
            document,
            base_revision=7,
            created_at=1770000018586,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshots = materialize_content_snapshot_plan(
                root,
                plan=plan,
                document=document,
                content_by_file_id={"file_live": payload},
            )
            staged = materialize_blob_staging_plan(
                root,
                plan=plan,
                snapshot_materialization=snapshots,
                encrypted_blob_by_file_id={
                    "file_live": encrypted_payload,
                    "file_unused": b"ignore me",
                },
            )

            blob_path = root / ".noteapp" / "staging" / "blob_live.blob.staging"
            snapshot_path = root / ".noteapp" / "staging" / "file_live.snapshot.plain"
            self.assertEqual(
                staged,
                BlobStagingMaterializationResult(
                    snapshot_plan=plan,
                    files=[
                        MaterializedBlobStagingFile(
                            file_id="file_live",
                            blob_id="blob_live",
                            snapshot_path=snapshot_path.resolve(),
                            blob_staging_path=blob_path.resolve(),
                            content_hash=content_hash,
                            plaintext_size=len(payload),
                            encrypted_size=len(encrypted_payload),
                        )
                    ],
                ),
            )
            self.assertEqual(blob_path.read_bytes(), encrypted_payload)
            self.assertEqual(snapshot_path.read_bytes(), payload)

    def test_materialize_blob_staging_plan_rejects_tampered_snapshot_file(self) -> None:
        payload = b"# frozen snapshot\n"
        encrypted_payload = b"x" * (len(payload) + 16)
        content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018587,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018577,
                    content_hash=content_hash,
                    meta={
                        "blob_id": "blob_live",
                        "size": len(payload),
                        "mtime": 1770000018567,
                    },
                )
            ],
        )
        plan = build_content_snapshot_plan(
            document,
            base_revision=7,
            created_at=1770000018588,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshots = materialize_content_snapshot_plan(
                root,
                plan=plan,
                document=document,
                content_by_file_id={"file_live": payload},
            )
            snapshot_path = root / ".noteapp" / "staging" / "file_live.snapshot.plain"
            snapshot_path.write_bytes(b"# tampered\n")

            with self.assertRaisesRegex(ValueError, "snapshot file hash mismatch for file_id file_live"):
                materialize_blob_staging_plan(
                    root,
                    plan=plan,
                    snapshot_materialization=snapshots,
                    encrypted_blob_by_file_id={"file_live": encrypted_payload},
                )

            self.assertFalse((root / ".noteapp" / "staging" / "blob_live.blob.staging").exists())

    def test_materialize_blob_staging_plan_cleans_partial_writes_on_failure(self) -> None:
        first_payload = b"# first\n"
        second_payload = b"# second\n"
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018589,
            files=[
                FileRecord(
                    file_id="file_first",
                    path="Notes/First.md",
                    type="note",
                    status="active",
                    updated_at=1770000018578,
                    content_hash="sha256:" + hashlib.sha256(first_payload).hexdigest(),
                    meta={
                        "blob_id": "blob_first",
                        "size": len(first_payload),
                        "mtime": 1770000018568,
                    },
                ),
                FileRecord(
                    file_id="file_second",
                    path="Notes/Second.md",
                    type="note",
                    status="active",
                    updated_at=1770000018579,
                    content_hash="sha256:" + hashlib.sha256(second_payload).hexdigest(),
                    meta={
                        "blob_id": "blob_second",
                        "size": len(second_payload),
                        "mtime": 1770000018569,
                    },
                ),
            ],
        )
        plan = build_content_snapshot_plan(
            document,
            base_revision=7,
            created_at=1770000018590,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshots = materialize_content_snapshot_plan(
                root,
                plan=plan,
                document=document,
                content_by_file_id={
                    "file_first": first_payload,
                    "file_second": second_payload,
                },
            )

            with self.assertRaisesRegex(ValueError, "encrypted blob size mismatch for file_id file_second"):
                materialize_blob_staging_plan(
                    root,
                    plan=plan,
                    snapshot_materialization=snapshots,
                    encrypted_blob_by_file_id={
                        "file_first": b"a" * (len(first_payload) + 16),
                        "file_second": b"b" * len(second_payload),
                    },
                )

            self.assertFalse((root / ".noteapp" / "staging" / "blob_first.blob.staging").exists())
            self.assertFalse((root / ".noteapp" / "staging" / "blob_second.blob.staging").exists())

    def test_build_commit_snapshot_table_collects_frozen_staged_entries(self) -> None:
        payload = b"# frozen snapshot\n"
        encrypted_payload = b"x" * (len(payload) + 16)
        content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018591,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018581,
                    content_hash=content_hash,
                    meta={
                        "blob_id": "blob_live",
                        "size": len(payload),
                        "mtime": 1770000018571,
                        "mime_type": "text/markdown",
                    },
                )
            ],
        )
        plan = build_content_snapshot_plan(
            document,
            base_revision=7,
            created_at=1770000018592,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshots = materialize_content_snapshot_plan(
                root,
                plan=plan,
                document=document,
                content_by_file_id={"file_live": payload},
            )
            staged = materialize_blob_staging_plan(
                root,
                plan=plan,
                snapshot_materialization=snapshots,
                encrypted_blob_by_file_id={"file_live": encrypted_payload},
            )

            table = build_commit_snapshot_table(
                plan,
                snapshot_materialization=snapshots,
                blob_staging_materialization=staged,
            )

            self.assertEqual(
                table,
                CommitSnapshotTable(
                    vault_id="vault_pkb_001",
                    base_revision=7,
                    created_at=1770000018592,
                    entries=[
                        CommitSnapshotEntry(
                            file_id="file_live",
                            path="Notes/Live.md",
                            type="note",
                            content_hash=content_hash,
                            blob_id="blob_live",
                            plaintext_size=len(payload),
                            encrypted_size=len(encrypted_payload),
                            mtime=1770000018571,
                            mime_type="text/markdown",
                            snapshot_path=(root / ".noteapp" / "staging" / "file_live.snapshot.plain").resolve(),
                            blob_staging_path=(root / ".noteapp" / "staging" / "blob_live.blob.staging").resolve(),
                        )
                    ],
                ),
            )

    def test_build_commit_snapshot_table_rejects_mismatched_blob_materialization(self) -> None:
        payload = b"# frozen snapshot\n"
        encrypted_payload = b"x" * (len(payload) + 16)
        content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018593,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018583,
                    content_hash=content_hash,
                    meta={
                        "blob_id": "blob_live",
                        "size": len(payload),
                        "mtime": 1770000018573,
                    },
                )
            ],
        )
        plan = build_content_snapshot_plan(
            document,
            base_revision=7,
            created_at=1770000018594,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshots = materialize_content_snapshot_plan(
                root,
                plan=plan,
                document=document,
                content_by_file_id={"file_live": payload},
            )
            staged = materialize_blob_staging_plan(
                root,
                plan=plan,
                snapshot_materialization=snapshots,
                encrypted_blob_by_file_id={"file_live": encrypted_payload},
            )

            tampered_staged = BlobStagingMaterializationResult(
                snapshot_plan=staged.snapshot_plan,
                files=[
                    MaterializedBlobStagingFile(
                        file_id="file_live",
                        blob_id="blob_live_tampered",
                        snapshot_path=staged.files[0].snapshot_path,
                        blob_staging_path=staged.files[0].blob_staging_path,
                        content_hash=staged.files[0].content_hash,
                        plaintext_size=staged.files[0].plaintext_size,
                        encrypted_size=staged.files[0].encrypted_size,
                    )
                ],
            )

            with self.assertRaisesRegex(ValueError, "materialized blob id mismatch for file_id file_live"):
                build_commit_snapshot_table(
                    plan,
                    snapshot_materialization=snapshots,
                    blob_staging_materialization=tampered_staged,
                )

    def test_build_blob_check_request_deduplicates_blob_ids_from_snapshot_table(self) -> None:
        snapshot_table = CommitSnapshotTable(
            vault_id="vault_pkb_001",
            base_revision=7,
            created_at=1770000018595,
            entries=[
                CommitSnapshotEntry(
                    file_id="file_b",
                    path="Notes/B.md",
                    type="note",
                    content_hash="sha256:same",
                    blob_id="blob_shared",
                    plaintext_size=16,
                    encrypted_size=32,
                    mtime=1770000018590,
                    mime_type=None,
                    snapshot_path=Path("C:/tmp/file_b.snapshot.plain"),
                    blob_staging_path=Path("C:/tmp/blob_shared.blob.staging"),
                ),
                CommitSnapshotEntry(
                    file_id="file_a",
                    path="Notes/A.md",
                    type="note",
                    content_hash="sha256:same",
                    blob_id="blob_shared",
                    plaintext_size=16,
                    encrypted_size=32,
                    mtime=1770000018589,
                    mime_type=None,
                    snapshot_path=Path("C:/tmp/file_a.snapshot.plain"),
                    blob_staging_path=Path("C:/tmp/blob_shared.blob.staging"),
                ),
                CommitSnapshotEntry(
                    file_id="file_c",
                    path="Notes/C.md",
                    type="note",
                    content_hash="sha256:other",
                    blob_id="blob_unique",
                    plaintext_size=8,
                    encrypted_size=24,
                    mtime=1770000018591,
                    mime_type=None,
                    snapshot_path=Path("C:/tmp/file_c.snapshot.plain"),
                    blob_staging_path=Path("C:/tmp/blob_unique.blob.staging"),
                ),
            ],
        )

        request = build_blob_check_request(snapshot_table)

        self.assertEqual(
            request,
            BlobCheckRequest(blob_ids=["blob_shared", "blob_unique"]),
        )

    def test_build_blob_check_request_allows_empty_snapshot_table(self) -> None:
        snapshot_table = CommitSnapshotTable(
            vault_id="vault_pkb_001",
            base_revision=7,
            created_at=1770000018596,
            entries=[],
        )

        request = build_blob_check_request(snapshot_table)

        self.assertEqual(request, BlobCheckRequest(blob_ids=[]))

    def test_resolve_blob_check_result_requires_full_partition_of_requested_blob_ids(self) -> None:
        snapshot_table = CommitSnapshotTable(
            vault_id="vault_pkb_001",
            base_revision=7,
            created_at=1770000018596,
            entries=[
                CommitSnapshotEntry(
                    file_id="file_a",
                    path="Notes/A.md",
                    type="note",
                    content_hash="sha256:a",
                    blob_id="blob_a",
                    plaintext_size=16,
                    encrypted_size=32,
                    mtime=1770000018592,
                    mime_type=None,
                    snapshot_path=Path("C:/tmp/file_a.snapshot.plain"),
                    blob_staging_path=Path("C:/tmp/blob_a.blob.staging"),
                ),
                CommitSnapshotEntry(
                    file_id="file_b",
                    path="Notes/B.md",
                    type="note",
                    content_hash="sha256:b",
                    blob_id="blob_b",
                    plaintext_size=32,
                    encrypted_size=48,
                    mtime=1770000018593,
                    mime_type=None,
                    snapshot_path=Path("C:/tmp/file_b.snapshot.plain"),
                    blob_staging_path=Path("C:/tmp/blob_b.blob.staging"),
                ),
            ],
        )

        resolved = resolve_blob_check_result(
            snapshot_table,
            existing_blob_ids=["blob_b"],
            missing_blob_ids=["blob_a"],
        )
        self.assertEqual(
            resolved,
            BlobCheckResult(
                requested_blob_ids=["blob_a", "blob_b"],
                existing_blob_ids=["blob_b"],
                missing_blob_ids=["blob_a"],
            ),
        )

        with self.assertRaisesRegex(ValueError, "unknown blob_ids: blob_extra"):
            resolve_blob_check_result(
                snapshot_table,
                existing_blob_ids=["blob_b", "blob_extra"],
                missing_blob_ids=["blob_a"],
            )

        with self.assertRaisesRegex(ValueError, "does not cover requested blob_ids: blob_b"):
            resolve_blob_check_result(
                snapshot_table,
                existing_blob_ids=[],
                missing_blob_ids=["blob_a"],
            )

    def test_build_blob_upload_plan_deduplicates_missing_blob_uploads(self) -> None:
        shared_blob_path = Path("C:/tmp/blob_shared.blob.staging")
        snapshot_table = CommitSnapshotTable(
            vault_id="vault_pkb_001",
            base_revision=7,
            created_at=1770000018597,
            entries=[
                CommitSnapshotEntry(
                    file_id="file_a",
                    path="Notes/A.md",
                    type="note",
                    content_hash="sha256:same",
                    blob_id="blob_shared",
                    plaintext_size=16,
                    encrypted_size=32,
                    mtime=1770000018594,
                    mime_type=None,
                    snapshot_path=Path("C:/tmp/file_a.snapshot.plain"),
                    blob_staging_path=shared_blob_path,
                ),
                CommitSnapshotEntry(
                    file_id="file_b",
                    path="Notes/B.md",
                    type="note",
                    content_hash="sha256:same",
                    blob_id="blob_shared",
                    plaintext_size=16,
                    encrypted_size=32,
                    mtime=1770000018595,
                    mime_type=None,
                    snapshot_path=Path("C:/tmp/file_b.snapshot.plain"),
                    blob_staging_path=shared_blob_path,
                ),
                CommitSnapshotEntry(
                    file_id="file_c",
                    path="Notes/C.md",
                    type="note",
                    content_hash="sha256:other",
                    blob_id="blob_unique",
                    plaintext_size=8,
                    encrypted_size=24,
                    mtime=1770000018596,
                    mime_type=None,
                    snapshot_path=Path("C:/tmp/file_c.snapshot.plain"),
                    blob_staging_path=Path("C:/tmp/blob_unique.blob.staging"),
                ),
            ],
        )

        upload_plan = build_blob_upload_plan(
            snapshot_table,
            missing_blob_ids=["blob_shared", "blob_unique"],
        )

        self.assertEqual(
            upload_plan,
            BlobUploadPlan(
                vault_id="vault_pkb_001",
                entries=[
                    BlobUploadPlanEntry(
                        blob_id="blob_shared",
                        content_hash="sha256:same",
                        encrypted_size=32,
                        blob_staging_path=shared_blob_path,
                        file_ids=["file_a", "file_b"],
                    ),
                    BlobUploadPlanEntry(
                        blob_id="blob_unique",
                        content_hash="sha256:other",
                        encrypted_size=24,
                        blob_staging_path=Path("C:/tmp/blob_unique.blob.staging"),
                        file_ids=["file_c"],
                    ),
                ],
            ),
        )

        inconsistent_snapshot_table = CommitSnapshotTable(
            vault_id=snapshot_table.vault_id,
            base_revision=snapshot_table.base_revision,
            created_at=snapshot_table.created_at,
            entries=[
                snapshot_table.entries[0],
                CommitSnapshotEntry(
                    file_id="file_b",
                    path="Notes/B.md",
                    type="note",
                    content_hash="sha256:different",
                    blob_id="blob_shared",
                    plaintext_size=16,
                    encrypted_size=32,
                    mtime=1770000018595,
                    mime_type=None,
                    snapshot_path=Path("C:/tmp/file_b.snapshot.plain"),
                    blob_staging_path=shared_blob_path,
                ),
            ],
        )

        with self.assertRaisesRegex(ValueError, "content hash mismatch for blob_id blob_shared"):
            build_blob_upload_plan(
                inconsistent_snapshot_table,
                missing_blob_ids=["blob_shared"],
            )

    def test_build_create_commit_request_payload_uses_snapshot_table_blob_refs(self) -> None:
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=0,
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=1770000018598,
            summary_hash="pending",
            files=[
                ManifestFileEntry(
                    file_id="file_a",
                    path="Notes/A.md",
                    type="note",
                    content_hash="sha256:a",
                    blob_id="blob_shared",
                    size=16,
                    mtime=1770000018594,
                ),
                ManifestFileEntry(
                    file_id="file_b",
                    path="Notes/B.md",
                    type="note",
                    content_hash="sha256:b",
                    blob_id="blob_unique",
                    size=8,
                    mtime=1770000018595,
                    mime_type="text/markdown",
                ),
            ],
            tombstones=[],
        )
        submission = CommitSubmissionBundle(
            manifest=manifest,
            intent_manifest_hash="sha256:intent",
            journal=CommitIntentJournalRecord(
                vault_id="vault_pkb_001",
                commit_intent_id="intent_1",
                intent_manifest_hash="sha256:intent",
                base_revision=7,
                created_by_device="desktop-shanghai",
                status="submitted",
                intent_delete_seq_upper_bound=5,
                created_at=1770000018598,
                updated_at=1770000018600,
            ),
            state=VaultStateRecord(
                vault_id="vault_pkb_001",
                last_applied_revision=7,
                remote_head_revision=7,
                acked_revision=7,
                pending_ack_to_server=[],
                commit_in_progress=True,
                last_manifest_summary="sha256:head7",
                last_manifest_summary_status="valid",
                local_delete_sequence=5,
            ),
        )
        snapshot_table = CommitSnapshotTable(
            vault_id="vault_pkb_001",
            base_revision=7,
            created_at=1770000018598,
            entries=[
                CommitSnapshotEntry(
                    file_id="file_b",
                    path="Notes/B.md",
                    type="note",
                    content_hash="sha256:b",
                    blob_id="blob_unique",
                    plaintext_size=8,
                    encrypted_size=24,
                    mtime=1770000018595,
                    mime_type="text/markdown",
                    snapshot_path=Path("C:/tmp/file_b.snapshot.plain"),
                    blob_staging_path=Path("C:/tmp/blob_unique.blob.staging"),
                ),
                CommitSnapshotEntry(
                    file_id="file_a",
                    path="Notes/A.md",
                    type="note",
                    content_hash="sha256:a",
                    blob_id="blob_shared",
                    plaintext_size=16,
                    encrypted_size=32,
                    mtime=1770000018594,
                    mime_type=None,
                    snapshot_path=Path("C:/tmp/file_a.snapshot.plain"),
                    blob_staging_path=Path("C:/tmp/blob_shared.blob.staging"),
                ),
            ],
        )

        payload = build_create_commit_request_payload(
            submission,
            snapshot_table=snapshot_table,
        )

        self.assertEqual(
            payload,
            CreateCommitRequestPayload(
                commit_intent_id="intent_1",
                base_revision=7,
                created_by_device="desktop-shanghai",
                intent_manifest_hash="sha256:intent",
                intent_delete_seq_upper_bound=5,
                manifest=manifest,
                blob_refs=[
                    CreateCommitBlobRef(blob_id="blob_shared", file_id="file_a"),
                    CreateCommitBlobRef(blob_id="blob_unique", file_id="file_b"),
                ],
            ),
        )

    def test_build_commit_network_plan_combines_commit_request_and_missing_uploads(self) -> None:
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=0,
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=1770000018601,
            summary_hash="pending",
            files=[
                ManifestFileEntry(
                    file_id="file_a",
                    path="Notes/A.md",
                    type="note",
                    content_hash="sha256:a",
                    blob_id="blob_shared",
                    size=16,
                    mtime=1770000018596,
                ),
                ManifestFileEntry(
                    file_id="file_b",
                    path="Notes/B.md",
                    type="note",
                    content_hash="sha256:b",
                    blob_id="blob_unique",
                    size=8,
                    mtime=1770000018597,
                ),
            ],
            tombstones=[],
        )
        submission = CommitSubmissionBundle(
            manifest=manifest,
            intent_manifest_hash="sha256:intent",
            journal=CommitIntentJournalRecord(
                vault_id="vault_pkb_001",
                commit_intent_id="intent_2",
                intent_manifest_hash="sha256:intent",
                base_revision=7,
                created_by_device="desktop-shanghai",
                status="submitted",
                intent_delete_seq_upper_bound=5,
                created_at=1770000018601,
                updated_at=1770000018602,
            ),
            state=VaultStateRecord(
                vault_id="vault_pkb_001",
                last_applied_revision=7,
                remote_head_revision=7,
                acked_revision=7,
                pending_ack_to_server=[],
                commit_in_progress=True,
                last_manifest_summary="sha256:head7",
                last_manifest_summary_status="valid",
                local_delete_sequence=5,
            ),
        )
        snapshot_table = CommitSnapshotTable(
            vault_id="vault_pkb_001",
            base_revision=7,
            created_at=1770000018601,
            entries=[
                CommitSnapshotEntry(
                    file_id="file_a",
                    path="Notes/A.md",
                    type="note",
                    content_hash="sha256:a",
                    blob_id="blob_shared",
                    plaintext_size=16,
                    encrypted_size=32,
                    mtime=1770000018596,
                    mime_type=None,
                    snapshot_path=Path("C:/tmp/file_a.snapshot.plain"),
                    blob_staging_path=Path("C:/tmp/blob_shared.blob.staging"),
                ),
                CommitSnapshotEntry(
                    file_id="file_b",
                    path="Notes/B.md",
                    type="note",
                    content_hash="sha256:b",
                    blob_id="blob_unique",
                    plaintext_size=8,
                    encrypted_size=24,
                    mtime=1770000018597,
                    mime_type=None,
                    snapshot_path=Path("C:/tmp/file_b.snapshot.plain"),
                    blob_staging_path=Path("C:/tmp/blob_unique.blob.staging"),
                ),
            ],
        )
        blob_check = BlobCheckResult(
            requested_blob_ids=["blob_shared", "blob_unique"],
            existing_blob_ids=["blob_unique"],
            missing_blob_ids=["blob_shared"],
        )

        network_plan = build_commit_network_plan(
            submission,
            snapshot_table=snapshot_table,
            blob_check=blob_check,
        )

        self.assertEqual(
            network_plan,
            CommitNetworkPlan(
                request=CreateCommitRequestPayload(
                    commit_intent_id="intent_2",
                    base_revision=7,
                    created_by_device="desktop-shanghai",
                    intent_manifest_hash="sha256:intent",
                    intent_delete_seq_upper_bound=5,
                    manifest=manifest,
                    blob_refs=[
                        CreateCommitBlobRef(blob_id="blob_shared", file_id="file_a"),
                        CreateCommitBlobRef(blob_id="blob_unique", file_id="file_b"),
                    ],
                ),
                blob_check=blob_check,
                blob_uploads=BlobUploadPlan(
                    vault_id="vault_pkb_001",
                    entries=[
                        BlobUploadPlanEntry(
                            blob_id="blob_shared",
                            content_hash="sha256:a",
                            encrypted_size=32,
                            blob_staging_path=Path("C:/tmp/blob_shared.blob.staging"),
                            file_ids=["file_a"],
                        )
                    ],
                ),
            ),
        )

        with self.assertRaisesRegex(ValueError, "blob check result does not match snapshot table blob_ids"):
            build_commit_network_plan(
                submission,
                snapshot_table=snapshot_table,
                blob_check=BlobCheckResult(
                    requested_blob_ids=["blob_shared"],
                    existing_blob_ids=[],
                    missing_blob_ids=["blob_shared"],
                ),
            )

    def test_cleanup_commit_staging_artifacts_removes_only_snapshot_and_blob_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            staging_root = root / ".noteapp" / "staging"
            staging_root.mkdir(parents=True, exist_ok=True)
            snapshot_file = staging_root / "file_live.snapshot.plain"
            blob_file = staging_root / "blob_live.blob.staging"
            ignored_file = staging_root / "keep.txt"
            snapshot_file.write_text("snapshot", encoding="utf-8")
            blob_file.write_text("blob", encoding="utf-8")
            ignored_file.write_text("keep", encoding="utf-8")

            removed = cleanup_commit_staging_artifacts(root)

            self.assertEqual(removed, [blob_file, snapshot_file])
            self.assertFalse(snapshot_file.exists())
            self.assertFalse(blob_file.exists())
            self.assertTrue(ignored_file.exists())

    def test_abort_drifted_commit_snapshot_cleans_prepared_state_and_staging(self) -> None:
        baseline = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018600,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018590,
                    content_hash="sha256:live",
                    meta={
                        "blob_id": "blob_live",
                        "size": 128,
                        "mtime": 1770000018580,
                    },
                )
            ],
        )
        drifted = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018601,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018591,
                    content_hash="sha256:changed",
                    meta={
                        "blob_id": "blob_live",
                        "size": 128,
                        "mtime": 1770000018581,
                    },
                )
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            staging_root = root / ".noteapp" / "staging"
            staging_root.mkdir(parents=True, exist_ok=True)
            snapshot_file = staging_root / "file_live.snapshot.plain"
            blob_file = staging_root / "blob_live.blob.staging"
            snapshot_file.write_text("snapshot", encoding="utf-8")
            blob_file.write_text("blob", encoding="utf-8")
            db_path = root / "state.sqlite3"

            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                state = VaultStateRecord(
                    vault_id="vault_pkb_001",
                    last_applied_revision=7,
                    remote_head_revision=7,
                    acked_revision=7,
                    pending_ack_to_server=[],
                    commit_in_progress=False,
                    last_manifest_summary="sha256:head7",
                    last_manifest_summary_status="valid",
                    local_delete_sequence=2,
                )
                upsert_vault_state(connection, state)
                frozen = prepare_frozen_commit_intent(
                    connection,
                    state=state,
                    document=baseline,
                    commit_intent_id="intent_prepared",
                    created_by_device="desktop-shanghai",
                    created_at=1770000018602,
                )

                aborted = abort_drifted_commit_snapshot(
                    connection,
                    vault_id="vault_pkb_001",
                    vault_root=root,
                    snapshot_plan=frozen.snapshot_plan,
                    document=drifted,
                )

                self.assertEqual(
                    aborted,
                    SnapshotDriftAbortResult(
                        state=aborted.state,
                        drifted_file_ids=["file_live"],
                        removed_staging_paths=[blob_file, snapshot_file],
                    ),
                )
                self.assertFalse(aborted.state.commit_in_progress)
                self.assertIsNone(load_commit_intent_journal(connection, "vault_pkb_001"))
                self.assertFalse(snapshot_file.exists())
                self.assertFalse(blob_file.exists())

    def test_abort_drifted_commit_snapshot_rejects_matching_snapshot_plan(self) -> None:
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018700,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018690,
                    content_hash="sha256:live",
                    meta={
                        "blob_id": "blob_live",
                        "size": 128,
                        "mtime": 1770000018680,
                    },
                )
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "state.sqlite3"
            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                state = VaultStateRecord(
                    vault_id="vault_pkb_001",
                    last_applied_revision=7,
                    remote_head_revision=7,
                    acked_revision=7,
                    pending_ack_to_server=[],
                    commit_in_progress=False,
                    last_manifest_summary="sha256:head7",
                    last_manifest_summary_status="valid",
                    local_delete_sequence=2,
                )
                upsert_vault_state(connection, state)
                frozen = prepare_frozen_commit_intent(
                    connection,
                    state=state,
                    document=document,
                    commit_intent_id="intent_prepared",
                    created_by_device="desktop-shanghai",
                    created_at=1770000018701,
                )

                with self.assertRaisesRegex(ValueError, "content snapshot plan still matches"):
                    abort_drifted_commit_snapshot(
                        connection,
                        vault_id="vault_pkb_001",
                        vault_root=root,
                        snapshot_plan=frozen.snapshot_plan,
                        document=document,
                    )

    def test_submit_prepared_commit_promotes_journal_to_submitted(self) -> None:
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018310,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018309,
                    content_hash="sha256:live",
                    meta={
                        "blob_id": "blob_live",
                        "size": 128,
                        "mtime": 1770000018308,
                    },
                )
            ],
        )
        tombstones = [
            TombstoneRecord(
                file_id="file_deleted",
                deleted_revision=None,
                deleted_at=1770000018307,
                local_delete_seq=2,
                last_known_path="Notes/Deleted.md",
            )
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with closing(open_database(Path(tmpdir) / "state.sqlite3")) as connection:
                bootstrap_database(connection)
                state = VaultStateRecord(
                    vault_id="vault_pkb_001",
                    last_applied_revision=7,
                    remote_head_revision=7,
                    acked_revision=7,
                    pending_ack_to_server=[],
                    commit_in_progress=False,
                    last_manifest_summary="sha256:head7",
                    last_manifest_summary_status="valid",
                    local_delete_sequence=2,
                )
                upsert_vault_state(connection, state)
                prepare_commit_intent(
                    connection,
                    state=state,
                    commit_intent_id="intent_prepared",
                    created_by_device="desktop-shanghai",
                    created_at=1770000018300,
                )

                bundle = submit_prepared_commit(
                    connection,
                    vault_id="vault_pkb_001",
                    document=document,
                    tombstones=tombstones,
                    submitted_at=1770000018311,
                )

                stored_journal = load_commit_intent_journal(connection, "vault_pkb_001")
                self.assertEqual(bundle.journal.status, "submitted")
                self.assertEqual(bundle.intent_manifest_hash, compute_intent_manifest_hash(bundle.manifest))
                self.assertEqual(bundle.manifest.base_revision, 7)
                self.assertIsNotNone(stored_journal)
                self.assertEqual(stored_journal, bundle.journal)

    def test_submit_prepared_commit_uses_snapshot_plan_for_frozen_manifest_fields(self) -> None:
        baseline = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018310,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018309,
                    content_hash="sha256:live",
                    meta={
                        "blob_id": "blob_live",
                        "size": 128,
                        "mtime": 1770000018308,
                        "mime_type": "text/markdown",
                    },
                )
            ],
        )
        current = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018311,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018310,
                    content_hash="sha256:live",
                    meta={
                        "blob_id": "blob_live_next",
                        "size": 512,
                        "mtime": 1770000018308,
                        "mime_type": "text/x-markdown",
                    },
                )
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with closing(open_database(Path(tmpdir) / "state.sqlite3")) as connection:
                bootstrap_database(connection)
                state = VaultStateRecord(
                    vault_id="vault_pkb_001",
                    last_applied_revision=7,
                    remote_head_revision=7,
                    acked_revision=7,
                    pending_ack_to_server=[],
                    commit_in_progress=False,
                    last_manifest_summary="sha256:head7",
                    last_manifest_summary_status="valid",
                    local_delete_sequence=2,
                )
                upsert_vault_state(connection, state)
                frozen = prepare_frozen_commit_intent(
                    connection,
                    state=state,
                    document=baseline,
                    commit_intent_id="intent_prepared",
                    created_by_device="desktop-shanghai",
                    created_at=1770000018312,
                )

                bundle = submit_prepared_commit(
                    connection,
                    vault_id="vault_pkb_001",
                    document=current,
                    tombstones=[],
                    submitted_at=1770000018313,
                    snapshot_plan=frozen.snapshot_plan,
                )

                self.assertEqual(bundle.manifest.files[0].blob_id, "blob_live")
                self.assertEqual(bundle.manifest.files[0].size, 128)
                self.assertEqual(bundle.manifest.files[0].mime_type, "text/markdown")

    def test_prepare_commit_submission_persists_submitted_journal_and_state(self) -> None:
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018300,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018290,
                    content_hash="sha256:live",
                    meta={
                        "blob_id": "blob_live",
                        "size": 128,
                        "mtime": 1770000018280,
                    },
                )
            ],
        )
        tombstones = [
            TombstoneRecord(
                file_id="file_deleted",
                deleted_revision=None,
                deleted_at=1770000018270,
                local_delete_seq=2,
                last_known_path="Notes/Deleted.md",
            )
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with closing(open_database(Path(tmpdir) / "state.sqlite3")) as connection:
                bootstrap_database(connection)
                state = initialize_vault_state(connection, "vault_pkb_001")
                state = VaultStateRecord(
                    vault_id=state.vault_id,
                    last_applied_revision=7,
                    remote_head_revision=7,
                    acked_revision=7,
                    pending_ack_to_server=[],
                    commit_in_progress=False,
                    last_manifest_summary="sha256:head7",
                    last_manifest_summary_status="valid",
                    local_delete_sequence=2,
                )
                upsert_vault_state(connection, state)

                bundle = prepare_commit_submission(
                    connection,
                    state=state,
                    document=document,
                    tombstones=tombstones,
                    commit_intent_id="intent_1",
                    created_by_device="desktop-shanghai",
                    created_at=1770000018301,
                )

                stored_journal = load_commit_intent_journal(connection, "vault_pkb_001")
                stored_state = load_vault_state(connection, "vault_pkb_001")

                self.assertEqual(bundle.intent_manifest_hash, compute_intent_manifest_hash(bundle.manifest))
                self.assertEqual(bundle.journal.status, "submitted")
                self.assertEqual(bundle.journal.intent_delete_seq_upper_bound, 2)
                self.assertIsNotNone(bundle.snapshot_plan)
                self.assertEqual([item.file_id for item in bundle.snapshot_plan.files], ["file_live"])
                self.assertIsNotNone(stored_journal)
                self.assertEqual(stored_journal, bundle.journal)
                self.assertIsNotNone(stored_state)
                self.assertTrue(stored_state.commit_in_progress)
                self.assertEqual(stored_state, bundle.state)

    def test_prepare_commit_submission_rejects_blocked_state_and_active_journal(self) -> None:
        document = FileMapDocument(
            vault_id="vault_pkb_001",
            updated_at=1770000018400,
            files=[
                FileRecord(
                    file_id="file_live",
                    path="Notes/Live.md",
                    type="note",
                    status="active",
                    updated_at=1770000018390,
                    content_hash="sha256:live",
                    meta={
                        "blob_id": "blob_live",
                        "size": 128,
                        "mtime": 1770000018380,
                    },
                )
            ],
        )
        stale_state = VaultStateRecord(
            vault_id="vault_pkb_001",
            last_applied_revision=7,
            remote_head_revision=7,
            acked_revision=7,
            pending_ack_to_server=[],
            commit_in_progress=False,
            last_manifest_summary=None,
            last_manifest_summary_status="stale",
            local_delete_sequence=2,
        )
        valid_state = VaultStateRecord(
            vault_id="vault_pkb_001",
            last_applied_revision=7,
            remote_head_revision=7,
            acked_revision=7,
            pending_ack_to_server=[],
            commit_in_progress=False,
            last_manifest_summary="sha256:head7",
            last_manifest_summary_status="valid",
            local_delete_sequence=2,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with closing(open_database(Path(tmpdir) / "stale.sqlite3")) as connection:
                bootstrap_database(connection)
                upsert_vault_state(connection, stale_state)

                with self.assertRaisesRegex(ValueError, "not eligible"):
                    prepare_commit_submission(
                        connection,
                        state=stale_state,
                        document=document,
                        tombstones=[],
                        commit_intent_id="intent_stale",
                        created_by_device="desktop-shanghai",
                        created_at=1770000018401,
                    )

            with closing(open_database(Path(tmpdir) / "journal.sqlite3")) as connection:
                bootstrap_database(connection)
                upsert_vault_state(connection, valid_state)
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_existing",
                        intent_manifest_hash="sha256:intent_existing",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=2,
                        created_at=1770000018400,
                        updated_at=1770000018400,
                    ),
                )

                with self.assertRaisesRegex(ValueError, "not eligible"):
                    prepare_commit_submission(
                        connection,
                        state=valid_state,
                        document=document,
                        tombstones=[],
                        commit_intent_id="intent_next",
                        created_by_device="desktop-shanghai",
                        created_at=1770000018402,
                    )

    def test_finalize_commit_manifest_sets_committed_revision_and_summary(self) -> None:
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=0,
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=1770000018500,
            summary_hash="pending",
            files=[],
            tombstones=[],
        )

        finalized = finalize_commit_manifest(manifest, committed_revision=8)

        self.assertEqual(finalized.revision, 8)
        self.assertEqual(finalized.summary_hash, compute_manifest_summary_hash(finalized))
        self.assertNotEqual(finalized.summary_hash, "pending")

    def test_finalize_commit_submission_rewrites_ledger_and_clears_journal(self) -> None:
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=0,
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=1770000018600,
            summary_hash="pending",
            files=[],
            tombstones=[],
        )
        tombstones = [
            TombstoneRecord(
                file_id="file_hit",
                deleted_revision=None,
                deleted_at=1770000018500,
                local_delete_seq=2,
                last_known_path="Notes/Hit.md",
            ),
            TombstoneRecord(
                file_id="file_skip",
                deleted_revision=None,
                deleted_at=1770000018510,
                local_delete_seq=5,
                last_known_path="Notes/Skip.md",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "tombstone-ledger.jsonl"
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
                        pending_ack_to_server=[4],
                        commit_in_progress=True,
                        last_manifest_summary="sha256:head7",
                        last_manifest_summary_status="valid",
                        local_delete_sequence=5,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_1",
                        intent_manifest_hash="sha256:intent_1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=2,
                        created_at=1770000018600,
                        updated_at=1770000018600,
                    ),
                )

                finalized = finalize_commit_submission(
                    connection,
                    ledger_path=ledger_path,
                    manifest=manifest,
                    local_tombstones=tombstones,
                    committed_revision=8,
                )

                self.assertEqual(finalized.manifest.revision, 8)
                self.assertEqual(finalized.manifest.summary_hash, compute_manifest_summary_hash(finalized.manifest))
                self.assertEqual(finalized.state.last_applied_revision, 8)
                self.assertEqual(finalized.state.remote_head_revision, 8)
                self.assertEqual(finalized.state.acked_revision, 8)
                self.assertEqual(finalized.state.pending_ack_to_server, [4])
                self.assertFalse(finalized.state.commit_in_progress)
                self.assertEqual(finalized.state.last_manifest_summary, finalized.manifest.summary_hash)
                self.assertEqual(
                    [(item.file_id, item.deleted_revision) for item in load_tombstone_ledger(ledger_path)],
                    [("file_hit", 8), ("file_skip", None)],
                )
                self.assertIsNone(load_commit_intent_journal(connection, "vault_pkb_001"))

    def test_finalize_commit_submission_cleanup_removes_commit_staging_artifacts_after_success(self) -> None:
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=0,
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=1770000018650,
            summary_hash="pending",
            files=[],
            tombstones=[],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            staging_root = root / ".noteapp" / "staging"
            staging_root.mkdir(parents=True, exist_ok=True)
            snapshot_file = staging_root / "file_live.snapshot.plain"
            blob_file = staging_root / "blob_live.blob.staging"
            snapshot_file.write_text("snapshot", encoding="utf-8")
            blob_file.write_text("blob", encoding="utf-8")
            ledger_path = root / "tombstone-ledger.jsonl"
            db_path = root / "state.sqlite3"

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
                        local_delete_sequence=2,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_1",
                        intent_manifest_hash="sha256:intent_1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=2,
                        created_at=1770000018650,
                        updated_at=1770000018650,
                    ),
                )

                cleaned = finalize_commit_submission_cleanup(
                    connection,
                    vault_root=root,
                    ledger_path=ledger_path,
                    manifest=manifest,
                    local_tombstones=[],
                    committed_revision=8,
                )

                self.assertEqual(
                    cleaned,
                    CommitFinalizeCleanupResult(
                        finalized=cleaned.finalized,
                        removed_staging_paths=[blob_file, snapshot_file],
                    ),
                )
                self.assertEqual(cleaned.finalized.state.last_applied_revision, 8)
                self.assertFalse(snapshot_file.exists())
                self.assertFalse(blob_file.exists())
                self.assertIsNone(load_commit_intent_journal(connection, "vault_pkb_001"))

    def test_finalize_commit_submission_requires_submitted_journal(self) -> None:
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=0,
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=1770000018700,
            summary_hash="pending",
            files=[],
            tombstones=[],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "tombstone-ledger.jsonl"
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
                        local_delete_sequence=0,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_prepared",
                        intent_manifest_hash="sha256:intent_prepared",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="prepared",
                        intent_delete_seq_upper_bound=None,
                        created_at=1770000018700,
                        updated_at=1770000018700,
                    ),
                )

                with self.assertRaisesRegex(ValueError, "submitted journal"):
                    finalize_commit_submission(
                        connection,
                        ledger_path=ledger_path,
                        manifest=manifest,
                        local_tombstones=[],
                        committed_revision=8,
                    )

    def test_cleanup_failed_commit_submission_clears_prepared_journal(self) -> None:
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
                        local_delete_sequence=0,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_prepared",
                        intent_manifest_hash="sha256:intent_prepared",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="prepared",
                        intent_delete_seq_upper_bound=None,
                        created_at=1770000018800,
                        updated_at=1770000018800,
                    ),
                )

                recovered = cleanup_failed_commit_submission(
                    connection,
                    "vault_pkb_001",
                    normalized_at=1770000018801,
                )

                self.assertFalse(recovered.commit_in_progress)
                self.assertIsNone(load_commit_intent_journal(connection, "vault_pkb_001"))

    def test_cleanup_failed_commit_submission_clears_submitted_journal(self) -> None:
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
                        pending_ack_to_server=[4],
                        commit_in_progress=True,
                        last_manifest_summary="sha256:head7",
                        last_manifest_summary_status="valid",
                        local_delete_sequence=2,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_submitted",
                        intent_manifest_hash="sha256:intent_submitted",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=2,
                        created_at=1770000018900,
                        updated_at=1770000018900,
                    ),
                )

                recovered = cleanup_failed_commit_submission(
                    connection,
                    "vault_pkb_001",
                    normalized_at=1770000018901,
                )

                self.assertFalse(recovered.commit_in_progress)
                self.assertEqual(recovered.pending_ack_to_server, [4])
                self.assertIsNone(load_commit_intent_journal(connection, "vault_pkb_001"))

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
        self.assertFalse(requires_full_pull(record, observed_head_revision=1))

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

    def test_apply_orphaned_commit_lock_recovery_releases_commit_lock(self) -> None:
        state = VaultStateRecord(
            vault_id="vault_pkb_001",
            last_applied_revision=7,
            remote_head_revision=7,
            acked_revision=7,
            pending_ack_to_server=[4],
            commit_in_progress=True,
            last_manifest_summary="sha256:head7",
            last_manifest_summary_status="valid",
            local_delete_sequence=2,
        )

        recovered = apply_orphaned_commit_lock_recovery(state)

        self.assertFalse(recovered.commit_in_progress)
        self.assertEqual(recovered.pending_ack_to_server, [4])
        self.assertEqual(recovered.last_manifest_summary, "sha256:head7")

    def test_recover_orphaned_commit_lock_requires_no_active_journal(self) -> None:
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
                        local_delete_sequence=0,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_existing",
                        intent_manifest_hash="sha256:intent_existing",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=1,
                        created_at=1770000019300,
                        updated_at=1770000019300,
                    ),
                )

                with self.assertRaisesRegex(ValueError, "no active journal"):
                    recover_orphaned_commit_lock(connection, "vault_pkb_001")

    def test_recover_orphaned_commit_lock_releases_lock_when_journal_missing(self) -> None:
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
                        pending_ack_to_server=[4],
                        commit_in_progress=True,
                        last_manifest_summary="sha256:head7",
                        last_manifest_summary_status="valid",
                        local_delete_sequence=2,
                    ),
                )

                recovered = recover_orphaned_commit_lock(connection, "vault_pkb_001")

                self.assertFalse(recovered.commit_in_progress)
                self.assertEqual(recovered.pending_ack_to_server, [4])
                self.assertIsNone(load_commit_intent_journal(connection, "vault_pkb_001"))

    def test_isolate_staging_orphans_moves_all_staging_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "vault"
            initialize_vault(root, vault_id="vault_pkb_001", now_ms=1770000019400)
            direct = root / ".noteapp" / "staging" / "direct.snapshot.plain"
            nested = root / ".noteapp" / "staging" / "nested" / "blob.staging"
            nested.parent.mkdir(parents=True, exist_ok=True)
            direct.write_text("direct", encoding="utf-8")
            nested.write_text("nested", encoding="utf-8")

            moved = isolate_staging_orphans(root)

            self.assertEqual(len(moved), 2)
            self.assertFalse(direct.exists())
            self.assertFalse(nested.exists())
            self.assertTrue(all(path.exists() for path in moved))
            self.assertTrue(all(path.parent.name == "staging-orphans" for path in moved))

    def test_recover_orphaned_commit_session_moves_staging_files_and_releases_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "vault"
            initialize_vault(root, vault_id="vault_pkb_001", now_ms=1770000019500)
            staging_file = root / ".noteapp" / "staging" / "leftover.blob.staging"
            staging_file.write_text("payload", encoding="utf-8")
            db_path = root / ".noteapp" / "state.sqlite3"

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
                        local_delete_sequence=0,
                    ),
                )

                recovered = recover_orphaned_commit_session(
                    connection,
                    vault_id="vault_pkb_001",
                    vault_root=root,
                )

                self.assertFalse(recovered.state.commit_in_progress)
                self.assertEqual(len(recovered.moved_staging_paths), 1)
                self.assertFalse(staging_file.exists())
                self.assertTrue(recovered.moved_staging_paths[0].exists())

    def test_plan_commit_recovery_classifies_idle_prepared_submitted_and_orphaned(self) -> None:
        idle_state = VaultStateRecord(
            vault_id="vault_pkb_001",
            last_applied_revision=7,
            remote_head_revision=7,
            acked_revision=7,
            pending_ack_to_server=[],
            commit_in_progress=False,
            last_manifest_summary="sha256:head7",
            last_manifest_summary_status="valid",
            local_delete_sequence=0,
        )
        orphaned_state = VaultStateRecord(
            vault_id="vault_pkb_001",
            last_applied_revision=7,
            remote_head_revision=7,
            acked_revision=7,
            pending_ack_to_server=[],
            commit_in_progress=True,
            last_manifest_summary="sha256:head7",
            last_manifest_summary_status="valid",
            local_delete_sequence=0,
        )
        prepared = CommitIntentJournalRecord(
            vault_id="vault_pkb_001",
            commit_intent_id="intent_prepared",
            intent_manifest_hash="pending",
            base_revision=7,
            created_by_device="desktop-shanghai",
            status="prepared",
            intent_delete_seq_upper_bound=None,
            created_at=1770000019600,
            updated_at=1770000019600,
        )
        submitted = CommitIntentJournalRecord(
            vault_id="vault_pkb_001",
            commit_intent_id="intent_submitted",
            intent_manifest_hash="sha256:intent_submitted",
            base_revision=7,
            created_by_device="desktop-shanghai",
            status="submitted",
            intent_delete_seq_upper_bound=1,
            created_at=1770000019601,
            updated_at=1770000019601,
        )
        acknowledged = CommitIntentJournalRecord(
            vault_id="vault_pkb_001",
            commit_intent_id="intent_ack",
            intent_manifest_hash="sha256:intent_ack",
            base_revision=7,
            created_by_device="desktop-shanghai",
            status="acknowledged",
            intent_delete_seq_upper_bound=1,
            created_at=1770000019602,
            updated_at=1770000019602,
        )

        self.assertEqual(plan_commit_recovery(idle_state, journal=None), CommitRecoveryPlan("idle", False, False))
        self.assertEqual(
            plan_commit_recovery(orphaned_state, journal=None),
            CommitRecoveryPlan("orphaned_lock", True, False),
        )
        self.assertEqual(
            plan_commit_recovery(idle_state, journal=prepared),
            CommitRecoveryPlan("prepared_cleanup", True, False),
        )
        self.assertEqual(
            plan_commit_recovery(idle_state, journal=submitted),
            CommitRecoveryPlan("submitted_confirmation", False, True),
        )
        self.assertEqual(
            plan_commit_recovery(idle_state, journal=acknowledged),
            CommitRecoveryPlan("submitted_confirmation", False, True),
        )

    def test_recover_local_commit_state_cleans_prepared_journal_and_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "vault"
            initialize_vault(root, vault_id="vault_pkb_001", now_ms=1770000019700)
            staging_file = root / ".noteapp" / "staging" / "prepared.snapshot.plain"
            staging_file.write_text("payload", encoding="utf-8")
            db_path = root / ".noteapp" / "state.sqlite3"

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
                        local_delete_sequence=0,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_prepared",
                        intent_manifest_hash="pending",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="prepared",
                        intent_delete_seq_upper_bound=None,
                        created_at=1770000019701,
                        updated_at=1770000019701,
                    ),
                )

                recovered = recover_local_commit_state(
                    connection,
                    vault_id="vault_pkb_001",
                    vault_root=root,
                )

                self.assertEqual(recovered.plan.mode, "prepared_cleanup")
                self.assertFalse(recovered.plan.requires_remote_confirmation)
                self.assertFalse(recovered.state.commit_in_progress)
                self.assertEqual(len(recovered.moved_staging_paths), 1)
                self.assertIsNone(load_commit_intent_journal(connection, "vault_pkb_001"))

    def test_recover_local_commit_state_rejects_submitted_recovery_without_remote_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "vault"
            initialize_vault(root, vault_id="vault_pkb_001", now_ms=1770000019800)
            db_path = root / ".noteapp" / "state.sqlite3"

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
                        local_delete_sequence=0,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_submitted",
                        intent_manifest_hash="sha256:intent_submitted",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=1,
                        created_at=1770000019801,
                        updated_at=1770000019801,
                    ),
                )

                with self.assertRaisesRegex(ValueError, "remote confirmation"):
                    recover_local_commit_state(
                        connection,
                        vault_id="vault_pkb_001",
                        vault_root=root,
                    )

    def test_plan_submitted_confirmation_prefers_head_match_before_scan(self) -> None:
        journal = CommitIntentJournalRecord(
            vault_id="vault_pkb_001",
            commit_intent_id="intent_1",
            intent_manifest_hash="sha256:intent_1",
            base_revision=7,
            created_by_device="desktop-shanghai",
            status="submitted",
            intent_delete_seq_upper_bound=2,
            created_at=1770000019900,
            updated_at=1770000019900,
        )

        plan = plan_submitted_confirmation(
            journal,
            observed_head_revision=9,
            head_commit_intent_id="intent_1",
        )

        self.assertEqual(
            plan,
            SubmittedConfirmationPlan(
                mode="head_match",
                observed_head_revision=9,
                matched_revision=9,
                scan_from_revision=None,
                scan_to_revision=None,
            ),
        )

    def test_plan_submitted_confirmation_scans_when_head_intent_differs(self) -> None:
        journal = CommitIntentJournalRecord(
            vault_id="vault_pkb_001",
            commit_intent_id="intent_1",
            intent_manifest_hash="sha256:intent_1",
            base_revision=7,
            created_by_device="desktop-shanghai",
            status="submitted",
            intent_delete_seq_upper_bound=2,
            created_at=1770000019901,
            updated_at=1770000019901,
        )

        plan = plan_submitted_confirmation(
            journal,
            observed_head_revision=10,
            head_commit_intent_id="intent_other",
        )

        self.assertEqual(
            plan,
            SubmittedConfirmationPlan(
                mode="scan_range",
                observed_head_revision=10,
                matched_revision=None,
                scan_from_revision=8,
                scan_to_revision=10,
            ),
        )

    def test_plan_submitted_confirmation_returns_miss_when_head_not_advanced(self) -> None:
        journal = CommitIntentJournalRecord(
            vault_id="vault_pkb_001",
            commit_intent_id="intent_1",
            intent_manifest_hash="sha256:intent_1",
            base_revision=7,
            created_by_device="desktop-shanghai",
            status="submitted",
            intent_delete_seq_upper_bound=2,
            created_at=1770000019902,
            updated_at=1770000019902,
        )

        plan = plan_submitted_confirmation(
            journal,
            observed_head_revision=7,
            head_commit_intent_id="intent_other",
        )

        self.assertEqual(
            plan,
            SubmittedConfirmationPlan(
                mode="miss",
                observed_head_revision=7,
                matched_revision=None,
                scan_from_revision=None,
                scan_to_revision=None,
            ),
        )

    def test_find_matching_revision_metadata_returns_first_matching_intent(self) -> None:
        revisions = [
            RevisionMetadata(
                revision=8,
                commit_intent_id="intent_other",
                intent_manifest_hash="sha256:other",
                created_by_device="mobile-hangzhou",
                created_at=1770000019903,
            ),
            RevisionMetadata(
                revision=9,
                commit_intent_id="intent_1",
                intent_manifest_hash="sha256:intent_1",
                created_by_device="desktop-shanghai",
                created_at=1770000019904,
            ),
        ]

        matched = find_matching_revision_metadata(
            revisions,
            commit_intent_id="intent_1",
        )

        self.assertIsNotNone(matched)
        self.assertEqual(matched.revision, 9)
        self.assertIsNone(
            find_matching_revision_metadata(
                revisions,
                commit_intent_id="intent_missing",
            )
        )

    def test_resolve_submitted_confirmation_uses_scan_match_when_present(self) -> None:
        plan = SubmittedConfirmationPlan(
            mode="scan_range",
            observed_head_revision=10,
            matched_revision=None,
            scan_from_revision=8,
            scan_to_revision=10,
        )
        revisions = [
            RevisionMetadata(
                revision=8,
                commit_intent_id="intent_other",
                intent_manifest_hash="sha256:other",
                created_by_device="mobile-hangzhou",
                created_at=1770000019905,
            ),
            RevisionMetadata(
                revision=9,
                commit_intent_id="intent_1",
                intent_manifest_hash="sha256:intent_1",
                created_by_device="desktop-shanghai",
                created_at=1770000019906,
            ),
        ]

        resolved = resolve_submitted_confirmation(
            plan,
            commit_intent_id="intent_1",
            revisions=revisions,
        )

        self.assertEqual(
            resolved,
            SubmittedConfirmationResolution(
                plan=plan,
                matched_metadata=revisions[1],
            ),
        )

    def test_resolve_submitted_confirmation_returns_none_for_head_match_and_miss(self) -> None:
        head_match = SubmittedConfirmationPlan(
            mode="head_match",
            observed_head_revision=10,
            matched_revision=10,
            scan_from_revision=None,
            scan_to_revision=None,
        )
        miss = SubmittedConfirmationPlan(
            mode="miss",
            observed_head_revision=7,
            matched_revision=None,
            scan_from_revision=None,
            scan_to_revision=None,
        )

        self.assertEqual(
            resolve_submitted_confirmation(
                head_match,
                commit_intent_id="intent_1",
            ),
            SubmittedConfirmationResolution(
                plan=head_match,
                matched_metadata=None,
            ),
        )
        self.assertEqual(
            resolve_submitted_confirmation(
                miss,
                commit_intent_id="intent_1",
            ),
            SubmittedConfirmationResolution(
                plan=miss,
                matched_metadata=None,
            ),
        )

    def test_execute_submitted_commit_confirmation_head_match_recovers_with_manifest(self) -> None:
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=8,
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=1770000020000,
            summary_hash="placeholder",
            files=[],
            tombstones=[],
        )
        tombstones = [
            TombstoneRecord(
                file_id="file_hit",
                deleted_revision=None,
                deleted_at=1770000019990,
                local_delete_seq=2,
                last_known_path="Notes/Hit.md",
            )
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "tombstone-ledger.jsonl"
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
                        pending_ack_to_server=[4],
                        commit_in_progress=True,
                        last_manifest_summary="sha256:head7",
                        last_manifest_summary_status="valid",
                        local_delete_sequence=2,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_1",
                        intent_manifest_hash="sha256:intent_1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=2,
                        created_at=1770000019995,
                        updated_at=1770000019995,
                    ),
                )

                executed = execute_submitted_commit_confirmation(
                    connection,
                    ledger_path=ledger_path,
                    vault_id="vault_pkb_001",
                    local_tombstones=tombstones,
                    observed_head_revision=8,
                    head_commit_intent_id="intent_1",
                    normalized_at=1770000020001,
                    matched_manifest=manifest,
                )

                self.assertEqual(executed.plan.mode, "head_match")
                self.assertIsNone(executed.resolution.matched_metadata)
                self.assertEqual(executed.recovery.state.last_applied_revision, 8)
                self.assertEqual(executed.recovery.state.remote_head_revision, 8)
                self.assertEqual(executed.recovery.state.last_manifest_summary, compute_manifest_summary_hash(manifest))
                self.assertEqual(
                    [(item.file_id, item.deleted_revision) for item in load_tombstone_ledger(ledger_path)],
                    [("file_hit", 8)],
                )
                self.assertIsNone(load_commit_intent_journal(connection, "vault_pkb_001"))

    def test_execute_submitted_commit_confirmation_scan_match_uses_matched_revision(self) -> None:
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=9,
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=1770000020100,
            summary_hash="placeholder",
            files=[],
            tombstones=[],
        )
        revisions = [
            RevisionMetadata(
                revision=8,
                commit_intent_id="intent_other",
                intent_manifest_hash="sha256:other",
                created_by_device="mobile-hangzhou",
                created_at=1770000020090,
            ),
            RevisionMetadata(
                revision=9,
                commit_intent_id="intent_1",
                intent_manifest_hash="sha256:intent_1",
                created_by_device="desktop-shanghai",
                created_at=1770000020095,
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "tombstone-ledger.jsonl"
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
                        local_delete_sequence=0,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_1",
                        intent_manifest_hash="sha256:intent_1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=None,
                        created_at=1770000020088,
                        updated_at=1770000020088,
                    ),
                )

                executed = execute_submitted_commit_confirmation(
                    connection,
                    ledger_path=ledger_path,
                    vault_id="vault_pkb_001",
                    local_tombstones=[],
                    observed_head_revision=10,
                    head_commit_intent_id="intent_other",
                    normalized_at=1770000020101,
                    revisions=revisions,
                    matched_manifest=manifest,
                )

                self.assertEqual(executed.plan.mode, "scan_range")
                self.assertIsNotNone(executed.resolution.matched_metadata)
                self.assertEqual(executed.resolution.matched_metadata.revision, 9)
                self.assertEqual(executed.recovery.state.last_applied_revision, 9)
                self.assertEqual(executed.recovery.state.remote_head_revision, 10)
                self.assertFalse(executed.recovery.requires_full_pull)

    def test_execute_submitted_commit_confirmation_rejects_manifest_with_wrong_revision(self) -> None:
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=10,
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=1770000020150,
            summary_hash="placeholder",
            files=[],
            tombstones=[],
        )
        revisions = [
            RevisionMetadata(
                revision=9,
                commit_intent_id="intent_1",
                intent_manifest_hash="sha256:intent_1",
                created_by_device="desktop-shanghai",
                created_at=1770000020140,
            )
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "tombstone-ledger.jsonl"
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
                        local_delete_sequence=0,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_1",
                        intent_manifest_hash="sha256:intent_1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=None,
                        created_at=1770000020139,
                        updated_at=1770000020139,
                    ),
                )

                with self.assertRaisesRegex(
                    ValueError,
                    "matched_manifest revision does not match submitted confirmation target",
                ):
                    execute_submitted_commit_confirmation(
                        connection,
                        ledger_path=ledger_path,
                        vault_id="vault_pkb_001",
                        local_tombstones=[],
                        observed_head_revision=10,
                        head_commit_intent_id="intent_other",
                        normalized_at=1770000020151,
                        revisions=revisions,
                        matched_manifest=manifest,
                    )

    def test_execute_submitted_commit_confirmation_remote_state_rejects_scan_revision_outside_range(self) -> None:
        remote_state = SubmittedConfirmationRemoteState(
            observed_head_revision=10,
            head_commit_intent_id="intent_other",
            revisions=(
                RevisionMetadata(
                    revision=11,
                    commit_intent_id="intent_1",
                    intent_manifest_hash="sha256:intent_1",
                    created_by_device="desktop-shanghai",
                    created_at=1770000020170,
                ),
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "tombstone-ledger.jsonl"
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
                        local_delete_sequence=0,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_1",
                        intent_manifest_hash="sha256:intent_1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=None,
                        created_at=1770000020169,
                        updated_at=1770000020169,
                    ),
                )

                with self.assertRaisesRegex(
                    ValueError,
                    "revision metadata falls outside submitted confirmation scan range",
                ):
                    execute_submitted_commit_confirmation_remote_state(
                        connection,
                        ledger_path=ledger_path,
                        vault_id="vault_pkb_001",
                        local_tombstones=[],
                        normalized_at=1770000020171,
                        remote_state=remote_state,
                    )

    def test_execute_submitted_commit_confirmation_scan_miss_cleans_up_without_rewriting_tombstones(self) -> None:
        tombstones = [
            TombstoneRecord(
                file_id="file_pending",
                deleted_revision=None,
                deleted_at=1770000020190,
                local_delete_seq=2,
                last_known_path="Notes/Pending.md",
            )
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "tombstone-ledger.jsonl"
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
                        local_delete_sequence=2,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_1",
                        intent_manifest_hash="sha256:intent_1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=2,
                        created_at=1770000020195,
                        updated_at=1770000020195,
                    ),
                )

                executed = execute_submitted_commit_confirmation(
                    connection,
                    ledger_path=ledger_path,
                    vault_id="vault_pkb_001",
                    local_tombstones=tombstones,
                    observed_head_revision=10,
                    head_commit_intent_id="intent_other",
                    normalized_at=1770000020200,
                    revisions=[],
                )

                self.assertEqual(executed.plan.mode, "scan_range")
                self.assertIsNone(executed.resolution.matched_metadata)
                self.assertFalse(executed.recovery.state.commit_in_progress)
                self.assertEqual(
                    [(item.file_id, item.deleted_revision) for item in executed.recovery.tombstones],
                    [("file_pending", None)],
                )
                self.assertIsNone(load_commit_intent_journal(connection, "vault_pkb_001"))

    def test_resume_commit_recovery_returns_idle_local_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "vault"
            initialize_vault(root, vault_id="vault_pkb_001", now_ms=1770000020300)
            db_path = root / ".noteapp" / "state.sqlite3"

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
                        commit_in_progress=False,
                        last_manifest_summary="sha256:head7",
                        last_manifest_summary_status="valid",
                        local_delete_sequence=0,
                    ),
                )

                resumed = resume_commit_recovery(
                    connection,
                    vault_root=root,
                    vault_id="vault_pkb_001",
                    normalized_at=1770000020301,
                )

                self.assertEqual(
                    resumed,
                    CommitRecoveryExecutionResult(
                        mode="idle",
                        local=resumed.local,
                        submitted=None,
                    ),
                )
                self.assertIsNotNone(resumed.local)
                self.assertEqual(resumed.local.plan.mode, "idle")
                self.assertEqual(resumed.local.moved_staging_paths, [])

    def test_resume_commit_recovery_executes_submitted_confirmation_when_remote_data_provided(self) -> None:
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=8,
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=1770000020400,
            summary_hash="placeholder",
            files=[],
            tombstones=[],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "vault"
            initialize_vault(root, vault_id="vault_pkb_001", now_ms=1770000020390)
            ledger_path = root / ".noteapp" / "tombstone-ledger.jsonl"
            db_path = root / ".noteapp" / "state.sqlite3"

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
                        local_delete_sequence=0,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_1",
                        intent_manifest_hash="sha256:intent_1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=None,
                        created_at=1770000020395,
                        updated_at=1770000020395,
                    ),
                )

                resumed = resume_commit_recovery(
                    connection,
                    vault_root=root,
                    vault_id="vault_pkb_001",
                    normalized_at=1770000020401,
                    ledger_path=ledger_path,
                    local_tombstones=[],
                    observed_head_revision=8,
                    head_commit_intent_id="intent_1",
                    matched_manifest=manifest,
                )

                self.assertEqual(resumed.mode, "submitted_confirmation")
                self.assertIsNone(resumed.local)
                self.assertIsNotNone(resumed.submitted)
                self.assertEqual(resumed.submitted.plan.mode, "head_match")
                self.assertEqual(resumed.submitted.recovery.state.last_applied_revision, 8)

    def test_resume_commit_recovery_accepts_structured_remote_state(self) -> None:
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=8,
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=1770000020450,
            summary_hash="placeholder",
            files=[],
            tombstones=[],
        )
        remote_state = SubmittedConfirmationRemoteState(
            observed_head_revision=8,
            head_commit_intent_id="intent_1",
            matched_manifest=manifest,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "vault"
            initialize_vault(root, vault_id="vault_pkb_001", now_ms=1770000020440)
            ledger_path = root / ".noteapp" / "tombstone-ledger.jsonl"
            db_path = root / ".noteapp" / "state.sqlite3"

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
                        local_delete_sequence=0,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_1",
                        intent_manifest_hash="sha256:intent_1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=None,
                        created_at=1770000020445,
                        updated_at=1770000020445,
                    ),
                )

                resumed = resume_commit_recovery(
                    connection,
                    vault_root=root,
                    vault_id="vault_pkb_001",
                    normalized_at=1770000020451,
                    ledger_path=ledger_path,
                    local_tombstones=[],
                    remote_state=remote_state,
                )

                self.assertEqual(resumed.mode, "submitted_confirmation")
                self.assertIsNotNone(resumed.submitted)
                self.assertEqual(resumed.submitted.plan.mode, "head_match")
                self.assertEqual(resumed.submitted.recovery.state.last_applied_revision, 8)

    def test_resume_commit_recovery_requires_remote_inputs_for_submitted_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "vault"
            initialize_vault(root, vault_id="vault_pkb_001", now_ms=1770000020500)
            db_path = root / ".noteapp" / "state.sqlite3"

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
                        local_delete_sequence=0,
                    ),
                )
                upsert_commit_intent_journal(
                    connection,
                    CommitIntentJournalRecord(
                        vault_id="vault_pkb_001",
                        commit_intent_id="intent_1",
                        intent_manifest_hash="sha256:intent_1",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="submitted",
                        intent_delete_seq_upper_bound=None,
                        created_at=1770000020501,
                        updated_at=1770000020501,
                    ),
                )

                with self.assertRaisesRegex(ValueError, "ledger_path is required"):
                    resume_commit_recovery(
                        connection,
                        vault_root=root,
                        vault_id="vault_pkb_001",
                        normalized_at=1770000020502,
                    )

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

    def test_plan_pull_reconcile_respects_full_pull_and_summary_shortcut_rules(self) -> None:
        fresh = build_initial_vault_state("vault_pkb_001")
        stale = apply_manifest_summary_stale(
            VaultStateRecord(
                vault_id="vault_pkb_001",
                last_applied_revision=8,
                remote_head_revision=8,
                acked_revision=8,
                pending_ack_to_server=[],
                commit_in_progress=False,
                last_manifest_summary="sha256:head8",
                last_manifest_summary_status="valid",
                local_delete_sequence=19,
            )
        )
        advanced = VaultStateRecord(
            vault_id="vault_pkb_001",
            last_applied_revision=8,
            remote_head_revision=8,
            acked_revision=8,
            pending_ack_to_server=[],
            commit_in_progress=False,
            last_manifest_summary="sha256:head8",
            last_manifest_summary_status="valid",
            local_delete_sequence=19,
        )

        fresh_plan = plan_pull_reconcile(fresh, observed_head_revision=0)
        stale_plan = plan_pull_reconcile(stale, observed_head_revision=8)
        advanced_plan = plan_pull_reconcile(advanced, observed_head_revision=10)

        self.assertIsInstance(fresh_plan, ReconcilePlan)
        self.assertFalse(fresh_plan.should_download_manifest)
        self.assertFalse(fresh_plan.requires_full_pull)
        self.assertFalse(fresh_plan.can_use_summary_shortcut)

        self.assertTrue(stale_plan.should_download_manifest)
        self.assertTrue(stale_plan.requires_full_pull)
        self.assertFalse(stale_plan.can_use_summary_shortcut)
        self.assertEqual(stale_plan.target_revision, 8)

        self.assertTrue(advanced_plan.should_download_manifest)
        self.assertFalse(advanced_plan.requires_full_pull)
        self.assertTrue(advanced_plan.can_use_summary_shortcut)
        self.assertEqual(advanced_plan.target_revision, 10)

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

    def test_apply_committed_tombstones_updates_seq_and_legacy_selected_entries(self) -> None:
        tombstones = [
            TombstoneRecord(
                file_id="file_seq_hit",
                deleted_revision=None,
                deleted_at=100,
                local_delete_seq=1,
            ),
            TombstoneRecord(
                file_id="file_legacy_hit",
                deleted_revision=None,
                deleted_at=110,
                local_delete_seq=5,
            ),
            TombstoneRecord(
                file_id="file_skip",
                deleted_revision=None,
                deleted_at=210,
                local_delete_seq=6,
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
            created_at=150,
            updated_at=150,
        )
        legacy_journal = CommitIntentJournalRecord(
            vault_id="vault_pkb_001",
            commit_intent_id="intent_legacy",
            intent_manifest_hash="sha256:intent_legacy",
            base_revision=7,
            created_by_device="desktop-shanghai",
            status="submitted",
            intent_delete_seq_upper_bound=None,
            created_at=150,
            updated_at=150,
        )

        seq_updated = {item.file_id: item for item in apply_committed_tombstones(tombstones, seq_journal, committed_revision=8)}
        legacy_updated = {
            item.file_id: item for item in apply_committed_tombstones(tombstones, legacy_journal, committed_revision=9)
        }

        self.assertEqual(seq_updated["file_seq_hit"].deleted_revision, 8)
        self.assertIsNone(seq_updated["file_legacy_hit"].deleted_revision)
        self.assertEqual(legacy_updated["file_seq_hit"].deleted_revision, 9)
        self.assertEqual(legacy_updated["file_legacy_hit"].deleted_revision, 9)
        self.assertIsNone(legacy_updated["file_skip"].deleted_revision)

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
                self.assertEqual(applied.required_blob_ids, ["blob_live"])
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

    def test_recover_submitted_commit_flow_with_manifest_404_rewrites_ledger_and_forces_full_pull(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ledger_path = root / "tombstone-ledger.jsonl"
            db_path = root / "state.sqlite3"
            tombstones = [
                TombstoneRecord(
                    file_id="file_seq_hit",
                    deleted_revision=None,
                    deleted_at=100,
                    local_delete_seq=1,
                    last_known_path="Notes/A.md",
                ),
                TombstoneRecord(
                    file_id="file_skip",
                    deleted_revision=None,
                    deleted_at=300,
                    local_delete_seq=5,
                    last_known_path="Notes/B.md",
                ),
            ]

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
                        intent_delete_seq_upper_bound=2,
                        created_at=150,
                        updated_at=150,
                    ),
                )

                recovered = recover_submitted_commit_flow(
                    connection,
                    ledger_path=ledger_path,
                    vault_id="vault_pkb_001",
                    local_tombstones=tombstones,
                    matched_revision=8,
                    observed_head_revision=10,
                    normalized_at=200,
                    matched_manifest=None,
                )

                self.assertIsInstance(recovered, SubmittedRecoveryResult)
                self.assertTrue(recovered.requires_full_pull)
                self.assertEqual(recovered.state.last_manifest_summary_status, "stale")
                self.assertIsNone(recovered.state.last_manifest_summary)
                self.assertEqual(recovered.state.pending_ack_to_server, [4, 6])
                self.assertEqual(
                    [(item.file_id, item.deleted_revision) for item in load_tombstone_ledger(ledger_path)],
                    [("file_seq_hit", 8), ("file_skip", None)],
                )
                self.assertIsNone(load_commit_intent_journal(connection, "vault_pkb_001"))

    def test_recover_submitted_commit_flow_with_legacy_journal_uses_deleted_at_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ledger_path = root / "tombstone-ledger.jsonl"
            db_path = root / "state.sqlite3"
            tombstones = [
                TombstoneRecord(
                    file_id="file_legacy_hit",
                    deleted_revision=None,
                    deleted_at=100,
                    local_delete_seq=5,
                    last_known_path="Notes/A.md",
                ),
                TombstoneRecord(
                    file_id="file_skip",
                    deleted_revision=None,
                    deleted_at=300,
                    local_delete_seq=1,
                    last_known_path="Notes/B.md",
                ),
            ]
            matched_manifest = ManifestRecord(
                vault_id="vault_pkb_001",
                revision=8,
                base_revision=7,
                created_by_device="desktop-shanghai",
                created_at=180,
                summary_hash="placeholder",
                files=[],
                tombstones=[],
            )

            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                upsert_vault_state(
                    connection,
                    VaultStateRecord(
                        vault_id="vault_pkb_001",
                        last_applied_revision=7,
                        remote_head_revision=7,
                        acked_revision=7,
                        pending_ack_to_server=[4],
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
                        commit_intent_id="intent_legacy",
                        intent_manifest_hash="sha256:intent_legacy",
                        base_revision=7,
                        created_by_device="desktop-shanghai",
                        status="acknowledged",
                        intent_delete_seq_upper_bound=None,
                        created_at=150,
                        updated_at=150,
                    ),
                )

                recovered = recover_submitted_commit_flow(
                    connection,
                    ledger_path=ledger_path,
                    vault_id="vault_pkb_001",
                    local_tombstones=tombstones,
                    matched_revision=8,
                    observed_head_revision=8,
                    normalized_at=200,
                    matched_manifest=matched_manifest,
                )

                self.assertFalse(recovered.requires_full_pull)
                self.assertEqual(
                    recovered.state.last_manifest_summary,
                    compute_manifest_summary_hash(matched_manifest),
                )
                self.assertEqual(recovered.state.pending_ack_to_server, [4])
                self.assertEqual(
                    [(item.file_id, item.deleted_revision) for item in load_tombstone_ledger(ledger_path)],
                    [("file_legacy_hit", 8), ("file_skip", None)],
                )
                self.assertIsNone(load_commit_intent_journal(connection, "vault_pkb_001"))

    def test_execute_pull_reconcile_noop_when_head_not_advanced_and_summary_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "state.sqlite3"
            current = FileMapDocument(vault_id="vault_pkb_001", updated_at=90, files=[])

            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                state = initialize_vault_state(connection, "vault_pkb_001")

                reconciled = execute_pull_reconcile(
                    connection,
                    filemap_path=root / "filemap.json",
                    ledger_path=root / "tombstone-ledger.jsonl",
                    current_document=current,
                    current_state=state,
                    local_tombstones=[],
                    observed_head_revision=0,
                    rewritten_at=100,
                    manifest=None,
                )

                self.assertIsInstance(reconciled, ReconcileResult)
                self.assertIsNone(reconciled.applied)
                self.assertFalse(reconciled.plan.should_download_manifest)
                self.assertEqual(reconciled.state, state)

    def test_execute_pull_reconcile_stale_state_requires_manifest_even_when_revision_same(self) -> None:
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
                        last_known_revision=7,
                    )
                ],
            )
            stale_state = apply_manifest_summary_stale(
                VaultStateRecord(
                    vault_id="vault_pkb_001",
                    last_applied_revision=8,
                    remote_head_revision=8,
                    acked_revision=8,
                    pending_ack_to_server=[],
                    commit_in_progress=False,
                    last_manifest_summary="sha256:head8",
                    last_manifest_summary_status="valid",
                    local_delete_sequence=19,
                )
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
                tombstones=[],
            )

            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                upsert_vault_state(connection, stale_state)

                with self.assertRaises(ValueError):
                    execute_pull_reconcile(
                        connection,
                        filemap_path=filemap_path,
                        ledger_path=ledger_path,
                        current_document=current,
                        current_state=stale_state,
                        local_tombstones=[],
                        observed_head_revision=8,
                        rewritten_at=220,
                        manifest=None,
                    )

                reconciled = execute_pull_reconcile(
                    connection,
                    filemap_path=filemap_path,
                    ledger_path=ledger_path,
                    current_document=current,
                    current_state=stale_state,
                    local_tombstones=[],
                    observed_head_revision=8,
                    rewritten_at=220,
                    manifest=manifest,
                )

                self.assertTrue(reconciled.plan.should_download_manifest)
                self.assertTrue(reconciled.plan.requires_full_pull)
                self.assertFalse(reconciled.plan.can_use_summary_shortcut)
                self.assertIsNotNone(reconciled.applied)
                self.assertEqual(reconciled.applied.required_blob_ids, ["blob_live"])
                self.assertFalse(should_block_new_commit(reconciled.state))
                self.assertFalse(requires_full_pull(reconciled.state, observed_head_revision=8))
                self.assertEqual(
                    reconciled.state.last_manifest_summary,
                    compute_manifest_summary_hash(manifest),
                )
                self.assertEqual(
                    [item.file_id for item in load_filemap(filemap_path).sorted_files()],
                    ["file_live"],
                )

    def test_execute_pull_reconcile_advanced_head_can_use_summary_shortcut_but_still_applies_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            filemap_path = root / "filemap.json"
            ledger_path = root / "tombstone-ledger.jsonl"
            db_path = root / "state.sqlite3"
            current = FileMapDocument(
                vault_id="vault_pkb_001",
                updated_at=90,
                files=[],
            )
            state = VaultStateRecord(
                vault_id="vault_pkb_001",
                last_applied_revision=8,
                remote_head_revision=8,
                acked_revision=8,
                pending_ack_to_server=[],
                commit_in_progress=False,
                last_manifest_summary="sha256:head8",
                last_manifest_summary_status="valid",
                local_delete_sequence=19,
            )
            manifest = ManifestRecord(
                vault_id="vault_pkb_001",
                revision=10,
                base_revision=8,
                created_by_device="desktop-shanghai",
                created_at=200,
                summary_hash="placeholder",
                files=[],
                tombstones=[],
            )

            with closing(open_database(db_path)) as connection:
                bootstrap_database(connection)
                upsert_vault_state(connection, state)

                reconciled = execute_pull_reconcile(
                    connection,
                    filemap_path=filemap_path,
                    ledger_path=ledger_path,
                    current_document=current,
                    current_state=state,
                    local_tombstones=[],
                    observed_head_revision=10,
                    rewritten_at=220,
                    manifest=manifest,
                )

                self.assertTrue(reconciled.plan.should_download_manifest)
                self.assertFalse(reconciled.plan.requires_full_pull)
                self.assertTrue(reconciled.plan.can_use_summary_shortcut)
                self.assertIsNotNone(reconciled.applied)
                self.assertEqual(reconciled.applied.required_blob_ids, [])
                self.assertEqual(reconciled.state.last_applied_revision, 10)
                self.assertEqual(reconciled.state.remote_head_revision, 10)
                self.assertEqual(reconciled.state.acked_revision, 10)
                self.assertEqual(reconciled.state.pending_ack_to_server, [10])


if __name__ == "__main__":
    unittest.main()
