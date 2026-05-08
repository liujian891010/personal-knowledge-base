import unittest
from pathlib import Path

from vault_core import (
    BlobCheckRequest,
    BlobCheckResponsePayload,
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
    ManifestFileEntry,
    ManifestRecord,
    ResolveCommitIntentRequestPayload,
    ResolveCommitIntentResponsePayload,
    build_blob_upload_init_request,
    parse_blob_check_response,
    parse_blob_upload_init_response,
    parse_commit_conflict_response,
    parse_create_commit_response,
    parse_manifest_response,
    parse_resolve_commit_intent_response,
    serialize_blob_check_request,
    serialize_blob_upload_init_request,
    serialize_create_commit_request,
    serialize_resolve_commit_intent_request,
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
        )

        serialized = serialize_create_commit_request(request)

        self.assertEqual(serialized["commit_intent_id"], "intent_1")
        self.assertEqual(serialized["base_revision"], 7)
        self.assertEqual(serialized["intent_manifest_hash"], "sha256:intent")
        self.assertEqual(serialized["blob_refs"], [{"blob_id": "blob_a", "file_id": "file_a"}])
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
