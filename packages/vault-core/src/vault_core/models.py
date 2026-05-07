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


def _require_non_negative_int(name: str, value: Optional[int], allow_none: bool = False) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


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
