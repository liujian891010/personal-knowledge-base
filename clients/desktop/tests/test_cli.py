from __future__ import annotations

import base64
import io
import json
import os
import tempfile
import unittest
from unittest import mock
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from clients.desktop import (
    E2EEDesktopBlobCryptoProvider,
    PlaceholderDesktopBlobCryptoProvider,
    DesktopPullRequiredBlobFile,
    DesktopPullRequiredBlobPlan,
    DesktopPullRequiredBlobResult,
)
from clients.desktop.cli import run_cli
from vault_core import (
    BlobDownloadCapability,
    BlobDownloadInitExecutionResult,
    BlobDownloadInitRequestPayload,
    BlobDownloadInitResponsePayload,
    BlobDownloadSessionResult,
)


@dataclass
class FakeResult:
    kind: str
    value: int


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []
        self.fail_recover_at_calls: set[int] = set()
        self.fail_submit_workspace_at_calls: set[int] = set()
        self.skip_submit_detected_if_needed = False
        self.commit_recovery_payload = None
        self.pull_and_ack_payload = None
        self.pull_and_plan_apply_payload = None
        self.pull_and_apply_payload = None
        self.pull_and_apply_nonblocking_payload = None
        self.download_and_decrypt_pull_payload = None
        self.materialize_pull_required_plaintext_payload = None
        self.stage_pull_required_plaintext_payload = None
        self.pull_apply_recovery_payload = None
        self.detect_local_changes_payload = {
            "vault_id": "vault-001",
            "tracked_record_count": 2,
            "change_count": 2,
            "modified_file_ids": ["file-a"],
            "missing_file_ids": ["file-b"],
            "changes": [
                {
                    "kind": "modified",
                    "path": "Notes/A.md",
                    "file_id": "file-a",
                    "file_type": "note",
                    "record_status": "active",
                    "content_hash": "sha256:aaa",
                    "size_bytes": 123,
                    "mtime_ms": 1770000044900,
                },
                {
                    "kind": "missing",
                    "path": "Notes/B.md",
                    "file_id": "file-b",
                    "file_type": "note",
                    "record_status": "active",
                    "content_hash": "sha256:bbb",
                    "size_bytes": None,
                    "mtime_ms": None,
                },
            ],
        }
        self.worker_state_payload = {
            "started_at_ms": 1770000045000,
            "finished_at_ms": 1770000045001,
            "effective_step_ms": 2500,
            "success_count": 2,
            "failure_count": 1,
            "stopped_early": False,
            "state_path": "C:/vault/.noteapp/sync-worker-state.json",
            "latest_failure": {
                "iteration": 1,
                "error_type": "RuntimeError",
                "error_message": "submit failed at call 0",
            },
            "config": {
                "iterations": 2,
                "interval_seconds": 2.5,
                "step_ms": None,
                "continue_on_error": True,
                "init_now_ms": None,
                "recovery_normalized_at": None,
                "submit_created_at": None,
                "submit_file_ids": ["file-a"],
                "commit_intent_id": None,
                "cleanup_normalized_at": None,
                "pull_rewritten_at": None,
                "encrypted_blob_by_file_id": None,
            },
            "time_plan": {
                "base_now_ms": 1770000045000,
                "init_now_ms": 1770000045000,
                "recovery_normalized_at": 1770000045010,
                "submit_created_at": 1770000045020,
                "cleanup_normalized_at": 1770000045021,
                "pull_rewritten_at": 1770000045030,
            },
        }
        self.worker_health_payload = {
            "status": "degraded",
            "started_at_ms": 1770000045000,
            "finished_at_ms": 1770000045001,
            "success_count": 2,
            "failure_count": 1,
            "stopped_early": False,
            "state_path": "C:/vault/.noteapp/sync-worker-state.json",
            "latest_failure": {
                "iteration": 1,
                "error_type": "RuntimeError",
                "error_message": "submit failed at call 0",
            },
        }
        self.sync_activity_payload = {
            "records": [
                {
                    "activity_id": "activity-001",
                    "occurred_at_ms": 1770000040666,
                    "level": "success",
                    "action_id": "list-conflicts",
                    "command": "list-conflicts",
                    "status": "executed",
                    "source": "card:conflicts",
                    "message": None,
                }
            ],
            "total_count": 1,
        }

    def ensure_initialized(self, *, now_ms=None):
        self.calls.append(("init", now_ms))
        return FakeResult(kind="init", value=now_ms)

    def load_snapshot(self):
        self.calls.append(("status", None))
        return {"kind": "status", "files": 1}

    def import_existing_workspace_files_if_empty(self):
        self.calls.append(("import-existing-workspace-files", None))
        return self.list_workspace_files()

    def list_workspace_files(self):
        self.calls.append(("workspace-files", None))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "files": [
                {
                    "file_id": "file-a",
                    "path": "Notes/A.md",
                    "type": "note",
                    "status": "active",
                    "updated_at": 1770000040000,
                    "exists_on_disk": True,
                    "size_bytes": 123,
                    "content_hash": "sha256:aaa",
                }
            ],
            "total_count": 1,
            "active_count": 1,
            "missing_count": 0,
        }

    def load_workspace_file_content(self, file_id):
        self.calls.append(("workspace-file-content", file_id))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "file_id": file_id,
            "path": "Notes/A.md",
            "type": "note",
            "status": "active",
            "updated_at": 1770000040000,
            "size_bytes": 8,
            "content_hash": "sha256:aaa",
            "tracked_content_hash": "sha256:old",
            "encoding": "utf-8",
            "text": "# A\n",
        }

    def load_workspace_file_blob(self, file_id):
        self.calls.append(("workspace-file-blob", file_id))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "file_id": file_id,
            "path": "Attachments/photo.png",
            "type": "attachment",
            "status": "active",
            "size_bytes": 8,
            "content_hash": "sha256:aaa",
            "content_base64": "iVBORw0KGgo=",
            "mime_type": "image/png",
        }

    def write_workspace_file_content(self, file_id, text):
        self.calls.append(("write-workspace-file-content", file_id, text))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "file_id": file_id,
            "path": "Notes/A.md",
            "type": "note",
            "status": "active",
            "updated_at": 1770000040001,
            "size_bytes": len(text.encode("utf-8")),
            "content_hash": "sha256:bbb",
            "tracked_content_hash": "sha256:aaa",
            "encoding": "utf-8",
            "text": text,
        }

    def create_workspace_note(self, path, *, text="", now_ms=None):
        self.calls.append(("create-workspace-note", path, text, now_ms))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "operation": "create",
            "file": {
                "file_id": "file-created",
                "path": path,
                "type": "note",
                "status": "active",
                "updated_at": now_ms or 1770000040001,
                "exists_on_disk": True,
                "size_bytes": len(text.encode("utf-8")),
            },
            "files": {
                "schema_version": "v1",
                "vault_id": "vault-001",
                "device_id": "desktop-shanghai",
                "vault_root": "C:/vault",
                "files": [],
                "total_count": 0,
                "active_count": 0,
                "missing_count": 0,
            },
        }

    def create_workspace_attachment(self, file_name, payload, *, now_ms=None):
        self.calls.append(("create-workspace-attachment", file_name, payload, now_ms))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "operation": "create_attachment",
            "file": {
                "file_id": "file-attachment",
                "path": f"Attachments/{file_name}",
                "type": "attachment",
                "status": "active",
                "updated_at": now_ms or 1770000040001,
                "exists_on_disk": True,
                "size_bytes": len(payload),
            },
            "files": {
                "schema_version": "v1",
                "vault_id": "vault-001",
                "device_id": "desktop-shanghai",
                "vault_root": "C:/vault",
                "files": [],
                "total_count": 0,
                "active_count": 0,
                "missing_count": 0,
            },
        }

    def rename_workspace_note(self, file_id, path, *, now_ms=None):
        self.calls.append(("rename-workspace-note", file_id, path, now_ms))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "operation": "rename",
            "file": {
                "file_id": file_id,
                "path": path,
                "type": "note",
                "status": "active",
                "updated_at": now_ms or 1770000040001,
                "exists_on_disk": True,
                "size_bytes": 123,
            },
            "files": {
                "schema_version": "v1",
                "vault_id": "vault-001",
                "device_id": "desktop-shanghai",
                "vault_root": "C:/vault",
                "files": [],
                "total_count": 0,
                "active_count": 0,
                "missing_count": 0,
            },
        }

    def move_workspace_note(self, file_id, path, *, now_ms=None):
        self.calls.append(("move-workspace-note", file_id, path, now_ms))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "operation": "move",
            "file": {
                "file_id": file_id,
                "path": path,
                "type": "note",
                "status": "active",
                "updated_at": now_ms or 1770000040001,
                "exists_on_disk": True,
                "size_bytes": 123,
            },
            "files": {
                "schema_version": "v1",
                "vault_id": "vault-001",
                "device_id": "desktop-shanghai",
                "vault_root": "C:/vault",
                "files": [],
                "total_count": 0,
                "active_count": 0,
                "missing_count": 0,
            },
        }

    def delete_workspace_note(self, file_id, *, now_ms=None):
        self.calls.append(("delete-workspace-note", file_id, now_ms))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "operation": "delete",
            "file": {
                "file_id": file_id,
                "path": "Notes/A.md",
                "type": "note",
                "status": "deleted",
                "updated_at": now_ms or 1770000040001,
                "exists_on_disk": False,
                "size_bytes": None,
            },
            "files": {
                "schema_version": "v1",
                "vault_id": "vault-001",
                "device_id": "desktop-shanghai",
                "vault_root": "C:/vault",
                "files": [],
                "total_count": 0,
                "active_count": 0,
                "missing_count": 0,
            },
        }

    def list_workspace_trash(self):
        self.calls.append(("workspace-trash",))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "trash_root": "C:/vault/.noteapp/trash",
            "items": [
                {
                    "file_id": "file-a",
                    "path": "Notes/A.md",
                    "type": "note",
                    "deleted_at": 1770000040001,
                    "trash_path": "C:/vault/.noteapp/trash/1770000040001-file-a.md",
                    "exists_in_trash": True,
                    "size_bytes": 8,
                }
            ],
            "total_count": 1,
        }

    def restore_workspace_trash_item(self, file_id, *, now_ms=None):
        self.calls.append(("restore-workspace-trash", file_id, now_ms))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "operation": "restore",
            "file": {
                "file_id": file_id,
                "path": "Notes/A.md",
                "type": "note",
                "status": "active",
                "updated_at": now_ms or 1770000040002,
                "exists_on_disk": True,
                "size_bytes": 8,
            },
            "files": {
                "schema_version": "v1",
                "vault_id": "vault-001",
                "device_id": "desktop-shanghai",
                "vault_root": "C:/vault",
                "files": [],
                "total_count": 0,
                "active_count": 0,
                "missing_count": 0,
            },
        }

    def purge_workspace_trash_item(self, file_id, *, now_ms=None):
        self.calls.append(("purge-workspace-trash", file_id, now_ms))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "trash_root": "C:/vault/.noteapp/trash",
            "items": [],
            "total_count": 0,
        }

    def empty_workspace_trash(self, *, now_ms=None):
        self.calls.append(("empty-workspace-trash", now_ms))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "trash_root": "C:/vault/.noteapp/trash",
            "items": [],
            "total_count": 0,
        }

    def compile_ai_wiki(self, *, now_ms=None):
        self.calls.append(("compile-ai-wiki", now_ms))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "generated_at": "2026-05-11T00:00:00Z",
            "source_count": 1,
            "artifact_count": 1,
            "written_count": 2,
            "skipped_count": 0,
            "index_path": ".ai/index.md",
            "artifacts": [
                {
                    "title": "Live Note",
                    "path": ".ai/wiki/live-note.md",
                    "source_file_id": "file-a",
                    "source_path": "Notes/A.md",
                    "source_content_hash": "sha256:aaa",
                }
            ],
            "skipped": [],
            "files": {
                "schema_version": "v1",
                "vault_id": "vault-001",
                "device_id": "desktop-shanghai",
                "vault_root": "C:/vault",
                "files": [],
                "total_count": 0,
                "active_count": 0,
                "missing_count": 0,
            },
        }

    def answer_ai_wiki(self, question, *, limit=5):
        self.calls.append(("ask-ai-wiki", question, limit))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "question": question,
            "answer": "Based on the local AI Wiki.",
            "citation_count": 1,
            "citations": [
                {
                    "file_id": "wiki-a",
                    "path": ".ai/wiki/live-note.md",
                    "title": "Live Note",
                    "excerpt": "Local search result",
                    "score": 3,
                }
            ],
            "model_status": "local_deterministic",
        }

    def check_ai_provider_health(self):
        self.calls.append(("ai-provider-health", None))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "configured": True,
            "status": "available",
            "provider_api": "openai-completions",
            "base_url": "https://llm.example.com/v1",
            "model_id": "test-model",
            "message": "AI provider responded successfully.",
        }

    def run_ai_context_task(
        self,
        *,
        context_type,
        instruction,
        file_ids=None,
        folder_path=None,
        recursive=True,
        max_files=None,
        max_chars_per_file=None,
        max_total_chars=None,
    ):
        self.calls.append(
            (
                "ai-context-task",
                context_type,
                instruction,
                list(file_ids or []),
                folder_path,
                recursive,
                max_files,
                max_chars_per_file,
                max_total_chars,
            )
        )
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "context_type": context_type,
            "instruction": instruction,
            "answer": "Context answer [1].",
            "source_count": 1,
            "sources": [
                {
                    "file_id": "file-a",
                    "path": "Notes/A.md",
                    "title": "A",
                    "excerpt": "Source text",
                    "included_chars": 11,
                    "original_chars": 11,
                    "truncated": False,
                }
            ],
            "model_status": "openai-completions:test-model",
            "truncation": {
                "max_files": 20,
                "max_chars_per_file": 4000,
                "max_total_chars": 30000,
                "included_file_count": 1,
                "skipped_file_count": 0,
                "included_chars": 11,
                "truncated": False,
                "note": "temporary",
            },
        }

    def load_workspace_file_draft(self, file_id):
        self.calls.append(("workspace-file-draft", file_id))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "file_id": file_id,
            "path": "Notes/A.md",
            "has_draft": True,
            "draft_path": "C:/vault/.noteapp/drafts/file-a.draft",
            "updated_at": 1770000040002,
            "size_bytes": 10,
            "text": "# Draft\n",
        }

    def write_workspace_file_draft(self, file_id, text):
        self.calls.append(("write-workspace-file-draft", file_id, text))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "file_id": file_id,
            "path": "Notes/A.md",
            "has_draft": True,
            "draft_path": "C:/vault/.noteapp/drafts/file-a.draft",
            "updated_at": 1770000040002,
            "size_bytes": len(text.encode("utf-8")),
            "text": text,
        }

    def clear_workspace_file_draft(self, file_id):
        self.calls.append(("clear-workspace-file-draft", file_id))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "file_id": file_id,
            "path": "Notes/A.md",
            "has_draft": False,
            "draft_path": "C:/vault/.noteapp/drafts/file-a.draft",
        }

    def rebuild_workspace_search_index(self):
        self.calls.append(("rebuild-search-index", None))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "query": "",
            "total_count": 0,
            "results": [],
        }

    def search_workspace(self, query, *, limit=20):
        self.calls.append(("search-workspace", query, limit))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "query": query,
            "total_count": 1,
            "results": [
                {
                    "file_id": "file-a",
                    "path": "Notes/A.md",
                    "title": "A",
                    "snippet": "Local search result",
                }
            ],
        }

    def load_workspace_note_links(self, file_id):
        self.calls.append(("workspace-links", file_id))
        return {
            "schema_version": "v1",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "file_id": file_id,
            "path": "Notes/A.md",
            "outgoing": [
                {
                    "source_file_id": file_id,
                    "source_path": "Notes/A.md",
                    "link_text": "B",
                    "target_file_id": "file-b",
                    "target_path": "Notes/B.md",
                    "ordinal": 0,
                }
            ],
            "backlinks": [],
            "outgoing_count": 1,
            "backlink_count": 0,
        }

    def load_local_settings_snapshot(self):
        self.calls.append(("local-settings-snapshot", None))
        return {
            "schema_version": "v1",
            "source": "default",
            "settings_path": "C:/vault/.noteapp/settings.json",
            "vault_root": "C:/vault",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "sync": {
                "base_url": "https://sync.example.com",
                "bearer_token_configured": True,
                "request_timeout_seconds": 30.0,
                "blob_timeout_seconds": 60.0,
                "user_agent": "pkb-desktop-sync/0.1",
            },
            "appearance": {
                "theme": "dark",
            },
            "ai": {
                "local_model_status": "not_configured",
                "embedding_status": "not_configured",
            },
            "crypto": {
                "schema_version": "crypto-v1",
                "crypto_scheme": "placeholder-v1",
                "key_epoch": 1,
                "unlocked": False,
                "key_available": False,
                "storage_provider": "insecure-file",
                "key_ref": "C:/vault/.noteapp/test-crypto-key.json",
                "message": "no local e2ee-v1 vault key is available",
                "error": None,
            },
        }

    def write_local_settings(self, payload):
        self.calls.append(("write-local-settings", payload))
        return {
            "schema_version": "v1",
            "source": "file",
            "settings_path": "C:/vault/.noteapp/settings.json",
            "vault_root": "C:/vault",
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "sync": {
                "base_url": "https://sync.example.com",
                "bearer_token_configured": True,
                "request_timeout_seconds": 30.0,
                "blob_timeout_seconds": 60.0,
                "user_agent": "pkb-desktop-sync/0.1",
            },
            "appearance": {
                "theme": payload["appearance"]["theme"],
            },
            "ai": {
                "local_model_status": payload["ai"]["local_model_status"],
                "embedding_status": payload["ai"]["embedding_status"],
            },
            "crypto": {
                "schema_version": "crypto-v1",
                "crypto_scheme": "placeholder-v1",
                "key_epoch": 1,
                "unlocked": False,
                "key_available": False,
                "storage_provider": "insecure-file",
                "key_ref": "C:/vault/.noteapp/test-crypto-key.json",
                "message": "no local e2ee-v1 vault key is available",
                "error": None,
            },
        }

    def export_crypto_recovery_package(
        self,
        *,
        recovery_phrase,
        created_at=None,
        memory_kib=None,
        iterations=None,
    ):
        self.calls.append(
            (
                "crypto-recovery-export",
                recovery_phrase,
                created_at,
                memory_kib,
                iterations,
            )
        )
        package = {
            "schema_version": "e2ee-recovery-v1",
            "vault_id": "vault-001",
            "crypto_scheme": "e2ee-v1",
            "kdf": {
                "name": "argon2id",
                "memory_kib": memory_kib or 8,
                "iterations": iterations or 1,
                "parallelism": 1,
                "salt_b64": "AQEBAQEBAQEBAQEBAQEBAQ==",
            },
            "wrap": {
                "alg": "xchacha20-poly1305",
                "nonce_b64": "AgICAgICAgICAgICAgICAgICAgICAgIC",
                "ciphertext_b64": "ZmFrZQ==",
            },
            "created_at": created_at or 1770000040000,
            "checksum": "sha256:fake",
        }
        return {
            "schema_version": "e2ee-recovery-export-v1",
            "vault_id": "vault-001",
            "crypto_scheme": "e2ee-v1",
            "key_epoch": 1,
            "created_at": created_at or 1770000040000,
            "recovery_package": package,
            "recovery_package_json": json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True),
        }

    def import_crypto_recovery_package(self, package_payload, *, recovery_phrase):
        self.calls.append(("crypto-recovery-import", package_payload, recovery_phrase))
        return {
            "recovery": {
                "schema_version": "e2ee-recovery-import-v1",
                "vault_id": "vault-001",
                "crypto_scheme": "e2ee-v1",
                "key_epoch": 1,
                "imported": True,
                "message": "recovery package decrypted; local vault key is ready to store",
            },
            "crypto": {
                "schema_version": "crypto-v1",
                "crypto_scheme": "e2ee-v1",
                "key_epoch": 1,
                "unlocked": True,
                "key_available": True,
                "storage_provider": "insecure-file",
                "key_ref": "C:/vault/.noteapp/test-crypto-key.json",
                "message": "e2ee-v1 vault key is available",
                "error": None,
            },
        }

    def detect_local_changes(self):
        self.calls.append(("detect-local-changes", None))
        return self.detect_local_changes_payload

    def load_worker_state(self):
        self.calls.append(("worker-state", None))
        return self.worker_state_payload

    def load_worker_health(self):
        self.calls.append(("worker-health", None))
        return self.worker_health_payload

    def summarize_vault(self):
        self.calls.append(("vault-summary", None))
        return {
            "state": {
                "vault_id": "vault-001",
                "last_applied_revision": 7,
                "remote_head_revision": 7,
                "acked_revision": 7,
                "pending_ack_to_server": [],
                "commit_in_progress": False,
                "last_manifest_summary": "sha256:head7",
                "last_manifest_summary_status": "valid",
                "local_delete_sequence": 1,
                "has_unresolved_conflicts": False,
                "schema_version": "v1",
                "meta": None,
            },
            "changes": self.detect_local_changes_payload,
            "conflicts": {
                "state": {
                    "vault_id": "vault-001",
                    "last_applied_revision": 7,
                    "remote_head_revision": 7,
                    "acked_revision": 7,
                    "pending_ack_to_server": [],
                    "commit_in_progress": False,
                    "last_manifest_summary": "sha256:head7",
                    "last_manifest_summary_status": "valid",
                    "local_delete_sequence": 1,
                    "has_unresolved_conflicts": False,
                    "schema_version": "v1",
                    "meta": None,
                },
                "actual_has_unresolved_conflicts": False,
                "conflict_copies": [],
                "conflict_orphans": [],
            },
            "worker_health": self.worker_health_payload,
            "commit_gate": {
                "can_submit_commit": True,
                "blocking_reasons": [],
                "requires_full_pull": False,
                "has_active_commit_journal": False,
                "has_active_sync_apply_journal": False,
            },
        }

    def build_sync_panel_model(self, *, now_ms=None):
        self.calls.append(("sync-panel", now_ms))
        return {
            "level": "warning",
            "headline": "2 unresolved conflict artifacts",
            "detail": "Resolve local conflict copies before the next commit can be submitted.",
            "conflict_badge_count": 2,
            "change_badge_count": 1,
            "primary_action": {
                "action_id": "list-conflicts",
                "label": "Review Conflicts",
                "enabled": True,
                "emphasis": "primary",
                "command": "list-conflicts",
                "argv": [],
                "reason": None,
                "requires_confirmation": False,
            },
            "secondary_actions": [
                {
                    "action_id": "show-vault-summary",
                    "label": "Open Summary",
                    "enabled": True,
                    "emphasis": "normal",
                    "command": "vault-summary",
                    "argv": [],
                    "reason": None,
                    "requires_confirmation": False,
                }
            ],
            "summary": self.summarize_vault(),
        }

    def build_sync_center_model(self, *, now_ms=None):
        self.calls.append(("sync-center", now_ms))
        return {
            "cards": [
                {
                    "card_id": "conflicts",
                    "kind": "conflicts",
                    "level": "warning",
                    "title": "2 unresolved local conflict artifacts",
                    "body": "Conflict copies and orphan conflict files must be reviewed or cleared before commit submission can reopen.",
                    "badge_count": 2,
                    "actions": [
                        {
                            "action_id": "list-conflicts",
                            "label": "Review Conflicts",
                            "enabled": True,
                            "emphasis": "primary",
                            "command": "list-conflicts",
                            "argv": [],
                            "reason": None,
                            "requires_confirmation": False,
                        }
                    ],
                },
                {
                    "card_id": "activity",
                    "kind": "activity",
                    "level": "info",
                    "title": "1 recent sync actions recorded",
                    "body": "Latest action `list-conflicts` finished with status `executed` from `card:conflicts`.",
                    "badge_count": 1,
                    "actions": [
                        {
                            "action_id": "sync-activity",
                            "label": "Open Activity Feed",
                            "enabled": True,
                            "emphasis": "primary",
                            "command": "sync-activity",
                            "argv": ["--limit", "20"],
                            "reason": None,
                            "requires_confirmation": False,
                        }
                    ],
                },
            ],
            "panel": self.build_sync_panel_model(now_ms=now_ms),
            "summary": self.summarize_vault(),
            "recent_activity": self.sync_activity_payload,
        }

    def list_sync_activity(self, *, limit=20):
        self.calls.append(("sync-activity", limit))
        return self.sync_activity_payload

    def build_sync_shell_snapshot(self, *, now_ms=None, activity_limit=20):
        self.calls.append(("sync-shell-snapshot", now_ms, activity_limit))
        return {
            "generated_at_ms": now_ms,
            "vault_id": "vault-001",
            "device_id": "desktop-shanghai",
            "vault_root": "C:/vault",
            "sync_center": self.build_sync_center_model(now_ms=now_ms),
            "activity_feed": self.sync_activity_payload,
        }

    def execute_sync_action(self, action_id: str, *, now_ms=None):
        self.calls.append(("execute-sync-action", action_id, now_ms))
        return {
            "action": {
                "action_id": action_id,
                "label": "Review Conflicts",
                "enabled": True,
                "emphasis": "primary",
                "command": "list-conflicts",
                "argv": [],
                "reason": None,
                "requires_confirmation": False,
            },
            "source": "card:conflicts",
            "status": "executed",
            "payload": self.list_conflicts(),
            "message": None,
        }

    def execute_sync_action_and_snapshot(self, action_id: str, *, now_ms=None, activity_limit=20):
        self.calls.append(("execute-sync-action-and-snapshot", action_id, now_ms, activity_limit))
        return {
            "execution": self.execute_sync_action(action_id, now_ms=now_ms),
            "snapshot": self.build_sync_shell_snapshot(now_ms=now_ms, activity_limit=activity_limit),
        }

    def export_vault_package(self, package_path: Path, *, include_ai_raw: bool = False):
        self.calls.append(("export-vault", str(package_path), include_ai_raw))
        return {
            "package_path": str(package_path),
            "vault_id": "vault-001",
            "exported_paths": [
                ".vaultinfo",
                ".noteapp/filemap.json",
                ".noteapp/tombstone-ledger.jsonl",
                "Notes/Live.md",
            ],
            "included_ai_raw": include_ai_raw,
            "included_conflict_orphans": False,
        }

    def import_vault_package(self, package_path: Path):
        self.calls.append(("import-vault", str(package_path)))
        return {
            "package_path": str(package_path),
            "vault_id": "vault-001",
            "imported_paths": [
                ".vaultinfo",
                ".noteapp/filemap.json",
                ".noteapp/tombstone-ledger.jsonl",
                "Notes/Live.md",
            ],
            "restored_ai_raw": True,
            "restored_conflict_orphans": False,
            "state": {
                "vault_id": "vault-001",
                "last_applied_revision": 0,
                "remote_head_revision": 0,
                "acked_revision": 0,
                "pending_ack_to_server": [],
                "commit_in_progress": False,
                "last_manifest_summary": None,
                "last_manifest_summary_status": "stale",
                "local_delete_sequence": 4,
                "has_unresolved_conflicts": False,
                "schema_version": "v1",
                "meta": {"device_id": "desktop-shanghai"},
            },
        }

    def list_conflicts(self):
        self.calls.append(("list-conflicts", None))
        return {
            "state": {
                "vault_id": "vault-001",
                "last_applied_revision": 7,
                "remote_head_revision": 7,
                "acked_revision": 7,
                "pending_ack_to_server": [],
                "commit_in_progress": False,
                "last_manifest_summary": "sha256:head7",
                "last_manifest_summary_status": "valid",
                "local_delete_sequence": 1,
                "has_unresolved_conflicts": True,
                "schema_version": "v1",
                "meta": None,
            },
            "actual_has_unresolved_conflicts": True,
            "conflict_copies": [
                {
                    "kind": "conflict_copy",
                    "file_id": "file-conflict",
                    "path": "Notes/Live (conflict).md",
                    "exists_on_disk": True,
                    "conflict_source_file_id": "file-live",
                    "content_hash": "sha256:abc",
                }
            ],
            "conflict_orphans": [
                {
                    "kind": "conflict_orphan",
                    "path": ".noteapp/conflict-orphans/Orphan.md",
                    "exists_on_disk": True,
                    "file_id": None,
                    "conflict_source_file_id": None,
                    "content_hash": None,
                }
            ],
        }

    def resolve_conflicts(self, *, resolved_at: int, conflict_file_ids=None, orphan_relative_paths=None, resolve_all=False):
        self.calls.append(
            (
                "resolve-conflicts",
                resolved_at,
                [] if conflict_file_ids is None else list(conflict_file_ids),
                [] if orphan_relative_paths is None else list(orphan_relative_paths),
                resolve_all,
            )
        )
        return {
            "resolved_at": resolved_at,
            "removed_conflict_paths": {"file-conflict": "C:/vault/Notes/Live (conflict).md"},
            "removed_orphan_paths": ["C:/vault/.noteapp/conflict-orphans/Orphan.md"],
            "skipped_conflict_file_ids": [],
            "skipped_orphan_paths": [],
            "state": {
                "vault_id": "vault-001",
                "last_applied_revision": 7,
                "remote_head_revision": 7,
                "acked_revision": 7,
                "pending_ack_to_server": [],
                "commit_in_progress": False,
                "last_manifest_summary": "sha256:head7",
                "last_manifest_summary_status": "valid",
                "local_delete_sequence": 1,
                "has_unresolved_conflicts": False,
                "schema_version": "v1",
                "meta": None,
            },
        }

    def pull_and_ack(self, *, rewritten_at: int):
        self.calls.append(("pull", rewritten_at))
        if self.pull_and_ack_payload is not None:
            return self.pull_and_ack_payload
        return {"kind": "pull", "rewritten_at": rewritten_at}

    def pull_and_plan_apply(self, *, rewritten_at: int):
        self.calls.append(("pull-and-plan-apply", rewritten_at))
        if self.pull_and_plan_apply_payload is not None:
            return self.pull_and_plan_apply_payload
        return {"kind": "pull-and-plan-apply", "rewritten_at": rewritten_at}

    def pull_and_apply_nonblocking(self, *, rewritten_at: int):
        self.calls.append(("pull-and-apply-nonblocking", rewritten_at))
        if self.pull_and_apply_nonblocking_payload is not None:
            return self.pull_and_apply_nonblocking_payload
        return {"kind": "pull-and-apply-nonblocking", "rewritten_at": rewritten_at}

    def pull_and_apply(self, *, rewritten_at: int):
        self.calls.append(("pull-and-apply", rewritten_at))
        if self.pull_and_apply_payload is not None:
            return self.pull_and_apply_payload
        return {"kind": "pull-and-apply", "rewritten_at": rewritten_at}

    def resume_commit_recovery(self, *, normalized_at: int):
        self.calls.append(("recover", normalized_at))
        recover_count = sum(1 for call in self.calls if call[0] == "recover") - 1
        if recover_count in self.fail_recover_at_calls:
            raise RuntimeError(f"recover failed at call {recover_count}")
        if self.commit_recovery_payload is not None:
            return self.commit_recovery_payload
        return {"kind": "recover", "normalized_at": normalized_at}

    def resume_pull_apply_recovery(self, *, normalized_at: int):
        self.calls.append(("recover-pull-apply", normalized_at))
        if self.pull_apply_recovery_payload is not None:
            return self.pull_apply_recovery_payload
        return {"kind": "recover-pull-apply", "normalized_at": normalized_at}

    def download_blobs(self, blob_ids):
        self.calls.append(("download-blobs", list(blob_ids)))
        return BlobDownloadSessionResult(
            init=BlobDownloadInitExecutionResult(
                request=BlobDownloadInitRequestPayload(blob_ids=list(blob_ids)),
                response=BlobDownloadInitResponsePayload(
                    downloads=[
                        BlobDownloadCapability(
                            blob_id=blob_id,
                            download_url=f"https://blob.example.com/download/{blob_id}",
                            encrypted_size=3,
                            expires_at="2026-05-08T12:00:00Z",
                        )
                        for blob_id in blob_ids
                    ]
                ),
            ),
            downloaded_blobs={blob_id: b"xyz" for blob_id in blob_ids},
        )

    def list_file_versions(self, *, file_id, limit=50, cursor=None, include_pinned=True):
        self.calls.append(("file-versions", file_id, limit, cursor, include_pinned))
        return {
            "request": {
                "file_id": file_id,
                "limit": limit,
                "cursor": cursor,
                "include_pinned": include_pinned,
            },
            "response": {
                "file_id": file_id,
                "versions": [
                    {
                        "version_id": "fv-001",
                        "file_id": file_id,
                        "path_at_revision": "Meetings/XXX.md",
                        "revision": 2,
                        "content_hash": "sha256:meeting-v2",
                        "blob_id": "blob-meeting-v2",
                        "size": 2048,
                        "mtime": 1770000029990,
                        "created_at": 1770000030000,
                        "created_by_device": "desktop-shanghai",
                        "source": "manual_meeting_checkpoint",
                        "version_label": "客户会议",
                        "change_note": "确认行动项后保存",
                        "is_pinned": True,
                    }
                ],
                "next_cursor": None,
            },
        }

    def load_file_version_content(self, *, file_id, version_id, include_text=True):
        self.calls.append(("file-version-content", file_id, version_id, include_text))
        return {
            "schema_version": "v1",
            "file_id": file_id,
            "version_id": version_id,
            "content_base64": "IyBNZWV0aW5nCg==",
            "text": "# Meeting\n" if include_text else None,
        }

    def diff_file_version_with_current(self, *, file_id, version_id, context_lines=3):
        self.calls.append(("diff-file-version", file_id, version_id, context_lines))
        return {
            "schema_version": "v1",
            "file_id": file_id,
            "version_id": version_id,
            "is_binary": False,
            "diff_text": "--- old\n+++ current\n",
        }

    def restore_file_version(
        self,
        *,
        file_id,
        version_id,
        created_at,
        commit_intent_id=None,
        cleanup_normalized_at=None,
        version_label=None,
        change_note=None,
        is_pinned=False,
    ):
        self.calls.append(
            (
                "restore-file-version",
                file_id,
                version_id,
                created_at,
                commit_intent_id,
                cleanup_normalized_at,
                version_label,
                change_note,
                is_pinned,
            )
        )
        return {
            "schema_version": "v1",
            "file_id": file_id,
            "version_id": version_id,
            "created_at": created_at,
            "commit_intent_id": commit_intent_id,
            "cleanup_normalized_at": cleanup_normalized_at,
            "version_label": version_label,
            "change_note": change_note,
            "is_pinned": is_pinned,
        }

    def update_file_version(self, *, version_id, version_label=None, change_note=None, is_pinned=None):
        self.calls.append(("update-file-version", version_id, version_label, change_note, is_pinned))
        return {
            "request": {
                "version_id": version_id,
                "version_label": version_label,
                "change_note": change_note,
                "is_pinned": is_pinned,
            },
            "response": {
                "version": {
                    "version_id": version_id,
                    "version_label": version_label,
                    "change_note": change_note,
                    "is_pinned": is_pinned,
                }
            },
        }

    def list_vault_devices(self):
        self.calls.append(("vault-devices",))
        return {
            "response": {
                "vault_id": "vault-001",
                "head_revision": 9,
                "inactive_after_ms": 604800000,
                "devices": [
                    {
                        "device_id": "desktop-shanghai",
                        "device_name": "Desktop",
                        "platform": "desktop",
                        "app_version": "1.0.43",
                        "protocol_version": "v1",
                        "registered_at_ms": 1770000000000,
                        "last_seen_at_ms": 1770000005000,
                        "acked_revision": 9,
                        "is_current_device": True,
                        "is_revoked": False,
                        "is_inactive_candidate": False,
                    }
                ],
            }
        }

    def heartbeat_vault_device(self):
        self.calls.append(("heartbeat-vault-device",))
        return {
            "response": {
                "vault_id": "vault-001",
                "device_id": "desktop-shanghai",
                "last_seen_at_ms": 1770000006000,
                "acked_revision": 9,
                "head_revision": 9,
            }
        }

    def revoke_device(self, *, device_id):
        self.calls.append(("revoke-device", device_id))
        return {
            "device_id": device_id,
            "revoked": True,
        }

    def download_and_decrypt_pull_required_blobs(self, pull_result):
        self.calls.append(("download-and-decrypt-pull-required-blobs", pull_result))
        if self.download_and_decrypt_pull_payload is not None:
            return self.download_and_decrypt_pull_payload
        return {
            "pull": pull_result,
            "plan": {"blob_ids": [], "files": [], "revision": 0, "vault_id": "vault-001"},
            "download": None,
            "plaintext_by_file_id_base64": {},
        }

    def materialize_pull_required_plaintext(self, resolved, output_root: Path):
        self.calls.append(("materialize-pull-required-plaintext", resolved, output_root))
        if self.materialize_pull_required_plaintext_payload is not None:
            return self.materialize_pull_required_plaintext_payload
        return {}

    def stage_pull_required_plaintext_for_apply(self, resolved, *, started_at: int):
        self.calls.append(("stage-pull-required-plaintext-for-apply", resolved, started_at))
        if self.stage_pull_required_plaintext_payload is not None:
            return self.stage_pull_required_plaintext_payload
        return {}

    def submit_commit(
        self,
        *,
        created_at: int,
        commit_intent_id=None,
        cleanup_normalized_at=None,
        content_by_file_id,
        encrypted_blob_by_file_id,
    ):
        self.calls.append(
            (
                "submit-commit",
                created_at,
                commit_intent_id,
                cleanup_normalized_at,
                content_by_file_id,
                encrypted_blob_by_file_id,
            )
        )
        return {
            "kind": "submit-commit",
            "created_at": created_at,
            "commit_intent_id": commit_intent_id,
            "content_sizes": {key: len(value) for key, value in content_by_file_id.items()},
            "encrypted_sizes": {key: len(value) for key, value in encrypted_blob_by_file_id.items()},
        }

    def submit_workspace_commit(
        self,
        *,
        created_at: int,
        file_ids,
        commit_intent_id=None,
        cleanup_normalized_at=None,
        encrypted_blob_by_file_id=None,
        file_version_directives=None,
    ):
        call = (
            "submit-workspace-commit",
            created_at,
            list(file_ids),
            commit_intent_id,
            cleanup_normalized_at,
            encrypted_blob_by_file_id,
        )
        if file_version_directives is not None:
            call = call + (file_version_directives,)
        self.calls.append(call)
        submit_count = sum(1 for call in self.calls if call[0] == "submit-workspace-commit") - 1
        if submit_count in self.fail_submit_workspace_at_calls:
            raise RuntimeError(f"submit failed at call {submit_count}")
        return {
            "kind": "submit-workspace-commit",
            "created_at": created_at,
            "file_ids": list(file_ids),
            "commit_intent_id": commit_intent_id,
            "encrypted_sizes": (
                None
                if encrypted_blob_by_file_id is None
                else {
                    key: len(value) for key, value in encrypted_blob_by_file_id.items()
                }
            ),
            **(
                {
                    "file_version_directives": [
                        {
                            "file_id": item.file_id,
                            "source": item.source,
                            "version_label": item.version_label,
                            "change_note": item.change_note,
                            "is_pinned": item.is_pinned,
                        }
                        for item in file_version_directives
                    ]
                }
                if file_version_directives is not None
                else {}
            ),
        }

    def submit_detected_changes(
        self,
        *,
        created_at: int,
        commit_intent_id=None,
        cleanup_normalized_at=None,
    ):
        self.calls.append(
            (
                "submit-detected-commit",
                created_at,
                commit_intent_id,
                cleanup_normalized_at,
            )
        )
        return {
            "kind": "submit-detected-commit",
            "created_at": created_at,
            "commit_intent_id": commit_intent_id,
            "cleanup_normalized_at": cleanup_normalized_at,
        }

    def submit_detected_changes_if_needed(
        self,
        *,
        created_at: int,
        commit_intent_id=None,
        cleanup_normalized_at=None,
    ):
        self.calls.append(
            (
                "submit-detected-commit",
                created_at,
                commit_intent_id,
                cleanup_normalized_at,
            )
        )
        if self.skip_submit_detected_if_needed:
            return None
        return {
            "kind": "submit-detected-commit",
            "created_at": created_at,
            "commit_intent_id": commit_intent_id,
            "cleanup_normalized_at": cleanup_normalized_at,
        }


class DesktopCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.created = []
        self.service = FakeService()

    def _builder(self, config, vault_root: Path, blob_crypto_provider=None):
        self.created.append((config, vault_root, blob_crypto_provider))
        return self.service

    def _run(self, *argv: str, sleep=None, now_ms_provider=None):
        stdout = io.StringIO()
        exit_code = run_cli(
            argv,
            stdout=stdout,
            service_builder=self._builder,
            sleep=(lambda _: None) if sleep is None else sleep,
            now_ms_provider=(lambda: 1770000040000) if now_ms_provider is None else now_ms_provider,
        )
        return exit_code, json.loads(stdout.getvalue())

    def _write_package(self, package_path: Path, *, vault_id: str = "vault-001") -> None:
        package_path.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(package_path, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(".vaultinfo", json.dumps({"vault_id": vault_id}) + "\n")
            archive.writestr(
                ".noteapp/filemap.json",
                json.dumps(
                    {
                        "schema_version": "v1",
                        "vault_id": vault_id,
                        "updated_at": 1770000040000,
                        "files": [],
                    }
                )
                + "\n",
            )
            archive.writestr(".noteapp/tombstone-ledger.jsonl", "")

    def test_init_command_builds_service_and_returns_json(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "--bearer-token",
            "token-1",
            "init",
            "--now-ms",
            "1770000040000",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, {"kind": "init", "value": 1770000040000})
        config, vault_root, blob_crypto_provider = self.created[0]
        self.assertEqual(config.base_url, "https://sync.example.com")
        self.assertEqual(config.vault_id, "vault-001")
        self.assertEqual(config.device_id, "desktop-shanghai")
        self.assertEqual(config.bearer_token, "token-1")
        self.assertEqual(vault_root, Path("C:/vault"))
        self.assertIsInstance(blob_crypto_provider, PlaceholderDesktopBlobCryptoProvider)
        self.assertEqual(self.service.calls, [("init", 1770000040000)])

    def test_init_command_can_build_e2ee_provider_from_base64_key(self) -> None:
        vault_key = b"\x07" * 32
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "--crypto-scheme",
            "e2ee-v1",
            "--vault-key-base64",
            base64.b64encode(vault_key).decode("ascii"),
            "init",
            "--now-ms",
            "1770000040000",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, {"kind": "init", "value": 1770000040000})
        provider = self.created[0][2]
        self.assertIsInstance(provider, E2EEDesktopBlobCryptoProvider)
        self.assertEqual(provider.vault_id, "vault-001")
        self.assertEqual(provider.vault_key, vault_key)

    def test_init_command_rejects_e2ee_without_vault_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "e2ee-v1 requires exactly one"):
            self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "--crypto-scheme",
                "e2ee-v1",
                "init",
            )

        self.assertEqual(self.created, [])

    def test_crypto_status_reports_insecure_store_state_for_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(
                os.environ,
                {
                    "NOTEAPP_ALLOW_INSECURE_CRYPTO_STORE": "true",
                    "NOTEAPP_CRYPTO_STORE_DIR": tmpdir,
                },
                clear=False,
            ):
                exit_code, payload = self._run(
                    "--vault-root",
                    "C:/vault",
                    "--base-url",
                    "https://sync.example.com",
                    "--vault-id",
                    "vault-001",
                    "--device-id",
                    "desktop-shanghai",
                    "crypto-status",
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["crypto_scheme"], "placeholder-v1")
        self.assertFalse(payload["unlocked"])
        self.assertEqual(payload["storage_provider"], "insecure-file")

    def test_crypto_unlock_and_lock_use_local_crypto_store(self) -> None:
        vault_key = b"\x08" * 32
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(
                os.environ,
                {
                    "NOTEAPP_ALLOW_INSECURE_CRYPTO_STORE": "true",
                    "NOTEAPP_CRYPTO_STORE_DIR": tmpdir,
                },
                clear=False,
            ):
                exit_code, unlocked = self._run(
                    "--vault-root",
                    "C:/vault",
                    "--base-url",
                    "https://sync.example.com",
                    "--vault-id",
                    "vault-001",
                    "--device-id",
                    "desktop-shanghai",
                    "crypto-unlock",
                    "--vault-key-base64",
                    base64.b64encode(vault_key).decode("ascii"),
                )
                status_exit_code, status = self._run(
                    "--vault-root",
                    "C:/vault",
                    "--base-url",
                    "https://sync.example.com",
                    "--vault-id",
                    "vault-001",
                    "--device-id",
                    "desktop-shanghai",
                    "crypto-status",
                )
                lock_exit_code, locked = self._run(
                    "--vault-root",
                    "C:/vault",
                    "--base-url",
                    "https://sync.example.com",
                    "--vault-id",
                    "vault-001",
                    "--device-id",
                    "desktop-shanghai",
                    "crypto-lock",
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(status_exit_code, 0)
        self.assertEqual(lock_exit_code, 0)
        self.assertEqual(unlocked["crypto_scheme"], "e2ee-v1")
        self.assertTrue(unlocked["unlocked"])
        self.assertTrue(status["key_available"])
        self.assertEqual(locked["crypto_scheme"], "placeholder-v1")
        self.assertFalse(locked["unlocked"])

    def test_crypto_recovery_export_writes_package_json(self) -> None:
        vault_key = b"\x0c" * 32
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "recovery.json"
            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "--crypto-scheme",
                "e2ee-v1",
                "--vault-key-base64",
                base64.b64encode(vault_key).decode("ascii"),
                "crypto-recovery-export",
                "--recovery-phrase",
                "meeting recovery phrase",
                "--created-at",
                "1770000040000",
                "--kdf-memory-kib",
                "8",
                "--kdf-iterations",
                "1",
                "--output-package-json",
                str(output_path),
            )

            package = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["schema_version"], "e2ee-recovery-export-v1")
        self.assertEqual(payload["recovery_package"]["schema_version"], "e2ee-recovery-v1")
        self.assertEqual(package["schema_version"], "e2ee-recovery-v1")
        self.assertEqual(package["vault_id"], "vault-001")

    def test_crypto_recovery_import_requires_recovery_package(self) -> None:
        with self.assertRaisesRegex(ValueError, "请提供恢复包文件"):
            self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "crypto-recovery-import",
                "--recovery-phrase",
                "phrase only",
            )

    def test_crypto_recovery_import_unlocks_local_crypto_store(self) -> None:
        vault_key = b"\x0d" * 32
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "recovery.json"
            store_root = Path(tmpdir) / "store"
            self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "--crypto-scheme",
                "e2ee-v1",
                "--vault-key-base64",
                base64.b64encode(vault_key).decode("ascii"),
                "crypto-recovery-export",
                "--recovery-phrase",
                "meeting recovery phrase",
                "--created-at",
                "1770000040000",
                "--kdf-memory-kib",
                "8",
                "--kdf-iterations",
                "1",
                "--output-package-json",
                str(output_path),
            )
            with mock.patch.dict(
                os.environ,
                {
                    "NOTEAPP_ALLOW_INSECURE_CRYPTO_STORE": "true",
                    "NOTEAPP_CRYPTO_STORE_DIR": str(store_root),
                },
                clear=False,
            ):
                exit_code, payload = self._run(
                    "--vault-root",
                    "C:/vault",
                    "--base-url",
                    "https://sync.example.com",
                    "--vault-id",
                    "vault-001",
                    "--device-id",
                    "desktop-shanghai",
                    "crypto-recovery-import",
                    "--recovery-phrase",
                    "meeting recovery phrase",
                    "--input-json",
                    str(output_path),
                )

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["recovery"]["imported"])
        self.assertTrue(payload["crypto"]["unlocked"])
        self.assertEqual(self.service.calls[-1][0], "crypto-recovery-import")

    def test_status_command_routes_to_load_snapshot(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "status",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, {"files": 1, "kind": "status"})
        self.assertEqual(self.service.calls, [("status", None)])

    def test_detect_local_changes_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "detect-local-changes",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["modified_file_ids"], ["file-a"])
        self.assertEqual(payload["missing_file_ids"], ["file-b"])
        self.assertEqual(self.service.calls, [("detect-local-changes", None)])

    def test_worker_state_command_routes_to_load_worker_state(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "worker-state",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["latest_failure"]["iteration"], 1)
        self.assertEqual(self.service.calls, [("worker-state", None)])

    def test_worker_health_command_routes_to_load_worker_health(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "worker-health",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(self.service.calls, [("worker-health", None)])

    def test_vault_summary_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "vault-summary",
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["commit_gate"]["can_submit_commit"])
        self.assertEqual(payload["changes"]["change_count"], 2)
        self.assertEqual(self.service.calls, [("vault-summary", None)])

    def test_local_settings_snapshot_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "--bearer-token",
            "token-1",
            "local-settings-snapshot",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["schema_version"], "v1")
        self.assertEqual(payload["sync"]["base_url"], "https://sync.example.com")
        self.assertTrue(payload["sync"]["bearer_token_configured"])
        self.assertEqual(payload["appearance"]["theme"], "dark")
        self.assertEqual(self.service.calls, [("local-settings-snapshot", None)])

    def test_workspace_files_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "workspace-files",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["schema_version"], "v1")
        self.assertEqual(payload["total_count"], 1)
        self.assertEqual(payload["files"][0]["path"], "Notes/A.md")
        self.assertTrue(payload["files"][0]["exists_on_disk"])
        self.assertEqual(self.service.calls, [("workspace-files", None)])

    def test_import_existing_workspace_files_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "import-existing-workspace-files",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["total_count"], 1)
        self.assertEqual(
            self.service.calls,
            [("import-existing-workspace-files", None), ("workspace-files", None)],
        )

    def test_workspace_file_content_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "workspace-file-content",
            "--file-id",
            "file-a",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["schema_version"], "v1")
        self.assertEqual(payload["file_id"], "file-a")
        self.assertEqual(payload["path"], "Notes/A.md")
        self.assertEqual(payload["text"], "# A\n")
        self.assertEqual(self.service.calls, [("workspace-file-content", "file-a")])

    def test_workspace_file_blob_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "workspace-file-blob",
            "--file-id",
            "file-attachment",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["content_base64"], "iVBORw0KGgo=")
        self.assertEqual(payload["mime_type"], "image/png")
        self.assertEqual(self.service.calls, [("workspace-file-blob", "file-attachment")])

    def test_write_workspace_file_content_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "write-workspace-file-content",
            "--file-id",
            "file-a",
            "--input-text",
            "# Updated\n",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["file_id"], "file-a")
        self.assertEqual(payload["text"], "# Updated\n")
        self.assertEqual(payload["tracked_content_hash"], "sha256:aaa")
        self.assertEqual(
            self.service.calls,
            [("write-workspace-file-content", "file-a", "# Updated\n")],
        )

    def test_write_workspace_file_content_command_reads_input_text_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "note.md"
            input_path.write_text("# File Input\n", encoding="utf-8")

            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "write-workspace-file-content",
                "--file-id",
                "file-a",
                "--input-text-file",
                str(input_path),
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["text"], "# File Input\n")
        self.assertEqual(
            self.service.calls,
            [("write-workspace-file-content", "file-a", "# File Input\n")],
        )

    def test_create_workspace_note_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "create-workspace-note",
            "--path",
            "Notes/New.md",
            "--input-text",
            "# New\n",
            "--now-ms",
            "1770000041111",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["operation"], "create")
        self.assertEqual(payload["file"]["path"], "Notes/New.md")
        self.assertEqual(
            self.service.calls,
            [("create-workspace-note", "Notes/New.md", "# New\n", 1770000041111)],
        )

    def test_create_workspace_attachment_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "create-workspace-attachment",
            "--file-name",
            "photo.png",
            "--input-base64",
            "aW1hZ2U=",
            "--now-ms",
            "1770000041111",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["operation"], "create_attachment")
        self.assertEqual(payload["file"]["path"], "Attachments/photo.png")
        self.assertEqual(
            self.service.calls,
            [("create-workspace-attachment", "photo.png", b"image", 1770000041111)],
        )

    def test_rename_workspace_note_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "rename-workspace-note",
            "--file-id",
            "file-a",
            "--path",
            "Notes/Renamed.md",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["operation"], "rename")
        self.assertEqual(self.service.calls, [("rename-workspace-note", "file-a", "Notes/Renamed.md", None)])

    def test_move_workspace_note_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "move-workspace-note",
            "--file-id",
            "file-a",
            "--path",
            "Archive/Renamed.md",
            "--now-ms",
            "1770000042222",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["operation"], "move")
        self.assertEqual(payload["file"]["file_id"], "file-a")
        self.assertEqual(payload["file"]["path"], "Archive/Renamed.md")
        self.assertEqual(
            self.service.calls,
            [("move-workspace-note", "file-a", "Archive/Renamed.md", 1770000042222)],
        )

    def test_delete_workspace_note_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "delete-workspace-note",
            "--file-id",
            "file-a",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["operation"], "delete")
        self.assertEqual(self.service.calls, [("delete-workspace-note", "file-a", None)])

    def test_workspace_trash_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "workspace-trash",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["total_count"], 1)
        self.assertEqual(payload["items"][0]["file_id"], "file-a")
        self.assertEqual(self.service.calls, [("workspace-trash",)])

    def test_restore_workspace_trash_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "restore-workspace-trash",
            "--file-id",
            "file-a",
            "--now-ms",
            "1770000042222",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["operation"], "restore")
        self.assertEqual(self.service.calls, [("restore-workspace-trash", "file-a", 1770000042222)])

    def test_purge_workspace_trash_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "purge-workspace-trash",
            "--file-id",
            "file-a",
            "--now-ms",
            "1770000043333",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["total_count"], 0)
        self.assertEqual(self.service.calls, [("purge-workspace-trash", "file-a", 1770000043333)])

    def test_empty_workspace_trash_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "empty-workspace-trash",
            "--now-ms",
            "1770000044444",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["total_count"], 0)
        self.assertEqual(self.service.calls, [("empty-workspace-trash", 1770000044444)])

    def test_compile_ai_wiki_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "compile-ai-wiki",
            "--now-ms",
            "1770000045555",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["artifact_count"], 1)
        self.assertEqual(payload["index_path"], ".ai/index.md")
        self.assertEqual(self.service.calls, [("compile-ai-wiki", 1770000045555)])

    def test_ask_ai_wiki_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "ask-ai-wiki",
            "--question",
            "What is local search?",
            "--limit",
            "3",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["citation_count"], 1)
        self.assertEqual(payload["model_status"], "local_deterministic")
        self.assertEqual(self.service.calls, [("ask-ai-wiki", "What is local search?", 3)])

    def test_ai_provider_health_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "ai-provider-health",
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["configured"])
        self.assertEqual(payload["status"], "available")
        self.assertEqual(self.service.calls, [("ai-provider-health", None)])

    def test_ai_context_task_command_routes_to_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "request.json"
            input_path.write_text(
                json.dumps(
                    {
                        "context": {
                            "type": "selected_files",
                            "file_ids": ["file-a"],
                        },
                        "instruction": "Write a report",
                        "output": {"mode": "preview"},
                        "max_files": 5,
                        "max_chars_per_file": 1200,
                        "max_total_chars": 2400,
                    }
                ),
                encoding="utf-8",
            )

            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "ai-context-task",
                "--input-json",
                str(input_path),
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["answer"], "Context answer [1].")
        self.assertEqual(
            self.service.calls,
            [
                (
                    "ai-context-task",
                    "selected_files",
                    "Write a report",
                    ["file-a"],
                    None,
                    True,
                    5,
                    1200,
                    2400,
                )
            ],
        )

    def test_workspace_file_draft_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "workspace-file-draft",
            "--file-id",
            "file-a",
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["has_draft"])
        self.assertEqual(payload["text"], "# Draft\n")
        self.assertEqual(self.service.calls, [("workspace-file-draft", "file-a")])

    def test_write_workspace_file_draft_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "write-workspace-file-draft",
            "--file-id",
            "file-a",
            "--input-text",
            "# Draft edit\n",
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["has_draft"])
        self.assertEqual(payload["text"], "# Draft edit\n")
        self.assertEqual(
            self.service.calls,
            [("write-workspace-file-draft", "file-a", "# Draft edit\n")],
        )

    def test_clear_workspace_file_draft_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "clear-workspace-file-draft",
            "--file-id",
            "file-a",
        )

        self.assertEqual(exit_code, 0)
        self.assertFalse(payload["has_draft"])
        self.assertEqual(self.service.calls, [("clear-workspace-file-draft", "file-a")])

    def test_rebuild_search_index_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "rebuild-search-index",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["schema_version"], "v1")
        self.assertEqual(self.service.calls, [("rebuild-search-index", None)])

    def test_search_workspace_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "search-workspace",
            "--query",
            "local",
            "--limit",
            "7",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["query"], "local")
        self.assertEqual(payload["results"][0]["file_id"], "file-a")
        self.assertEqual(self.service.calls, [("search-workspace", "local", 7)])

    def test_workspace_links_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "workspace-links",
            "--file-id",
            "file-a",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["file_id"], "file-a")
        self.assertEqual(payload["outgoing"][0]["target_file_id"], "file-b")
        self.assertEqual(self.service.calls, [("workspace-links", "file-a")])

    def test_write_local_settings_command_routes_to_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "settings.json"
            input_path.write_text(
                json.dumps(
                    {
                        "appearance": {"theme": "light"},
                        "ai": {
                            "local_model_status": "available",
                            "embedding_status": "indexing",
                        },
                    }
                ),
                encoding="utf-8",
            )

            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "--bearer-token",
                "token-1",
                "write-local-settings",
                "--input-json",
                str(input_path),
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["source"], "file")
        self.assertEqual(payload["appearance"]["theme"], "light")
        self.assertEqual(payload["ai"]["local_model_status"], "available")
        self.assertEqual(payload["ai"]["embedding_status"], "indexing")
        self.assertEqual(
            self.service.calls,
            [
                (
                    "write-local-settings",
                    {
                        "appearance": {"theme": "light"},
                        "ai": {
                            "local_model_status": "available",
                            "embedding_status": "indexing",
                        },
                    },
                )
            ],
        )

    def test_sync_panel_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-panel",
            "--now-ms",
            "1770000040123",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["level"], "warning")
        self.assertEqual(payload["primary_action"]["action_id"], "list-conflicts")
        self.assertEqual(payload["primary_action"]["command"], "list-conflicts")
        self.assertEqual(
            self.service.calls,
            [("sync-panel", 1770000040123), ("vault-summary", None)],
        )

    def test_sync_center_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-center",
            "--now-ms",
            "1770000040456",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["cards"][0]["card_id"], "conflicts")
        self.assertEqual(payload["cards"][1]["card_id"], "activity")
        self.assertEqual(payload["panel"]["primary_action"]["action_id"], "list-conflicts")
        self.assertEqual(payload["recent_activity"]["records"][0]["action_id"], "list-conflicts")
        self.assertEqual(
            self.service.calls,
            [
                ("sync-center", 1770000040456),
                ("sync-panel", 1770000040456),
                ("vault-summary", None),
                ("vault-summary", None),
            ],
        )

    def test_sync_shell_snapshot_command_routes_to_service_and_can_write_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "sync-shell-snapshot.json"
            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "sync-shell-snapshot",
                "--now-ms",
                "1770000040555",
                "--activity-limit",
                "7",
                "--output-json",
                str(output_path),
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["generated_at_ms"], 1770000040555)
            self.assertEqual(payload["sync_center"]["cards"][0]["card_id"], "conflicts")
            self.assertEqual(payload["activity_feed"]["total_count"], 1)
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8")), payload)
            self.assertEqual(
                self.service.calls,
                [
                    ("sync-shell-snapshot", 1770000040555, 7),
                    ("sync-center", 1770000040555),
                    ("sync-panel", 1770000040555),
                    ("vault-summary", None),
                    ("vault-summary", None),
                ],
            )

    def test_sync_activity_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-activity",
            "--limit",
            "5",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["total_count"], 1)
        self.assertEqual(payload["records"][0]["action_id"], "list-conflicts")
        self.assertEqual(self.service.calls, [("sync-activity", 5)])

    def test_execute_sync_action_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "execute-sync-action",
            "--action-id",
            "list-conflicts",
            "--now-ms",
            "1770000040666",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "executed")
        self.assertEqual(payload["action"]["action_id"], "list-conflicts")
        self.assertEqual(
            self.service.calls,
            [
                ("execute-sync-action", "list-conflicts", 1770000040666),
                ("list-conflicts", None),
            ],
        )

    def test_execute_sync_action_and_snapshot_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "execute-sync-action-and-snapshot",
            "--action-id",
            "list-conflicts",
            "--now-ms",
            "1770000040777",
            "--activity-limit",
            "9",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["execution"]["status"], "executed")
        self.assertEqual(payload["snapshot"]["generated_at_ms"], 1770000040777)
        self.assertEqual(payload["snapshot"]["activity_feed"]["total_count"], 1)
        self.assertEqual(
            self.service.calls,
            [
                ("execute-sync-action-and-snapshot", "list-conflicts", 1770000040777, 9),
                ("execute-sync-action", "list-conflicts", 1770000040777),
                ("list-conflicts", None),
                ("sync-shell-snapshot", 1770000040777, 9),
                ("sync-center", 1770000040777),
                ("sync-panel", 1770000040777),
                ("vault-summary", None),
                ("vault-summary", None),
            ],
        )

    def test_export_vault_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "export-vault",
            "--output-package",
            "C:/exports/vault.zip",
            "--include-ai-raw",
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["included_ai_raw"])
        self.assertEqual(
            self.service.calls,
            [("export-vault", "C:\\exports\\vault.zip", True)],
        )

    def test_export_vault_command_can_infer_local_vault_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_root = Path(tmpdir)
            (vault_root / ".noteapp").mkdir(parents=True, exist_ok=True)
            (vault_root / ".noteapp" / "filemap.json").write_text(
                json.dumps(
                    {
                        "schema_version": "v1",
                        "vault_id": "vault-local",
                        "updated_at": 1770000040000,
                        "files": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            exit_code, _ = self._run(
                "--vault-root",
                str(vault_root),
                "--base-url",
                "https://sync.example.com",
                "--device-id",
                "desktop-shanghai",
                "export-vault",
                "--output-package",
                "C:/exports/vault.zip",
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(self.created[0][0].vault_id, "vault-local")

    def test_import_vault_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "import-vault",
            "--input-package",
            "C:/exports/vault.zip",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["state"]["last_manifest_summary_status"], "stale")
        self.assertEqual(
            self.service.calls,
            [("import-vault", "C:\\exports\\vault.zip")],
        )

    def test_import_vault_command_can_infer_vault_id_from_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            package_path = Path(tmpdir) / "vault.zip"
            self._write_package(package_path, vault_id="vault-imported")

            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--device-id",
                "desktop-shanghai",
                "import-vault",
                "--input-package",
                str(package_path),
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["state"]["last_manifest_summary_status"], "stale")
        self.assertEqual(self.created[0][0].vault_id, "vault-imported")

    def test_inspect_vault_package_command_reads_package_without_building_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            package_path = Path(tmpdir) / "vault.zip"
            self._write_package(package_path, vault_id="vault-inspected")

            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--device-id",
                "desktop-shanghai",
                "inspect-vault-package",
                "--input-package",
                str(package_path),
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["vault_id"], "vault-inspected")
        self.assertFalse(payload["includes_ai_raw"])
        self.assertEqual(self.created, [])

    def test_list_conflicts_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "list-conflicts",
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["actual_has_unresolved_conflicts"])
        self.assertEqual(payload["conflict_copies"][0]["file_id"], "file-conflict")
        self.assertEqual(self.service.calls, [("list-conflicts", None)])

    def test_resolve_conflicts_command_routes_selected_targets(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "resolve-conflicts",
            "--resolved-at",
            "1770000040090",
            "--file-id",
            "file-conflict",
            "--orphan-path",
            ".noteapp/conflict-orphans/Orphan.md",
        )

        self.assertEqual(exit_code, 0)
        self.assertFalse(payload["state"]["has_unresolved_conflicts"])
        self.assertEqual(
            self.service.calls,
            [
                (
                    "resolve-conflicts",
                    1770000040090,
                    ["file-conflict"],
                    [".noteapp/conflict-orphans/Orphan.md"],
                    False,
                )
            ],
        )

    def test_resolve_conflicts_command_supports_resolve_all(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "resolve-conflicts",
            "--resolved-at",
            "1770000040100",
            "--all",
        )

        self.assertEqual(exit_code, 0)
        self.assertFalse(payload["state"]["has_unresolved_conflicts"])
        self.assertEqual(
            self.service.calls,
            [
                (
                    "resolve-conflicts",
                    1770000040100,
                    [],
                    [],
                    True,
                )
            ],
        )

    def test_pull_command_routes_rewritten_at(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "pull",
            "--rewritten-at",
            "1770000040100",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, {"kind": "pull", "rewritten_at": 1770000040100})
        self.assertEqual(self.service.calls, [("pull", 1770000040100)])

    def test_pull_command_can_download_required_blobs(self) -> None:
        self.service.pull_and_ack_payload = {
            "pull": {
                "reconcile": {
                    "applied": {
                        "required_blob_ids": ["blob-a", "blob-b"],
                    }
                }
            }
        }

        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "pull",
            "--rewritten-at",
            "1770000040100",
            "--download-required-blobs",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "pull": {
                    "pull": {
                        "reconcile": {
                            "applied": {
                                "required_blob_ids": ["blob-a", "blob-b"],
                            }
                        }
                    }
                },
                "download": {
                    "init": {
                        "request": {"blob_ids": ["blob-a", "blob-b"]},
                        "response": {
                            "downloads": [
                                {
                                    "blob_id": "blob-a",
                                    "download_url": "https://blob.example.com/download/blob-a",
                                    "encrypted_size": 3,
                                    "expires_at": "2026-05-08T12:00:00Z",
                                    "headers": None,
                                },
                                {
                                    "blob_id": "blob-b",
                                    "download_url": "https://blob.example.com/download/blob-b",
                                    "encrypted_size": 3,
                                    "expires_at": "2026-05-08T12:00:00Z",
                                    "headers": None,
                                },
                            ]
                        },
                    },
                    "downloaded_blobs_base64": {
                        "blob-a": base64.b64encode(b"xyz").decode("ascii"),
                        "blob-b": base64.b64encode(b"xyz").decode("ascii"),
                    },
                },
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                ("pull", 1770000040100),
                ("download-blobs", ["blob-a", "blob-b"]),
            ],
        )

    def test_pull_command_can_write_downloaded_required_blobs(self) -> None:
        self.service.pull_and_ack_payload = {
            "pull": {
                "reconcile": {
                    "applied": {
                        "required_blob_ids": ["blob-a"],
                    }
                }
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "pull",
                "--rewritten-at",
                "1770000040100",
                "--download-required-blobs",
                "--output-dir",
                tmpdir,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                payload,
                {
                    "pull": {
                        "pull": {
                            "reconcile": {
                                "applied": {
                                    "required_blob_ids": ["blob-a"],
                                }
                            }
                        }
                    },
                    "download": {
                        "init": {
                            "request": {"blob_ids": ["blob-a"]},
                            "response": {
                                "downloads": [
                                    {
                                        "blob_id": "blob-a",
                                        "download_url": "https://blob.example.com/download/blob-a",
                                        "encrypted_size": 3,
                                        "expires_at": "2026-05-08T12:00:00Z",
                                        "headers": None,
                                    }
                                ]
                            },
                        },
                        "written_blob_paths": {
                            "blob-a": str(Path(tmpdir) / "blob-a.blob"),
                        },
                    },
                },
            )
            self.assertEqual((Path(tmpdir) / "blob-a.blob").read_bytes(), b"xyz")

    def test_pull_command_can_download_and_decrypt_required_blobs(self) -> None:
        self.service.pull_and_ack_payload = {
            "pull": {
                "reconcile": {
                    "applied": {
                        "required_blob_ids": ["blob-a"],
                    }
                }
            }
        }
        self.service.download_and_decrypt_pull_payload = {
            "pull": self.service.pull_and_ack_payload,
            "plan": {
                "vault_id": "vault-001",
                "revision": 8,
                "blob_ids": ["blob-a"],
                "files": [
                    {
                        "file_id": "file-a",
                        "path": "Notes/A.md",
                        "type": "note",
                        "blob_id": "blob-a",
                        "content_hash": "sha256:abc",
                    }
                ],
            },
            "download": {
                "init": {
                    "request": {"blob_ids": ["blob-a"]},
                    "response": {
                        "downloads": [
                            {
                                "blob_id": "blob-a",
                                "download_url": "https://blob.example.com/download/blob-a",
                                "encrypted_size": 3,
                                "expires_at": "2026-05-08T12:00:00Z",
                                "headers": None,
                            }
                        ]
                    },
                },
                "downloaded_blobs_base64": {
                    "blob-a": base64.b64encode(b"xyz").decode("ascii"),
                },
            },
            "plaintext_by_file_id_base64": {
                "file-a": base64.b64encode(b"# A\n").decode("ascii"),
            },
        }

        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "pull",
            "--rewritten-at",
            "1770000040100",
            "--download-required-blobs",
            "--decrypt-required-blobs",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, self.service.download_and_decrypt_pull_payload)
        self.assertEqual([call[0] for call in self.service.calls], ["pull", "download-and-decrypt-pull-required-blobs"])

    def test_pull_command_rejects_plaintext_output_dir_without_decrypt_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(
                ValueError,
                "pull --plaintext-output-dir requires --decrypt-required-blobs",
            ):
                self._run(
                    "--vault-root",
                    "C:/vault",
                    "--base-url",
                    "https://sync.example.com",
                    "--vault-id",
                    "vault-001",
                    "--device-id",
                    "desktop-shanghai",
                    "pull",
                    "--rewritten-at",
                    "1770000040100",
                    "--plaintext-output-dir",
                    tmpdir,
                )

    def test_pull_command_rejects_stage_required_blobs_without_decrypt_flag(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "pull --stage-required-blobs requires --decrypt-required-blobs",
        ):
            self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "pull",
                "--rewritten-at",
                "1770000040100",
                "--stage-required-blobs",
            )

    def test_pull_command_rejects_plan_apply_with_blob_flags(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "pull --plan-apply cannot be combined with blob download or materialization flags",
        ):
            self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "pull",
                "--rewritten-at",
                "1770000040100",
                "--plan-apply",
                "--download-required-blobs",
            )

    def test_pull_command_rejects_apply_nonblocking_with_blob_flags(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "pull --apply-nonblocking cannot be combined with blob download or materialization flags",
        ):
            self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "pull",
                "--rewritten-at",
                "1770000040100",
                "--apply-nonblocking",
                "--download-required-blobs",
            )

    def test_pull_command_rejects_apply_with_blob_flags(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "pull --apply cannot be combined with blob download or materialization flags",
        ):
            self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "pull",
                "--rewritten-at",
                "1770000040100",
                "--apply",
                "--download-required-blobs",
            )

    def test_pull_command_rejects_multiple_apply_modes(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "pull apply modes are mutually exclusive",
        ):
            self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "pull",
                "--rewritten-at",
                "1770000040100",
                "--apply",
                "--apply-nonblocking",
            )

    def test_pull_command_can_return_apply_plan(self) -> None:
        self.service.pull_and_plan_apply_payload = {
            "pull": {
                "pull": {
                    "reconcile": {
                        "applied": {
                            "required_blob_ids": ["blob-a"],
                        }
                    }
                }
            },
            "plan": {
                "vault_id": "vault-001",
                "revision": 8,
                "writes": [
                    {
                        "file_id": "file-a",
                        "target_path": "Notes/A.md",
                        "staging_path": ".noteapp/staging/file-a.staging",
                        "type": "note",
                        "content_hash": "sha256:abc",
                    }
                ],
                "moves": [
                    {
                        "file_id": "file-b",
                        "source_path": "Notes/B-old.md",
                        "target_path": "Notes/B.md",
                        "type": "note",
                        "content_hash": "sha256:def",
                    }
                ],
                "deletes": [
                    {
                        "file_id": "file-c",
                        "path": "Notes/C.md",
                        "reason": "deleted",
                    }
                ],
                "blocking_paths": ["Notes/B.md"],
                "ops_hash": "sha256:ops8",
            },
        }

        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "pull",
            "--rewritten-at",
            "1770000040100",
            "--plan-apply",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, self.service.pull_and_plan_apply_payload)
        self.assertEqual(self.service.calls, [("pull-and-plan-apply", 1770000040100)])

    def test_pull_command_can_apply_full_plan(self) -> None:
        self.service.pull_and_apply_payload = {
            "pull": {
                "pull": {
                    "reconcile": {
                        "applied": {
                            "required_blob_ids": [],
                        }
                    }
                }
            },
            "plan": {
                "vault_id": "vault-001",
                "revision": 8,
                "writes": [],
                "moves": [
                    {
                        "file_id": "file-a",
                        "source_path": "Notes/A.md",
                        "target_path": "Notes/B.md",
                        "type": "note",
                        "content_hash": "sha256:a",
                    },
                    {
                        "file_id": "file-b",
                        "source_path": "Notes/B.md",
                        "target_path": "Notes/A.md",
                        "type": "note",
                        "content_hash": "sha256:b",
                    },
                ],
                "deletes": [],
                "blocking_paths": ["Notes/A.md", "Notes/B.md"],
                "ops_hash": "sha256:ops8",
            },
            "staged": {
                "journal": {
                    "vault_id": "vault-001",
                    "journal_id": "journal-1",
                    "target_revision": 8,
                    "target_manifest_hash": "sha256:head8",
                    "phase": "staging",
                    "ops_hash": "sha256:ops8",
                    "created_at": 1770000040100,
                    "updated_at": 1770000040100,
                },
                "written_staging_paths": {},
            },
            "execution": {
                "journal": {
                    "vault_id": "vault-001",
                    "journal_id": "journal-1",
                    "target_revision": 8,
                    "target_manifest_hash": "sha256:head8",
                    "phase": "materializing",
                    "ops_hash": "sha256:ops8",
                    "created_at": 1770000040100,
                    "updated_at": 1770000040100,
                },
                "written_paths": {},
                "moved_paths": {
                    "file-a": "C:/vault/Notes/B.md",
                    "file-b": "C:/vault/Notes/A.md",
                },
                "deleted_paths": [],
            },
            "finalized": {
                "state": {
                    "vault_id": "vault-001",
                    "last_applied_revision": 8,
                    "remote_head_revision": 8,
                    "acked_revision": 8,
                    "pending_ack_to_server": [8],
                    "commit_in_progress": False,
                    "last_manifest_summary": "sha256:head8",
                    "last_manifest_summary_status": "valid",
                    "local_delete_sequence": 1,
                    "has_unresolved_conflicts": False,
                    "schema_version": 1,
                    "meta": None,
                },
                "removed_staging_paths": [],
            },
        }

        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "pull",
            "--rewritten-at",
            "1770000040100",
            "--apply",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, self.service.pull_and_apply_payload)
        self.assertEqual(self.service.calls, [("pull-and-apply", 1770000040100)])

    def test_pull_command_can_apply_nonblocking(self) -> None:
        self.service.pull_and_apply_nonblocking_payload = {
            "pull": {
                "pull": {
                    "reconcile": {
                        "applied": {
                            "required_blob_ids": ["blob-a"],
                        }
                    }
                }
            },
            "plan": {
                "vault_id": "vault-001",
                "revision": 8,
                "writes": [
                    {
                        "file_id": "file-a",
                        "target_path": "Notes/A.md",
                        "staging_path": ".noteapp/staging/file-a.staging",
                        "type": "note",
                        "content_hash": "sha256:abc",
                    }
                ],
                "moves": [],
                "deletes": [],
                "blocking_paths": [],
                "ops_hash": "sha256:ops8",
            },
            "staged": {
                "journal": {
                    "vault_id": "vault-001",
                    "journal_id": "journal-1",
                    "target_revision": 8,
                    "target_manifest_hash": "sha256:head8",
                    "phase": "staging",
                    "ops_hash": "sha256:ops8",
                    "created_at": 1770000040100,
                    "updated_at": 1770000040100,
                },
                "written_staging_paths": {
                    "file-a": "C:/vault/.noteapp/staging/file-a.staging",
                },
            },
            "execution": {
                "journal": {
                    "vault_id": "vault-001",
                    "journal_id": "journal-1",
                    "target_revision": 8,
                    "target_manifest_hash": "sha256:head8",
                    "phase": "materializing",
                    "ops_hash": "sha256:ops8",
                    "created_at": 1770000040100,
                    "updated_at": 1770000040100,
                },
                "written_paths": {
                    "file-a": "C:/vault/Notes/A.md",
                },
                "moved_paths": {},
                "deleted_paths": [],
            },
            "finalized": {
                "state": {
                    "vault_id": "vault-001",
                    "last_applied_revision": 8,
                    "remote_head_revision": 8,
                    "acked_revision": 8,
                    "pending_ack_to_server": [8],
                    "commit_in_progress": False,
                    "last_manifest_summary": "sha256:head8",
                    "last_manifest_summary_status": "valid",
                    "local_delete_sequence": 1,
                    "has_unresolved_conflicts": False,
                    "schema_version": 1,
                    "meta": None,
                },
                "removed_staging_paths": [
                    "C:/vault/.noteapp/staging/file-a.staging",
                ],
            },
        }

        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "pull",
            "--rewritten-at",
            "1770000040100",
            "--apply-nonblocking",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, self.service.pull_and_apply_nonblocking_payload)
        self.assertEqual(self.service.calls, [("pull-and-apply-nonblocking", 1770000040100)])

    def test_pull_command_can_materialize_decrypted_required_blobs(self) -> None:
        self.service.pull_and_ack_payload = {
            "pull": {
                "reconcile": {
                    "applied": {
                        "required_blob_ids": ["blob-a"],
                    }
                }
            }
        }
        self.service.download_and_decrypt_pull_payload = DesktopPullRequiredBlobResult(
            pull=self.service.pull_and_ack_payload,
            plan=DesktopPullRequiredBlobPlan(
                vault_id="vault-001",
                revision=8,
                blob_ids=["blob-a"],
                files=[
                    DesktopPullRequiredBlobFile(
                        file_id="file-a",
                        path="Notes/A.md",
                        type="note",
                        blob_id="blob-a",
                        content_hash="sha256:abc",
                    )
                ],
            ),
            download=None,
            plaintext_by_file_id={"file-a": b"# A\n"},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            self.service.materialize_pull_required_plaintext_payload = {
                "file-a": Path(tmpdir) / "Notes" / "A.md"
            }
            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "pull",
                "--rewritten-at",
                "1770000040100",
                "--download-required-blobs",
                "--decrypt-required-blobs",
                "--plaintext-output-dir",
                tmpdir,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                payload,
                {
                    "pull": self.service.pull_and_ack_payload,
                    "plan": {
                        "vault_id": "vault-001",
                        "revision": 8,
                        "blob_ids": ["blob-a"],
                        "files": [
                            {
                                "file_id": "file-a",
                                "path": "Notes/A.md",
                                "type": "note",
                                "blob_id": "blob-a",
                                "content_hash": "sha256:abc",
                            }
                        ],
                    },
                    "download": None,
                    "written_plaintext_paths": {
                        "file-a": str(Path(tmpdir) / "Notes" / "A.md"),
                    },
                },
            )
            self.assertEqual(
                [call[0] for call in self.service.calls],
                [
                    "pull",
                    "download-and-decrypt-pull-required-blobs",
                    "materialize-pull-required-plaintext",
                ],
            )

    def test_pull_command_can_stage_decrypted_required_blobs_for_apply(self) -> None:
        self.service.pull_and_ack_payload = {
            "pull": {
                "reconcile": {
                    "applied": {
                        "required_blob_ids": ["blob-a"],
                    }
                }
            }
        }
        self.service.download_and_decrypt_pull_payload = DesktopPullRequiredBlobResult(
            pull=self.service.pull_and_ack_payload,
            plan=DesktopPullRequiredBlobPlan(
                vault_id="vault-001",
                revision=8,
                blob_ids=["blob-a"],
                files=[
                    DesktopPullRequiredBlobFile(
                        file_id="file-a",
                        path="Notes/A.md",
                        type="note",
                        blob_id="blob-a",
                        content_hash="sha256:abc",
                    )
                ],
            ),
            download=None,
            plaintext_by_file_id={"file-a": b"# A\n"},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            staging_path = Path(tmpdir) / ".noteapp" / "staging" / "file-a.staging"
            self.service.stage_pull_required_plaintext_payload = {
                "journal": {
                    "vault_id": "vault-001",
                    "journal_id": "journal-1",
                    "target_revision": 8,
                    "target_manifest_hash": "sha256:head8",
                    "phase": "staging",
                    "ops_hash": "sha256:ops8",
                    "created_at": 1770000040100,
                    "updated_at": 1770000040100,
                },
                "written_staging_paths": {
                    "file-a": staging_path,
                },
            }
            exit_code, payload = self._run(
                "--vault-root",
                tmpdir,
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "pull",
                "--rewritten-at",
                "1770000040100",
                "--download-required-blobs",
                "--decrypt-required-blobs",
                "--stage-required-blobs",
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                payload,
                {
                    "pull": self.service.pull_and_ack_payload,
                    "plan": {
                        "vault_id": "vault-001",
                        "revision": 8,
                        "blob_ids": ["blob-a"],
                        "files": [
                            {
                                "file_id": "file-a",
                                "path": "Notes/A.md",
                                "type": "note",
                                "blob_id": "blob-a",
                                "content_hash": "sha256:abc",
                            }
                        ],
                    },
                    "download": None,
                    "apply_staging": {
                        "journal": {
                            "vault_id": "vault-001",
                            "journal_id": "journal-1",
                            "target_revision": 8,
                            "target_manifest_hash": "sha256:head8",
                            "phase": "staging",
                            "ops_hash": "sha256:ops8",
                            "created_at": 1770000040100,
                            "updated_at": 1770000040100,
                        },
                        "written_staging_paths": {
                            "file-a": str(staging_path),
                        },
                    },
                },
            )
            self.assertEqual(
                [call[0] for call in self.service.calls],
                [
                    "pull",
                    "download-and-decrypt-pull-required-blobs",
                    "stage-pull-required-plaintext-for-apply",
                ],
            )

    def test_recover_command_routes_normalized_at(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "recover",
            "--normalized-at",
            "1770000040200",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, {"kind": "recover", "normalized_at": 1770000040200})
        self.assertEqual(self.service.calls, [("recover", 1770000040200)])

    def test_recover_pull_apply_command_routes_normalized_at(self) -> None:
        self.service.pull_apply_recovery_payload = {
            "mode": "finalized",
            "journal_phase": "materializing",
            "state": {
                "vault_id": "vault-001",
                "last_applied_revision": 8,
                "remote_head_revision": 8,
                "acked_revision": 8,
                "pending_ack_to_server": [8],
                "commit_in_progress": False,
                "last_manifest_summary": "sha256:head8",
                "last_manifest_summary_status": "valid",
                "local_delete_sequence": 1,
                "has_unresolved_conflicts": False,
                "schema_version": 1,
                "meta": None,
            },
            "removed_staging_paths": [
                "C:/vault/.noteapp/staging/file-a.staging",
            ],
        }

        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "recover-pull-apply",
            "--normalized-at",
            "1770000040200",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, self.service.pull_apply_recovery_payload)
        self.assertEqual(self.service.calls, [("recover-pull-apply", 1770000040200)])

    def test_sync_once_command_routes_runner_sequence(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-once",
            "--now-ms",
            "1770000040250",
            "--normalized-at",
            "1770000040260",
            "--rewritten-at",
            "1770000040270",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "final_snapshot": {"files": 1, "kind": "status"},
                "initialized": {"kind": "init", "value": 1770000040250},
                "pull": {"kind": "pull-and-apply", "rewritten_at": 1770000040270},
                "pull_apply_recovery": {"kind": "recover-pull-apply", "normalized_at": 1770000040260},
                "recovery": {"kind": "recover", "normalized_at": 1770000040260},
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040250),
                ("recover", 1770000040260),
                ("recover-pull-apply", 1770000040260),
                ("pull-and-apply", 1770000040270),
                ("status", None),
            ],
        )

    def test_sync_once_command_can_auto_generate_time_arguments(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-once",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040000),
                ("recover", 1770000040010),
                ("recover-pull-apply", 1770000040010),
                ("pull-and-apply", 1770000040020),
                ("status", None),
            ],
        )
        self.assertEqual(payload["pull"]["rewritten_at"], 1770000040020)

    def test_sync_loop_command_routes_scheduler_sequence(self) -> None:
        slept: list[float] = []
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-loop",
            "--iterations",
            "3",
            "--now-ms",
            "1770000040400",
            "--normalized-at",
            "1770000040410",
            "--rewritten-at",
            "1770000040420",
            "--step-ms",
            "50",
            "--interval-seconds",
            "1.25",
            sleep=slept.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "config": {
                    "continue_on_error": False,
                    "init_now_ms": 1770000040400,
                    "interval_seconds": 1.25,
                    "iterations": 3,
                    "pull_rewritten_at": 1770000040420,
                    "recovery_normalized_at": 1770000040410,
                    "step_ms": 50,
                },
                "iterations": [
                    {
                        "init_now_ms": 1770000040400,
                        "iteration": 0,
                        "pull_rewritten_at": 1770000040420,
                        "recovery_normalized_at": 1770000040410,
                        "failure": None,
                        "run_once": {
                            "final_snapshot": {"files": 1, "kind": "status"},
                            "initialized": {"kind": "init", "value": 1770000040400},
                            "pull": {"kind": "pull-and-apply", "rewritten_at": 1770000040420},
                            "pull_apply_recovery": {"kind": "recover-pull-apply", "normalized_at": 1770000040410},
                            "recovery": {"kind": "recover", "normalized_at": 1770000040410},
                        },
                    },
                    {
                        "init_now_ms": 1770000040450,
                        "iteration": 1,
                        "pull_rewritten_at": 1770000040470,
                        "recovery_normalized_at": 1770000040460,
                        "failure": None,
                        "run_once": {
                            "final_snapshot": {"files": 1, "kind": "status"},
                            "initialized": {"kind": "init", "value": 1770000040450},
                            "pull": {"kind": "pull-and-apply", "rewritten_at": 1770000040470},
                            "pull_apply_recovery": {"kind": "recover-pull-apply", "normalized_at": 1770000040460},
                            "recovery": {"kind": "recover", "normalized_at": 1770000040460},
                        },
                    },
                    {
                        "init_now_ms": 1770000040500,
                        "iteration": 2,
                        "pull_rewritten_at": 1770000040520,
                        "recovery_normalized_at": 1770000040510,
                        "failure": None,
                        "run_once": {
                            "final_snapshot": {"files": 1, "kind": "status"},
                            "initialized": {"kind": "init", "value": 1770000040500},
                            "pull": {"kind": "pull-and-apply", "rewritten_at": 1770000040520},
                            "pull_apply_recovery": {"kind": "recover-pull-apply", "normalized_at": 1770000040510},
                            "recovery": {"kind": "recover", "normalized_at": 1770000040510},
                        },
                    },
                ],
                "failure_count": 0,
                "stopped_early": False,
                "success_count": 3,
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040400),
                ("recover", 1770000040410),
                ("recover-pull-apply", 1770000040410),
                ("pull-and-apply", 1770000040420),
                ("status", None),
                ("init", 1770000040450),
                ("recover", 1770000040460),
                ("recover-pull-apply", 1770000040460),
                ("pull-and-apply", 1770000040470),
                ("status", None),
                ("init", 1770000040500),
                ("recover", 1770000040510),
                ("recover-pull-apply", 1770000040510),
                ("pull-and-apply", 1770000040520),
                ("status", None),
            ],
        )
        self.assertEqual(slept, [1.25, 1.25])

    def test_sync_loop_command_can_auto_generate_time_arguments(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-loop",
            "--iterations",
            "2",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["config"]["init_now_ms"], 1770000040000)
        self.assertEqual(payload["config"]["recovery_normalized_at"], 1770000040010)
        self.assertEqual(payload["config"]["pull_rewritten_at"], 1770000040020)
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040000),
                ("recover", 1770000040010),
                ("recover-pull-apply", 1770000040010),
                ("pull-and-apply", 1770000040020),
                ("status", None),
                ("init", 1770000040000),
                ("recover", 1770000040010),
                ("recover-pull-apply", 1770000040010),
                ("pull-and-apply", 1770000040020),
                ("status", None),
            ],
        )

    def test_sync_cycle_command_routes_optional_workspace_submit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            encrypted_dir = Path(tmpdir) / "encrypted"
            encrypted_dir.mkdir(parents=True, exist_ok=True)
            (encrypted_dir / "file-a.blob").write_bytes(b"enc-a")

            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "sync-cycle",
                "--now-ms",
                "1770000040430",
                "--normalized-at",
                "1770000040440",
                "--submit-created-at",
                "1770000040450",
                "--file-id",
                "file-a",
                "--commit-intent-id",
                "intent-010",
                "--cleanup-normalized-at",
                "1770000040451",
                "--encrypted-dir",
                str(encrypted_dir),
                "--rewritten-at",
                "1770000040460",
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "final_snapshot": {"files": 1, "kind": "status"},
                "initialized": {"kind": "init", "value": 1770000040430},
                "pull": {"kind": "pull-and-apply", "rewritten_at": 1770000040460},
                "pull_apply_recovery": {"kind": "recover-pull-apply", "normalized_at": 1770000040440},
                "recovery": {"kind": "recover", "normalized_at": 1770000040440},
                "submitted": {
                    "kind": "submit-workspace-commit",
                    "created_at": 1770000040450,
                    "file_ids": ["file-a"],
                    "commit_intent_id": "intent-010",
                    "encrypted_sizes": {"file-a": 5},
                },
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040430),
                ("recover", 1770000040440),
                ("recover-pull-apply", 1770000040440),
                (
                    "submit-workspace-commit",
                    1770000040450,
                    ["file-a"],
                    "intent-010",
                    1770000040451,
                    {"file-a": b"enc-a"},
                ),
                ("pull-and-apply", 1770000040460),
                ("status", None),
            ],
        )

    def test_sync_cycle_command_can_submit_without_explicit_encrypted_payloads(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-cycle",
            "--now-ms",
            "1770000040530",
            "--normalized-at",
            "1770000040540",
            "--submit-created-at",
            "1770000040550",
            "--file-id",
            "file-a",
            "--rewritten-at",
            "1770000040560",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["submitted"]["encrypted_sizes"], None)
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040530),
                ("recover", 1770000040540),
                ("recover-pull-apply", 1770000040540),
                ("submit-workspace-commit", 1770000040550, ["file-a"], None, 1770000040551, None),
                ("pull-and-apply", 1770000040560),
                ("status", None),
            ],
        )

    def test_sync_cycle_command_pulls_before_submit_when_recovery_requires_full_pull(self) -> None:
        self.service.pull_apply_recovery_payload = {
            "kind": "recover-pull-apply",
            "normalized_at": 1770000040540,
            "mode": "degraded",
            "requires_full_pull": True,
            "state": {
                "last_manifest_summary_status": "stale",
            },
        }

        exit_code, _ = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-cycle",
            "--now-ms",
            "1770000040530",
            "--normalized-at",
            "1770000040540",
            "--submit-created-at",
            "1770000040550",
            "--file-id",
            "file-a",
            "--rewritten-at",
            "1770000040560",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040530),
                ("recover", 1770000040540),
                ("recover-pull-apply", 1770000040540),
                ("pull-and-apply", 1770000040560),
                ("submit-workspace-commit", 1770000040561, ["file-a"], None, 1770000040562, None),
                ("status", None),
            ],
        )

    def test_sync_cycle_command_pulls_before_submit_when_commit_recovery_requires_full_pull(self) -> None:
        self.service.commit_recovery_payload = {
            "mode": "submitted_confirmation",
            "requires_full_pull": True,
            "submitted": {
                "recovery": {
                    "requires_full_pull": True,
                    "state": {
                        "last_manifest_summary_status": "stale",
                    },
                },
            },
        }

        exit_code, _ = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-cycle",
            "--now-ms",
            "1770000040530",
            "--normalized-at",
            "1770000040540",
            "--submit-created-at",
            "1770000040550",
            "--cleanup-normalized-at",
            "1770000040551",
            "--file-id",
            "file-a",
            "--rewritten-at",
            "1770000040560",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040530),
                ("recover", 1770000040540),
                ("recover-pull-apply", 1770000040540),
                ("pull-and-apply", 1770000040560),
                ("submit-workspace-commit", 1770000040561, ["file-a"], None, 1770000040562, None),
                ("status", None),
            ],
        )

    def test_sync_cycle_command_can_auto_generate_time_arguments(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-cycle",
            "--file-id",
            "file-a",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040000),
                ("recover", 1770000040010),
                ("recover-pull-apply", 1770000040010),
                ("submit-workspace-commit", 1770000040020, ["file-a"], None, 1770000040021, None),
                ("pull-and-apply", 1770000040030),
                ("status", None),
            ],
        )
        self.assertEqual(payload["submitted"]["created_at"], 1770000040020)

    def test_sync_cycle_loop_command_routes_scheduler_over_full_cycle(self) -> None:
        slept: list[float] = []
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-cycle-loop",
            "--iterations",
            "2",
            "--now-ms",
            "1770000040600",
            "--normalized-at",
            "1770000040610",
            "--submit-created-at",
            "1770000040620",
            "--file-id",
            "file-a",
            "--cleanup-normalized-at",
            "1770000040621",
            "--rewritten-at",
            "1770000040630",
            "--step-ms",
            "100",
            "--interval-seconds",
            "0.5",
            sleep=slept.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "config": {
                    "cleanup_normalized_at": 1770000040621,
                    "commit_intent_id": None,
                    "continue_on_error": False,
                    "encrypted_blob_by_file_id": None,
                    "init_now_ms": 1770000040600,
                    "interval_seconds": 0.5,
                    "iterations": 2,
                    "pull_rewritten_at": 1770000040630,
                    "recovery_normalized_at": 1770000040610,
                    "step_ms": 100,
                    "submit_created_at": 1770000040620,
                    "submit_detected": False,
                    "submit_file_ids": ["file-a"],
                },
                "iterations": [
                    {
                        "cleanup_normalized_at": 1770000040621,
                        "init_now_ms": 1770000040600,
                        "iteration": 0,
                        "pull_rewritten_at": 1770000040630,
                        "recovery_normalized_at": 1770000040610,
                        "failure": None,
                        "run_cycle": {
                            "final_snapshot": {"files": 1, "kind": "status"},
                            "initialized": {"kind": "init", "value": 1770000040600},
                            "pull": {"kind": "pull-and-apply", "rewritten_at": 1770000040630},
                            "pull_apply_recovery": {"kind": "recover-pull-apply", "normalized_at": 1770000040610},
                            "recovery": {"kind": "recover", "normalized_at": 1770000040610},
                            "submitted": {
                                "kind": "submit-workspace-commit",
                                "created_at": 1770000040620,
                                "commit_intent_id": None,
                                "encrypted_sizes": None,
                                "file_ids": ["file-a"],
                            },
                        },
                        "submit_created_at": 1770000040620,
                    },
                    {
                        "cleanup_normalized_at": 1770000040721,
                        "init_now_ms": 1770000040700,
                        "iteration": 1,
                        "pull_rewritten_at": 1770000040730,
                        "recovery_normalized_at": 1770000040710,
                        "failure": None,
                        "run_cycle": {
                            "final_snapshot": {"files": 1, "kind": "status"},
                            "initialized": {"kind": "init", "value": 1770000040700},
                            "pull": {"kind": "pull-and-apply", "rewritten_at": 1770000040730},
                            "pull_apply_recovery": {"kind": "recover-pull-apply", "normalized_at": 1770000040710},
                            "recovery": {"kind": "recover", "normalized_at": 1770000040710},
                            "submitted": {
                                "kind": "submit-workspace-commit",
                                "created_at": 1770000040720,
                                "commit_intent_id": None,
                                "encrypted_sizes": None,
                                "file_ids": ["file-a"],
                            },
                        },
                        "submit_created_at": 1770000040720,
                    },
                ],
                "failure_count": 0,
                "stopped_early": False,
                "success_count": 2,
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040600),
                ("recover", 1770000040610),
                ("recover-pull-apply", 1770000040610),
                ("submit-workspace-commit", 1770000040620, ["file-a"], None, 1770000040621, None),
                ("pull-and-apply", 1770000040630),
                ("status", None),
                ("init", 1770000040700),
                ("recover", 1770000040710),
                ("recover-pull-apply", 1770000040710),
                ("submit-workspace-commit", 1770000040720, ["file-a"], None, 1770000040721, None),
                ("pull-and-apply", 1770000040730),
                ("status", None),
            ],
        )
        self.assertEqual(slept, [0.5])

    def test_sync_cycle_command_can_submit_detected_changes(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-cycle",
            "--now-ms",
            "1770000040640",
            "--normalized-at",
            "1770000040650",
            "--submit-created-at",
            "1770000040660",
            "--submit-detected",
            "--commit-intent-id",
            "intent-detected-010",
            "--cleanup-normalized-at",
            "1770000040661",
            "--rewritten-at",
            "1770000040670",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["submitted"]["kind"], "submit-detected-commit")
        self.assertEqual(payload["submitted"]["commit_intent_id"], "intent-detected-010")
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040640),
                ("recover", 1770000040650),
                ("recover-pull-apply", 1770000040650),
                ("submit-detected-commit", 1770000040660, "intent-detected-010", 1770000040661),
                ("pull-and-apply", 1770000040670),
                ("status", None),
            ],
        )

    def test_sync_cycle_loop_command_can_submit_detected_changes(self) -> None:
        slept: list[float] = []
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-cycle-loop",
            "--iterations",
            "2",
            "--submit-detected",
            "--interval-seconds",
            "0.25",
            sleep=slept.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["config"]["submit_detected"])
        self.assertEqual(payload["iterations"][0]["run_cycle"]["submitted"]["kind"], "submit-detected-commit")
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040000),
                ("recover", 1770000040010),
                ("recover-pull-apply", 1770000040010),
                ("submit-detected-commit", 1770000040020, None, 1770000040021),
                ("pull-and-apply", 1770000040030),
                ("status", None),
                ("init", 1770000040000),
                ("recover", 1770000040010),
                ("recover-pull-apply", 1770000040010),
                ("submit-detected-commit", 1770000040020, None, 1770000040021),
                ("pull-and-apply", 1770000040030),
                ("status", None),
            ],
        )
        self.assertEqual(slept, [0.25])

    def test_sync_worker_command_can_submit_detected_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            exit_code, payload = self._run(
                "--vault-root",
                tmpdir,
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "sync-worker",
                "--iterations",
                "1",
                "--submit-detected",
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["config"]["submit_detected"])
            self.assertEqual(payload["loop"]["iterations"][0]["run_cycle"]["submitted"]["kind"], "submit-detected-commit")
            self.assertEqual(
                self.service.calls,
                [
                    ("init", 1770000040000),
                    ("recover", 1770000040010),
                    ("recover-pull-apply", 1770000040010),
                    ("submit-detected-commit", 1770000040020, None, 1770000040021),
                    ("pull-and-apply", 1770000040030),
                    ("status", None),
                ],
            )

    def test_sync_cycle_loop_command_can_auto_generate_time_arguments(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-cycle-loop",
            "--iterations",
            "2",
            "--file-id",
            "file-a",
            "--step-ms",
            "50",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["config"]["init_now_ms"], 1770000040000)
        self.assertEqual(payload["config"]["recovery_normalized_at"], 1770000040010)
        self.assertEqual(payload["config"]["submit_created_at"], 1770000040020)
        self.assertEqual(payload["config"]["cleanup_normalized_at"], 1770000040021)
        self.assertEqual(payload["config"]["pull_rewritten_at"], 1770000040030)
        self.assertEqual(
            self.service.calls,
            [
                ("init", 1770000040000),
                ("recover", 1770000040010),
                ("recover-pull-apply", 1770000040010),
                ("submit-workspace-commit", 1770000040020, ["file-a"], None, 1770000040021, None),
                ("pull-and-apply", 1770000040030),
                ("status", None),
                ("init", 1770000040050),
                ("recover", 1770000040060),
                ("recover-pull-apply", 1770000040060),
                ("submit-workspace-commit", 1770000040070, ["file-a"], None, 1770000040071, None),
                ("pull-and-apply", 1770000040080),
                ("status", None),
            ],
        )

    def test_sync_worker_command_wraps_cycle_loop_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            slept: list[float] = []
            exit_code, payload = self._run(
                "--vault-root",
                tmpdir,
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "sync-worker",
                "--iterations",
                "2",
                "--file-id",
                "file-a",
                "--interval-seconds",
                "2.5",
                sleep=slept.append,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["started_at_ms"], 1770000040000)
            self.assertEqual(payload["effective_step_ms"], 2500)
            self.assertTrue(payload["config"]["continue_on_error"])
            self.assertEqual(payload["time_plan"]["submit_created_at"], 1770000040020)
            self.assertEqual(
                payload["state_path"],
                str(Path(tmpdir) / ".noteapp" / "sync-worker-state.json"),
            )
            self.assertEqual(
                self.service.calls,
                [
                    ("init", 1770000040000),
                    ("recover", 1770000040010),
                    ("recover-pull-apply", 1770000040010),
                    ("submit-workspace-commit", 1770000040020, ["file-a"], None, 1770000040021, None),
                    ("pull-and-apply", 1770000040030),
                    ("status", None),
                    ("init", 1770000042500),
                    ("recover", 1770000042510),
                    ("recover-pull-apply", 1770000042510),
                    ("submit-workspace-commit", 1770000042520, ["file-a"], None, 1770000042521, None),
                    ("pull-and-apply", 1770000042530),
                    ("status", None),
                ],
            )
            self.assertEqual(slept, [2.5])

    def test_sync_worker_command_writes_worker_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            exit_code, payload = self._run(
                "--vault-root",
                tmpdir,
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "sync-worker",
                "--iterations",
                "1",
                "--file-id",
                "file-a",
            )

            self.assertEqual(exit_code, 0)
            state_path = Path(tmpdir) / ".noteapp" / "sync-worker-state.json"
            written = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(written["started_at_ms"], 1770000040000)
            self.assertEqual(written["success_count"], 1)
            self.assertEqual(written["state_path"], str(state_path))
            self.assertEqual(payload["state_path"], str(state_path))

    def test_sync_worker_command_can_stop_on_error(self) -> None:
        self.service.fail_submit_workspace_at_calls = {0}
        with self.assertRaisesRegex(RuntimeError, "submit failed at call 0"):
            self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "sync-worker",
                "--iterations",
                "2",
                "--file-id",
                "file-a",
                "--stop-on-error",
            )

    def test_sync_loop_command_can_continue_on_error(self) -> None:
        self.service.fail_recover_at_calls = {1}
        slept: list[float] = []
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-loop",
            "--iterations",
            "3",
            "--normalized-at",
            "1770000040800",
            "--rewritten-at",
            "1770000040810",
            "--step-ms",
            "5",
            "--interval-seconds",
            "0.1",
            "--continue-on-error",
            sleep=slept.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["success_count"], 2)
        self.assertEqual(payload["failure_count"], 1)
        self.assertEqual(payload["iterations"][1]["run_once"], None)
        self.assertEqual(payload["iterations"][1]["failure"]["error_type"], "RuntimeError")
        self.assertEqual(slept, [0.1, 0.1])

    def test_sync_cycle_loop_command_can_continue_on_error(self) -> None:
        self.service.fail_submit_workspace_at_calls = {0}
        slept: list[float] = []
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "sync-cycle-loop",
            "--iterations",
            "2",
            "--normalized-at",
            "1770000040900",
            "--submit-created-at",
            "1770000040910",
            "--file-id",
            "file-a",
            "--rewritten-at",
            "1770000040920",
            "--continue-on-error",
            "--interval-seconds",
            "0.2",
            sleep=slept.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["success_count"], 1)
        self.assertEqual(payload["failure_count"], 1)
        self.assertEqual(payload["iterations"][0]["run_cycle"], None)
        self.assertEqual(payload["iterations"][0]["failure"]["error_type"], "RuntimeError")
        self.assertIsNotNone(payload["iterations"][1]["run_cycle"])
        self.assertEqual(slept, [0.2])

    def test_download_blobs_command_routes_blob_ids_and_base64_encodes_payload(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "download-blobs",
            "--blob-id",
            "blob-a",
            "--blob-id",
            "blob-b",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "downloaded_blobs_base64": {
                    "blob-a": "eHl6",
                    "blob-b": "eHl6",
                },
                "init": {
                    "request": {"blob_ids": ["blob-a", "blob-b"]},
                    "response": {
                        "downloads": [
                            {
                                "blob_id": "blob-a",
                                "download_url": "https://blob.example.com/download/blob-a",
                                "encrypted_size": 3,
                                "expires_at": "2026-05-08T12:00:00Z",
                                "headers": None,
                            },
                            {
                                "blob_id": "blob-b",
                                "download_url": "https://blob.example.com/download/blob-b",
                                "encrypted_size": 3,
                                "expires_at": "2026-05-08T12:00:00Z",
                                "headers": None,
                            },
                        ]
                    },
                },
            },
        )
        self.assertEqual(self.service.calls, [("download-blobs", ["blob-a", "blob-b"])])

    def test_file_versions_command_routes_file_id_and_pagination(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "file-versions",
            "--file-id",
            "file-xxx-md",
            "--limit",
            "20",
            "--cursor",
            "offset:20",
            "--exclude-pinned",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["request"]["file_id"], "file-xxx-md")
        self.assertEqual(payload["request"]["limit"], 20)
        self.assertFalse(payload["request"]["include_pinned"])
        self.assertEqual(
            self.service.calls,
            [("file-versions", "file-xxx-md", 20, "offset:20", False)],
        )

    def test_file_version_content_command_routes_version_and_text_flag(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "file-version-content",
            "--file-id",
            "file-xxx-md",
            "--version-id",
            "fv-001",
            "--no-text",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["file_id"], "file-xxx-md")
        self.assertEqual(payload["version_id"], "fv-001")
        self.assertIsNone(payload["text"])
        self.assertEqual(
            self.service.calls,
            [("file-version-content", "file-xxx-md", "fv-001", False)],
        )

    def test_diff_file_version_command_routes_context_lines(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "diff-file-version",
            "--file-id",
            "file-xxx-md",
            "--version-id",
            "fv-001",
            "--context-lines",
            "8",
        )

        self.assertEqual(exit_code, 0)
        self.assertFalse(payload["is_binary"])
        self.assertEqual(payload["diff_text"], "--- old\n+++ current\n")
        self.assertEqual(
            self.service.calls,
            [("diff-file-version", "file-xxx-md", "fv-001", 8)],
        )

    def test_restore_file_version_command_routes_commit_options(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "restore-file-version",
            "--file-id",
            "file-xxx-md",
            "--version-id",
            "fv-001",
            "--created-at",
            "1770000040700",
            "--commit-intent-id",
            "intent-restore-001",
            "--cleanup-normalized-at",
            "1770000040701",
            "--version-label",
            "restore checkpoint",
            "--change-note",
            "restore meeting version",
            "--pin-version",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["version_id"], "fv-001")
        self.assertEqual(payload["commit_intent_id"], "intent-restore-001")
        self.assertTrue(payload["is_pinned"])
        self.assertEqual(
            self.service.calls,
            [
                (
                    "restore-file-version",
                    "file-xxx-md",
                    "fv-001",
                    1770000040700,
                    "intent-restore-001",
                    1770000040701,
                    "restore checkpoint",
                    "restore meeting version",
                    True,
                )
            ],
        )

    def test_update_file_version_command_routes_label_note_and_pin(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "update-file-version",
            "--version-id",
            "fv-001",
            "--version-label",
            "客户会议复盘",
            "--change-note",
            "补充行动项",
            "--unpin",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["request"]["version_id"], "fv-001")
        self.assertEqual(payload["request"]["version_label"], "客户会议复盘")
        self.assertEqual(payload["request"]["change_note"], "补充行动项")
        self.assertFalse(payload["request"]["is_pinned"])
        self.assertEqual(
            self.service.calls,
            [("update-file-version", "fv-001", "客户会议复盘", "补充行动项", False)],
        )

    def test_vault_devices_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "vault-devices",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["response"]["vault_id"], "vault-001")
        self.assertEqual(payload["response"]["devices"][0]["device_id"], "desktop-shanghai")
        self.assertEqual(self.service.calls, [("vault-devices",)])

    def test_heartbeat_vault_device_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "heartbeat-vault-device",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["response"]["device_id"], "desktop-shanghai")
        self.assertEqual(payload["response"]["acked_revision"], 9)
        self.assertEqual(self.service.calls, [("heartbeat-vault-device",)])

    def test_revoke_device_command_routes_target_device_id(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "revoke-device",
            "--target-device-id",
            "dev_phone",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, {"device_id": "dev_phone", "revoked": True})
        self.assertEqual(self.service.calls, [("revoke-device", "dev_phone")])

    def test_download_blobs_command_writes_blob_files_when_output_dir_is_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "downloaded"
            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "download-blobs",
                "--blob-id",
                "blob-a",
                "--blob-id",
                "blob-b",
                "--output-dir",
                str(output_dir),
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                payload,
                {
                    "init": {
                        "request": {"blob_ids": ["blob-a", "blob-b"]},
                        "response": {
                            "downloads": [
                                {
                                    "blob_id": "blob-a",
                                    "download_url": "https://blob.example.com/download/blob-a",
                                    "encrypted_size": 3,
                                    "expires_at": "2026-05-08T12:00:00Z",
                                    "headers": None,
                                },
                                {
                                    "blob_id": "blob-b",
                                    "download_url": "https://blob.example.com/download/blob-b",
                                    "encrypted_size": 3,
                                    "expires_at": "2026-05-08T12:00:00Z",
                                    "headers": None,
                                },
                            ]
                        },
                    },
                    "written_blob_paths": {
                        "blob-a": str(output_dir / "blob-a.blob"),
                        "blob-b": str(output_dir / "blob-b.blob"),
                    },
                },
            )
            self.assertEqual((output_dir / "blob-a.blob").read_bytes(), b"xyz")
            self.assertEqual((output_dir / "blob-b.blob").read_bytes(), b"xyz")

        self.assertEqual(self.service.calls, [("download-blobs", ["blob-a", "blob-b"])])

    def test_submit_commit_command_decodes_payload_files_and_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            content_map = Path(tmpdir) / "content.json"
            encrypted_map = Path(tmpdir) / "encrypted.json"
            content_map.write_text(
                json.dumps(
                    {
                        "file-a": base64.b64encode(b"hello").decode("ascii"),
                    }
                ),
                encoding="utf-8",
            )
            encrypted_map.write_text(
                json.dumps(
                    {
                        "file-a": base64.b64encode(b"encrypted-payload").decode("ascii"),
                    }
                ),
                encoding="utf-8",
            )

            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "submit-commit",
                "--created-at",
                "1770000040300",
                "--commit-intent-id",
                "intent-001",
                "--cleanup-normalized-at",
                "1770000040301",
                "--content-map",
                str(content_map),
                "--encrypted-map",
                str(encrypted_map),
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "kind": "submit-commit",
                "created_at": 1770000040300,
                "commit_intent_id": "intent-001",
                "content_sizes": {"file-a": 5},
                "encrypted_sizes": {"file-a": 17},
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                (
                    "submit-commit",
                    1770000040300,
                    "intent-001",
                    1770000040301,
                    {"file-a": b"hello"},
                    {"file-a": b"encrypted-payload"},
                )
            ],
        )

    def test_submit_workspace_commit_command_routes_selected_file_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            encrypted_map = Path(tmpdir) / "encrypted.json"
            encrypted_map.write_text(
                json.dumps(
                    {
                        "file-a": base64.b64encode(b"encrypted-a").decode("ascii"),
                        "file-b": base64.b64encode(b"encrypted-bb").decode("ascii"),
                    }
                ),
                encoding="utf-8",
            )

            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "submit-workspace-commit",
                "--created-at",
                "1770000040500",
                "--commit-intent-id",
                "intent-002",
                "--cleanup-normalized-at",
                "1770000040501",
                "--file-id",
                "file-a",
                "--file-id",
                "file-b",
                "--encrypted-map",
                str(encrypted_map),
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "kind": "submit-workspace-commit",
                "created_at": 1770000040500,
                "file_ids": ["file-a", "file-b"],
                "commit_intent_id": "intent-002",
                "encrypted_sizes": {"file-a": 11, "file-b": 12},
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                (
                    "submit-workspace-commit",
                    1770000040500,
                    ["file-a", "file-b"],
                    "intent-002",
                    1770000040501,
                    {"file-a": b"encrypted-a", "file-b": b"encrypted-bb"},
                )
            ],
        )

    def test_submit_workspace_commit_command_loads_blob_dir_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            encrypted_dir = Path(tmpdir) / "encrypted"
            encrypted_dir.mkdir(parents=True, exist_ok=True)
            (encrypted_dir / "file-a.blob").write_bytes(b"enc-a")
            (encrypted_dir / "file-b.blob").write_bytes(b"enc-bb")

            exit_code, payload = self._run(
                "--vault-root",
                "C:/vault",
                "--base-url",
                "https://sync.example.com",
                "--vault-id",
                "vault-001",
                "--device-id",
                "desktop-shanghai",
                "submit-workspace-commit",
                "--created-at",
                "1770000040600",
                "--file-id",
                "file-a",
                "--file-id",
                "file-b",
                "--encrypted-dir",
                str(encrypted_dir),
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "kind": "submit-workspace-commit",
                "created_at": 1770000040600,
                "file_ids": ["file-a", "file-b"],
                "commit_intent_id": None,
                "encrypted_sizes": {"file-a": 5, "file-b": 6},
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                (
                    "submit-workspace-commit",
                    1770000040600,
                    ["file-a", "file-b"],
                    None,
                    None,
                    {"file-a": b"enc-a", "file-b": b"enc-bb"},
                )
            ],
        )

    def test_submit_workspace_commit_command_allows_auto_generated_encrypted_payloads(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "submit-workspace-commit",
            "--created-at",
            "1770000040700",
            "--file-id",
            "file-a",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "kind": "submit-workspace-commit",
                "created_at": 1770000040700,
                "file_ids": ["file-a"],
                "commit_intent_id": None,
                "encrypted_sizes": None,
            },
        )
        self.assertEqual(
            self.service.calls,
            [
                (
                    "submit-workspace-commit",
                    1770000040700,
                    ["file-a"],
                    None,
                    None,
                    None,
                )
            ],
        )

    def test_submit_workspace_commit_command_can_attach_file_version_directives(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "submit-workspace-commit",
            "--created-at",
            "1770000040750",
            "--file-id",
            "file-a",
            "--version-source",
            "manual_meeting_checkpoint",
            "--version-label",
            "2026-05-15 周会",
            "--change-note",
            "会后确认版",
            "--pin-version",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["file_version_directives"][0]["source"], "manual_meeting_checkpoint")
        self.assertEqual(payload["file_version_directives"][0]["version_label"], "2026-05-15 周会")
        self.assertTrue(payload["file_version_directives"][0]["is_pinned"])
        call = self.service.calls[-1]
        self.assertEqual(call[:6], ("submit-workspace-commit", 1770000040750, ["file-a"], None, None, None))
        self.assertEqual(call[6][0].file_id, "file-a")
        self.assertEqual(call[6][0].source, "manual_meeting_checkpoint")
        self.assertEqual(call[6][0].version_label, "2026-05-15 周会")
        self.assertEqual(call[6][0].change_note, "会后确认版")
        self.assertTrue(call[6][0].is_pinned)

    def test_submit_detected_commit_command_routes_to_service(self) -> None:
        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "submit-detected-commit",
            "--created-at",
            "1770000040800",
            "--commit-intent-id",
            "intent-003",
            "--cleanup-normalized-at",
            "1770000040801",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "kind": "submit-detected-commit",
                "created_at": 1770000040800,
                "commit_intent_id": "intent-003",
                "cleanup_normalized_at": 1770000040801,
            },
        )
        self.assertEqual(
            self.service.calls,
            [("submit-detected-commit", 1770000040800, "intent-003", 1770000040801)],
        )

    def test_submit_detected_commit_command_reports_skipped_when_no_local_changes(self) -> None:
        self.service.skip_submit_detected_if_needed = True

        exit_code, payload = self._run(
            "--vault-root",
            "C:/vault",
            "--base-url",
            "https://sync.example.com",
            "--vault-id",
            "vault-001",
            "--device-id",
            "desktop-shanghai",
            "submit-detected-commit",
            "--created-at",
            "1770000040810",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, {"reason": "no_local_changes", "status": "skipped"})
        self.assertEqual(
            self.service.calls,
            [("submit-detected-commit", 1770000040810, None, None)],
        )


if __name__ == "__main__":
    unittest.main()
