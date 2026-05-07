from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


VALID_FILE_TYPES = {
    "note",
    "attachment",
    "ai_wiki",
    "ai_index",
    "ai_agents",
}

VALID_FILE_STATUSES = {
    "active",
    "deleted",
    "conflict_copy",
}

VALID_WIKI_TASK_STATUSES = {
    "pending",
    "running",
    "done",
    "failed",
    "skipped",
    "superseded",
}

VALID_COMMIT_INTENT_STATUSES = {
    "prepared",
    "submitted",
    "acknowledged",
}

VALID_SYNC_APPLY_PHASES = {
    "preparing",
    "staging",
    "materializing",
    "filemap_rewrite",
    "finalizing",
}


def _require_non_negative_int(name: str, value: Optional[int], allow_none: bool = False) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _require_positive_int(name: str, value: Optional[int], allow_none: bool = False) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class FileRecord:
    file_id: str
    path: str
    type: str
    status: str
    updated_at: int
    content_hash: Optional[str] = None
    last_known_revision: Optional[int] = None
    conflict_source_file_id: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if not self.file_id:
            raise ValueError("file_id must be non-empty")
        if not self.path:
            raise ValueError("path must be non-empty")
        if self.type not in VALID_FILE_TYPES:
            raise ValueError(f"unsupported file type: {self.type}")
        if self.status not in VALID_FILE_STATUSES:
            raise ValueError(f"unsupported file status: {self.status}")
        _require_non_negative_int("updated_at", self.updated_at)
        _require_non_negative_int("last_known_revision", self.last_known_revision, allow_none=True)
        if self.status == "conflict_copy" and not self.conflict_source_file_id:
            raise ValueError("conflict_copy records must include conflict_source_file_id")

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "file_id": self.file_id,
            "path": self.path,
            "type": self.type,
            "status": self.status,
            "updated_at": self.updated_at,
        }
        if self.content_hash is not None:
            payload["content_hash"] = self.content_hash
        if self.last_known_revision is not None or self.status == "conflict_copy":
            payload["last_known_revision"] = self.last_known_revision
        if self.conflict_source_file_id is not None:
            payload["conflict_source_file_id"] = self.conflict_source_file_id
        if self.meta is not None:
            payload["meta"] = self.meta
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "FileRecord":
        return cls(
            file_id=payload["file_id"],
            path=payload["path"],
            type=payload["type"],
            status=payload["status"],
            updated_at=payload["updated_at"],
            content_hash=payload.get("content_hash"),
            last_known_revision=payload.get("last_known_revision"),
            conflict_source_file_id=payload.get("conflict_source_file_id"),
            meta=payload.get("meta"),
        )


@dataclass(frozen=True)
class FileMapDocument:
    vault_id: str
    updated_at: int
    files: List[FileRecord] = field(default_factory=list)
    schema_version: str = "v1"
    meta: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.schema_version != "v1":
            raise ValueError("unsupported schema_version")
        if not self.vault_id:
            raise ValueError("vault_id must be non-empty")
        _require_non_negative_int("updated_at", self.updated_at)

    def sorted_files(self) -> List[FileRecord]:
        return sorted(self.files, key=lambda item: (item.path, item.file_id))

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "vault_id": self.vault_id,
            "updated_at": self.updated_at,
            "files": [record.to_dict() for record in self.sorted_files()],
        }
        if self.meta is not None:
            payload["meta"] = self.meta
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "FileMapDocument":
        return cls(
            schema_version=payload.get("schema_version", "v1"),
            vault_id=payload["vault_id"],
            updated_at=payload["updated_at"],
            files=[FileRecord.from_dict(item) for item in payload.get("files", [])],
            meta=payload.get("meta"),
        )

    def replace_files(self, files: Iterable[FileRecord], updated_at: int) -> "FileMapDocument":
        return FileMapDocument(
            schema_version=self.schema_version,
            vault_id=self.vault_id,
            updated_at=updated_at,
            files=list(files),
            meta=self.meta,
        )


