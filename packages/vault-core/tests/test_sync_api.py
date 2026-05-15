import unittest
from pathlib import Path

from vault_core import (
    AckRequestPayload,
    AckResponsePayload,
    BlobCheckRequest,
    BlobCheckResponsePayload,
    BlobDownloadRange,
    BlobDownloadCapability,
    BlobDownloadInitRequestPayload,
    BlobDownloadInitResponsePayload,
    BlobChunkDescriptor,
    BlobUploadCapability,
    BlobUploadInitRequestItem,
    BlobUploadInitRequestPayload,
    BlobUploadInitResponsePayload,
    BlobUploadPlan,
    BlobUploadPlanEntry,
    CommitConflictResponsePayload,
    CreateCommitBlobRef,
    CreateCommitRequestPayload,
    CreateCommitResponsePayload,
    FileVersionCommitDirective,
    FileVersionListRequestPayload,
    FileVersionListResponsePayload,
    FileVersionRecord,
    FileVersionRetentionPolicy,
    FileVersionUpdateRequestPayload,
    FileVersionUpdateResponsePayload,
    ManifestFileEntry,
    ManifestRecord,
    ResumableBlobDownloadCapability,
    ResumableBlobDownloadInitRequestItem,
    ResumableBlobDownloadInitRequestPayload,
    ResumableBlobDownloadInitResponsePayload,
    ResumableBlobUploadCapability,
    ResumableBlobUploadChunkState,
    ResumableBlobUploadCompleteRequestItem,
    ResumableBlobUploadCompleteRequestPayload,
    ResumableBlobUploadCompleteResponseItem,
    ResumableBlobUploadCompleteResponsePayload,
    ResumableBlobUploadInitRequestItem,
    ResumableBlobUploadInitRequestPayload,
    ResumableBlobUploadInitResponsePayload,
    ResolveCommitIntentRequestPayload,
    ResolveCommitIntentResponsePayload,
    TombstoneGcBlockedDevicePayload,
    TombstoneGcBlockedTombstonePayload,
    TombstoneGcRequestPayload,
    TombstoneGcResponsePayload,
    TombstoneGcTombstonePayload,
    VaultDeviceHeartbeatResponsePayload,
    VaultDeviceListResponsePayload,
    VaultDeviceRecordPayload,
    VaultHeadResponsePayload,
    parse_ack_response,
    parse_blob_download_init_response,
    build_blob_chunk_descriptors,
    build_file_version_id,
    build_file_version_records_from_manifest,
    build_blob_upload_init_request,
    parse_blob_check_response,
    parse_blob_upload_init_response,
    parse_commit_conflict_response,
    parse_create_commit_response,
    parse_file_version_commit_directives,
    parse_file_version_list_response,
    parse_file_version_update_response,
    parse_manifest_response,
    parse_resolve_commit_intent_response,
    parse_resumable_blob_download_init_response,
    parse_resumable_blob_upload_complete_response,
    parse_resumable_blob_upload_init_response,
    parse_tombstone_gc_response,
    parse_vault_device_heartbeat_response,
    parse_vault_device_list_response,
    parse_vault_head_response,
    serialize_ack_request,
    serialize_blob_download_init_request,
    serialize_blob_check_request,
    serialize_blob_upload_init_request,
    serialize_create_commit_request,
    serialize_file_version_commit_directives,
    serialize_file_version_list_request,
    serialize_file_version_update_request,
    serialize_resolve_commit_intent_request,
    serialize_resumable_blob_download_init_request,
    serialize_resumable_blob_upload_complete_request,
    serialize_resumable_blob_upload_init_request,
    serialize_tombstone_gc_request,
)


