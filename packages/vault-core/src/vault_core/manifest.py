from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any, Dict, Literal

from .models import ManifestFileEntry, ManifestRecord, TombstoneRecord


ManifestHashPurpose = Literal["final", "intent"]


def _normalize_path(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _normalize_file_entry(record: ManifestFileEntry) -> Dict[str, Any]:
    payload = record.to_dict()
    payload["path"] = _normalize_path(payload["path"])
    return payload


def _normalize_tombstone(record: TombstoneRecord, *, purpose: ManifestHashPurpose) -> Dict[str, Any]:
    payload = record.to_dict()
    if payload.get("last_known_path") is not None:
        payload["last_known_path"] = _normalize_path(payload["last_known_path"])
    if purpose == "intent" and payload.get("deleted_revision") is None:
        payload.pop("deleted_revision", None)
    return payload


def canonical_manifest_payload(
    manifest: ManifestRecord,
    *,
    purpose: ManifestHashPurpose = "final",
) -> Dict[str, Any]:
    files = sorted(
        (_normalize_file_entry(record) for record in manifest.files),
        key=lambda item: (item["path"], item["file_id"]),
    )
    tombstones = sorted(
        (_normalize_tombstone(record, purpose=purpose) for record in manifest.tombstones),
        key=lambda item: (item.get("local_delete_seq", 0), item["file_id"]),
    )

    if purpose == "final":
        return {
            "schema_version": manifest.schema_version,
            "revision": manifest.revision,
            "files": files,
            "tombstones": tombstones,
        }

    return {
        "schema_version": manifest.schema_version,
        "vault_id": manifest.vault_id,
        "base_revision": manifest.base_revision,
        "created_by_device": manifest.created_by_device,
        "created_at": manifest.created_at,
        "files": files,
        "tombstones": tombstones,
    }


def serialize_manifest_canonical(
    manifest: ManifestRecord,
    *,
    purpose: ManifestHashPurpose = "final",
) -> bytes:
    payload = canonical_manifest_payload(manifest, purpose=purpose)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_hex(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def compute_manifest_summary_hash(manifest: ManifestRecord) -> str:
    return _sha256_hex(serialize_manifest_canonical(manifest, purpose="final"))


def compute_intent_manifest_hash(manifest: ManifestRecord) -> str:
    return _sha256_hex(serialize_manifest_canonical(manifest, purpose="intent"))


def with_computed_manifest_summary(manifest: ManifestRecord) -> ManifestRecord:
    return ManifestRecord(
        schema_version=manifest.schema_version,
        vault_id=manifest.vault_id,
        revision=manifest.revision,
        base_revision=manifest.base_revision,
        created_by_device=manifest.created_by_device,
        created_at=manifest.created_at,
        files=list(manifest.files),
        tombstones=list(manifest.tombstones),
        summary_hash=compute_manifest_summary_hash(manifest),
        meta=manifest.meta,
    )


def build_empty_vault_manifest_summary() -> str:
    empty_manifest = ManifestRecord(
        vault_id="__empty_vault__",
        revision=0,
        base_revision=0,
        created_by_device="__system__",
        created_at=0,
        files=[],
        tombstones=[],
        summary_hash="placeholder",
    )
    return compute_manifest_summary_hash(empty_manifest)


EMPTY_VAULT_FINAL_MANIFEST_SUMMARY = build_empty_vault_manifest_summary()
