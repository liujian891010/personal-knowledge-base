from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import unicodedata
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


class SyncStoreError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BlobCapabilityError(SyncStoreError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(code, message)
        self.status_code = status_code


class CommitConflict(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(payload["code"])
        self.payload = payload


def _now_ms() -> int:
    return int(time.time() * 1000)


def _expires_at(minutes: int = 15) -> str:
    value = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return value.isoformat().replace("+00:00", "Z")


def _require_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise SyncStoreError("invalid_request", f"{key} must be a non-empty string")
    return value


def _require_non_negative_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or value < 0:
        raise SyncStoreError("invalid_request", f"{key} must be a non-negative integer")
    return value


def _require_positive_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or value <= 0:
        raise SyncStoreError("invalid_request", f"{key} must be a positive integer")
    return value


def _require_unique_strings(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise SyncStoreError("invalid_request", f"{key} must be a non-empty list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise SyncStoreError("invalid_request", f"{key} items must be non-empty strings")
        result.append(item)
    if len(set(result)) != len(result):
        raise SyncStoreError("invalid_request", f"{key} must be unique")
    return result


def _require_unique_positive_ints(payload: dict[str, Any], key: str) -> list[int]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise SyncStoreError("invalid_request", f"{key} must be a non-empty list")
    result: list[int] = []
    for item in value:
        if not isinstance(item, int) or item <= 0:
            raise SyncStoreError("invalid_request", f"{key} items must be positive integers")
        result.append(item)
    if len(set(result)) != len(result):
        raise SyncStoreError("invalid_request", f"{key} must be unique")
    return result


def _blob_filename(blob_id: str) -> str:
    return hashlib.sha256(blob_id.encode("utf-8")).hexdigest()


def _canonical_json_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _normalize_path(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _normalize_file_entry(payload: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(payload)
    result["path"] = _normalize_path(_require_string(result, "path"))
    return result


def _normalize_tombstone(payload: dict[str, Any], revision: int) -> dict[str, Any]:
    result = deepcopy(payload)
    _require_string(result, "file_id")
    if result.get("deleted_revision") is None:
        result["deleted_revision"] = revision
    if result.get("last_known_path") is not None:
        if not isinstance(result["last_known_path"], str) or not result["last_known_path"]:
            raise SyncStoreError("invalid_manifest", "last_known_path must be a non-empty string when provided")
        result["last_known_path"] = _normalize_path(result["last_known_path"])
    if "deleted_at" not in result:
        result["deleted_at"] = 0
    if "local_delete_seq" not in result:
        result["local_delete_seq"] = 0
    return result


def finalize_manifest_payload(manifest: dict[str, Any], revision: int) -> dict[str, Any]:
    payload = deepcopy(manifest)
    if payload.get("schema_version", "v1") != "v1":
        raise SyncStoreError("invalid_manifest", "manifest schema_version must be v1")
    _require_string(payload, "vault_id")
    _require_non_negative_int(payload, "base_revision")
    _require_string(payload, "created_by_device")
    _require_non_negative_int(payload, "created_at")

    files_payload = payload.get("files")
    tombstones_payload = payload.get("tombstones")
    if not isinstance(files_payload, list):
        raise SyncStoreError("invalid_manifest", "manifest.files must be a list")
    if not isinstance(tombstones_payload, list):
        raise SyncStoreError("invalid_manifest", "manifest.tombstones must be a list")

    files = [_normalize_file_entry(item) for item in files_payload if isinstance(item, dict)]
    if len(files) != len(files_payload):
        raise SyncStoreError("invalid_manifest", "manifest.files items must be objects")
    tombstones = [_normalize_tombstone(item, revision) for item in tombstones_payload if isinstance(item, dict)]
    if len(tombstones) != len(tombstones_payload):
        raise SyncStoreError("invalid_manifest", "manifest.tombstones items must be objects")

    files.sort(key=lambda item: (item["path"], item["file_id"]))
    tombstones.sort(key=lambda item: (item.get("local_delete_seq", 0), item["file_id"]))

    payload["schema_version"] = "v1"
    payload["revision"] = revision
    payload["files"] = files
    payload["tombstones"] = tombstones
    payload["summary_hash"] = _canonical_json_hash(
        {
            "schema_version": "v1",
            "revision": revision,
            "files": files,
            "tombstones": tombstones,
        }
    )
    return payload


class SyncStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.state_path = data_dir / "sync-state.json"
        self.blob_dir = data_dir / "blobs"
        self._lock = threading.RLock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.blob_dir.mkdir(parents=True, exist_ok=True)

    def _default_state(self) -> dict[str, Any]:
        return {
            "schema_version": "server-dev-v1",
            "devices": {},
            "tokens": {},
            "vaults": {},
            "blobs": {},
            "capabilities": {},
        }

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._default_state()
        with self.state_path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
        for key, default in self._default_state().items():
            state.setdefault(key, default)
        return state

    def _save(self, state: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(state, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        tmp_path.replace(self.state_path)

    def _ensure_vault(self, state: dict[str, Any], vault_id: str) -> dict[str, Any]:
        return state["vaults"].setdefault(
            vault_id,
            {
                "head_revision": 0,
                "manifest_summary": None,
                "manifests": {},
                "commits": {},
                "acks": {},
            },
        )

    def device_id_for_token(self, token: str) -> Optional[str]:
        with self._lock:
            state = self._load()
            device_id = state["tokens"].get(token)
            if not isinstance(device_id, str):
                return None
            device = state["devices"].get(device_id)
            if not isinstance(device, dict) or device.get("revoked"):
                return None
            return device_id

    def register_device(self, payload: dict[str, Any]) -> dict[str, Any]:
        device_name = _require_string(payload, "device_name")
        platform = _require_string(payload, "platform")
        if platform not in {"desktop", "mobile", "web"}:
            raise SyncStoreError("invalid_platform", "platform must be desktop, mobile, or web")

        device_id = f"dev_{uuid.uuid4().hex}"
        token = secrets.token_urlsafe(32)
        with self._lock:
            state = self._load()
            state["devices"][device_id] = {
                "device_id": device_id,
                "device_name": device_name,
                "platform": platform,
                "app_version": payload.get("app_version"),
                "protocol_version": payload.get("protocol_version", "v1"),
                "access_token": token,
                "revoked": False,
                "registered_at_ms": _now_ms(),
            }
            state["tokens"][token] = device_id
            self._save(state)
        return {
            "device_id": device_id,
            "access_token": token,
            "expires_at": _expires_at(minutes=60 * 24 * 30),
        }

    def delete_device(self, device_id: str) -> bool:
        with self._lock:
            state = self._load()
            device = state["devices"].get(device_id)
            if not isinstance(device, dict):
                return False
            device["revoked"] = True
            token = device.get("access_token")
            if isinstance(token, str):
                state["tokens"].pop(token, None)
            state["capabilities"] = {
                token: capability
                for token, capability in state["capabilities"].items()
                if not isinstance(capability, dict) or capability.get("device_id") != device_id
            }
            self._save(state)
            return True

    def get_vault_head(self, vault_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._load()
            vault = self._ensure_vault(state, vault_id)
            self._save(state)
            return {
                "vault_id": vault_id,
                "head_revision": vault["head_revision"],
                "manifest_summary": vault["manifest_summary"],
            }

    def get_manifest(self, vault_id: str, revision: int) -> Optional[dict[str, Any]]:
        with self._lock:
            state = self._load()
            vault = self._ensure_vault(state, vault_id)
            manifest = vault["manifests"].get(str(revision))
            return deepcopy(manifest) if isinstance(manifest, dict) else None

    def check_blobs(self, vault_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_vault_id(vault_id)
        blob_ids = _require_unique_strings(payload, "blob_ids")
        with self._lock:
            state = self._load()
            existing = [blob_id for blob_id in blob_ids if blob_id in state["blobs"]]
        return {
            "existing_blob_ids": existing,
            "missing_blob_ids": [blob_id for blob_id in blob_ids if blob_id not in set(existing)],
        }

    def init_blob_upload(
        self,
        vault_id: str,
        payload: dict[str, Any],
        *,
        request_base_url: str,
        device_id: Optional[str],
    ) -> dict[str, Any]:
        self._validate_vault_id(vault_id)
        blobs = payload.get("blobs")
        if not isinstance(blobs, list) or not blobs:
            raise SyncStoreError("invalid_request", "blobs must be a non-empty list")

        uploads: list[dict[str, Any]] = []
        with self._lock:
            state = self._load()
            for item in blobs:
                if not isinstance(item, dict):
                    raise SyncStoreError("invalid_request", "blobs items must be objects")
                blob_id = _require_string(item, "blob_id")
                encrypted_size = _require_non_negative_int(item, "encrypted_size")
                content_hash = _require_string(item, "content_hash")
                capability_token = secrets.token_urlsafe(32)
                state["capabilities"][capability_token] = {
                    "kind": "upload",
                    "vault_id": vault_id,
                    "blob_id": blob_id,
                    "encrypted_size": encrypted_size,
                    "content_hash": content_hash,
                    "device_id": device_id,
                    "expires_at_ms": _now_ms() + 15 * 60 * 1000,
                }
                uploads.append(
                    {
                        "blob_id": blob_id,
                        "upload_url": self._capability_url(request_base_url, capability_token),
                        "method": "PUT",
                        "headers": {},
                        "expires_at": _expires_at(),
                    }
                )
            self._save(state)
        return {"uploads": uploads}

    def complete_blob_upload(self, capability_token: str, payload: bytes) -> None:
        with self._lock:
            state = self._load()
            capability = self._consume_capability(state, capability_token, "upload")
            expected_size = capability["encrypted_size"]
            if len(payload) != expected_size:
                raise BlobCapabilityError(400, "blob_size_mismatch", "Uploaded blob size does not match capability.")
            blob_id = capability["blob_id"]
            filename = _blob_filename(blob_id)
            path = self.blob_dir / filename
            path.write_bytes(payload)
            state["blobs"][blob_id] = {
                "blob_id": blob_id,
                "filename": filename,
                "encrypted_size": len(payload),
                "content_hash": capability.get("content_hash"),
                "uploaded_at_ms": _now_ms(),
            }
            self._save(state)

    def init_blob_download(
        self,
        vault_id: str,
        payload: dict[str, Any],
        *,
        request_base_url: str,
        device_id: Optional[str],
    ) -> dict[str, Any]:
        self._validate_vault_id(vault_id)
        blob_ids = _require_unique_strings(payload, "blob_ids")
        downloads: list[dict[str, Any]] = []
        with self._lock:
            state = self._load()
            missing = [blob_id for blob_id in blob_ids if blob_id not in state["blobs"]]
            if missing:
                raise SyncStoreError("blob_not_found", f"Blob was not found: {missing[0]}")
            for blob_id in blob_ids:
                blob = state["blobs"][blob_id]
                capability_token = secrets.token_urlsafe(32)
                state["capabilities"][capability_token] = {
                    "kind": "download",
                    "vault_id": vault_id,
                    "blob_id": blob_id,
                    "device_id": device_id,
                    "expires_at_ms": _now_ms() + 15 * 60 * 1000,
                }
                downloads.append(
                    {
                        "blob_id": blob_id,
                        "download_url": self._capability_url(request_base_url, capability_token),
                        "encrypted_size": blob["encrypted_size"],
                        "headers": {},
                        "expires_at": _expires_at(),
                    }
                )
            self._save(state)
        return {"downloads": downloads}

    def read_blob_download(self, capability_token: str) -> bytes:
        with self._lock:
            state = self._load()
            capability = self._consume_capability(state, capability_token, "download")
            blob = state["blobs"].get(capability["blob_id"])
            if not isinstance(blob, dict):
                raise BlobCapabilityError(404, "blob_not_found", "Blob was not found.")
            path = self.blob_dir / blob["filename"]
            if not path.exists():
                raise BlobCapabilityError(404, "blob_not_found", "Blob payload was not found.")
            payload = path.read_bytes()
            self._save(state)
            return payload

    def create_commit(
        self,
        vault_id: str,
        payload: dict[str, Any],
        *,
        auth_device_id: Optional[str],
    ) -> dict[str, Any]:
        commit_intent_id = _require_string(payload, "commit_intent_id")
        base_revision = _require_non_negative_int(payload, "base_revision")
        intent_manifest_hash = _require_string(payload, "intent_manifest_hash")
        manifest = payload.get("manifest")
        if not isinstance(manifest, dict):
            raise SyncStoreError("invalid_request", "manifest must be an object")
        if manifest.get("vault_id") != vault_id:
            raise SyncStoreError("invalid_manifest", "manifest.vault_id must match path vault_id")
        if manifest.get("base_revision") != base_revision:
            raise SyncStoreError("invalid_manifest", "manifest.base_revision must match request base_revision")

        created_by_device = payload.get("created_by_device") or manifest.get("created_by_device") or auth_device_id
        if not isinstance(created_by_device, str) or not created_by_device:
            raise SyncStoreError("invalid_request", "created_by_device is required")

        with self._lock:
            state = self._load()
            vault = self._ensure_vault(state, vault_id)
            existing_commit = vault["commits"].get(commit_intent_id)
            if isinstance(existing_commit, dict):
                if existing_commit.get("intent_manifest_hash") == intent_manifest_hash:
                    revision = existing_commit["revision"]
                    vault["acks"][created_by_device] = max(vault["acks"].get(created_by_device, 0), revision)
                    self._save(state)
                    return {
                        "vault_id": vault_id,
                        "new_revision": revision,
                        "head_manifest_summary": vault["manifest_summary"],
                        "acked_revision_for_device": vault["acks"][created_by_device],
                    }
                raise CommitConflict(self._commit_conflict_payload(vault, code="manifest_conflict"))

            if base_revision != vault["head_revision"]:
                raise CommitConflict(self._commit_conflict_payload(vault, code="base_revision_conflict"))

            self._require_commit_blobs_exist(state, payload)
            new_revision = vault["head_revision"] + 1
            finalized_manifest = finalize_manifest_payload(manifest, new_revision)
            vault["manifests"][str(new_revision)] = finalized_manifest
            vault["head_revision"] = new_revision
            vault["manifest_summary"] = finalized_manifest["summary_hash"]
            vault["commits"][commit_intent_id] = {
                "commit_intent_id": commit_intent_id,
                "intent_manifest_hash": intent_manifest_hash,
                "revision": new_revision,
                "created_by_device": created_by_device,
                "committed_at_ms": _now_ms(),
            }
            vault["acks"][created_by_device] = max(vault["acks"].get(created_by_device, 0), new_revision)
            self._save(state)
            return {
                "vault_id": vault_id,
                "new_revision": new_revision,
                "head_manifest_summary": finalized_manifest["summary_hash"],
                "acked_revision_for_device": vault["acks"][created_by_device],
            }

    def resolve_commit_intent(self, vault_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        commit_intent_id = _require_string(payload, "commit_intent_id")
        intent_manifest_hash = _require_string(payload, "intent_manifest_hash")
        with self._lock:
            state = self._load()
            vault = self._ensure_vault(state, vault_id)
            commit = vault["commits"].get(commit_intent_id)
            base_payload = {
                "observed_head_revision": vault["head_revision"],
                "head_manifest_summary": vault["manifest_summary"],
            }
            if not isinstance(commit, dict):
                return {"status": "not_found", **base_payload}
            if commit.get("intent_manifest_hash") != intent_manifest_hash:
                return {"status": "mismatched", **base_payload}
            return {
                "status": "found",
                "matched_revision": commit["revision"],
                **base_payload,
            }

    def ack_revisions(
        self,
        vault_id: str,
        payload: dict[str, Any],
        *,
        device_id: Optional[str],
    ) -> dict[str, Any]:
        revisions = _require_unique_positive_ints(payload, "revisions")
        actor = device_id or "anonymous"
        with self._lock:
            state = self._load()
            vault = self._ensure_vault(state, vault_id)
            max_revision = max(revisions)
            if max_revision > vault["head_revision"]:
                raise SyncStoreError("ack_revision_ahead_of_head", "Ack revision cannot exceed current vault head")
            vault["acks"][actor] = max(vault["acks"].get(actor, 0), max_revision)
            self._save(state)
            return {"max_acked_revision": vault["acks"][actor]}

    def _consume_capability(self, state: dict[str, Any], capability_token: str, expected_kind: str) -> dict[str, Any]:
        capability = state["capabilities"].pop(capability_token, None)
        if not isinstance(capability, dict):
            raise BlobCapabilityError(404, "capability_not_found", "Blob capability was not found.")
        if capability.get("kind") != expected_kind:
            raise BlobCapabilityError(403, "capability_wrong_kind", "Blob capability cannot be used for this operation.")
        if capability.get("expires_at_ms", 0) < _now_ms():
            raise BlobCapabilityError(403, "capability_expired", "Blob capability has expired.")
        return capability

    def _capability_url(self, request_base_url: str, capability_token: str) -> str:
        return f"{request_base_url.rstrip('/')}/_capabilities/blobs/{capability_token}"

    def _commit_conflict_payload(self, vault: dict[str, Any], *, code: str) -> dict[str, Any]:
        return {
            "code": code,
            "current_head_revision": vault["head_revision"],
            "current_manifest_summary": vault["manifest_summary"],
        }

    def _require_commit_blobs_exist(self, state: dict[str, Any], payload: dict[str, Any]) -> None:
        blob_refs = payload.get("blob_refs", [])
        if blob_refs is None:
            blob_refs = []
        if not isinstance(blob_refs, list):
            raise SyncStoreError("invalid_request", "blob_refs must be a list when provided")
        for item in blob_refs:
            if not isinstance(item, dict):
                raise SyncStoreError("invalid_request", "blob_refs items must be objects")
            blob_id = _require_string(item, "blob_id")
            _require_string(item, "file_id")
            if blob_id not in state["blobs"]:
                raise SyncStoreError("blob_not_uploaded", f"Commit references a blob that has not been uploaded: {blob_id}")

    def _validate_vault_id(self, vault_id: str) -> None:
        if not vault_id:
            raise SyncStoreError("invalid_vault_id", "vault_id must be non-empty")
