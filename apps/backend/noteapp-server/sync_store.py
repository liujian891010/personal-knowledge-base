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
from urllib.parse import quote

from blob_storage import BlobStorageError, FileSystemBlobStore
from sync_repository import JsonStateRepository, StateRepositoryConflict


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


VALID_FILE_VERSION_SOURCES = {
    "commit_success",
    "manual_checkpoint",
    "manual_meeting_checkpoint",
    "restore",
}

DEFAULT_FILE_VERSION_RETENTION_POLICY = {
    "keep_latest": 50,
    "keep_pinned": True,
}
DEFAULT_ACCESS_TOKEN_TTL_MS = 60 * 60 * 1000
DEFAULT_REFRESH_TOKEN_TTL_MS = 30 * 24 * 60 * 60 * 1000
DEFAULT_DEVICE_INACTIVE_AFTER_MS = 7 * 24 * 60 * 60 * 1000
DEFAULT_TOMBSTONE_GC_MIN_RETENTION_MS = 7 * 24 * 60 * 60 * 1000
MAX_TOMBSTONE_GC_LOGS = 100


def _now_ms() -> int:
    return int(time.time() * 1000)


def _expires_at(minutes: int = 15) -> str:
    value = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return value.isoformat().replace("+00:00", "Z")


def _iso_at_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def _optional_positive_int(payload: dict[str, Any], key: str, default: int) -> int:
    if key not in payload:
        return default
    value = payload.get(key)
    if not isinstance(value, int) or value <= 0:
        raise SyncStoreError("invalid_request", f"{key} must be a positive integer")
    return value


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


def _blob_object_key(vault_id: str, blob_id: str) -> str:
    vault_segment = quote(vault_id, safe="")
    blob_segment = hashlib.sha256(blob_id.encode("utf-8")).hexdigest()
    return f"vaults/{vault_segment}/blobs/{blob_segment}"


def _chunk_filename(session_id: str, chunk_id: str) -> str:
    return hashlib.sha256(f"{session_id}:{chunk_id}".encode("utf-8")).hexdigest()