@dataclass(frozen=True)
class TombstoneRecord:
    file_id: str
    deleted_revision: Optional[int]
    deleted_at: int
    local_delete_seq: int
    last_known_path: Optional[str] = None
    deleted_by_device: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if not self.file_id:
            raise ValueError("file_id must be non-empty")
        _require_non_negative_int("local_delete_seq", self.local_delete_seq)
        _require_non_negative_int("deleted_at", self.deleted_at)
        _require_positive_int("deleted_revision", self.deleted_revision, allow_none=True)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "file_id": self.file_id,
            "deleted_revision": self.deleted_revision,
            "deleted_at": self.deleted_at,
            "local_delete_seq": self.local_delete_seq,
        }
        if self.last_known_path is not None:
            payload["last_known_path"] = self.last_known_path
        if self.deleted_by_device is not None:
            payload["deleted_by_device"] = self.deleted_by_device
        if self.meta is not None:
            payload["meta"] = self.meta
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "TombstoneRecord":
        return cls(
            file_id=payload["file_id"],
            deleted_revision=payload.get("deleted_revision"),
            deleted_at=payload["deleted_at"],
            local_delete_seq=payload["local_delete_seq"],
            last_known_path=payload.get("last_known_path"),
            deleted_by_device=payload.get("deleted_by_device"),
            meta=payload.get("meta"),
        )


@dataclass(frozen=True)
class ManifestFileEntry:
    file_id: str
    path: str
    type: str
    content_hash: str
    blob_id: str
    size: int
    mtime: int
    mime_type: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if not self.file_id:
            raise ValueError("file_id must be non-empty")
        if not self.path:
            raise ValueError("path must be non-empty")
        if self.type not in VALID_FILE_TYPES:
            raise ValueError(f"unsupported file type: {self.type}")
        if not self.content_hash:
            raise ValueError("content_hash must be non-empty")
        if not self.blob_id:
            raise ValueError("blob_id must be non-empty")
        _require_non_negative_int("size", self.size)
        _require_non_negative_int("mtime", self.mtime)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "file_id": self.file_id,
            "path": self.path,
            "type": self.type,
            "content_hash": self.content_hash,
            "blob_id": self.blob_id,
            "size": self.size,
            "mtime": self.mtime,
        }
        if self.mime_type is not None:
            payload["mime_type"] = self.mime_type
        if self.meta is not None:
            payload["meta"] = self.meta
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ManifestFileEntry":
        return cls(
            file_id=payload["file_id"],
            path=payload["path"],
            type=payload["type"],
            content_hash=payload["content_hash"],
            blob_id=payload["blob_id"],
            size=payload["size"],
            mtime=payload["mtime"],
            mime_type=payload.get("mime_type"),
            meta=payload.get("meta"),
        )


@dataclass(frozen=True)
class ManifestRecord:
    vault_id: str
    revision: int
    base_revision: int
    created_by_device: str
    created_at: int
    files: List[ManifestFileEntry] = field(default_factory=list)
    tombstones: List[TombstoneRecord] = field(default_factory=list)
    summary_hash: str = ""
    schema_version: str = "v1"
    meta: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.schema_version != "v1":
            raise ValueError("unsupported schema_version")
        if not self.vault_id:
            raise ValueError("vault_id must be non-empty")
        if not self.created_by_device:
            raise ValueError("created_by_device must be non-empty")
        if not self.summary_hash:
            raise ValueError("summary_hash must be non-empty")
        _require_non_negative_int("revision", self.revision)
        _require_non_negative_int("base_revision", self.base_revision)
        _require_non_negative_int("created_at", self.created_at)

    def sorted_files(self) -> List[ManifestFileEntry]:
        return sorted(self.files, key=lambda item: (item.path, item.file_id))

    def sorted_tombstones(self) -> List[TombstoneRecord]:
        return sorted(self.tombstones, key=lambda item: item.file_id)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "vault_id": self.vault_id,
            "revision": self.revision,
            "base_revision": self.base_revision,
            "created_by_device": self.created_by_device,
            "created_at": self.created_at,
            "files": [record.to_dict() for record in self.sorted_files()],
            "tombstones": [record.to_dict() for record in self.sorted_tombstones()],
            "summary_hash": self.summary_hash,
        }
        if self.meta is not None:
            payload["meta"] = self.meta
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ManifestRecord":
        return cls(
            schema_version=payload.get("schema_version", "v1"),
            vault_id=payload["vault_id"],
            revision=payload["revision"],
            base_revision=payload["base_revision"],
            created_by_device=payload["created_by_device"],
            created_at=payload["created_at"],
            files=[ManifestFileEntry.from_dict(item) for item in payload.get("files", [])],
            tombstones=[TombstoneRecord.from_dict(item) for item in payload.get("tombstones", [])],
            summary_hash=payload["summary_hash"],
            meta=payload.get("meta"),
        )