class SyncApiAdapterTests(unittest.TestCase):
    def test_serialize_blob_check_request_returns_protocol_shape(self) -> None:
        request = BlobCheckRequest(blob_ids=["blob_a", "blob_b"])

        self.assertEqual(
            serialize_blob_check_request(request),
            {"blob_ids": ["blob_a", "blob_b"]},
        )

    def test_parse_blob_check_response_validates_required_lists(self) -> None:
        parsed = parse_blob_check_response(
            {
                "existing_blob_ids": ["blob_a"],
                "missing_blob_ids": ["blob_b"],
            }
        )

        self.assertEqual(
            parsed,
            BlobCheckResponsePayload(
                existing_blob_ids=["blob_a"],
                missing_blob_ids=["blob_b"],
            ),
        )

        with self.assertRaisesRegex(ValueError, "existing_blob_ids must be a list"):
            parse_blob_check_response(
                {
                    "existing_blob_ids": "blob_a",
                    "missing_blob_ids": ["blob_b"],
                }
            )

        parsed_empty = parse_blob_check_response(
            {
                "existing_blob_ids": [],
                "missing_blob_ids": [],
            }
        )
        self.assertEqual(
            parsed_empty,
            BlobCheckResponsePayload(existing_blob_ids=[], missing_blob_ids=[]),
        )

    def test_parse_vault_head_response_validates_nullable_manifest_summary(self) -> None:
        parsed = parse_vault_head_response(
            {
                "vault_id": "vault_pkb_001",
                "head_revision": 8,
                "manifest_summary": "sha256:head8",
            }
        )
        self.assertEqual(
            parsed,
            VaultHeadResponsePayload(
                vault_id="vault_pkb_001",
                head_revision=8,
                manifest_summary="sha256:head8",
            ),
        )
        self.assertEqual(
            parse_vault_head_response(
                {
                    "vault_id": "vault_pkb_001",
                    "head_revision": 0,
                    "manifest_summary": None,
                }
            ),
            VaultHeadResponsePayload(
                vault_id="vault_pkb_001",
                head_revision=0,
                manifest_summary=None,
            ),
        )

    def test_parse_vault_device_list_and_heartbeat_responses(self) -> None:
        parsed_list = parse_vault_device_list_response(
            {
                "vault_id": "vault_pkb_001",
                "head_revision": 8,
                "inactive_after_ms": 604800000,
                "devices": [
                    {
                        "device_id": "dev_desktop",
                        "device_name": "Desktop",
                        "platform": "desktop",
                        "app_version": "1.0.43",
                        "protocol_version": "v1",
                        "registered_at_ms": 1770000000000,
                        "last_seen_at_ms": 1770000005000,
                        "acked_revision": 8,
                        "is_current_device": True,
                        "is_revoked": False,
                        "is_inactive_candidate": False,
                    }
                ],
            }
        )

        self.assertEqual(
            parsed_list,
            VaultDeviceListResponsePayload(
                vault_id="vault_pkb_001",
                head_revision=8,
                inactive_after_ms=604800000,
                devices=[
                    VaultDeviceRecordPayload(
                        device_id="dev_desktop",
                        device_name="Desktop",
                        platform="desktop",
                        app_version="1.0.43",
                        protocol_version="v1",
                        registered_at_ms=1770000000000,
                        last_seen_at_ms=1770000005000,
                        acked_revision=8,
                        is_current_device=True,
                        is_revoked=False,
                        is_inactive_candidate=False,
                    )
                ],
            ),
        )

        parsed_heartbeat = parse_vault_device_heartbeat_response(
            {
                "vault_id": "vault_pkb_001",
                "device_id": "dev_desktop",
                "last_seen_at_ms": 1770000006000,
                "acked_revision": 8,
                "head_revision": 9,
            }
        )
        self.assertEqual(
            parsed_heartbeat,
            VaultDeviceHeartbeatResponsePayload(
                vault_id="vault_pkb_001",
                device_id="dev_desktop",
                last_seen_at_ms=1770000006000,
                acked_revision=8,
                head_revision=9,
            ),
        )

        with self.assertRaisesRegex(ValueError, "inactive_after_ms must be a positive integer"):
            parse_vault_device_list_response(
                {
                    "vault_id": "vault_pkb_001",
                    "head_revision": 8,
                    "inactive_after_ms": 0,
                    "devices": [],
                }
            )

        with self.assertRaisesRegex(ValueError, "is_current_device must be a boolean"):
            parse_vault_device_list_response(
                {
                    "vault_id": "vault_pkb_001",
                    "head_revision": 8,
                    "inactive_after_ms": 604800000,
                    "devices": [
                        {
                            "device_id": "dev_desktop",
                            "device_name": "Desktop",
                            "platform": "desktop",
                            "protocol_version": "v1",
                            "acked_revision": 8,
                            "is_current_device": "yes",
                            "is_revoked": False,
                            "is_inactive_candidate": False,
                        }
                    ],
                }
            )

    def test_serialize_and_parse_tombstone_gc_payloads(self) -> None:
        self.assertEqual(
            serialize_tombstone_gc_request(
                TombstoneGcRequestPayload(
                    now_ms=1770000000000,
                    min_retention_ms=0,
                    inactive_after_ms=604800000,
                )
            ),
            {
                "now_ms": 1770000000000,
                "min_retention_ms": 0,
                "inactive_after_ms": 604800000,
            },
        )

        parsed = parse_tombstone_gc_response(
            {
                "vault_id": "vault_pkb_001",
                "run_id": "tgc_1",
                "ran_at_ms": 1770000000000,
                "base_revision": 8,
                "new_revision": 9,
                "head_revision": 9,
                "active_device_ids": ["dev_desktop", "dev_phone"],
                "active_device_count": 2,
                "min_retention_ms": 0,
                "inactive_after_ms": 604800000,
                "reclaimed_tombstones": [
                    {
                        "file_id": "file_deleted",
                        "deleted_revision": 7,
                        "deleted_at": 1760000000000,
                        "last_known_path": "Notes/old.md",
                        "deleted_by_device": "dev_desktop",
                    }
                ],
                "blocked_tombstones": [
                    {
                        "file_id": "file_waiting",
                        "deleted_revision": 8,
                        "deleted_at": 1760000000000,
                        "last_known_path": "Notes/waiting.md",
                        "deleted_by_device": "dev_desktop",
                        "reason": "waiting_for_ack",
                        "retention_remaining_ms": 0,
                        "blocked_by_devices": [
                            {
                                "device_id": "dev_phone",
                                "acked_revision": 7,
                                "required_revision": 8,
                            }
                        ],
                    }
                ],
                "reclaimed_count": 1,
                "reason": "reclaimed",
            }
        )

        self.assertEqual(
            parsed,
            TombstoneGcResponsePayload(
                vault_id="vault_pkb_001",
                run_id="tgc_1",
                ran_at_ms=1770000000000,
                base_revision=8,
                new_revision=9,
                head_revision=9,
                active_device_ids=["dev_desktop", "dev_phone"],
                active_device_count=2,
                min_retention_ms=0,
                inactive_after_ms=604800000,
                reclaimed_tombstones=[
                    TombstoneGcTombstonePayload(
                        file_id="file_deleted",
                        deleted_revision=7,
                        deleted_at=1760000000000,
                        last_known_path="Notes/old.md",
                        deleted_by_device="dev_desktop",
                    )
                ],
                blocked_tombstones=[
                    TombstoneGcBlockedTombstonePayload(
                        tombstone=TombstoneGcTombstonePayload(
                            file_id="file_waiting",
                            deleted_revision=8,
                            deleted_at=1760000000000,
                            last_known_path="Notes/waiting.md",
                            deleted_by_device="dev_desktop",
                        ),
                        reason="waiting_for_ack",
                        retention_remaining_ms=0,
                        blocked_by_devices=[
                            TombstoneGcBlockedDevicePayload(
                                device_id="dev_phone",
                                acked_revision=7,
                                required_revision=8,
                            )
                        ],
                    )
                ],
                reclaimed_count=1,
                reason="reclaimed",
            ),
        )

        with self.assertRaisesRegex(ValueError, "inactive_after_ms must be positive"):
            serialize_tombstone_gc_request(TombstoneGcRequestPayload(inactive_after_ms=0))

    def test_build_and_serialize_blob_upload_init_request_uses_protocol_items(self) -> None:
        upload_plan = BlobUploadPlan(
            vault_id="vault_pkb_001",
            entries=[
                BlobUploadPlanEntry(
                    blob_id="blob_a",
                    content_hash="sha256:a",
                    encrypted_size=32,
                    blob_staging_path=Path("C:/tmp/blob_a.blob.staging"),
                    file_ids=["file_a"],
                ),
                BlobUploadPlanEntry(
                    blob_id="blob_b",
                    content_hash="sha256:b",
                    encrypted_size=48,
                    blob_staging_path=Path("C:/tmp/blob_b.blob.staging"),
                    file_ids=["file_b"],
                ),
            ],
        )

        request = build_blob_upload_init_request(upload_plan)
        self.assertEqual(
            request,
            BlobUploadInitRequestPayload(
                blobs=[
                    BlobUploadInitRequestItem(
                        blob_id="blob_a",
                        encrypted_size=32,
                        content_hash="sha256:a",
                    ),
                    BlobUploadInitRequestItem(
                        blob_id="blob_b",
                        encrypted_size=48,
                        content_hash="sha256:b",
                    ),
                ]
            ),
        )
        self.assertEqual(
            serialize_blob_upload_init_request(request),
            {
                "blobs": [
                    {
                        "blob_id": "blob_a",
                        "encrypted_size": 32,
                        "content_hash": "sha256:a",
                    },
                    {
                        "blob_id": "blob_b",
                        "encrypted_size": 48,
                        "content_hash": "sha256:b",
                    },
                ]
            },
        )

    def test_serialize_and_parse_blob_download_init_payloads(self) -> None:
        request = BlobDownloadInitRequestPayload(blob_ids=["blob_a", "blob_b"])
        self.assertEqual(
            serialize_blob_download_init_request(request),
            {"blob_ids": ["blob_a", "blob_b"]},
        )
        self.assertEqual(
            parse_blob_download_init_response(
                {
                    "downloads": [
                        {
                            "blob_id": "blob_a",
                            "download_url": "https://example.com/download/blob_a",
                            "encrypted_size": 32,
                            "expires_at": "2026-05-08T12:00:00Z",
                            "headers": {"x-token": "a"},
                        }
                    ]
                }
            ),
            BlobDownloadInitResponsePayload(
                downloads=[
                    BlobDownloadCapability(
                        blob_id="blob_a",
                        download_url="https://example.com/download/blob_a",
                        encrypted_size=32,
                        expires_at="2026-05-08T12:00:00Z",
                        headers={"x-token": "a"},
                    )
                ]
            ),
        )

        with self.assertRaisesRegex(ValueError, "must be unique"):
            serialize_blob_download_init_request(
                BlobDownloadInitRequestPayload(blob_ids=["blob_a", "blob_a"])
            )

    def test_parse_blob_upload_init_response_supports_optional_method_and_headers(self) -> None:
        parsed = parse_blob_upload_init_response(
            {
                "uploads": [
                    {
                        "blob_id": "blob_a",
                        "upload_url": "https://example.com/upload/blob_a",
                        "expires_at": "2026-05-07T12:00:00Z",
                        "method": "PUT",
                        "headers": {"x-test": "value"},
                    },
                    {
                        "blob_id": "blob_b",
                        "upload_url": "https://example.com/upload/blob_b",
                        "expires_at": "2026-05-07T12:01:00Z",
                    },
                ]
            }
        )

        self.assertEqual(
            parsed,
            BlobUploadInitResponsePayload(
                uploads=[
                    BlobUploadCapability(
                        blob_id="blob_a",
                        upload_url="https://example.com/upload/blob_a",
                        expires_at="2026-05-07T12:00:00Z",
                        method="PUT",
                        headers={"x-test": "value"},
                    ),
                    BlobUploadCapability(
                        blob_id="blob_b",
                        upload_url="https://example.com/upload/blob_b",
                        expires_at="2026-05-07T12:01:00Z",
                    ),
                ]
            ),
        )

    def test_build_blob_chunk_descriptors_is_stable_and_covers_ranges(self) -> None:
        chunks = build_blob_chunk_descriptors(
            blob_id="blob_a",
            encrypted_size=10,
            chunk_size=4,
        )

        self.assertEqual(
            [(chunk.offset, chunk.size) for chunk in chunks],
            [(0, 4), (4, 4), (8, 2)],
        )
        self.assertEqual(chunks, build_blob_chunk_descriptors(blob_id="blob_a", encrypted_size=10, chunk_size=4))
        self.assertEqual(len({chunk.chunk_id for chunk in chunks}), 3)

    def test_serialize_resumable_upload_init_includes_chunks_and_hash(self) -> None:
        request = ResumableBlobUploadInitRequestPayload(
            blobs=[
                ResumableBlobUploadInitRequestItem(
                    blob_id="blob_a",
                    encrypted_size=10,
                    content_hash="sha256:plain",
                    encrypted_sha256="sha256:encrypted",
                    chunk_size=4,
                )
            ]
        )

        serialized = serialize_resumable_blob_upload_init_request(request)

        self.assertEqual(serialized["blobs"][0]["blob_id"], "blob_a")
        self.assertEqual(serialized["blobs"][0]["encrypted_sha256"], "sha256:encrypted")
        self.assertEqual(
            [(chunk["offset"], chunk["size"]) for chunk in serialized["blobs"][0]["chunks"]],
            [(0, 4), (4, 4), (8, 2)],
        )

    def test_parse_resumable_upload_init_response_tracks_missing_and_uploaded_chunks(self) -> None:
        chunk_a = build_blob_chunk_descriptors(blob_id="blob_a", encrypted_size=10, chunk_size=4)[0]
        chunk_b = build_blob_chunk_descriptors(blob_id="blob_a", encrypted_size=10, chunk_size=4)[1]

        parsed = parse_resumable_blob_upload_init_response(
            {
                "uploads": [
                    {
                        "blob_id": "blob_a",
                        "session_id": "session_a",
                        "upload_url": "https://blob.example.test/upload/session_a",
                        "expires_at": "2026-05-08T12:00:00Z",
                        "chunk_size": 4,
                        "encrypted_size": 10,
                        "uploaded_chunks": [
                            {
                                "chunk_id": chunk_a.chunk_id,
                                "offset": chunk_a.offset,
                                "size": chunk_a.size,
                                "status": "uploaded",
                            }
                        ],
                        "missing_chunks": [
                            {
                                "chunk_id": chunk_b.chunk_id,
                                "offset": chunk_b.offset,
                                "size": chunk_b.size,
                                "status": "missing",
                            }
                        ],
                        "headers": {"x-session": "session_a"},
                    }
                ]
            }
        )

        self.assertEqual(
            parsed,
            ResumableBlobUploadInitResponsePayload(
                uploads=[
                    ResumableBlobUploadCapability(
                        blob_id="blob_a",
                        session_id="session_a",
                        upload_url="https://blob.example.test/upload/session_a",
                        expires_at="2026-05-08T12:00:00Z",
                        chunk_size=4,
                        encrypted_size=10,
                        uploaded_chunks=[
                            ResumableBlobUploadChunkState(
                                chunk_id=chunk_a.chunk_id,
                                offset=chunk_a.offset,
                                size=chunk_a.size,
                                status="uploaded",
                            )
                        ],
                        missing_chunks=[
                            ResumableBlobUploadChunkState(
                                chunk_id=chunk_b.chunk_id,
                                offset=chunk_b.offset,
                                size=chunk_b.size,
                                status="missing",
                            )
                        ],
                        headers={"x-session": "session_a"},
                    )
                ]
            ),
        )

    def test_resumable_upload_complete_serialization_and_incomplete_response(self) -> None:
        request = ResumableBlobUploadCompleteRequestPayload(
            uploads=[
                ResumableBlobUploadCompleteRequestItem(
                    blob_id="blob_a",
                    session_id="session_a",
                    encrypted_size=10,
                    encrypted_sha256="sha256:encrypted",
                    uploaded_chunk_ids=["chunk-a", "chunk-b"],
                )
            ]
        )

        self.assertEqual(
            serialize_resumable_blob_upload_complete_request(request),
            {
                "uploads": [
                    {
                        "blob_id": "blob_a",
                        "session_id": "session_a",
                        "encrypted_size": 10,
                        "encrypted_sha256": "sha256:encrypted",
                        "uploaded_chunk_ids": ["chunk-a", "chunk-b"],
                    }
                ]
            },
        )

        parsed = parse_resumable_blob_upload_complete_response(
            {
                "uploads": [
                    {
                        "blob_id": "blob_a",
                        "status": "incomplete",
                        "missing_chunks": [
                            {
                                "chunk_id": "chunk-c",
                                "offset": 8,
                                "size": 2,
                            }
                        ],
                    }
                ]
            }
        )
        self.assertEqual(
            parsed,
            ResumableBlobUploadCompleteResponsePayload(
                uploads=[
                    ResumableBlobUploadCompleteResponseItem(
                        blob_id="blob_a",
                        status="incomplete",
                        missing_chunks=[BlobChunkDescriptor(chunk_id="chunk-c", offset=8, size=2)],
                    )
                ]
            ),
        )

    def test_resumable_download_init_supports_ranges(self) -> None:
        request = ResumableBlobDownloadInitRequestPayload(
            blobs=[
                ResumableBlobDownloadInitRequestItem(
                    blob_id="blob_a",
                    ranges=[BlobDownloadRange(offset=4, size=6)],
                )
            ]
        )

        self.assertEqual(
            serialize_resumable_blob_download_init_request(request),
            {"blobs": [{"blob_id": "blob_a", "ranges": [{"offset": 4, "size": 6}]}]},
        )

        parsed = parse_resumable_blob_download_init_response(
            {
                "downloads": [
                    {
                        "blob_id": "blob_a",
                        "download_url": "https://blob.example.test/download/blob_a",
                        "encrypted_size": 10,
                        "expires_at": "2026-05-08T12:00:00Z",
                        "ranges": [{"offset": 4, "size": 6}],
                    }
                ]
            }
        )
        self.assertEqual(
            parsed,
            ResumableBlobDownloadInitResponsePayload(
                downloads=[
                    ResumableBlobDownloadCapability(
                        blob_id="blob_a",
                        download_url="https://blob.example.test/download/blob_a",
                        encrypted_size=10,
                        expires_at="2026-05-08T12:00:00Z",
                        ranges=[BlobDownloadRange(offset=4, size=6)],
                    )
                ]
            ),
        )

    def test_serialize_and_parse_resolve_commit_intent_payloads(self) -> None:
        request = ResolveCommitIntentRequestPayload(
            commit_intent_id="intent_1",
            intent_manifest_hash="sha256:intent_1",
        )
        self.assertEqual(
            serialize_resolve_commit_intent_request(request),
            {
                "commit_intent_id": "intent_1",
                "intent_manifest_hash": "sha256:intent_1",
            },
        )

        found = parse_resolve_commit_intent_response(
            {
                "status": "found",
                "matched_revision": 8,
                "observed_head_revision": 10,
                "head_manifest_summary": "sha256:head10",
            }
        )
        self.assertEqual(
            found,
            ResolveCommitIntentResponsePayload(
                status="found",
                matched_revision=8,
                observed_head_revision=10,
                head_manifest_summary="sha256:head10",
            ),
        )

        mismatched = parse_resolve_commit_intent_response(
            {
                "status": "mismatched",
                "observed_head_revision": 10,
                "head_manifest_summary": "sha256:head10",
            }
        )
        self.assertEqual(
            mismatched,
            ResolveCommitIntentResponsePayload(
                status="mismatched",
                matched_revision=None,
                observed_head_revision=10,
                head_manifest_summary="sha256:head10",
            ),
        )

        with self.assertRaisesRegex(ValueError, "matched_revision is required"):
            parse_resolve_commit_intent_response({"status": "found"})

        with self.assertRaisesRegex(ValueError, "only allowed"):
            parse_resolve_commit_intent_response(
                {
                    "status": "not_found",
                    "matched_revision": 8,
                }
            )

    def test_parse_manifest_response_uses_manifest_record_shape(self) -> None:
        parsed = parse_manifest_response(
            {
                "vault_id": "vault_pkb_001",
                "revision": 8,
                "base_revision": 7,
                "created_by_device": "desktop-shanghai",
                "created_at": 1770000020000,
                "summary_hash": "sha256:head8",
                "files": [
                    {
                        "file_id": "file_a",
                        "path": "Notes/A.md",
                        "type": "note",
                        "content_hash": "sha256:a",
                        "blob_id": "blob_a",
                        "size": 16,
                        "mtime": 1770000019990,
                    }
                ],
                "tombstones": [],
            }
        )

        self.assertEqual(
            parsed,
            ManifestRecord(
                vault_id="vault_pkb_001",
                revision=8,
                base_revision=7,
                created_by_device="desktop-shanghai",
                created_at=1770000020000,
                summary_hash="sha256:head8",
                files=[
                    ManifestFileEntry(
                        file_id="file_a",
                        path="Notes/A.md",
                        type="note",
                        content_hash="sha256:a",
                        blob_id="blob_a",
                        size=16,
                        mtime=1770000019990,
                    )
                ],
                tombstones=[],
            ),
        )

    def test_build_file_version_records_from_manifest_uses_stable_file_id(self) -> None:
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=12,
            base_revision=11,
            created_by_device="desktop-shanghai",
            created_at=1770000030000,
            summary_hash="sha256:head12",
            files=[
                ManifestFileEntry(
                    file_id="file_xxx_md",
                    path="Meetings/XXX.md",
                    type="note",
                    content_hash="sha256:meeting-v2",
                    blob_id="blob_meeting_v2",
                    size=2048,
                    mtime=1770000029990,
                )
            ],
            tombstones=[],
        )

        records = build_file_version_records_from_manifest(
            manifest,
            directives=[
                FileVersionCommitDirective(
                    file_id="file_xxx_md",
                    source="manual_meeting_checkpoint",
                    version_label="2026-05-15 周会",
                    change_note="会议后整理版",
                    is_pinned=True,
                )
            ],
        )

        expected_version_id = build_file_version_id(
            vault_id="vault_pkb_001",
            file_id="file_xxx_md",
            revision=12,
            blob_id="blob_meeting_v2",
            source="manual_meeting_checkpoint",
        )
        self.assertEqual(
            records,
            [
                FileVersionRecord(
                    version_id=expected_version_id,
                    file_id="file_xxx_md",
                    path_at_revision="Meetings/XXX.md",
                    revision=12,
                    content_hash="sha256:meeting-v2",
                    blob_id="blob_meeting_v2",
                    size=2048,
                    mtime=1770000029990,
                    created_at=1770000030000,
                    created_by_device="desktop-shanghai",
                    source="manual_meeting_checkpoint",
                    version_label="2026-05-15 周会",
                    change_note="会议后整理版",
                    is_pinned=True,
                )
            ],
        )
        moved_manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=13,
            base_revision=12,
            created_by_device="desktop-shanghai",
            created_at=1770000040000,
            summary_hash="sha256:head13",
            files=[
                ManifestFileEntry(
                    file_id="file_xxx_md",
                    path="Archive/Meetings/XXX.md",
                    type="note",
                    content_hash="sha256:meeting-v3",
                    blob_id="blob_meeting_v3",
                    size=4096,
                    mtime=1770000039990,
                )
            ],
            tombstones=[],
        )
        moved_records = build_file_version_records_from_manifest(moved_manifest)
        self.assertEqual(moved_records[0].file_id, "file_xxx_md")
        self.assertEqual(moved_records[0].path_at_revision, "Archive/Meetings/XXX.md")
        self.assertEqual(moved_records[0].source, "commit_success")

        with self.assertRaisesRegex(ValueError, "finalized"):
            build_file_version_records_from_manifest(
                ManifestRecord(
                    vault_id="vault_pkb_001",
                    revision=0,
                    base_revision=0,
                    created_by_device="desktop-shanghai",
                    created_at=1770000040000,
                    summary_hash="pending",
                    files=[],
                    tombstones=[],
                )
            )

    def test_file_version_protocol_serialization_and_updates(self) -> None:
        directive = FileVersionCommitDirective(
            file_id="file_xxx_md",
            source="manual_meeting_checkpoint",
            version_label="客户会议",
            change_note="确认行动项后保存",
            is_pinned=True,
        )
        self.assertEqual(
            serialize_file_version_commit_directives([directive]),
            [
                {
                    "file_id": "file_xxx_md",
                    "source": "manual_meeting_checkpoint",
                    "version_label": "客户会议",
                    "change_note": "确认行动项后保存",
                    "is_pinned": True,
                }
            ],
        )
        self.assertEqual(
            parse_file_version_commit_directives([directive.to_dict()]),
            [directive],
        )
        with self.assertRaisesRegex(ValueError, "unique"):
            serialize_file_version_commit_directives([directive, directive])

        self.assertEqual(
            serialize_file_version_list_request(
                FileVersionListRequestPayload(
                    file_id="file_xxx_md",
                    limit=20,
                    cursor="cursor_1",
                    include_pinned=False,
                )
            ),
            {
                "file_id": "file_xxx_md",
                "limit": 20,
                "cursor": "cursor_1",
                "include_pinned": False,
            },
        )

        version = FileVersionRecord(
            version_id="fv_abc",
            file_id="file_xxx_md",
            path_at_revision="Meetings/XXX.md",
            revision=12,
            content_hash="sha256:meeting-v2",
            blob_id="blob_meeting_v2",
            size=2048,
            mtime=1770000029990,
            created_at=1770000030000,
            created_by_device="desktop-shanghai",
            source="manual_meeting_checkpoint",
            version_label="客户会议",
            change_note="确认行动项后保存",
            is_pinned=True,
        )
        parsed_list = parse_file_version_list_response(
            {
                "file_id": "file_xxx_md",
                "versions": [version.to_dict()],
                "next_cursor": None,
                "retention_policy": {
                    "keep_latest": 100,
                    "keep_pinned": True,
                    "max_age_days": 365,
                },
            }
        )
        self.assertEqual(
            parsed_list,
            FileVersionListResponsePayload(
                file_id="file_xxx_md",
                versions=[version],
                next_cursor=None,
                retention_policy=FileVersionRetentionPolicy(
                    keep_latest=100,
                    keep_pinned=True,
                    max_age_days=365,
                ),
            ),
        )

        self.assertEqual(
            serialize_file_version_update_request(
                FileVersionUpdateRequestPayload(
                    version_label="复盘版",
                    change_note="会后补充结论",
                    is_pinned=False,
                )
            ),
            {
                "version_label": "复盘版",
                "change_note": "会后补充结论",
                "is_pinned": False,
            },
        )
        self.assertEqual(
            parse_file_version_update_response({"version": version.to_dict()}),
            FileVersionUpdateResponsePayload(version=version),
        )
        with self.assertRaisesRegex(ValueError, "at least one field"):
            serialize_file_version_update_request(FileVersionUpdateRequestPayload())

    def test_serialize_and_parse_ack_payloads(self) -> None:
        request = AckRequestPayload(revisions=[8, 10])
        self.assertEqual(
            serialize_ack_request(request),
            {
                "revisions": [8, 10],
            },
        )
        self.assertEqual(
            parse_ack_response({"max_acked_revision": 10}),
            AckResponsePayload(max_acked_revision=10),
        )

        with self.assertRaisesRegex(ValueError, "at least one revision"):
            serialize_ack_request(AckRequestPayload(revisions=[]))
        with self.assertRaisesRegex(ValueError, "must be unique"):
            serialize_ack_request(AckRequestPayload(revisions=[8, 8]))

    def test_serialize_create_commit_request_omits_optional_delete_seq_when_absent(self) -> None:
        manifest = ManifestRecord(
            vault_id="vault_pkb_001",
            revision=0,
            base_revision=7,
            created_by_device="desktop-shanghai",
            created_at=1770000019000,
            summary_hash="pending",
            files=[
                ManifestFileEntry(
                    file_id="file_a",
                    path="Notes/A.md",
                    type="note",
                    content_hash="sha256:a",
                    blob_id="blob_a",
                    size=16,
                    mtime=1770000018990,
                )
            ],
            tombstones=[],
        )
        request = CreateCommitRequestPayload(
            commit_intent_id="intent_1",
            base_revision=7,
            created_by_device="desktop-shanghai",
            intent_manifest_hash="sha256:intent",
            intent_delete_seq_upper_bound=None,
            manifest=manifest,
            blob_refs=[CreateCommitBlobRef(blob_id="blob_a", file_id="file_a")],
            file_version_directives=[
                FileVersionCommitDirective(
                    file_id="file_a",
                    source="manual_meeting_checkpoint",
                    version_label="周会版本",
                    change_note="会后确认版",
                    is_pinned=True,
                )
            ],
        )

        serialized = serialize_create_commit_request(request)

        self.assertEqual(serialized["commit_intent_id"], "intent_1")
        self.assertEqual(serialized["base_revision"], 7)
        self.assertEqual(serialized["intent_manifest_hash"], "sha256:intent")
        self.assertEqual(serialized["blob_refs"], [{"blob_id": "blob_a", "file_id": "file_a"}])
        self.assertEqual(
            serialized["file_version_directives"],
            [
                {
                    "file_id": "file_a",
                    "source": "manual_meeting_checkpoint",
                    "version_label": "周会版本",
                    "change_note": "会后确认版",
                    "is_pinned": True,
                }
            ],
        )
        self.assertNotIn("intent_delete_seq_upper_bound", serialized)

    def test_parse_create_commit_response_and_conflict_response(self) -> None:
        response = parse_create_commit_response(
            {
                "vault_id": "vault_pkb_001",
                "new_revision": 8,
                "head_manifest_summary": "sha256:head8",
                "acked_revision_for_device": 8,
            }
        )
        self.assertEqual(
            response,
            CreateCommitResponsePayload(
                vault_id="vault_pkb_001",
                new_revision=8,
                head_manifest_summary="sha256:head8",
                acked_revision_for_device=8,
            ),
        )

        conflict = parse_commit_conflict_response(
            {
                "code": "base_revision_conflict",
                "current_head_revision": 9,
                "current_manifest_summary": None,
            }
        )
        self.assertEqual(
            conflict,
            CommitConflictResponsePayload(
                code="base_revision_conflict",
                current_head_revision=9,
                current_manifest_summary=None,
            ),
        )

        with self.assertRaisesRegex(ValueError, "supported commit conflict code"):
            parse_commit_conflict_response(
                {
                    "code": "unexpected",
                    "current_head_revision": 9,
                    "current_manifest_summary": "sha256:head9",
                }
            )


if __name__ == "__main__":
    unittest.main()