def _sha256_payload(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _capability_error_from_blob_storage(error: BlobStorageError) -> BlobCapabilityError:
    return BlobCapabilityError(error.status_code, error.code, str(error))


def _sync_error_from_blob_storage(error: BlobStorageError) -> SyncStoreError:
    return SyncStoreError(error.code, str(error))


def _file_version_id(*, vault_id: str, file_id: str, revision: int, blob_id: str, source: str) -> str:
    digest = hashlib.sha256(f"{vault_id}:{file_id}:{revision}:{blob_id}:{source}".encode("utf-8")).hexdigest()[:32]
    return f"fv_{digest}"


def _canonical_json_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _normalize_path(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _blob_chunks(blob_id: str, encrypted_size: int, chunk_size: int) -> list[dict[str, int | str]]:
    if encrypted_size < 0:
        raise SyncStoreError("invalid_request", "encrypted_size must be a non-negative integer")
    if chunk_size <= 0:
        raise SyncStoreError("invalid_request", "chunk_size must be a positive integer")
    if encrypted_size == 0:
        digest = hashlib.sha256(f"{blob_id}:0:0".encode("utf-8")).hexdigest()[:32]
        return [{"chunk_id": f"chunk-{digest}", "offset": 0, "size": 0}]

    chunks: list[dict[str, int | str]] = []
    offset = 0
    while offset < encrypted_size:
        size = min(chunk_size, encrypted_size - offset)
        digest = hashlib.sha256(f"{blob_id}:{offset}:{size}".encode("utf-8")).hexdigest()[:32]
        chunks.append({"chunk_id": f"chunk-{digest}", "offset": offset, "size": size})
        offset += size
    return chunks


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
    def __init__(self, data_dir: Path, *, repository: Optional[Any] = None, blob_store: Optional[Any] = None) -> None:
        self.data_dir = data_dir
        self.state_path = data_dir / "sync-state.json"
        self.blob_dir = data_dir / "blobs"
        self.chunk_dir = data_dir / "blob-upload-chunks"
        self.repository = repository or JsonStateRepository(self.state_path)
        self.blob_store = blob_store or FileSystemBlobStore(self.blob_dir, backend_name="local")
        self._lock = threading.RLock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        self.chunk_dir.mkdir(parents=True, exist_ok=True)

    def _default_state(self) -> dict[str, Any]:
        return {
            "schema_version": "server-dev-v1",
            "users": {},
            "user_logins": {},
            "devices": {},
            "sessions": {},
            "tokens": {},
            "refresh_tokens": {},
            "vaults": {},
            "blobs": {},
            "capabilities": {},
            "resumable_upload_sessions": {},
        }

    def _load(self) -> dict[str, Any]:
        defaults = self._default_state()
        state = self.repository.load(defaults)
        for key, default in defaults.items():
            state.setdefault(key, default)
        return state

    def _save(self, state: dict[str, Any]) -> None:
        try:
            self.repository.save(state)
        except StateRepositoryConflict as error:
            raise SyncStoreError(error.code, str(error)) from error

    def _ensure_vault(self, state: dict[str, Any], vault_id: str) -> dict[str, Any]:
        return state["vaults"].setdefault(
            vault_id,
            {
                "head_revision": 0,
                "manifest_summary": None,
                "manifests": {},
                "commits": {},
                "acks": {},
                "tombstone_gc": {
                    "runs": [],
                },
                "file_versions": {
                    "records": {},
                    "by_file_id": {},
                    "retention_policy": dict(DEFAULT_FILE_VERSION_RETENTION_POLICY),
                },
            },
        )

    def _ensure_tombstone_gc(self, vault: dict[str, Any]) -> dict[str, Any]:
        tombstone_gc = vault.setdefault("tombstone_gc", {"runs": []})
        if not isinstance(tombstone_gc, dict):
            tombstone_gc = {"runs": []}
            vault["tombstone_gc"] = tombstone_gc
        runs = tombstone_gc.setdefault("runs", [])
        if not isinstance(runs, list):
            tombstone_gc["runs"] = []
        return tombstone_gc

    def _joined_vault_device_ids(self, vault: dict[str, Any]) -> list[str]:
        acks = vault.setdefault("acks", {})
        if not isinstance(acks, dict):
            vault["acks"] = {}
            return []
        return sorted(device_id for device_id in acks if isinstance(device_id, str) and device_id)

    def _active_vault_device_ids(self, state: dict[str, Any], vault: dict[str, Any]) -> list[str]:
        active_device_ids: list[str] = []
        for device_id in self._joined_vault_device_ids(vault):
            device = state["devices"].get(device_id)
            if isinstance(device, dict) and device.get("revoked"):
                continue
            active_device_ids.append(device_id)
        return active_device_ids

    def _ensure_user(self, state: dict[str, Any], *, account_key: str, display_name: str, now_ms: int) -> dict[str, Any]:
        user_id = state["user_logins"].get(account_key)
        if isinstance(user_id, str):
            user = state["users"].get(user_id)
            if isinstance(user, dict):
                user["display_name"] = display_name or user.get("display_name") or account_key
                user["updated_at_ms"] = now_ms
                return user

        user_id = f"user_{uuid.uuid4().hex}"
        user = {
            "user_id": user_id,
            "account_key": account_key,
            "display_name": display_name or account_key,
            "status": "active",
            "created_at_ms": now_ms,
            "updated_at_ms": now_ms,
        }
        state["users"][user_id] = user
        state["user_logins"][account_key] = user_id
        return user

    def _session_token_response(
        self,
        *,
        user_id: str,
        device_id: str,
        access_token: str,
        refresh_token: str,
        access_expires_at_ms: int,
        refresh_expires_at_ms: int,
    ) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "device_id": device_id,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": _iso_at_ms(access_expires_at_ms),
            "refresh_expires_at": _iso_at_ms(refresh_expires_at_ms),
        }

    def _revoke_device_sessions_locked(self, state: dict[str, Any], device_id: str, *, revoked_at_ms: int) -> None:
        for session in state["sessions"].values():
            if not isinstance(session, dict) or session.get("device_id") != device_id:
                continue
            session["revoked_at_ms"] = revoked_at_ms
            refresh_token = session.get("refresh_token")
            if isinstance(refresh_token, str):
                state["refresh_tokens"].pop(refresh_token, None)

    def authorize_access_token(self, token: str, *, now_ms: Optional[int] = None) -> dict[str, str]:
        resolved_now_ms = _now_ms() if now_ms is None else now_ms
        with self._lock:
            state = self._load()
            session_id = state["tokens"].get(token)
            if isinstance(session_id, str):
                session = state["sessions"].get(session_id)
                if isinstance(session, dict):
                    device_id = session.get("device_id")
                    user_id = session.get("user_id")
                    if not isinstance(device_id, str) or not isinstance(user_id, str):
                        raise SyncStoreError("invalid_access_token", "Access token is invalid")
                    device = state["devices"].get(device_id)
                    if (
                        not isinstance(device, dict)
                        or device.get("revoked")
                        or session.get("revoked_at_ms") is not None
                    ):
                        raise SyncStoreError("device_revoked", "Device token is invalid or revoked")
                    expires_at_ms = session.get("access_expires_at_ms")
                    if not isinstance(expires_at_ms, int) or expires_at_ms <= resolved_now_ms:
                        raise SyncStoreError("access_token_expired", "Access token has expired")
                    return {"user_id": user_id, "device_id": device_id, "session_id": session_id}

                # Compatibility for pre-M3 JSON states where tokens mapped directly to device IDs.
                device_id = session_id
            else:
                raise SyncStoreError("invalid_access_token", "Access token is invalid")

            device = state["devices"].get(device_id)
            if not isinstance(device, dict) or device.get("revoked"):
                raise SyncStoreError("device_revoked", "Device token is invalid or revoked")
            user_id = device.get("user_id")
            if not isinstance(user_id, str):
                user_id = "legacy-user"
            return {"user_id": user_id, "device_id": device_id, "session_id": ""}

    def device_id_for_token(self, token: str, *, now_ms: Optional[int] = None) -> Optional[str]:
        try:
            return self.authorize_access_token(token, now_ms=now_ms)["device_id"]
        except SyncStoreError:
            return None

    def touch_device(self, device_id: Optional[str], *, now_ms: Optional[int] = None) -> None:
        if not device_id:
            return
        with self._lock:
            state = self._load()
            device = state["devices"].get(device_id)
            if not isinstance(device, dict) or device.get("revoked"):
                return
            resolved_now_ms = _now_ms() if now_ms is None else now_ms
            device["last_seen_at_ms"] = resolved_now_ms
            self._save(state)

    def register_device(self, payload: dict[str, Any], *, now_ms: Optional[int] = None) -> dict[str, Any]:
        device_name = _require_string(payload, "device_name")
        platform = _require_string(payload, "platform")
        if platform not in {"desktop", "mobile", "web"}:
            raise SyncStoreError("invalid_platform", "platform must be desktop, mobile, or web")
        account_key = payload.get("account_key", payload.get("login_key", "local-dev"))
        if not isinstance(account_key, str) or not account_key:
            raise SyncStoreError("invalid_request", "account_key must be a non-empty string when provided")
        display_name = payload.get("display_name")
        if display_name is None:
            display_name = account_key
        if not isinstance(display_name, str) or not display_name:
            raise SyncStoreError("invalid_request", "display_name must be a non-empty string when provided")
        access_token_ttl_ms = _optional_positive_int(payload, "access_token_ttl_ms", DEFAULT_ACCESS_TOKEN_TTL_MS)
        refresh_token_ttl_ms = _optional_positive_int(payload, "refresh_token_ttl_ms", DEFAULT_REFRESH_TOKEN_TTL_MS)

        device_id = f"dev_{uuid.uuid4().hex}"
        session_id = f"sess_{uuid.uuid4().hex}"
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        resolved_now_ms = _now_ms() if now_ms is None else now_ms
        access_expires_at_ms = resolved_now_ms + access_token_ttl_ms
        refresh_expires_at_ms = resolved_now_ms + refresh_token_ttl_ms
        with self._lock:
            state = self._load()
            user = self._ensure_user(
                state,
                account_key=account_key,
                display_name=display_name,
                now_ms=resolved_now_ms,
            )
            user_id = user["user_id"]
            state["devices"][device_id] = {
                "device_id": device_id,
                "user_id": user_id,
                "device_name": device_name,
                "platform": platform,
                "app_version": payload.get("app_version"),
                "protocol_version": payload.get("protocol_version", "v1"),
                "session_id": session_id,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "access_token_expires_at_ms": access_expires_at_ms,
                "refresh_token_expires_at_ms": refresh_expires_at_ms,
                "revoked": False,
                "registered_at_ms": resolved_now_ms,
                "last_seen_at_ms": resolved_now_ms,
            }
            state["sessions"][session_id] = {
                "session_id": session_id,
                "user_id": user_id,
                "device_id": device_id,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "access_expires_at_ms": access_expires_at_ms,
                "refresh_expires_at_ms": refresh_expires_at_ms,
                "created_at_ms": resolved_now_ms,
                "updated_at_ms": resolved_now_ms,
                "revoked_at_ms": None,
            }
            state["tokens"][access_token] = session_id
            state["refresh_tokens"][refresh_token] = session_id
            self._save(state)
        return self._session_token_response(
            user_id=user_id,
            device_id=device_id,
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_at_ms=access_expires_at_ms,
            refresh_expires_at_ms=refresh_expires_at_ms,
        )

    def refresh_session(self, payload: dict[str, Any], *, now_ms: Optional[int] = None) -> dict[str, Any]:
        refresh_token = _require_string(payload, "refresh_token")
        access_token_ttl_ms = _optional_positive_int(payload, "access_token_ttl_ms", DEFAULT_ACCESS_TOKEN_TTL_MS)
        refresh_token_ttl_ms = _optional_positive_int(payload, "refresh_token_ttl_ms", DEFAULT_REFRESH_TOKEN_TTL_MS)
        resolved_now_ms = _now_ms() if now_ms is None else now_ms
        with self._lock:
            state = self._load()
            session_id = state["refresh_tokens"].get(refresh_token)
            if not isinstance(session_id, str):
                raise SyncStoreError("invalid_refresh_token", "Refresh token is invalid")
            session = state["sessions"].get(session_id)
            if not isinstance(session, dict):
                state["refresh_tokens"].pop(refresh_token, None)
                raise SyncStoreError("invalid_refresh_token", "Refresh token is invalid")
            device_id = session.get("device_id")
            user_id = session.get("user_id")
            if not isinstance(device_id, str) or not isinstance(user_id, str):
                raise SyncStoreError("invalid_refresh_token", "Refresh token is invalid")
            device = state["devices"].get(device_id)
            if (
                not isinstance(device, dict)
                or device.get("revoked")
                or session.get("revoked_at_ms") is not None
            ):
                raise SyncStoreError("device_revoked", "Device token is invalid or revoked")
            refresh_expires_at_ms = session.get("refresh_expires_at_ms")
            if not isinstance(refresh_expires_at_ms, int) or refresh_expires_at_ms <= resolved_now_ms:
                state["refresh_tokens"].pop(refresh_token, None)
                raise SyncStoreError("refresh_token_expired", "Refresh token has expired")

            old_access_token = session.get("access_token")
            if isinstance(old_access_token, str):
                state["tokens"].pop(old_access_token, None)
            state["refresh_tokens"].pop(refresh_token, None)

            access_token = secrets.token_urlsafe(32)
            next_refresh_token = secrets.token_urlsafe(32)
            access_expires_at_ms = resolved_now_ms + access_token_ttl_ms
            next_refresh_expires_at_ms = resolved_now_ms + refresh_token_ttl_ms
            session.update(
                {
                    "access_token": access_token,
                    "refresh_token": next_refresh_token,
                    "access_expires_at_ms": access_expires_at_ms,
                    "refresh_expires_at_ms": next_refresh_expires_at_ms,
                    "updated_at_ms": resolved_now_ms,
                }
            )
            device["access_token"] = access_token
            device["refresh_token"] = next_refresh_token
            device["access_token_expires_at_ms"] = access_expires_at_ms
            device["refresh_token_expires_at_ms"] = next_refresh_expires_at_ms
            device["last_seen_at_ms"] = resolved_now_ms
            state["tokens"][access_token] = session_id
            state["refresh_tokens"][next_refresh_token] = session_id
            self._save(state)
        return self._session_token_response(
            user_id=user_id,
            device_id=device_id,
            access_token=access_token,
            refresh_token=next_refresh_token,
            access_expires_at_ms=access_expires_at_ms,
            refresh_expires_at_ms=next_refresh_expires_at_ms,
        )

    def revoke_session_for_token(self, token: str, *, now_ms: Optional[int] = None) -> bool:
        resolved_now_ms = _now_ms() if now_ms is None else now_ms
        with self._lock:
            state = self._load()
            session_id = state["tokens"].pop(token, None)
            if not isinstance(session_id, str):
                return False
            session = state["sessions"].get(session_id)
            if not isinstance(session, dict):
                return False
            session["revoked_at_ms"] = resolved_now_ms
            refresh_token = session.get("refresh_token")
            if isinstance(refresh_token, str):
                state["refresh_tokens"].pop(refresh_token, None)
            self._save(state)
            return True

    def delete_device(self, device_id: str, *, actor_device_id: Optional[str] = None) -> bool:
        with self._lock:
            state = self._load()
            device = state["devices"].get(device_id)
            if not isinstance(device, dict):
                return False
            if actor_device_id is not None:
                actor_device = state["devices"].get(actor_device_id)
                if not isinstance(actor_device, dict) or actor_device.get("user_id") != device.get("user_id"):
                    raise SyncStoreError("device_forbidden", "Device does not belong to the current user")
            device["revoked"] = True
            revoked_at_ms = _now_ms()
            device["revoked_at_ms"] = revoked_at_ms
            self._revoke_device_sessions_locked(state, device_id, revoked_at_ms=revoked_at_ms)
            legacy_refresh_token = device.get("refresh_token")
            if isinstance(legacy_refresh_token, str):
                state["refresh_tokens"].pop(legacy_refresh_token, None)
            state["capabilities"] = {
                token: capability
                for token, capability in state["capabilities"].items()
                if not isinstance(capability, dict) or capability.get("device_id") != device_id
            }
            retained_sessions = {}
            for session_id, session in state["resumable_upload_sessions"].items():
                if isinstance(session, dict) and session.get("device_id") == device_id:
                    self._delete_session_chunk_files(session)
                    continue
                retained_sessions[session_id] = session
            state["resumable_upload_sessions"] = retained_sessions
            resolved_now_ms = _now_ms()
            for vault_id, vault in state["vaults"].items():
                if isinstance(vault_id, str) and isinstance(vault, dict):
                    self._run_tombstone_gc_locked(
                        state,
                        vault_id,
                        vault,
                        now_ms=resolved_now_ms,
                        min_retention_ms=DEFAULT_TOMBSTONE_GC_MIN_RETENTION_MS,
                        inactive_after_ms=DEFAULT_DEVICE_INACTIVE_AFTER_MS,
                        actor_device_id=None,
                        record_noop=False,
                    )
            self._save(state)
            return True

    def list_vault_devices(
        self,
        vault_id: str,
        *,
        current_device_id: Optional[str],
        now_ms: Optional[int] = None,
        inactive_after_ms: int = DEFAULT_DEVICE_INACTIVE_AFTER_MS,
    ) -> dict[str, Any]:
        if inactive_after_ms <= 0:
            raise SyncStoreError("invalid_request", "inactive_after_ms must be positive")
        resolved_now_ms = _now_ms() if now_ms is None else now_ms
        with self._lock:
            state = self._load()
            vault = self._ensure_vault(state, vault_id)
            devices: list[dict[str, Any]] = []
            for device_id in self._joined_vault_device_ids(vault):
                device = state["devices"].get(device_id)
                if not isinstance(device, dict):
                    device = {}
                registered_at = device.get("registered_at_ms")
                if not isinstance(registered_at, int):
                    registered_at = None
                last_seen_at = device.get("last_seen_at_ms")
                if not isinstance(last_seen_at, int):
                    last_seen_at = registered_at
                acked_revision = vault["acks"].get(device_id, 0)
                if not isinstance(acked_revision, int):
                    acked_revision = 0
                revoked = bool(device.get("revoked"))
                inactive = (
                    not revoked
                    and isinstance(last_seen_at, int)
                    and resolved_now_ms - last_seen_at >= inactive_after_ms
                )
                devices.append(
                    {
                        "device_id": device_id,
                        "device_name": device.get("device_name") if isinstance(device.get("device_name"), str) else device_id,
                        "platform": device.get("platform") if isinstance(device.get("platform"), str) else "unknown",
                        "app_version": device.get("app_version") if isinstance(device.get("app_version"), str) else None,
                        "protocol_version": device.get("protocol_version") if isinstance(device.get("protocol_version"), str) else "v1",
                        "registered_at_ms": registered_at,
                        "last_seen_at_ms": last_seen_at,
                        "acked_revision": acked_revision,
                        "is_current_device": device_id == current_device_id,
                        "is_revoked": revoked,
                        "is_inactive_candidate": inactive,
                    }
                )
            devices.sort(
                key=lambda item: (
                    not item["is_current_device"],
                    item["is_revoked"],
                    -(item["last_seen_at_ms"] or 0),
                    item["device_id"],
                )
            )
            self._save(state)
            return {
                "vault_id": vault_id,
                "head_revision": vault["head_revision"],
                "inactive_after_ms": inactive_after_ms,
                "devices": devices,
            }

    def heartbeat_device(
        self,
        vault_id: str,
        *,
        device_id: str,
        now_ms: Optional[int] = None,
    ) -> dict[str, Any]:
        resolved_now_ms = _now_ms() if now_ms is None else now_ms
        with self._lock:
            state = self._load()
            device = state["devices"].get(device_id)
            if not isinstance(device, dict) or device.get("revoked"):
                raise SyncStoreError("device_not_found", "Device was not found")
            vault = self._ensure_vault(state, vault_id)
            device["last_seen_at_ms"] = resolved_now_ms
            acked_revision = vault["acks"].get(device_id, 0)
            if not isinstance(acked_revision, int):
                acked_revision = 0
            self._save(state)
            return {
                "vault_id": vault_id,
                "device_id": device_id,
                "last_seen_at_ms": resolved_now_ms,
                "acked_revision": acked_revision,
                "head_revision": vault["head_revision"],
            }

    def run_tombstone_gc(
        self,
        vault_id: str,
        *,
        now_ms: Optional[int] = None,
        min_retention_ms: int = DEFAULT_TOMBSTONE_GC_MIN_RETENTION_MS,
        inactive_after_ms: int = DEFAULT_DEVICE_INACTIVE_AFTER_MS,
        actor_device_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if min_retention_ms < 0:
            raise SyncStoreError("invalid_request", "min_retention_ms must be non-negative")
        if inactive_after_ms <= 0:
            raise SyncStoreError("invalid_request", "inactive_after_ms must be positive")
        resolved_now_ms = _now_ms() if now_ms is None else now_ms
        with self._lock:
            state = self._load()
            vault = self._ensure_vault(state, vault_id)
            result = self._run_tombstone_gc_locked(
                state,
                vault_id,
                vault,
                now_ms=resolved_now_ms,
                min_retention_ms=min_retention_ms,
                inactive_after_ms=inactive_after_ms,
                actor_device_id=actor_device_id,
                record_noop=True,
            )
            self._save(state)
            return result

    def _append_tombstone_gc_log(self, vault: dict[str, Any], log_record: dict[str, Any]) -> None:
        tombstone_gc = self._ensure_tombstone_gc(vault)
        runs = tombstone_gc.setdefault("runs", [])
        if not isinstance(runs, list):
            runs = []
            tombstone_gc["runs"] = runs
        runs.append(deepcopy(log_record))
        if len(runs) > MAX_TOMBSTONE_GC_LOGS:
            del runs[: len(runs) - MAX_TOMBSTONE_GC_LOGS]

    def _tombstone_gc_item(self, tombstone: dict[str, Any]) -> dict[str, Any]:
        return {
            "file_id": tombstone.get("file_id"),
            "deleted_revision": tombstone.get("deleted_revision"),
            "deleted_at": tombstone.get("deleted_at"),
            "last_known_path": tombstone.get("last_known_path"),
            "deleted_by_device": tombstone.get("deleted_by_device"),
        }

    def _run_tombstone_gc_locked(
        self,
        state: dict[str, Any],
        vault_id: str,
        vault: dict[str, Any],
        *,
        now_ms: int,
        min_retention_ms: int,
        inactive_after_ms: int,
        actor_device_id: Optional[str],
        record_noop: bool,
    ) -> dict[str, Any]:
        base_revision = vault["head_revision"]
        active_device_ids = self._active_vault_device_ids(state, vault)
        run_id = f"tgc_{uuid.uuid4().hex}"
        head_manifest = vault["manifests"].get(str(base_revision))
        if not isinstance(head_manifest, dict):
            result = {
                "vault_id": vault_id,
                "run_id": run_id,
                "ran_at_ms": now_ms,
                "base_revision": base_revision,
                "new_revision": None,
                "head_revision": base_revision,
                "active_device_ids": active_device_ids,
                "active_device_count": len(active_device_ids),
                "min_retention_ms": min_retention_ms,
                "inactive_after_ms": inactive_after_ms,
                "reclaimed_tombstones": [],
                "blocked_tombstones": [],
                "reclaimed_count": 0,
                "reason": "no_head_manifest",
            }
            if record_noop:
                self._append_tombstone_gc_log(vault, result)
            return result

        tombstones_payload = head_manifest.get("tombstones", [])
        if not isinstance(tombstones_payload, list):
            tombstones_payload = []

        retained_tombstones: list[dict[str, Any]] = []
        reclaimed_tombstones: list[dict[str, Any]] = []
        blocked_tombstones: list[dict[str, Any]] = []
        acks = vault.setdefault("acks", {})
        if not isinstance(acks, dict):
            acks = {}
            vault["acks"] = acks

        for tombstone in tombstones_payload:
            if not isinstance(tombstone, dict):
                retained_tombstones.append(tombstone)
                continue
            deleted_revision = tombstone.get("deleted_revision")
            if not isinstance(deleted_revision, int) or deleted_revision <= 0:
                retained_tombstones.append(tombstone)
                blocked_tombstones.append(
                    {
                        **self._tombstone_gc_item(tombstone),
                        "reason": "pending_deleted_revision",
                        "retention_remaining_ms": 0,
                        "blocked_by_devices": [],
                    }
                )
                continue

            deleted_at = tombstone.get("deleted_at")
            retention_remaining_ms = 0
            if isinstance(deleted_at, int):
                retention_remaining_ms = max(0, min_retention_ms - max(0, now_ms - deleted_at))

            blocked_by_devices: list[dict[str, Any]] = []
            for device_id in active_device_ids:
                acked_revision = acks.get(device_id, 0)
                if not isinstance(acked_revision, int):
                    acked_revision = 0
                if acked_revision < deleted_revision:
                    blocked_by_devices.append(
                        {
                            "device_id": device_id,
                            "acked_revision": acked_revision,
                            "required_revision": deleted_revision,
                        }
                    )

            if blocked_by_devices or retention_remaining_ms > 0:
                retained_tombstones.append(tombstone)
                if blocked_by_devices and retention_remaining_ms > 0:
                    reason = "waiting_for_ack_and_retention"
                elif blocked_by_devices:
                    reason = "waiting_for_ack"
                else:
                    reason = "retention_window"
                blocked_tombstones.append(
                    {
                        **self._tombstone_gc_item(tombstone),
                        "reason": reason,
                        "retention_remaining_ms": retention_remaining_ms,
                        "blocked_by_devices": blocked_by_devices,
                    }
                )
                continue

            reclaimed_tombstones.append(self._tombstone_gc_item(tombstone))

        new_revision: Optional[int] = None
        if reclaimed_tombstones:
            new_revision = base_revision + 1
            gc_manifest = deepcopy(head_manifest)
            gc_manifest["base_revision"] = base_revision
            gc_manifest["created_by_device"] = actor_device_id or "server:tombstone-gc"
            gc_manifest["created_at"] = now_ms
            gc_manifest["tombstones"] = retained_tombstones
            finalized_manifest = finalize_manifest_payload(gc_manifest, new_revision)
            vault["manifests"][str(new_revision)] = finalized_manifest
            vault["head_revision"] = new_revision
            vault["manifest_summary"] = finalized_manifest["summary_hash"]
            commit_intent_id = f"tombstone-gc:{run_id}"
            vault["commits"][commit_intent_id] = {
                "commit_intent_id": commit_intent_id,
                "intent_manifest_hash": finalized_manifest["summary_hash"],
                "revision": new_revision,
                "created_by_device": finalized_manifest["created_by_device"],
                "committed_at_ms": now_ms,
                "kind": "tombstone_gc",
                "reclaimed_file_ids": [
                    item["file_id"]
                    for item in reclaimed_tombstones
                    if isinstance(item.get("file_id"), str)
                ],
            }

        result = {
            "vault_id": vault_id,
            "run_id": run_id,
            "ran_at_ms": now_ms,
            "base_revision": base_revision,
            "new_revision": new_revision,
            "head_revision": vault["head_revision"],
            "active_device_ids": active_device_ids,
            "active_device_count": len(active_device_ids),
            "min_retention_ms": min_retention_ms,
            "inactive_after_ms": inactive_after_ms,
            "reclaimed_tombstones": reclaimed_tombstones,
            "blocked_tombstones": blocked_tombstones,
            "reclaimed_count": len(reclaimed_tombstones),
            "reason": "reclaimed" if reclaimed_tombstones else "no_reclaimable_tombstones",
        }
        if record_noop or reclaimed_tombstones:
            self._append_tombstone_gc_log(vault, result)
        return result

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
                encrypted_sha256 = item.get("encrypted_sha256")
                if encrypted_sha256 is not None and (not isinstance(encrypted_sha256, str) or not encrypted_sha256):
                    raise SyncStoreError("invalid_request", "encrypted_sha256 must be a non-empty string when provided")
                capability_token = secrets.token_urlsafe(32)
                capability = {
                    "kind": "upload",
                    "vault_id": vault_id,
                    "blob_id": blob_id,
                    "encrypted_size": encrypted_size,
                    "content_hash": content_hash,
                    "object_key": _blob_object_key(vault_id, blob_id),
                    "device_id": device_id,
                    "expires_at_ms": _now_ms() + 15 * 60 * 1000,
                }
                if isinstance(encrypted_sha256, str):
                    capability["encrypted_sha256"] = encrypted_sha256
                state["capabilities"][capability_token] = capability
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
            encrypted_sha256 = _sha256_payload(payload)
            expected_sha256 = capability.get("encrypted_sha256")
            if isinstance(expected_sha256, str) and encrypted_sha256 != expected_sha256:
                state["capabilities"][capability_token] = capability
                self._save(state)
                raise BlobCapabilityError(400, "blob_hash_mismatch", "Uploaded blob encrypted_sha256 does not match capability.")
            object_key = capability.get("object_key")
            if not isinstance(object_key, str):
                object_key = _blob_object_key(capability["vault_id"], blob_id)
            try:
                self.blob_store.put_object(object_key, payload)
            except BlobStorageError as error:
                state["capabilities"][capability_token] = capability
                self._save(state)
                raise _capability_error_from_blob_storage(error) from error
            state["blobs"][blob_id] = {
                "blob_id": blob_id,
                "vault_id": capability.get("vault_id"),
                "object_key": object_key,
                "storage_backend": self.blob_store.backend_name,
                "status": "available",
                "encrypted_size": len(payload),
                "content_hash": capability.get("content_hash"),
                "encrypted_sha256": encrypted_sha256,
                "uploaded_at_ms": _now_ms(),
            }
            self._save(state)

    def init_resumable_blob_upload(
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

        seen_blob_ids: set[str] = set()
        requests: list[dict[str, Any]] = []
        for item in blobs:
            if not isinstance(item, dict):
                raise SyncStoreError("invalid_request", "blobs items must be objects")
            blob_id = _require_string(item, "blob_id")
            if blob_id in seen_blob_ids:
                raise SyncStoreError("invalid_request", "blob_id values must be unique")
            seen_blob_ids.add(blob_id)
            encrypted_size = _require_non_negative_int(item, "encrypted_size")
            content_hash = _require_string(item, "content_hash")
            encrypted_sha256 = _require_string(item, "encrypted_sha256")
            chunk_size = _require_positive_int(item, "chunk_size")
            chunks = self._require_blob_chunks(item, blob_id=blob_id, encrypted_size=encrypted_size, chunk_size=chunk_size)
            requests.append(
                {
                    "blob_id": blob_id,
                    "encrypted_size": encrypted_size,
                    "content_hash": content_hash,
                    "encrypted_sha256": encrypted_sha256,
                    "chunk_size": chunk_size,
                    "chunks": chunks,
                }
            )

        uploads: list[dict[str, Any]] = []
        with self._lock:
            state = self._load()
            self._purge_expired_resumable_upload_sessions(state)
            for request in requests:
                blob = state["blobs"].get(request["blob_id"])
                if isinstance(blob, dict):
                    self._validate_existing_blob_metadata(
                        blob,
                        encrypted_size=request["encrypted_size"],
                        content_hash=request["content_hash"],
                        encrypted_sha256=request["encrypted_sha256"],
                    )
                session_id = self._find_resumable_upload_session(
                    state,
                    vault_id=vault_id,
                    request=request,
                    device_id=device_id,
                )
                if session_id is None:
                    session_id = f"upload_{uuid.uuid4().hex}"
                    uploaded_chunk_ids = [chunk["chunk_id"] for chunk in request["chunks"]] if isinstance(blob, dict) else []
                    state["resumable_upload_sessions"][session_id] = {
                        "vault_id": vault_id,
                        "blob_id": request["blob_id"],
                        "encrypted_size": request["encrypted_size"],
                        "content_hash": request["content_hash"],
                        "encrypted_sha256": request["encrypted_sha256"],
                        "chunk_size": request["chunk_size"],
                        "chunks": request["chunks"],
                        "uploaded_chunk_ids": uploaded_chunk_ids,
                        "chunk_files": {},
                        "device_id": device_id,
                        "expires_at_ms": _now_ms() + 15 * 60 * 1000,
                        "created_at_ms": _now_ms(),
                        "updated_at_ms": _now_ms(),
                    }
                uploads.append(self._resumable_upload_capability(state["resumable_upload_sessions"][session_id], session_id, request_base_url))
            self._save(state)
        return {"uploads": uploads}

    def put_resumable_blob_chunk(
        self,
        session_id: str,
        *,
        chunk_id: str,
        offset: int,
        size: int,
        payload: bytes,
    ) -> None:
        if not session_id:
            raise BlobCapabilityError(404, "resumable_upload_session_not_found", "Resumable upload session was not found.")
        if not chunk_id:
            raise BlobCapabilityError(400, "invalid_chunk", "chunk_id must be a non-empty string.")
        if offset < 0 or size < 0:
            raise BlobCapabilityError(400, "invalid_chunk", "chunk offset and size must be non-negative.")
        if len(payload) != size:
            raise BlobCapabilityError(400, "chunk_size_mismatch", "Uploaded chunk size does not match chunk header.")

        with self._lock:
            state = self._load()
            session = self._require_resumable_upload_session(state, session_id)
            self._assert_resumable_upload_session_active(session)
            chunk = self._session_chunk_by_id(session, chunk_id)
            if chunk is None:
                raise BlobCapabilityError(400, "chunk_not_found", "Chunk does not belong to this upload session.")
            if chunk["offset"] != offset or chunk["size"] != size:
                raise BlobCapabilityError(400, "chunk_range_mismatch", "Uploaded chunk range does not match session chunk metadata.")

            chunk_filename = _chunk_filename(session_id, chunk_id)
            chunk_path = self.chunk_dir / chunk_filename
            chunk_path.write_bytes(payload)
            chunk_files = session.setdefault("chunk_files", {})
            if not isinstance(chunk_files, dict):
                chunk_files = {}
                session["chunk_files"] = chunk_files
            chunk_files[chunk_id] = chunk_filename
            uploaded_chunk_ids = self._session_uploaded_chunk_ids(session)
            if chunk_id not in uploaded_chunk_ids:
                uploaded_chunk_ids.append(chunk_id)
            session["uploaded_chunk_ids"] = uploaded_chunk_ids
            session["updated_at_ms"] = _now_ms()
            self._save(state)

    def complete_resumable_blob_upload(self, vault_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_vault_id(vault_id)
        uploads_payload = payload.get("uploads")
        if not isinstance(uploads_payload, list) or not uploads_payload:
            raise SyncStoreError("invalid_request", "uploads must be a non-empty list")

        requests: list[dict[str, Any]] = []
        seen_sessions: set[str] = set()
        for item in uploads_payload:
            if not isinstance(item, dict):
                raise SyncStoreError("invalid_request", "uploads items must be objects")
            blob_id = _require_string(item, "blob_id")
            session_id = _require_string(item, "session_id")
            if session_id in seen_sessions:
                raise SyncStoreError("invalid_request", "session_id values must be unique")
            seen_sessions.add(session_id)
            uploaded_chunk_ids = _require_unique_strings(item, "uploaded_chunk_ids")
            requests.append(
                {
                    "blob_id": blob_id,
                    "session_id": session_id,
                    "encrypted_size": _require_non_negative_int(item, "encrypted_size"),
                    "encrypted_sha256": _require_string(item, "encrypted_sha256"),
                    "uploaded_chunk_ids": uploaded_chunk_ids,
                }
            )

        results: list[dict[str, Any]] = []
        with self._lock:
            state = self._load()
            self._purge_expired_resumable_upload_sessions(state)
            for request in requests:
                existing_blob = state["blobs"].get(request["blob_id"])
                if isinstance(existing_blob, dict):
                    self._validate_existing_blob_metadata(
                        existing_blob,
                        encrypted_size=request["encrypted_size"],
                        content_hash=None,
                        encrypted_sha256=request["encrypted_sha256"],
                    )
                    results.append(
                        {
                            "blob_id": request["blob_id"],
                            "status": "already_exists",
                            "missing_chunks": [],
                        }
                    )
                    continue

                session = state["resumable_upload_sessions"].get(request["session_id"])
                if not isinstance(session, dict):
                    raise SyncStoreError("resumable_upload_session_not_found", "Resumable upload session was not found.")
                self._validate_resumable_complete_request(vault_id, session, request)
                missing_chunks = self._missing_session_chunks(session)
                if missing_chunks:
                    results.append(
                        {
                            "blob_id": request["blob_id"],
                            "status": "incomplete",
                            "missing_chunks": [
                                {
                                    "chunk_id": chunk["chunk_id"],
                                    "offset": chunk["offset"],
                                    "size": chunk["size"],
                                }
                                for chunk in missing_chunks
                            ],
                        }
                    )
                    continue

                self._finalize_resumable_upload_blob(state, request["session_id"], session)
                results.append(
                    {
                        "blob_id": request["blob_id"],
                        "status": "accepted",
                        "missing_chunks": [],
                    }
                )
            self._save(state)
        return {"uploads": results}

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

    def init_resumable_blob_download(
        self,
        vault_id: str,
        payload: dict[str, Any],
        *,
        request_base_url: str,
        device_id: Optional[str],
    ) -> dict[str, Any]:
        self._validate_vault_id(vault_id)
        blobs_payload = payload.get("blobs")
        if not isinstance(blobs_payload, list) or not blobs_payload:
            raise SyncStoreError("invalid_request", "blobs must be a non-empty list")

        seen_blob_ids: set[str] = set()
        requested_blobs: list[dict[str, Any]] = []
        for item in blobs_payload:
            if not isinstance(item, dict):
                raise SyncStoreError("invalid_request", "blobs items must be objects")
            blob_id = _require_string(item, "blob_id")
            if blob_id in seen_blob_ids:
                raise SyncStoreError("invalid_request", "blob_id values must be unique")
            seen_blob_ids.add(blob_id)
            requested_blobs.append(
                {
                    "blob_id": blob_id,
                    "ranges": item.get("ranges"),
                }
            )

        downloads: list[dict[str, Any]] = []
        with self._lock:
            state = self._load()
            for requested_blob in requested_blobs:
                blob_id = requested_blob["blob_id"]
                blob = state["blobs"].get(blob_id)
                if not isinstance(blob, dict):
                    raise SyncStoreError("blob_not_found", f"Blob was not found: {blob_id}")
                encrypted_size = _require_non_negative_int(blob, "encrypted_size")
                ranges = self._resolve_download_ranges(requested_blob["ranges"], encrypted_size=encrypted_size)
                capability_token = secrets.token_urlsafe(32)
                state["capabilities"][capability_token] = {
                    "kind": "resumable_download",
                    "vault_id": vault_id,
                    "blob_id": blob_id,
                    "ranges": ranges,
                    "device_id": device_id,
                    "expires_at_ms": _now_ms() + 15 * 60 * 1000,
                }
                downloads.append(
                    {
                        "blob_id": blob_id,
                        "download_url": self._resumable_download_url(request_base_url, capability_token),
                        "encrypted_size": encrypted_size,
                        "ranges": ranges,
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
            try:
                payload = self.blob_store.read_object(self._object_key_for_blob(blob))
            except BlobStorageError as error:
                state["capabilities"][capability_token] = capability
                self._save(state)
                raise _capability_error_from_blob_storage(error) from error
            self._save(state)
            return payload

    def read_resumable_blob_download(self, capability_token: str, *, offset: int, size: int) -> bytes:
        if offset < 0 or size <= 0:
            raise BlobCapabilityError(400, "invalid_range", "Download range offset must be non-negative and size must be positive.")
        with self._lock:
            state = self._load()
            capability = self._get_capability(state, capability_token, "resumable_download")
            if not self._range_is_authorized(capability.get("ranges"), offset=offset, size=size):
                raise BlobCapabilityError(403, "range_not_authorized", "Requested range is not authorized by capability.")
            blob = state["blobs"].get(capability["blob_id"])
            if not isinstance(blob, dict):
                raise BlobCapabilityError(404, "blob_not_found", "Blob was not found.")
            try:
                payload = self.blob_store.read_range(self._object_key_for_blob(blob), offset=offset, size=size)
            except BlobStorageError as error:
                raise _capability_error_from_blob_storage(error) from error
            if len(payload) != size:
                raise BlobCapabilityError(416, "range_not_satisfiable", "Blob payload does not contain requested range.")
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
            self._append_file_versions_for_commit(vault, payload, finalized_manifest)
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

    def list_file_versions(self, vault_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        file_id = _require_string(payload, "file_id")
        limit = payload.get("limit", 50)
        if not isinstance(limit, int) or limit <= 0:
            raise SyncStoreError("invalid_request", "limit must be a positive integer")
        cursor = payload.get("cursor")
        if cursor is not None and (not isinstance(cursor, str) or not cursor):
            raise SyncStoreError("invalid_request", "cursor must be a non-empty string when provided")
        include_pinned = payload.get("include_pinned", True)
        if not isinstance(include_pinned, bool):
            raise SyncStoreError("invalid_request", "include_pinned must be a boolean")
        offset = 0
        if cursor is not None:
            if not cursor.startswith("offset:"):
                raise SyncStoreError("invalid_request", "cursor is invalid")
            try:
                offset = int(cursor.split(":", 1)[1])
            except ValueError as error:
                raise SyncStoreError("invalid_request", "cursor is invalid") from error
            if offset < 0:
                raise SyncStoreError("invalid_request", "cursor is invalid")

        with self._lock:
            state = self._load()
            vault = self._ensure_vault(state, vault_id)
            file_versions = self._ensure_file_versions(vault)
            version_ids = file_versions["by_file_id"].get(file_id, [])
            if not isinstance(version_ids, list):
                version_ids = []
            records = [
                deepcopy(file_versions["records"][version_id])
                for version_id in version_ids
                if isinstance(version_id, str) and isinstance(file_versions["records"].get(version_id), dict)
            ]
            if not include_pinned:
                records = [record for record in records if not record.get("is_pinned", False)]
            records.sort(key=lambda item: (-item["revision"], item["version_id"]))
            page = records[offset : offset + limit]
            next_offset = offset + len(page)
            next_cursor = f"offset:{next_offset}" if next_offset < len(records) else None
            return {
                "file_id": file_id,
                "versions": page,
                "next_cursor": next_cursor,
                "retention_policy": deepcopy(file_versions["retention_policy"]),
            }

    def update_file_version(self, vault_id: str, version_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not version_id:
            raise SyncStoreError("invalid_request", "version_id must be non-empty")
        allowed_keys = {"version_label", "change_note", "is_pinned"}
        if not payload:
            raise SyncStoreError("invalid_request", "file version update payload must not be empty")
        unexpected_keys = sorted(set(payload) - allowed_keys)
        if unexpected_keys:
            raise SyncStoreError("invalid_request", "unsupported file version update fields: " + ", ".join(unexpected_keys))
        version_label = payload.get("version_label")
        if version_label is not None and (not isinstance(version_label, str) or not version_label):
            raise SyncStoreError("invalid_request", "version_label must be a non-empty string when provided")
        change_note = payload.get("change_note")
        if change_note is not None and (not isinstance(change_note, str) or not change_note):
            raise SyncStoreError("invalid_request", "change_note must be a non-empty string when provided")
        is_pinned = payload.get("is_pinned")
        if is_pinned is not None and not isinstance(is_pinned, bool):
            raise SyncStoreError("invalid_request", "is_pinned must be a boolean when provided")

        with self._lock:
            state = self._load()
            vault = self._ensure_vault(state, vault_id)
            file_versions = self._ensure_file_versions(vault)
            record = file_versions["records"].get(version_id)
            if not isinstance(record, dict):
                raise SyncStoreError("file_version_not_found", "File version was not found")
            if "version_label" in payload:
                record["version_label"] = version_label
            if "change_note" in payload:
                record["change_note"] = change_note
            if "is_pinned" in payload:
                record["is_pinned"] = is_pinned
            self._save(state)
            return {"version": deepcopy(record)}

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
            self._run_tombstone_gc_locked(
                state,
                vault_id,
                vault,
                now_ms=_now_ms(),
                min_retention_ms=DEFAULT_TOMBSTONE_GC_MIN_RETENTION_MS,
                inactive_after_ms=DEFAULT_DEVICE_INACTIVE_AFTER_MS,
                actor_device_id=actor,
                record_noop=False,
            )
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

    def _get_capability(self, state: dict[str, Any], capability_token: str, expected_kind: str) -> dict[str, Any]:
        capability = state["capabilities"].get(capability_token)
        if not isinstance(capability, dict):
            raise BlobCapabilityError(404, "capability_not_found", "Blob capability was not found.")
        if capability.get("kind") != expected_kind:
            raise BlobCapabilityError(403, "capability_wrong_kind", "Blob capability cannot be used for this operation.")
        if capability.get("expires_at_ms", 0) < _now_ms():
            state["capabilities"].pop(capability_token, None)
            raise BlobCapabilityError(403, "capability_expired", "Blob capability has expired.")
        return capability

    def _capability_url(self, request_base_url: str, capability_token: str) -> str:
        return f"{request_base_url.rstrip('/')}/_capabilities/blobs/{capability_token}"

    def _resumable_upload_url(self, request_base_url: str, session_id: str) -> str:
        return f"{request_base_url.rstrip('/')}/_capabilities/blobs/resumable-upload/{session_id}"

    def _resumable_download_url(self, request_base_url: str, capability_token: str) -> str:
        return f"{request_base_url.rstrip('/')}/_capabilities/blobs/resumable-download/{capability_token}"

    def _resolve_download_ranges(self, ranges_payload: Any, *, encrypted_size: int) -> list[dict[str, int]]:
        if encrypted_size == 0:
            return []
        if ranges_payload is None:
            return [{"offset": 0, "size": encrypted_size}]
        if not isinstance(ranges_payload, list) or not ranges_payload:
            raise SyncStoreError("invalid_range", "ranges must be a non-empty list when provided")
        ranges: list[dict[str, int]] = []
        for item in ranges_payload:
            if not isinstance(item, dict):
                raise SyncStoreError("invalid_range", "ranges items must be objects")
            offset = _require_non_negative_int(item, "offset")
            size = _require_positive_int(item, "size")
            if offset + size > encrypted_size:
                raise SyncStoreError("invalid_range", "download range exceeds blob encrypted_size")
            ranges.append({"offset": offset, "size": size})
        return ranges

    def _range_is_authorized(self, ranges_payload: Any, *, offset: int, size: int) -> bool:
        if not isinstance(ranges_payload, list):
            return False
        for item in ranges_payload:
            if not isinstance(item, dict):
                continue
            allowed_offset = item.get("offset")
            allowed_size = item.get("size")
            if not isinstance(allowed_offset, int) or not isinstance(allowed_size, int):
                continue
            if offset >= allowed_offset and offset + size <= allowed_offset + allowed_size:
                return True
        return False

    def _require_blob_chunks(
        self,
        item: dict[str, Any],
        *,
        blob_id: str,
        encrypted_size: int,
        chunk_size: int,
    ) -> list[dict[str, int | str]]:
        chunks_payload = item.get("chunks")
        expected_chunks = _blob_chunks(blob_id, encrypted_size, chunk_size)
        if chunks_payload is None:
            return expected_chunks
        if not isinstance(chunks_payload, list) or not chunks_payload:
            raise SyncStoreError("invalid_request", "chunks must be a non-empty list")

        chunks: list[dict[str, int | str]] = []
        for chunk in chunks_payload:
            if not isinstance(chunk, dict):
                raise SyncStoreError("invalid_request", "chunks items must be objects")
            chunks.append(
                {
                    "chunk_id": _require_string(chunk, "chunk_id"),
                    "offset": _require_non_negative_int(chunk, "offset"),
                    "size": _require_non_negative_int(chunk, "size"),
                }
            )
        if chunks != expected_chunks:
            raise SyncStoreError("invalid_request", "chunks must match blob_id, encrypted_size, and chunk_size")
        return chunks

    def _validate_existing_blob_metadata(
        self,
        blob: dict[str, Any],
        *,
        encrypted_size: int,
        content_hash: Optional[str],
        encrypted_sha256: str,
    ) -> None:
        if blob.get("encrypted_size") != encrypted_size:
            raise SyncStoreError("blob_metadata_mismatch", "Existing blob encrypted_size does not match request")
        if content_hash is not None and blob.get("content_hash") != content_hash:
            raise SyncStoreError("blob_metadata_mismatch", "Existing blob content_hash does not match request")
        existing_sha256 = blob.get("encrypted_sha256")
        if existing_sha256 is None:
            try:
                existing_sha256 = _sha256_payload(self.blob_store.read_object(self._object_key_for_blob(blob)))
            except BlobStorageError as error:
                raise _sync_error_from_blob_storage(error) from error
            blob["encrypted_sha256"] = existing_sha256
        if existing_sha256 is not None and existing_sha256 != encrypted_sha256:
            raise SyncStoreError("blob_metadata_mismatch", "Existing blob encrypted_sha256 does not match request")

    def _object_key_for_blob(self, blob: dict[str, Any]) -> str:
        object_key = blob.get("object_key")
        if isinstance(object_key, str) and object_key:
            return object_key
        filename = blob.get("filename")
        if isinstance(filename, str) and filename:
            return filename
        raise BlobStorageError(404, "object_not_found", "Blob object key was not found in metadata.")

    def _find_resumable_upload_session(
        self,
        state: dict[str, Any],
        *,
        vault_id: str,
        request: dict[str, Any],
        device_id: Optional[str],
    ) -> Optional[str]:
        for session_id, session in state["resumable_upload_sessions"].items():
            if not isinstance(session, dict):
                continue
            if session.get("expires_at_ms", 0) < _now_ms():
                continue
            if (
                session.get("vault_id") == vault_id
                and session.get("blob_id") == request["blob_id"]
                and session.get("encrypted_size") == request["encrypted_size"]
                and session.get("content_hash") == request["content_hash"]
                and session.get("encrypted_sha256") == request["encrypted_sha256"]
                and session.get("chunk_size") == request["chunk_size"]
                and session.get("device_id") == device_id
            ):
                session["updated_at_ms"] = _now_ms()
                return session_id
        return None

    def _resumable_upload_capability(
        self,
        session: dict[str, Any],
        session_id: str,
        request_base_url: str,
    ) -> dict[str, Any]:
        uploaded_chunk_ids = set(self._session_uploaded_chunk_ids(session))
        uploaded_chunks: list[dict[str, Any]] = []
        missing_chunks: list[dict[str, Any]] = []
        for chunk in session["chunks"]:
            target = uploaded_chunks if chunk["chunk_id"] in uploaded_chunk_ids else missing_chunks
            target.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "offset": chunk["offset"],
                    "size": chunk["size"],
                    "status": "uploaded" if target is uploaded_chunks else "missing",
                }
            )
        return {
            "blob_id": session["blob_id"],
            "session_id": session_id,
            "upload_url": self._resumable_upload_url(request_base_url, session_id),
            "method": "PUT",
            "headers": {},
            "expires_at": _expires_at(),
            "chunk_size": session["chunk_size"],
            "encrypted_size": session["encrypted_size"],
            "uploaded_chunks": uploaded_chunks,
            "missing_chunks": missing_chunks,
        }

    def _purge_expired_resumable_upload_sessions(self, state: dict[str, Any]) -> None:
        expired_session_ids = [
            session_id
            for session_id, session in state["resumable_upload_sessions"].items()
            if not isinstance(session, dict) or session.get("expires_at_ms", 0) < _now_ms()
        ]
        for session_id in expired_session_ids:
            session = state["resumable_upload_sessions"].pop(session_id, None)
            if isinstance(session, dict):
                self._delete_session_chunk_files(session)

    def _delete_session_chunk_files(self, session: dict[str, Any]) -> None:
        chunk_files = session.get("chunk_files")
        if not isinstance(chunk_files, dict):
            return
        for filename in chunk_files.values():
            if isinstance(filename, str):
                (self.chunk_dir / filename).unlink(missing_ok=True)

    def _require_resumable_upload_session(self, state: dict[str, Any], session_id: str) -> dict[str, Any]:
        session = state["resumable_upload_sessions"].get(session_id)
        if not isinstance(session, dict):
            raise BlobCapabilityError(404, "resumable_upload_session_not_found", "Resumable upload session was not found.")
        return session

    def _assert_resumable_upload_session_active(self, session: dict[str, Any]) -> None:
        if session.get("expires_at_ms", 0) < _now_ms():
            raise BlobCapabilityError(403, "resumable_upload_session_expired", "Resumable upload session has expired.")

    def _session_chunk_by_id(self, session: dict[str, Any], chunk_id: str) -> Optional[dict[str, Any]]:
        chunks = session.get("chunks")
        if not isinstance(chunks, list):
            return None
        for chunk in chunks:
            if isinstance(chunk, dict) and chunk.get("chunk_id") == chunk_id:
                return chunk
        return None

    def _session_uploaded_chunk_ids(self, session: dict[str, Any]) -> list[str]:
        uploaded_chunk_ids = session.get("uploaded_chunk_ids", [])
        if not isinstance(uploaded_chunk_ids, list):
            return []
        return [chunk_id for chunk_id in uploaded_chunk_ids if isinstance(chunk_id, str) and chunk_id]

    def _missing_session_chunks(self, session: dict[str, Any]) -> list[dict[str, Any]]:
        uploaded_chunk_ids = set(self._session_uploaded_chunk_ids(session))
        chunk_files = session.get("chunk_files")
        if not isinstance(chunk_files, dict):
            chunk_files = {}
        missing: list[dict[str, Any]] = []
        for chunk in session["chunks"]:
            chunk_id = chunk["chunk_id"]
            if chunk_id not in uploaded_chunk_ids:
                missing.append(chunk)
                continue
            filename = chunk_files.get(chunk_id)
            if chunk["size"] > 0 and (not isinstance(filename, str) or not (self.chunk_dir / filename).exists()):
                missing.append(chunk)
        return missing

    def _validate_resumable_complete_request(
        self,
        vault_id: str,
        session: dict[str, Any],
        request: dict[str, Any],
    ) -> None:
        if session.get("vault_id") != vault_id:
            raise SyncStoreError("resumable_upload_session_mismatch", "Resumable upload session vault does not match request")
        if session.get("blob_id") != request["blob_id"]:
            raise SyncStoreError("resumable_upload_session_mismatch", "Resumable upload session blob_id does not match request")
        if session.get("encrypted_size") != request["encrypted_size"]:
            raise SyncStoreError("resumable_upload_session_mismatch", "Resumable upload session encrypted_size does not match request")
        if session.get("encrypted_sha256") != request["encrypted_sha256"]:
            raise SyncStoreError("resumable_upload_session_mismatch", "Resumable upload session encrypted_sha256 does not match request")
        known_chunk_ids = {chunk["chunk_id"] for chunk in session["chunks"]}
        uploaded_chunk_ids = set(request["uploaded_chunk_ids"])
        if not uploaded_chunk_ids.issubset(known_chunk_ids):
            raise SyncStoreError("invalid_request", "uploaded_chunk_ids contains unknown chunks")

    def _finalize_resumable_upload_blob(self, state: dict[str, Any], session_id: str, session: dict[str, Any]) -> None:
        chunks = sorted(session["chunks"], key=lambda chunk: chunk["offset"])
        blob_payload = bytearray()
        chunk_files = session.get("chunk_files")
        if not isinstance(chunk_files, dict):
            chunk_files = {}
        for chunk in chunks:
            if chunk["size"] == 0:
                continue
            filename = chunk_files.get(chunk["chunk_id"])
            if not isinstance(filename, str):
                raise SyncStoreError("resumable_upload_incomplete", "Resumable upload is missing chunk payloads")
            chunk_path = self.chunk_dir / filename
            chunk_payload = chunk_path.read_bytes()
            if len(chunk_payload) != chunk["size"]:
                raise SyncStoreError("chunk_size_mismatch", "Stored chunk size does not match session metadata")
            blob_payload.extend(chunk_payload)

        expected_size = session["encrypted_size"]
        if len(blob_payload) != expected_size:
            raise SyncStoreError("blob_size_mismatch", "Uploaded blob size does not match session metadata")
        encrypted_sha256 = _sha256_payload(bytes(blob_payload))
        if encrypted_sha256 != session["encrypted_sha256"]:
            raise SyncStoreError("blob_hash_mismatch", "Uploaded blob encrypted_sha256 does not match session metadata")

        blob_id = session["blob_id"]
        object_key = _blob_object_key(session["vault_id"], blob_id)
        try:
            self.blob_store.put_object(object_key, bytes(blob_payload))
        except BlobStorageError as error:
            raise _sync_error_from_blob_storage(error) from error
        state["blobs"][blob_id] = {
            "blob_id": blob_id,
            "vault_id": session.get("vault_id"),
            "object_key": object_key,
            "storage_backend": self.blob_store.backend_name,
            "status": "available",
            "encrypted_size": len(blob_payload),
            "content_hash": session.get("content_hash"),
            "encrypted_sha256": encrypted_sha256,
            "uploaded_at_ms": _now_ms(),
        }
        state["resumable_upload_sessions"].pop(session_id, None)
        self._delete_session_chunk_files(session)

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

    def _ensure_file_versions(self, vault: dict[str, Any]) -> dict[str, Any]:
        file_versions = vault.setdefault("file_versions", {})
        if not isinstance(file_versions, dict):
            file_versions = {}
            vault["file_versions"] = file_versions
        records = file_versions.setdefault("records", {})
        if not isinstance(records, dict):
            records = {}
            file_versions["records"] = records
        by_file_id = file_versions.setdefault("by_file_id", {})
        if not isinstance(by_file_id, dict):
            by_file_id = {}
            file_versions["by_file_id"] = by_file_id
        retention_policy = file_versions.setdefault("retention_policy", dict(DEFAULT_FILE_VERSION_RETENTION_POLICY))
        if not isinstance(retention_policy, dict):
            file_versions["retention_policy"] = dict(DEFAULT_FILE_VERSION_RETENTION_POLICY)
        return file_versions

    def _parse_file_version_directives(self, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        directives_payload = payload.get("file_version_directives", [])
        if directives_payload is None:
            return {}
        if not isinstance(directives_payload, list):
            raise SyncStoreError("invalid_request", "file_version_directives must be a list when provided")
        directives: dict[str, dict[str, Any]] = {}
        for item in directives_payload:
            if not isinstance(item, dict):
                raise SyncStoreError("invalid_request", "file_version_directives items must be objects")
            file_id = _require_string(item, "file_id")
            if file_id in directives:
                raise SyncStoreError("invalid_request", "file_version_directives file_id values must be unique")
            source = _require_string(item, "source")
            if source not in VALID_FILE_VERSION_SOURCES:
                raise SyncStoreError("invalid_request", "unsupported file version source")
            version_label = item.get("version_label")
            if version_label is not None and (not isinstance(version_label, str) or not version_label):
                raise SyncStoreError("invalid_request", "version_label must be a non-empty string when provided")
            change_note = item.get("change_note")
            if change_note is not None and (not isinstance(change_note, str) or not change_note):
                raise SyncStoreError("invalid_request", "change_note must be a non-empty string when provided")
            is_pinned = item.get("is_pinned", False)
            if not isinstance(is_pinned, bool):
                raise SyncStoreError("invalid_request", "is_pinned must be a boolean")
            directives[file_id] = {
                "source": source,
                "version_label": version_label,
                "change_note": change_note,
                "is_pinned": is_pinned,
            }
        return directives

    def _commit_blob_ref_file_ids(self, payload: dict[str, Any]) -> set[str]:
        blob_refs = payload.get("blob_refs", [])
        if blob_refs is None:
            return set()
        if not isinstance(blob_refs, list):
            raise SyncStoreError("invalid_request", "blob_refs must be a list when provided")
        file_ids: set[str] = set()
        for item in blob_refs:
            if not isinstance(item, dict):
                raise SyncStoreError("invalid_request", "blob_refs items must be objects")
            file_ids.add(_require_string(item, "file_id"))
        return file_ids

    def _previous_manifest_file_by_id(
        self,
        vault: dict[str, Any],
        base_revision: int,
    ) -> dict[str, dict[str, Any]]:
        if base_revision <= 0:
            return {}
        previous = vault["manifests"].get(str(base_revision))
        if not isinstance(previous, dict):
            return {}
        files = previous.get("files", [])
        if not isinstance(files, list):
            return {}
        return {
            item["file_id"]: item
            for item in files
            if isinstance(item, dict) and isinstance(item.get("file_id"), str)
        }

    def _append_file_versions_for_commit(
        self,
        vault: dict[str, Any],
        request_payload: dict[str, Any],
        manifest: dict[str, Any],
    ) -> None:
        file_versions = self._ensure_file_versions(vault)
        directives = self._parse_file_version_directives(request_payload)
        referenced_file_ids = self._commit_blob_ref_file_ids(request_payload)
        previous_by_file_id = self._previous_manifest_file_by_id(vault, manifest["base_revision"])
        manifest_files = manifest.get("files", [])
        if not isinstance(manifest_files, list):
            raise SyncStoreError("invalid_manifest", "manifest.files must be a list")
        active_by_file_id = {
            item["file_id"]: item
            for item in manifest_files
            if isinstance(item, dict) and isinstance(item.get("file_id"), str)
        }
        unknown_directives = sorted(set(directives) - set(active_by_file_id))
        if unknown_directives:
            raise SyncStoreError(
                "invalid_request",
                "file_version_directives reference files not present in manifest: " + ", ".join(unknown_directives),
            )
        target_file_ids = set(referenced_file_ids) | set(directives)
        for file_id in sorted(target_file_ids):
            item = active_by_file_id.get(file_id)
            if item is None:
                continue
            previous = previous_by_file_id.get(file_id)
            directive = directives.get(file_id)
            content_changed = (
                previous is None
                or previous.get("content_hash") != item.get("content_hash")
                or previous.get("blob_id") != item.get("blob_id")
            )
            if directive is None and not content_changed:
                continue
            source = directive["source"] if directive is not None else "commit_success"
            version_id = _file_version_id(
                vault_id=manifest["vault_id"],
                file_id=file_id,
                revision=manifest["revision"],
                blob_id=item["blob_id"],
                source=source,
            )
            if version_id in file_versions["records"]:
                continue
            record = {
                "version_id": version_id,
                "file_id": file_id,
                "path_at_revision": item["path"],
                "revision": manifest["revision"],
                "content_hash": item["content_hash"],
                "blob_id": item["blob_id"],
                "size": item["size"],
                "mtime": item["mtime"],
                "created_at": manifest["created_at"],
                "created_by_device": manifest["created_by_device"],
                "source": source,
                "is_pinned": directive["is_pinned"] if directive is not None else False,
            }
            if directive is not None:
                if directive["version_label"] is not None:
                    record["version_label"] = directive["version_label"]
                if directive["change_note"] is not None:
                    record["change_note"] = directive["change_note"]
            file_versions["records"][version_id] = record
            by_file_id = file_versions["by_file_id"].setdefault(file_id, [])
            if not isinstance(by_file_id, list):
                by_file_id = []
                file_versions["by_file_id"][file_id] = by_file_id
            if version_id not in by_file_id:
                by_file_id.append(version_id)
            self._apply_file_version_retention_policy(file_versions, file_id)

    def _apply_file_version_retention_policy(self, file_versions: dict[str, Any], file_id: str) -> None:
        policy = file_versions.get("retention_policy")
        if not isinstance(policy, dict):
            policy = dict(DEFAULT_FILE_VERSION_RETENTION_POLICY)
            file_versions["retention_policy"] = policy
        keep_latest = policy.get("keep_latest", DEFAULT_FILE_VERSION_RETENTION_POLICY["keep_latest"])
        if not isinstance(keep_latest, int) or keep_latest <= 0:
            keep_latest = DEFAULT_FILE_VERSION_RETENTION_POLICY["keep_latest"]
        keep_pinned = policy.get("keep_pinned", DEFAULT_FILE_VERSION_RETENTION_POLICY["keep_pinned"])
        if not isinstance(keep_pinned, bool):
            keep_pinned = DEFAULT_FILE_VERSION_RETENTION_POLICY["keep_pinned"]

        records_by_id = file_versions.get("records")
        version_ids = file_versions.get("by_file_id", {}).get(file_id)
        if not isinstance(records_by_id, dict) or not isinstance(version_ids, list):
            return

        records = [
            records_by_id[version_id]
            for version_id in version_ids
            if isinstance(version_id, str) and isinstance(records_by_id.get(version_id), dict)
        ]
        records.sort(key=lambda item: (-item.get("revision", 0), item.get("version_id", "")))

        retained_ids: list[str] = []
        regular_count = 0
        for record in records:
            version_id = record["version_id"]
            if keep_pinned and record.get("is_pinned") is True:
                retained_ids.append(version_id)
                continue
            if regular_count < keep_latest:
                retained_ids.append(version_id)
                regular_count += 1
                continue
            records_by_id.pop(version_id, None)

        file_versions["by_file_id"][file_id] = retained_ids

    def _validate_vault_id(self, vault_id: str) -> None:
        if not vault_id:
            raise SyncStoreError("invalid_vault_id", "vault_id must be non-empty")