@dataclass(frozen=True)
class VaultStateRecord:
    vault_id: str
    last_applied_revision: int
    remote_head_revision: int
    acked_revision: int
    pending_ack_to_server: List[int]
    commit_in_progress: bool
    last_manifest_summary: Optional[str]
    last_manifest_summary_status: str
    local_delete_sequence: int
    has_unresolved_conflicts: bool = False
    schema_version: str = "v1"
    meta: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.schema_version != "v1":
            raise ValueError("unsupported schema_version")
        if not self.vault_id:
            raise ValueError("vault_id must be non-empty")
        _require_non_negative_int("last_applied_revision", self.last_applied_revision)
        _require_non_negative_int("remote_head_revision", self.remote_head_revision)
        _require_non_negative_int("acked_revision", self.acked_revision)
        _require_non_negative_int("local_delete_sequence", self.local_delete_sequence)
        if self.last_manifest_summary_status not in {"valid", "missing", "stale"}:
            raise ValueError("unsupported last_manifest_summary_status")
        for revision in self.pending_ack_to_server:
            _require_positive_int("pending_ack_to_server revision", revision)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "vault_id": self.vault_id,
            "last_applied_revision": self.last_applied_revision,
            "remote_head_revision": self.remote_head_revision,
            "acked_revision": self.acked_revision,
            "pending_ack_to_server": list(self.pending_ack_to_server),
            "commit_in_progress": self.commit_in_progress,
            "last_manifest_summary": self.last_manifest_summary,
            "last_manifest_summary_status": self.last_manifest_summary_status,
            "local_delete_sequence": self.local_delete_sequence,
            "has_unresolved_conflicts": self.has_unresolved_conflicts,
        }
        if self.meta is not None:
            payload["meta"] = self.meta
        return payload


@dataclass(frozen=True)
class WikiTaskRecord:
    task_id: str
    target_wiki_path: str
    task_base_page_hash: Optional[str]
    task_base_revision: int
    task_sources_hash: str
    status: str
    created_at: int
    updated_at: int

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("task_id must be non-empty")
        if not self.target_wiki_path:
            raise ValueError("target_wiki_path must be non-empty")
        if not self.task_sources_hash:
            raise ValueError("task_sources_hash must be non-empty")
        if self.status not in VALID_WIKI_TASK_STATUSES:
            raise ValueError(f"unsupported wiki task status: {self.status}")
        _require_non_negative_int("task_base_revision", self.task_base_revision)
        _require_non_negative_int("created_at", self.created_at)
        _require_non_negative_int("updated_at", self.updated_at)


@dataclass(frozen=True)
class CommitIntentJournalRecord:
    vault_id: str
    commit_intent_id: str
    intent_manifest_hash: str
    base_revision: int
    created_by_device: str
    status: str
    created_at: int
    updated_at: int
    intent_delete_seq_upper_bound: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.vault_id:
            raise ValueError("vault_id must be non-empty")
        if not self.commit_intent_id:
            raise ValueError("commit_intent_id must be non-empty")
        if not self.intent_manifest_hash:
            raise ValueError("intent_manifest_hash must be non-empty")
        if not self.created_by_device:
            raise ValueError("created_by_device must be non-empty")
        if self.status not in VALID_COMMIT_INTENT_STATUSES:
            raise ValueError(f"unsupported commit intent status: {self.status}")
        _require_non_negative_int("base_revision", self.base_revision)
        _require_non_negative_int("created_at", self.created_at)
        _require_non_negative_int("updated_at", self.updated_at)
        _require_positive_int(
            "intent_delete_seq_upper_bound",
            self.intent_delete_seq_upper_bound,
            allow_none=True,
        )


@dataclass(frozen=True)
class SyncApplyJournalRecord:
    vault_id: str
    journal_id: str
    target_revision: int
    target_manifest_hash: str
    phase: str
    created_at: int
    updated_at: int
    ops_hash: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.vault_id:
            raise ValueError("vault_id must be non-empty")
        if not self.journal_id:
            raise ValueError("journal_id must be non-empty")
        if not self.target_manifest_hash:
            raise ValueError("target_manifest_hash must be non-empty")
        if self.phase not in VALID_SYNC_APPLY_PHASES:
            raise ValueError(f"unsupported sync apply phase: {self.phase}")
        _require_non_negative_int("target_revision", self.target_revision)
        _require_non_negative_int("created_at", self.created_at)
        _require_non_negative_int("updated_at", self.updated_at)
