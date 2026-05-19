from __future__ import annotations

import hashlib
import difflib
import json
import mimetypes
import os
import re
import base64
import binascii
from contextlib import closing, suppress
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.request import Request, urlopen
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from vault_core import (
    allocate_conflict_copy_path,
    BlobDownloadSessionResult,
    BlobStagingMaterializationResult,
    CommitFinalizeCleanupResult,
    CommitRecoverySessionResult,
    CommitSnapshotTable,
    CommitSubmissionBundle,
    CommitSubmissionExecutionResult,
    ContentSnapshotMaterializationResult,
    FileVersionCommitDirective,
    FileVersionListExecutionResult,
    FileVersionRecord,
    FileVersionUpdateExecutionResult,
    PullReconcileSessionResult,
    PullSyncSessionResult,
    SyncApplyJournalRecord,
    TombstoneRecord,
    VaultDeviceHeartbeatExecutionResult,
    VaultDeviceListExecutionResult,
    VaultStateRecord,
    add_file,
    apply_manifest_summary_stale,
    append_tombstone,
    build_commit_snapshot_table,
    clear_sync_apply_journal,
    cleanup_commit_staging_artifacts,
    cleanup_failed_commit_submission,
    finalize_commit_submission_cleanup,
    FileMapDocument,
    FileRecord,
    isolate_staging_orphans,
    load_commit_intent_journal,
    load_filemap,
    list_note_backlinks_for_file,
    list_note_links_for_file,
    load_sync_apply_journal,
    load_tombstone_ledger,
    load_vault_state,
    materialize_blob_staging_plan,
    move_staging_orphan,
    materialize_content_snapshot_plan,
    mark_deleted,
    prepare_commit_submission,
    register_conflict_copy,
    rebuild_filemap_from_manifest,
    remove_conflict_copy,
    recover_sync_apply_finalizing_state,
    replace_note_links,
    replace_search_index_entries,
    upsert_search_index_entry,
    delete_search_index_entry,
    rename_file,
    upsert_vault_state,
    upsert_sync_apply_journal,
    search_index,
    write_filemap_atomic,
)
from vault_core.constants import (
    CONFLICT_ORPHANS_DIRNAME,
    FILEMAP_FILENAME,
    NOTEAPP_DIRNAME,
    STAGING_DIRNAME,
    STAGING_ORPHANS_DIRNAME,
    TOMBSTONE_LEDGER_FILENAME,
    VAULTINFO_FILENAME,
)
from vault_core.ledger import rewrite_tombstone_ledger
from vault_core.sync_http import UrlopenLike

from .change_detection import (
    DesktopTrackedChangeCommitPlan,
    DesktopWorkspaceChangeSet,
    build_tracked_change_commit_plan,
    detect_local_workspace_changes,
)
from .crypto import (
    DesktopBlobCryptoProvider,
    build_e2ee_blob_crypto_provider,
    build_missing_vault_key_blob_crypto_provider,
    build_placeholder_blob_crypto_provider,
)
from .crypto_store import (
    DesktopCryptoStatus,
    load_desktop_crypto_status,
    load_desktop_vault_key,
    store_desktop_vault_key,
)
from .recovery import (
    DesktopRecoveryPackageExport,
    DesktopRecoveryPackageImport,
    export_recovery_package,
    import_recovery_package,
)
from .sync_runtime import DesktopSyncHttpConfig
from .worker_state import DesktopSyncWorkerHealth, DesktopSyncWorkerStateRecord
from .workspace import (
    DesktopVaultWorkspace,
    DesktopWorkspaceSnapshot,
    build_desktop_vault_workspace,
)


def _resolve_workspace_file_path(vault_root: Path, relative_path: str) -> Path:
    path = Path(relative_path)
    if path.anchor or path.drive or ".." in path.parts:
        raise ValueError(f"workspace file path is not safe: {relative_path!r}")
    return vault_root / path


def _is_hidden_workspace_context_path(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/").strip("/")
    return (
        normalized == ".ai"
        or normalized.startswith(".ai/")
        or normalized == ".noteapp"
        or normalized.startswith(".noteapp/")
    )


def _normalize_ai_context_folder_path(folder_path: str) -> str:
    normalized = folder_path.replace("\\", "/").strip("/")
    if normalized in (".", "/"):
        return ""
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"AI context folder path is not safe: {folder_path!r}")
    return "/".join(path.parts)


def _record_belongs_to_ai_context_folder(record_path: str, folder_path: str, *, recursive: bool) -> bool:
    record_folder = "/".join(PurePosixPath(record_path.replace("\\", "/")).parts[:-1])
    if not folder_path:
        return recursive or record_folder == ""
    if recursive:
        return record_folder == folder_path or record_folder.startswith(folder_path.rstrip("/") + "/")
    return record_folder == folder_path


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    try:
        temp_path.write_bytes(payload)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _compute_content_hash(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _build_source_version_token(
    *,
    content_hash: str,
    size_bytes: int,
    mtime_ms: int,
) -> str:
    return f"mtime:{mtime_ms}:size:{size_bytes}:hash:{content_hash}"


def _current_time_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _decode_workspace_text(payload: bytes, *, file_id: str) -> tuple[str, str]:
    encodings = ("utf-8-sig",) if payload.startswith(b"\xef\xbb\xbf") else ("utf-8", "gb18030")
    for encoding in encodings:
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError(f"workspace file is not valid UTF-8 or GB18030 text: {file_id}")


def _append_jsonl_record(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    _write_bytes_atomic(path, (rendered + "\n").encode("utf-8"))


def _safe_draft_file_name(file_id: str) -> str:
    if not file_id or "/" in file_id or "\\" in file_id or ".." in Path(file_id).parts:
        raise ValueError(f"workspace draft file_id is not safe: {file_id!r}")
    return f"{file_id}.draft"


def _safe_deleted_file_name(file_id: str, deleted_at: int, original_path: str) -> str:
    if not file_id or "/" in file_id or "\\" in file_id or ".." in Path(file_id).parts:
        raise ValueError(f"workspace deleted file_id is not safe: {file_id!r}")
    suffix = PurePosixPath(original_path).suffix
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,16}", suffix):
        suffix = ".bin"
    return f"{deleted_at}-{file_id}{suffix}"


def _load_jsonl_records(path: Path) -> list[dict[str, object]]:
    if not path.exists() or not path.is_file():
        return []
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _normalize_ai_context_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _extract_ai_context_search_terms(text: str) -> list[str]:
    normalized = text.lower()
    terms: set[str] = set(_AI_CONTEXT_ASCII_TERM_PATTERN.findall(normalized))
    for token in _AI_CONTEXT_CJK_TERM_PATTERN.findall(text):
        terms.add(token)
        if len(token) > 2:
            for index in range(len(token) - 1):
                terms.add(token[index : index + 2])
    return sorted(terms, key=lambda value: (-len(value), value))


def _split_ai_context_blocks(text: str) -> list[str]:
    parts = re.split(r"(?m)(?=^\s{0,3}#{1,6}\s+\S)|\n{2,}", text)
    blocks = [part.strip() for part in parts if part and part.strip()]
    return blocks or [text]


def _split_ai_context_chunks(text: str, *, max_chars_per_chunk: int) -> list[str]:
    normalized = _normalize_ai_context_text(text)
    if not normalized:
        return []
    if len(normalized) <= max_chars_per_chunk:
        return [normalized]
    overlap_chars = min(_AI_CONTEXT_CHUNK_OVERLAP_CHARS, max(0, max_chars_per_chunk // 5))
    blocks = _split_ai_context_blocks(normalized)
    chunks: list[str] = []
    current = ""

    def append_chunk(chunk_text: str) -> None:
        trimmed = chunk_text.strip()
        if trimmed:
            chunks.append(trimmed)

    for block in blocks:
        candidate = block if not current else current + "\n\n" + block
        if len(candidate) <= max_chars_per_chunk:
            current = candidate
            continue
        if current:
            append_chunk(current)
            current = ""
        pending = block
        while len(pending) > max_chars_per_chunk:
            append_chunk(pending[:max_chars_per_chunk])
            remainder = pending[max_chars_per_chunk:].lstrip()
            if not remainder:
                pending = ""
                break
            if overlap_chars > 0:
                overlap_text = pending[max(0, max_chars_per_chunk - overlap_chars) : max_chars_per_chunk].strip()
                pending = overlap_text + "\n\n" + remainder if overlap_text else remainder
            else:
                pending = remainder
        current = pending
    append_chunk(current)
    return chunks or [normalized[:max_chars_per_chunk].strip()]


def _ai_context_heading_text(text: str) -> str:
    headings = [
        line.lstrip().lstrip("#").strip()
        for line in text.splitlines()
        if _AI_CONTEXT_HEADING_PATTERN.match(line)
    ]
    return "\n".join(headings)


def _resolve_sync_activity_limit(argv: list[str]) -> int:
    for index, item in enumerate(argv):
        if item != "--limit" or index + 1 >= len(argv):
            continue
        try:
            return int(argv[index + 1])
        except ValueError:
            return 20
    return 20


def _resolve_activity_feed_level(feed: "DesktopSyncActivityFeed") -> str:
    if any(record.level == "danger" for record in feed.records):
        return "danger"
    if any(record.level == "warning" for record in feed.records):
        return "warning"
    return "info"


_LOCAL_SETTINGS_SCHEMA_VERSION = "v1"
_LOCAL_SETTINGS_TOP_LEVEL_KEYS = {"schema_version", "appearance", "ai"}
_LOCAL_SETTINGS_APPEARANCE_KEYS = {"theme"}
_LOCAL_SETTINGS_AI_KEYS = {
    "local_model_status",
    "embedding_status",
    "provider_api",
    "base_url",
    "model_id",
    "api_key",
}
_LOCAL_SETTINGS_THEMES = {"dark", "light"}
_LOCAL_SETTINGS_MODEL_STATUSES = {
    "not_configured",
    "available",
    "unavailable",
    "disabled",
    "error",
}
_LOCAL_SETTINGS_EMBEDDING_STATUSES = {
    "not_configured",
    "ready",
    "indexing",
    "disabled",
    "error",
}
_LOCAL_SETTINGS_AI_PROVIDER_APIS = {
    "openai-completions",
    "anthropic-messages",
    "google-generative-ai",
}
_AI_PROVIDER_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_AI_PROVIDER_DEFAULT_API = "openai-completions"
_AI_PROVIDER_DEFAULT_TIMEOUT_SECONDS = 30.0
_LOCAL_SETTINGS_DEFAULT_AI_PROVIDER_API_ENV = "NOTEAPP_LOCAL_SETTINGS_DEFAULT_AI_PROVIDER_API"
_LOCAL_SETTINGS_DEFAULT_AI_BASE_URL_ENV = "NOTEAPP_LOCAL_SETTINGS_DEFAULT_AI_BASE_URL"
_LOCAL_SETTINGS_DEFAULT_AI_MODEL_ENV = "NOTEAPP_LOCAL_SETTINGS_DEFAULT_AI_MODEL"
_LOCAL_SETTINGS_FALLBACK_AI_PROVIDER_API = _AI_PROVIDER_DEFAULT_API
_LOCAL_SETTINGS_FALLBACK_AI_BASE_URL = _AI_PROVIDER_DEFAULT_BASE_URL
_LOCAL_SETTINGS_FALLBACK_AI_MODEL_ID = "gpt-4o-mini"
_PLACEHOLDER_CRYPTO_COMPAT_ENV = "NOTEAPP_ALLOW_PLACEHOLDER_CRYPTO"
_WORKSPACE_FILE_CONTENT_MAX_BYTES = 1_000_000
_WORKSPACE_FILE_BLOB_MAX_BYTES = 10_000_000
_AI_CONTEXT_DEFAULT_MAX_FILES = 20
_AI_CONTEXT_DEFAULT_MAX_CHARS_PER_FILE = 4000
_AI_CONTEXT_DEFAULT_MAX_TOTAL_CHARS = 30000
_AI_CONTEXT_CHUNK_OVERLAP_CHARS = 240
_AI_WRITEBACK_PREVIEW_TTL_MS = 15 * 60 * 1000
_WORKSPACE_TRASH_DIRNAME = "trash"
_WORKSPACE_TRASH_PURGED_META_KEY = "trash_purged_at"
_WIKI_LINK_PATTERN = re.compile(r"\[\[([^\]\n]+)\]\]")
_MARKDOWN_FRONTMATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---", re.DOTALL)
_AI_CONTEXT_HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.MULTILINE)
_AI_CONTEXT_ASCII_TERM_PATTERN = re.compile(r"[a-z0-9_]{2,}")
_AI_CONTEXT_CJK_TERM_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,}")


def _local_settings_default_ai_provider_api(environ: Optional[Mapping[str, str]] = None) -> str:
    env = os.environ if environ is None else environ
    provider_api = env.get(_LOCAL_SETTINGS_DEFAULT_AI_PROVIDER_API_ENV, "").strip()
    if provider_api in _LOCAL_SETTINGS_AI_PROVIDER_APIS:
        return provider_api
    return _LOCAL_SETTINGS_FALLBACK_AI_PROVIDER_API


def _local_settings_default_ai_base_url(environ: Optional[Mapping[str, str]] = None) -> str:
    env = os.environ if environ is None else environ
    base_url = env.get(_LOCAL_SETTINGS_DEFAULT_AI_BASE_URL_ENV, "").strip().rstrip("/")
    return base_url or _LOCAL_SETTINGS_FALLBACK_AI_BASE_URL


def _local_settings_default_ai_model_id(environ: Optional[Mapping[str, str]] = None) -> str:
    env = os.environ if environ is None else environ
    model_id = env.get(_LOCAL_SETTINGS_DEFAULT_AI_MODEL_ENV, "").strip()
    return model_id or _LOCAL_SETTINGS_FALLBACK_AI_MODEL_ID


def _placeholder_crypto_compat_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get(_PLACEHOLDER_CRYPTO_COMPAT_ENV) == "true"


def _missing_vault_key_message(vault_id: str) -> str:
    return (
        f"e2ee-v1 vault key is required for vault {vault_id}; "
        "unlock the vault, provide NOTEAPP_VAULT_KEY_BASE64/NOTEAPP_VAULT_KEY_HEX, "
        "or explicitly choose placeholder-v1 compatibility mode for tests/migration only"
    )


def _build_default_blob_crypto_provider(
    config: DesktopSyncHttpConfig,
    *,
    allow_placeholder_crypto: bool,
) -> DesktopBlobCryptoProvider:
    vault_key = load_desktop_vault_key(config.vault_id)
    if vault_key is not None:
        return build_e2ee_blob_crypto_provider(vault_id=config.vault_id, vault_key=vault_key)
    if allow_placeholder_crypto or _placeholder_crypto_compat_enabled():
        return build_placeholder_blob_crypto_provider()
    return build_missing_vault_key_blob_crypto_provider(_missing_vault_key_message(config.vault_id))


def _require_local_settings_object(payload: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"local settings {key} must be an object")
    return dict(value)


def _require_allowed_keys(payload: Mapping[str, Any], allowed_keys: set[str], label: str) -> None:
    unknown_keys = sorted(str(key) for key in payload if key not in allowed_keys)
    if unknown_keys:
        raise ValueError(f"{label} contains unsupported keys: {', '.join(unknown_keys)}")


def _normalize_local_settings_choice(
    payload: Mapping[str, Any],
    key: str,
    allowed_values: set[str],
    default: str,
    label: str,
) -> str:
    value = payload.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    if value not in allowed_values:
        raise ValueError(f"{label} is not supported: {value}")
    return value


def _normalize_local_settings_string(
    payload: Mapping[str, Any],
    key: str,
    default: str,
    label: str,
) -> str:
    value = payload.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = value.strip()
    if not normalized:
        return default
    return normalized


def _normalize_optional_local_settings_secret(payload: Mapping[str, Any], key: str, label: str) -> Optional[str]:
    if key not in payload:
        return None
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = value.strip()
    return normalized if normalized else None


def _normalize_local_settings_payload(payload: Mapping[str, Any]) -> dict[str, object]:
    _require_allowed_keys(payload, _LOCAL_SETTINGS_TOP_LEVEL_KEYS, "local settings")
    schema_version = payload.get("schema_version", _LOCAL_SETTINGS_SCHEMA_VERSION)
    if schema_version != _LOCAL_SETTINGS_SCHEMA_VERSION:
        raise ValueError(f"local settings schema_version is not supported: {schema_version}")
    appearance_payload = _require_local_settings_object(payload, "appearance")
    ai_payload = _require_local_settings_object(payload, "ai")
    _require_allowed_keys(appearance_payload, _LOCAL_SETTINGS_APPEARANCE_KEYS, "local settings appearance")
    _require_allowed_keys(ai_payload, _LOCAL_SETTINGS_AI_KEYS, "local settings ai")
    default_ai_provider_api = _local_settings_default_ai_provider_api()
    default_ai_base_url = _local_settings_default_ai_base_url()
    default_ai_model_id = _local_settings_default_ai_model_id()

    normalized = {
        "schema_version": _LOCAL_SETTINGS_SCHEMA_VERSION,
        "appearance": {
            "theme": _normalize_local_settings_choice(
                appearance_payload,
                "theme",
                _LOCAL_SETTINGS_THEMES,
                "dark",
                "local settings appearance.theme",
            ),
        },
        "ai": {
            "local_model_status": _normalize_local_settings_choice(
                ai_payload,
                "local_model_status",
                _LOCAL_SETTINGS_MODEL_STATUSES,
                "not_configured",
                "local settings ai.local_model_status",
            ),
            "embedding_status": _normalize_local_settings_choice(
                ai_payload,
                "embedding_status",
                _LOCAL_SETTINGS_EMBEDDING_STATUSES,
                "not_configured",
                "local settings ai.embedding_status",
            ),
            "provider_api": _normalize_local_settings_choice(
                ai_payload,
                "provider_api",
                _LOCAL_SETTINGS_AI_PROVIDER_APIS,
                default_ai_provider_api,
                "local settings ai.provider_api",
            ),
            "base_url": _normalize_local_settings_string(
                ai_payload,
                "base_url",
                default_ai_base_url,
                "local settings ai.base_url",
            ),
            "model_id": _normalize_local_settings_string(
                ai_payload,
                "model_id",
                default_ai_model_id,
                "local settings ai.model_id",
            ),
        },
    }
    api_key = _normalize_optional_local_settings_secret(ai_payload, "api_key", "local settings ai.api_key")
    if api_key is not None:
        normalized["ai"]["api_key"] = api_key
    return normalized


def _workspace_posix_relative_path(vault_root: Path, path: Path) -> str:
    return PurePosixPath(*path.relative_to(vault_root).parts).as_posix()


def _is_existing_workspace_import_path(relative_path: str) -> bool:
    normalized = PurePosixPath(relative_path)
    if not normalized.parts:
        return False
    if normalized.parts[:1] == (NOTEAPP_DIRNAME,):
        return False
    if normalized.parts == (VAULTINFO_FILENAME,):
        return False
    if normalized.parts[:2] == (".ai", "raw"):
        return False
    if len(normalized.parts) == 2 and normalized.parts[0] == ".ai" and normalized.parts[1].lower() == "log.md":
        return False
    if normalized.parts[:1] == (".ai",):
        return (
            normalized == PurePosixPath(".ai/index.md")
            or (
                normalized.parts[:2] == (".ai", "wiki")
                and normalized.suffix.lower() in {".md", ".markdown"}
            )
            or (
                len(normalized.parts) == 2
                and normalized.parts[1].lower() == "agents.md"
            )
        )
    return True


def _infer_imported_workspace_file_type(relative_path: str) -> str:
    normalized = PurePosixPath(relative_path)
    if normalized == PurePosixPath(".ai/index.md"):
        return "ai_index"
    if normalized.parts[:2] == (".ai", "wiki") and normalized.suffix.lower() in {".md", ".markdown"}:
        return "ai_wiki"
    if len(normalized.parts) == 2 and normalized.parts[0] == ".ai" and normalized.parts[1].lower() == "agents.md":
        return "ai_agents"
    suffix = PurePosixPath(relative_path).suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "note"
    return "attachment"


def _infer_imported_workspace_mime_type(relative_path: str) -> Optional[str]:
    suffix = PurePosixPath(relative_path).suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "text/markdown"
    if suffix == ".txt":
        return "text/plain"
    return mimetypes.guess_type(relative_path)[0]


def _search_result_title(path: str) -> str:
    name = PurePosixPath(path).name
    for suffix in (".markdown", ".md", ".txt"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return name


def _note_title_from_path(path: str) -> str:
    name = PurePosixPath(path).name
    for suffix in (".markdown", ".md", ".txt"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return name


def _is_search_indexable_type(file_type: str) -> bool:
    return file_type in {"note", "ai_index", "ai_wiki", "ai_agents"}


def _normalize_wiki_link_target(value: str) -> str:
    return value.split("|", 1)[0].split("#", 1)[0].strip()


def _wiki_link_rewrite_target(value: str, new_target: str) -> str:
    target_and_anchor, separator, alias = value.partition("|")
    target, anchor_separator, anchor = target_and_anchor.partition("#")
    rewritten = new_target
    if anchor_separator:
        rewritten = f"{rewritten}#{anchor}"
    if separator:
        rewritten = f"{rewritten}|{alias}"
    return rewritten


def _rewrite_wiki_links(text: str, old_target: str, new_target: str) -> tuple[str, int]:
    old_key = old_target.strip().lower()
    rewrite_count = 0

    def replace_match(match: re.Match[str]) -> str:
        nonlocal rewrite_count
        link_text = match.group(1)
        if _normalize_wiki_link_target(link_text).lower() != old_key:
            return match.group(0)
        rewrite_count += 1
        return f"[[{_wiki_link_rewrite_target(link_text, new_target)}]]"

    return _WIKI_LINK_PATTERN.sub(replace_match, text), rewrite_count


def _parse_markdown_frontmatter(text: str) -> dict[str, str]:
    match = _MARKDOWN_FRONTMATTER_PATTERN.match(text.replace("\r\n", "\n").replace("\r", "\n"))
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip():
            fields[key.strip()] = value.strip()
    return fields


def _markdown_frontmatter_flag(text: str, key: str) -> bool:
    value = _parse_markdown_frontmatter(text).get(key, "")
    return value.strip().strip("\"'").lower() == "true"


def _safe_ai_writeback_note_title(value: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", value).strip(" .")
    sanitized = re.sub(r"\s+", " ", sanitized)
    if not sanitized:
        return "AI Note"
    return sanitized[:80].strip(" .") or "AI Note"


def _normalize_ai_writeback_new_note_path(value: str) -> str:
    raw_value = value.strip().replace("\\", "/")
    if not raw_value:
        raise ValueError("ai_writeback_invalid_request: target_path must be non-empty")
    path = PurePosixPath(raw_value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"ai_writeback_invalid_request: target_path is not safe: {value!r}")
    if path.suffix.lower() not in {".md", ".markdown"}:
        path = path.with_suffix(".md")
    parts_lower = tuple(part.lower() for part in path.parts)
    if not parts_lower:
        raise ValueError("ai_writeback_invalid_request: target_path must be non-empty")
    if parts_lower[0] in {NOTEAPP_DIRNAME.lower(), ".ai", "attachments"}:
        raise ValueError(f"ai_writeback_invalid_request: target_path is reserved: {value!r}")
    normalized = path.as_posix()
    if not _is_existing_workspace_import_path(normalized):
        raise ValueError(f"ai_writeback_invalid_request: target_path is not importable: {value!r}")
    return normalized


def _stable_json_text(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _ai_writeback_signature_payload(
    *,
    vault_id: str,
    device_id: str,
    mode: str,
    target_key: str,
    before_hash: Optional[str],
    after_hash: str,
    request_hash: str,
    idempotency_key: str,
    expires_at_ms: int,
) -> dict[str, Any]:
    return {
        "vault_id": vault_id,
        "device_id": device_id,
        "mode": mode,
        "target_key": target_key,
        "before_hash": before_hash,
        "after_hash": after_hash,
        "request_hash": request_hash,
        "idempotency_key": idempotency_key,
        "expires_at_ms": expires_at_ms,
    }


def _build_ai_writeback_confirmation_token(
    *,
    vault_id: str,
    device_id: str,
    mode: str,
    target_key: str,
    before_hash: Optional[str],
    after_hash: str,
    request_hash: str,
    idempotency_key: str,
    expires_at_ms: int,
) -> str:
    signature_payload = _ai_writeback_signature_payload(
        vault_id=vault_id,
        device_id=device_id,
        mode=mode,
        target_key=target_key,
        before_hash=before_hash,
        after_hash=after_hash,
        request_hash=request_hash,
        idempotency_key=idempotency_key,
        expires_at_ms=expires_at_ms,
    )
    signature = hashlib.sha256(_stable_json_text(signature_payload).encode("utf-8")).hexdigest()
    token_payload = {
        "v": 1,
        "mode": mode,
        "target_key": target_key,
        "before_hash": before_hash,
        "after_hash": after_hash,
        "request_hash": request_hash,
        "idempotency_key": idempotency_key,
        "expires_at_ms": expires_at_ms,
        "signature": signature,
    }
    encoded = base64.urlsafe_b64encode(_stable_json_text(token_payload).encode("utf-8")).decode("ascii").rstrip("=")
    return f"aiwb1.{encoded}"


def _parse_ai_writeback_confirmation_token(token: str) -> dict[str, Any]:
    if not token.startswith("aiwb1."):
        raise ValueError("ai_writeback_missing_confirmation: invalid confirmation token")
    encoded = token.split(".", 1)[1]
    try:
        padded = encoded + ("=" * (-len(encoded) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("ai_writeback_missing_confirmation: invalid confirmation token") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ValueError("ai_writeback_missing_confirmation: invalid confirmation token")
    required_string_keys = {
        "mode",
        "target_key",
        "after_hash",
        "request_hash",
        "idempotency_key",
        "signature",
    }
    if any(not isinstance(payload.get(key), str) or not payload.get(key) for key in required_string_keys):
        raise ValueError("ai_writeback_missing_confirmation: invalid confirmation token")
    if payload.get("before_hash") is not None and not isinstance(payload.get("before_hash"), str):
        raise ValueError("ai_writeback_missing_confirmation: invalid confirmation token")
    if not isinstance(payload.get("expires_at_ms"), int):
        raise ValueError("ai_writeback_missing_confirmation: invalid confirmation token")
    return payload


def _load_ai_provider_config(environ: Optional[Mapping[str, str]] = None) -> Optional[DesktopAiProviderConfig]:
    env = os.environ if environ is None else environ
    api_key = env.get("NOTEAPP_AI_API_KEY", "").strip()
    model = env.get("NOTEAPP_AI_MODEL", "").strip()
    if not api_key or not model:
        return None

    base_url = env.get("NOTEAPP_AI_BASE_URL", _AI_PROVIDER_DEFAULT_BASE_URL).strip().rstrip("/")
    if not base_url:
        base_url = _AI_PROVIDER_DEFAULT_BASE_URL
    timeout_raw = env.get("NOTEAPP_AI_TIMEOUT_SECONDS", "").strip()
    if timeout_raw:
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError:
            timeout_seconds = _AI_PROVIDER_DEFAULT_TIMEOUT_SECONDS
        if timeout_seconds <= 0:
            timeout_seconds = _AI_PROVIDER_DEFAULT_TIMEOUT_SECONDS
    else:
        timeout_seconds = _AI_PROVIDER_DEFAULT_TIMEOUT_SECONDS

    return DesktopAiProviderConfig(
        provider_api=env.get("NOTEAPP_AI_PROVIDER_API", _AI_PROVIDER_DEFAULT_API).strip() or _AI_PROVIDER_DEFAULT_API,
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
    )


def _load_ai_provider_config_from_settings(settings_path: Path) -> Optional[DesktopAiProviderConfig]:
    if not settings_path.exists() or not settings_path.is_file():
        return None
    raw_payload = json.loads(settings_path.read_text(encoding="utf-8"))
    if not isinstance(raw_payload, dict):
        return None
    ai_payload = raw_payload.get("ai")
    if not isinstance(ai_payload, dict):
        return None
    api_key = ai_payload.get("api_key")
    provider_api = ai_payload.get("provider_api", _local_settings_default_ai_provider_api())
    base_url = ai_payload.get("base_url", _local_settings_default_ai_base_url())
    model = ai_payload.get("model_id", _local_settings_default_ai_model_id())
    if not all(isinstance(value, str) and value.strip() for value in (api_key, provider_api, base_url, model)):
        return None
    if provider_api not in _LOCAL_SETTINGS_AI_PROVIDER_APIS:
        return None
    return DesktopAiProviderConfig(
        provider_api=provider_api.strip(),
        base_url=base_url.strip(),
        api_key=api_key.strip(),
        model=model.strip(),
        timeout_seconds=_AI_PROVIDER_DEFAULT_TIMEOUT_SECONDS,
    )


def _default_ai_urlopen(request: Request, timeout: float):
    return urlopen(request, timeout=timeout)


def _normalize_workspace_note_path(value: str) -> str:
    raw_value = value.strip().replace("\\", "/")
    if not raw_value:
        raise ValueError("workspace note path must be non-empty")
    path = PurePosixPath(raw_value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"workspace note path is not safe: {value!r}")
    if path.parts[:1] in ((NOTEAPP_DIRNAME,), (".ai",)):
        raise ValueError(f"workspace note path is reserved: {value!r}")
    if path.suffix.lower() not in {".md", ".markdown"}:
        path = path.with_suffix(".md")
    normalized = path.as_posix()
    if not _is_existing_workspace_import_path(normalized):
        raise ValueError(f"workspace note path is not importable: {value!r}")
    return normalized


def _normalize_workspace_note_rename_path(current_path: str, new_name: str) -> str:
    raw_value = new_name.strip().replace("\\", "/")
    if not raw_value:
        raise ValueError("workspace note name must be non-empty")
    path = PurePosixPath(raw_value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"workspace note name is not safe: {new_name!r}")
    if len(path.parts) > 1:
        raise ValueError("workspace note rename only supports changing the file name, not moving folders")
    if path.suffix.lower() not in {".md", ".markdown"}:
        path = path.with_suffix(".md")
    parent = PurePosixPath(current_path).parent
    normalized = (parent / path.name).as_posix()
    if not _is_existing_workspace_import_path(normalized):
        raise ValueError(f"workspace note path is not importable: {new_name!r}")
    return normalized


def _normalize_workspace_note_move_path(value: str) -> str:
    return _normalize_workspace_note_path(value)


def _normalize_workspace_attachment_name(value: str) -> str:
    name = PurePosixPath(value.strip().replace("\\", "/")).name
    if not name or name in {".", ".."}:
        raise ValueError("workspace attachment file name must be non-empty")
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", name).strip(" .")
    if not sanitized:
        raise ValueError("workspace attachment file name is not safe")
    return sanitized


def _next_available_workspace_attachment_path(document: FileMapDocument, file_name: str, vault_root: Path) -> str:
    normalized_name = _normalize_workspace_attachment_name(file_name)
    stem = PurePosixPath(normalized_name).stem or "attachment"
    suffix = PurePosixPath(normalized_name).suffix
    active_paths = {record.path for record in document.files if record.status != "deleted"}
    index = 0
    while True:
        candidate_name = normalized_name if index == 0 else f"{stem}-{index}{suffix}"
        candidate = (PurePosixPath("Attachments") / candidate_name).as_posix()
        disk_path = _resolve_workspace_file_path(vault_root, candidate)
        if candidate not in active_paths and not disk_path.exists():
            return candidate
        index += 1


def _iter_existing_workspace_import_files(vault_root: Path) -> Iterable[tuple[str, Path]]:
    for path in vault_root.rglob("*"):
        if not path.is_file():
            continue
        relative_path = _workspace_posix_relative_path(vault_root, path)
        if _is_existing_workspace_import_path(relative_path):
            yield relative_path, path


def _is_local_import_record(record: FileRecord) -> bool:
    return (
        record.status == "active"
        and record.content_hash is None
        and record.last_known_revision is None
        and record.conflict_source_file_id is None
    )


def _should_replace_local_import_filemap(document: FileMapDocument, import_paths: list[str]) -> bool:
    if not document.files:
        return True
    if not all(_is_local_import_record(record) for record in document.files):
        return False
    existing_paths = sorted(record.path for record in document.files)
    return existing_paths != sorted(import_paths)


def _build_pull_apply_ops_hash(plan: "DesktopPullRequiredBlobPlan") -> str:
    payload = {
        "vault_id": plan.vault_id,
        "revision": plan.revision,
        "files": [
            {
                "file_id": item.file_id,
                "path": item.path,
                "type": item.type,
                "blob_id": item.blob_id,
                "content_hash": item.content_hash,
            }
            for item in plan.files
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _build_pull_apply_plan_ops_hash(plan: "DesktopPullApplyPlan") -> str:
    payload = {
        "vault_id": plan.vault_id,
        "revision": plan.revision,
        "writes": [
            {
                "file_id": item.file_id,
                "target_path": item.target_path,
                "staging_path": item.staging_path,
                "type": item.type,
                "content_hash": item.content_hash,
                "previous_path": item.previous_path,
                "expected_previous_content_hash": item.expected_previous_content_hash,
            }
            for item in plan.writes
        ],
        "moves": [
            {
                "file_id": item.file_id,
                "source_path": item.source_path,
                "target_path": item.target_path,
                "type": item.type,
                "content_hash": item.content_hash,
            }
            for item in plan.moves
        ],
        "deletes": [
            {
                "file_id": item.file_id,
                "path": item.path,
                "reason": item.reason,
                "expected_content_hash": item.expected_content_hash,
            }
            for item in plan.deletes
        ],
        "blocking_paths": plan.blocking_paths,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _build_pull_apply_staging_relative_path(file_id: str) -> str:
    if not file_id or "/" in file_id or "\\" in file_id or file_id in {".", ".."}:
        raise ValueError(f"file_id is not safe for staging: {file_id!r}")
    return f"{STAGING_DIRNAME}/{file_id}.staging"


def _resolve_pull_apply_staging_path(vault_root: Path, file_id: str) -> Path:
    return vault_root / Path(_build_pull_apply_staging_relative_path(file_id))


def _relative_vault_path(vault_root: Path, path: Path) -> str:
    return path.relative_to(vault_root).as_posix()


def _has_conflict_orphan_files(vault_root: Path) -> bool:
    orphan_root = vault_root / CONFLICT_ORPHANS_DIRNAME
    if not orphan_root.exists():
        return False
    return any(path.is_file() for path in orphan_root.rglob("*"))


def _has_conflict_copy_records(document) -> bool:
    return any(record.status == "conflict_copy" for record in document.files)


def _resolve_conflict_orphan_path(vault_root: Path, relative_path: str) -> Path:
    orphan_path = _resolve_workspace_file_path(vault_root, relative_path)
    orphan_root = (vault_root / CONFLICT_ORPHANS_DIRNAME).resolve()
    try:
        orphan_path.resolve().relative_to(orphan_root)
    except ValueError as exc:
        raise ValueError(f"conflict orphan path is not inside {CONFLICT_ORPHANS_DIRNAME}: {relative_path}") from exc
    return orphan_path


_MIGRATION_EXPORT_EXCLUDED_FILES = {
    f"{NOTEAPP_DIRNAME}/filemap.json.tmp",
    f"{NOTEAPP_DIRNAME}/state.sqlite3",
    f"{NOTEAPP_DIRNAME}/sync-apply-plan.json",
    f"{NOTEAPP_DIRNAME}/sync-worker-state.json",
    ".ai/log.md",
}
_MIGRATION_EXPORT_EXCLUDED_PREFIXES = (
    f"{NOTEAPP_DIRNAME}/drafts/",
    f"{STAGING_DIRNAME}/",
    f"{STAGING_ORPHANS_DIRNAME}/",
)
_MIGRATION_REQUIRED_FILES = {
    VAULTINFO_FILENAME,
    f"{NOTEAPP_DIRNAME}/{FILEMAP_FILENAME}",
    f"{NOTEAPP_DIRNAME}/{TOMBSTONE_LEDGER_FILENAME}",
}


def _normalize_migration_relative_path(relative_path: str) -> str:
    path = PurePosixPath(relative_path)
    if not relative_path or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"migration package path is not safe: {relative_path!r}")
    return path.as_posix()


def _should_skip_migration_export_path(relative_path: str, *, include_ai_raw: bool) -> bool:
    normalized = _normalize_migration_relative_path(relative_path)
    if normalized in _MIGRATION_EXPORT_EXCLUDED_FILES:
        return True
    if any(normalized.startswith(prefix) for prefix in _MIGRATION_EXPORT_EXCLUDED_PREFIXES):
        return True
    if normalized.startswith(".ai/raw/") and not include_ai_raw:
        return True
    return False


def _is_forbidden_migration_import_path(relative_path: str) -> bool:
    normalized = _normalize_migration_relative_path(relative_path)
    if normalized in _MIGRATION_EXPORT_EXCLUDED_FILES:
        return True
    if any(normalized.startswith(prefix) for prefix in _MIGRATION_EXPORT_EXCLUDED_PREFIXES):
        return True
    return False


def _list_migration_export_files(
    vault_root: Path,
    *,
    include_ai_raw: bool,
    package_path: Path,
) -> list[tuple[str, Path]]:
    package_target = package_path.resolve()
    export_files: list[tuple[str, Path]] = []
    for path in sorted(
        (item for item in vault_root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(vault_root).as_posix(),
    ):
        if path.resolve() == package_target:
            continue
        relative_path = path.relative_to(vault_root).as_posix()
        if _should_skip_migration_export_path(relative_path, include_ai_raw=include_ai_raw):
            continue
        export_files.append((relative_path, path))
    return export_files


def _read_migration_package(archive: ZipFile) -> tuple[FileMapDocument, dict[str, str]]:
    archive_entries: dict[str, str] = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        normalized = _normalize_migration_relative_path(info.filename)
        if _is_forbidden_migration_import_path(normalized):
            raise ValueError(f"migration package contains unsupported runtime entry: {normalized}")
        existing = archive_entries.get(normalized)
        if existing is not None and existing != info.filename:
            raise ValueError(f"migration package contains duplicate entry: {normalized}")
        archive_entries[normalized] = info.filename

    missing_required = sorted(_MIGRATION_REQUIRED_FILES - set(archive_entries))
    if missing_required:
        raise ValueError("migration package is missing required files: " + ", ".join(missing_required))

    filemap_payload = archive.read(archive_entries[f"{NOTEAPP_DIRNAME}/{FILEMAP_FILENAME}"])
    return FileMapDocument.from_dict(json.loads(filemap_payload.decode("utf-8"))), archive_entries


def _serialize_pull_apply_plan(plan: "DesktopPullApplyPlan") -> bytes:
    return json.dumps(asdict(plan), sort_keys=True, separators=(",", ":")).encode("utf-8")


def _deserialize_pull_apply_plan(payload: bytes) -> "DesktopPullApplyPlan":
    data = json.loads(payload.decode("utf-8"))
    return DesktopPullApplyPlan(
        vault_id=data["vault_id"],
        revision=data["revision"],
        writes=[DesktopPullApplyWriteFile(**item) for item in data.get("writes", [])],
        moves=[DesktopPullApplyMoveFile(**item) for item in data.get("moves", [])],
        deletes=[DesktopPullApplyDeleteFile(**item) for item in data.get("deletes", [])],
        blocking_paths=list(data.get("blocking_paths", [])),
        ops_hash=data["ops_hash"],
    )


@dataclass(frozen=True)
class DesktopPreparedCommit:
    snapshot: DesktopWorkspaceSnapshot
    submission: CommitSubmissionBundle
    snapshot_materialization: ContentSnapshotMaterializationResult
    blob_staging_materialization: BlobStagingMaterializationResult
    snapshot_table: CommitSnapshotTable


@dataclass(frozen=True)
class DesktopCommitCleanupResult:
    state: VaultStateRecord
    removed_staging_paths: list[Path]


@dataclass(frozen=True)
class DesktopCommitSessionResult:
    prepared: DesktopPreparedCommit
    network: CommitSubmissionExecutionResult
    finalized: Optional[CommitFinalizeCleanupResult] = None
    cleanup: Optional[DesktopCommitCleanupResult] = None


@dataclass(frozen=True)
class DesktopPullRequiredBlobFile:
    file_id: str
    path: str
    type: str
    blob_id: str
    content_hash: str


@dataclass(frozen=True)
class DesktopPullRequiredBlobPlan:
    vault_id: str
    revision: int
    files: list[DesktopPullRequiredBlobFile]
    blob_ids: list[str]


@dataclass(frozen=True)
class DesktopPullRequiredBlobResult:
    pull: PullSyncSessionResult
    plan: DesktopPullRequiredBlobPlan
    download: Optional[BlobDownloadSessionResult]
    plaintext_by_file_id: dict[str, bytes]


@dataclass(frozen=True)
class DesktopFileVersionContent:
    schema_version: str
    vault_id: str
    device_id: str
    vault_root: Path
    version: FileVersionRecord
    size_bytes: int
    content_hash: str
    content_base64: str
    text: Optional[str] = None
    encoding: Optional[str] = None


@dataclass(frozen=True)
class DesktopFileVersionDiff:
    schema_version: str
    vault_id: str
    device_id: str
    vault_root: Path
    version: FileVersionRecord
    current_file_id: str
    current_path: str
    current_content_hash: str
    version_content_hash: str
    is_binary: bool
    diff_text: str


@dataclass(frozen=True)
class DesktopFileVersionRestoreResult:
    schema_version: str
    vault_id: str
    device_id: str
    vault_root: Path
    version: FileVersionRecord
    file_id: str
    path: str
    restored_content_hash: str
    commit: DesktopCommitSessionResult


@dataclass(frozen=True)
class DesktopPullApplyStagingResult:
    journal: Optional[SyncApplyJournalRecord]
    written_staging_paths: dict[str, Path]


@dataclass(frozen=True)
class DesktopPullApplyWriteFile:
    file_id: str
    target_path: str
    staging_path: str
    type: str
    content_hash: str
    previous_path: Optional[str] = None
    expected_previous_content_hash: Optional[str] = None


@dataclass(frozen=True)
class DesktopPullApplyMoveFile:
    file_id: str
    source_path: str
    target_path: str
    type: str
    content_hash: str


@dataclass(frozen=True)
class DesktopPullApplyDeleteFile:
    file_id: str
    path: str
    reason: str
    expected_content_hash: Optional[str] = None


@dataclass(frozen=True)
class DesktopPullApplyPlan:
    vault_id: str
    revision: int
    writes: list[DesktopPullApplyWriteFile]
    moves: list[DesktopPullApplyMoveFile]
    deletes: list[DesktopPullApplyDeleteFile]
    blocking_paths: list[str]
    ops_hash: str


@dataclass(frozen=True)
class DesktopPullApplyPlanResult:
    pull: PullSyncSessionResult
    plan: DesktopPullApplyPlan


@dataclass(frozen=True)
class DesktopPullApplyExecutionResult:
    journal: Optional[SyncApplyJournalRecord]
    written_paths: dict[str, Path]
    moved_paths: dict[str, Path]
    deleted_paths: list[Path]


@dataclass(frozen=True)
class DesktopPullApplyFinalizeResult:
    state: VaultStateRecord
    removed_staging_paths: list[Path]
    removed_plan_path: Optional[Path] = None


@dataclass(frozen=True)
class DesktopPullApplyRecoveryResult:
    mode: str
    requires_full_pull: bool
    journal_phase: Optional[str]
    state: Optional[VaultStateRecord]
    removed_staging_paths: list[Path]
    isolated_staging_paths: Optional[list[Path]] = None
    removed_plan_path: Optional[Path] = None


@dataclass(frozen=True)
class DesktopConflictResolutionResult:
    state: VaultStateRecord
    removed_conflict_paths: dict[str, Path]
    removed_orphan_paths: list[Path]
    skipped_conflict_file_ids: list[str]
    skipped_orphan_paths: list[str]


@dataclass(frozen=True)
class DesktopConflictArtifact:
    kind: str
    path: str
    exists_on_disk: bool
    file_id: Optional[str] = None
    conflict_source_file_id: Optional[str] = None
    content_hash: Optional[str] = None


@dataclass(frozen=True)
class DesktopConflictStatus:
    state: VaultStateRecord
    actual_has_unresolved_conflicts: bool
    conflict_copies: list[DesktopConflictArtifact]
    conflict_orphans: list[DesktopConflictArtifact]


@dataclass(frozen=True)
class DesktopVaultExportResult:
    package_path: Path
    vault_id: str
    exported_paths: list[str]
    included_ai_raw: bool
    included_conflict_orphans: bool


@dataclass(frozen=True)
class DesktopVaultPackageInspection:
    package_path: Path
    vault_id: str
    package_entries: list[str]
    includes_ai_raw: bool
    includes_conflict_orphans: bool
    has_unresolved_conflicts: bool


@dataclass(frozen=True)
class DesktopVaultImportResult:
    package_path: Path
    vault_id: str
    imported_paths: list[str]
    restored_ai_raw: bool
    restored_conflict_orphans: bool
    state: VaultStateRecord


@dataclass(frozen=True)
class DesktopCommitGateStatus:
    can_submit_commit: bool
    blocking_reasons: list[str]
    requires_full_pull: bool
    has_active_commit_journal: bool
    has_active_sync_apply_journal: bool


@dataclass(frozen=True)
class DesktopVaultSummary:
    state: VaultStateRecord
    changes: DesktopWorkspaceChangeSet
    conflicts: DesktopConflictStatus
    worker_health: Optional[DesktopSyncWorkerHealth]
    commit_gate: DesktopCommitGateStatus


@dataclass(frozen=True)
class DesktopLocalSyncSettings:
    base_url: str
    bearer_token_configured: bool
    request_timeout_seconds: float
    blob_timeout_seconds: float
    user_agent: str


@dataclass(frozen=True)
class DesktopLocalAppearanceSettings:
    theme: str


@dataclass(frozen=True)
class DesktopLocalAiSettings:
    local_model_status: str
    embedding_status: str
    provider_api: str
    base_url: str
    model_id: str
    api_key_configured: bool
    api_key: Optional[str] = None


@dataclass(frozen=True)
class DesktopLocalCryptoSettings:
    schema_version: str
    crypto_scheme: str
    key_epoch: int
    unlocked: bool
    key_available: bool
    storage_provider: str
    key_ref: str
    message: str
    error: Optional[str] = None


def _local_crypto_settings_from_status(status: DesktopCryptoStatus) -> DesktopLocalCryptoSettings:
    return DesktopLocalCryptoSettings(
        schema_version=status.schema_version,
        crypto_scheme=status.crypto_scheme,
        key_epoch=status.key_epoch,
        unlocked=status.unlocked,
        key_available=status.key_available,
        storage_provider=status.storage_provider,
        key_ref=status.key_ref,
        message=status.message,
        error=status.error,
    )


@dataclass(frozen=True)
class DesktopLocalSettingsSnapshot:
    schema_version: str
    source: str
    settings_path: Path
    vault_root: Path
    vault_id: str
    device_id: str
    sync: DesktopLocalSyncSettings
    appearance: DesktopLocalAppearanceSettings
    ai: DesktopLocalAiSettings
    crypto: DesktopLocalCryptoSettings


@dataclass(frozen=True)
class DesktopCryptoRecoveryImportResult:
    recovery: DesktopRecoveryPackageImport
    crypto: DesktopLocalCryptoSettings


@dataclass(frozen=True)
class DesktopWorkspaceFileEntry:
    file_id: str
    path: str
    type: str
    status: str
    updated_at: int
    exists_on_disk: bool
    size_bytes: Optional[int]
    content_hash: Optional[str] = None
    last_known_revision: Optional[int] = None
    conflict_source_file_id: Optional[str] = None


@dataclass(frozen=True)
class DesktopWorkspaceFilesSnapshot:
    schema_version: str
    vault_id: str
    device_id: str
    vault_root: Path
    files: list[DesktopWorkspaceFileEntry]
    total_count: int
    active_count: int
    missing_count: int


@dataclass(frozen=True)
class DesktopAiWikiArtifact:
    title: str
    path: str
    source_file_id: str
    source_path: str
    source_content_hash: str


@dataclass(frozen=True)
class DesktopAiWikiSkippedPage:
    path: str
    reason: str
    source_path: Optional[str] = None


@dataclass(frozen=True)
class DesktopAiWikiCompileResult:
    schema_version: str
    vault_id: str
    device_id: str
    vault_root: Path
    generated_at: str
    source_count: int
    artifact_count: int
    written_count: int
    skipped_count: int
    index_path: str
    artifacts: list[DesktopAiWikiArtifact]
    skipped: list[DesktopAiWikiSkippedPage]
    files: DesktopWorkspaceFilesSnapshot


@dataclass(frozen=True)
class DesktopAiWikiAnswerCitation:
    file_id: str
    path: str
    title: str
    excerpt: str
    score: int


@dataclass(frozen=True)
class DesktopAiWikiAnswerResult:
    schema_version: str
    vault_id: str
    device_id: str
    vault_root: Path
    question: str
    answer: str
    citation_count: int
    citations: list[DesktopAiWikiAnswerCitation]
    model_status: str


@dataclass(frozen=True)
class DesktopAiContextTaskSource:
    file_id: str
    path: str
    title: str
    excerpt: str
    included_chars: int
    original_chars: int
    truncated: bool


@dataclass(frozen=True)
class DesktopAiContextTaskTruncation:
    max_files: int
    max_chars_per_file: int
    max_total_chars: int
    included_file_count: int
    skipped_file_count: int
    included_chars: int
    truncated: bool
    note: str


@dataclass(frozen=True)
class DesktopAiContextTaskResult:
    schema_version: str
    vault_id: str
    device_id: str
    vault_root: Path
    context_type: str
    instruction: str
    answer: str
    source_count: int
    sources: list[DesktopAiContextTaskSource]
    model_status: str
    truncation: DesktopAiContextTaskTruncation


@dataclass(frozen=True)
class DesktopAiWritebackSource:
    file_id: str
    path: str
    title: Optional[str] = None
    excerpt: Optional[str] = None


@dataclass(frozen=True)
class DesktopAiWritebackDiff:
    format: str
    text: str


@dataclass(frozen=True)
class DesktopAiWritebackPreview:
    schema_version: str
    mode: str
    target_path: str
    before_hash: Optional[str]
    after_hash: str
    confirmation_token: str
    expires_at_ms: int
    rendered_markdown: str
    diff: DesktopAiWritebackDiff
    warnings: list[str]


@dataclass(frozen=True)
class DesktopAiWritebackApplyResult:
    schema_version: str
    mode: str
    status: str
    file_id: str
    path: str
    content_hash: str
    wrote_draft: bool
    wrote_file: bool
    search_index_refreshed: bool
    requires_user_save: bool


@dataclass(frozen=True)
class _DesktopAiContextChunk:
    file_id: str
    path: str
    title: str
    file_order: int
    chunk_index: int
    chunk_count: int
    text: str
    original_chars: int


@dataclass(frozen=True)
class DesktopAiProviderHealthResult:
    schema_version: str
    vault_id: str
    device_id: str
    vault_root: Path
    configured: bool
    status: str
    provider_api: Optional[str]
    base_url: Optional[str]
    model_id: Optional[str]
    message: str


@dataclass(frozen=True)
class DesktopAiProviderConfig:
    provider_api: str
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float


@dataclass(frozen=True)
class DesktopWorkspaceTrashItem:
    file_id: str
    path: str
    type: str
    deleted_at: int
    trash_path: Path
    exists_in_trash: bool
    size_bytes: Optional[int]


@dataclass(frozen=True)
class DesktopWorkspaceTrashSnapshot:
    schema_version: str
    vault_id: str
    device_id: str
    vault_root: Path
    trash_root: Path
    items: list[DesktopWorkspaceTrashItem]
    total_count: int


@dataclass(frozen=True)
class DesktopWorkspaceFileContent:
    schema_version: str
    vault_id: str
    device_id: str
    vault_root: Path
    file_id: str
    path: str
    type: str
    status: str
    updated_at: int
    size_bytes: int
    content_hash: Optional[str]
    tracked_content_hash: Optional[str]
    text: str
    encoding: str = "utf-8"


@dataclass(frozen=True)
class DesktopWorkspaceFileBlob:
    schema_version: str
    vault_id: str
    device_id: str
    vault_root: Path
    file_id: str
    path: str
    type: str
    status: str
    size_bytes: int
    content_hash: str
    content_base64: str
    mime_type: Optional[str] = None


@dataclass(frozen=True)
class DesktopWorkspaceFileDraft:
    schema_version: str
    vault_id: str
    device_id: str
    vault_root: Path
    file_id: str
    path: str
    has_draft: bool
    draft_path: Path
    updated_at: Optional[int] = None
    size_bytes: Optional[int] = None
    text: Optional[str] = None


@dataclass(frozen=True)
class DesktopWorkspaceSearchResult:
    file_id: str
    path: str
    title: str
    snippet: str


@dataclass(frozen=True)
class DesktopWorkspaceSearchSnapshot:
    schema_version: str
    vault_id: str
    device_id: str
    vault_root: Path
    query: str
    total_count: int
    results: list[DesktopWorkspaceSearchResult]


@dataclass(frozen=True)
class DesktopWorkspaceNoteLink:
    source_file_id: str
    source_path: str
    link_text: str
    target_file_id: Optional[str]
    target_path: Optional[str]
    ordinal: int


@dataclass(frozen=True)
class DesktopWorkspaceNoteLinksSnapshot:
    schema_version: str
    vault_id: str
    device_id: str
    vault_root: Path
    file_id: str
    path: str
    outgoing: list[DesktopWorkspaceNoteLink]
    backlinks: list[DesktopWorkspaceNoteLink]
    outgoing_count: int
    backlink_count: int


@dataclass(frozen=True)
class DesktopWorkspaceFileMutationResult:
    schema_version: str
    vault_id: str
    device_id: str
    vault_root: Path
    operation: str
    file: DesktopWorkspaceFileEntry
    files: DesktopWorkspaceFilesSnapshot


@dataclass(frozen=True)
class DesktopSyncPanelAction:
    action_id: str
    label: str
    enabled: bool
    emphasis: str
    command: str
    argv: list[str]
    reason: Optional[str] = None
    requires_confirmation: bool = False


@dataclass(frozen=True)
class DesktopSyncPanelModel:
    level: str
    headline: str
    detail: str
    conflict_badge_count: int
    change_badge_count: int
    primary_action: DesktopSyncPanelAction
    secondary_actions: list[DesktopSyncPanelAction]
    summary: DesktopVaultSummary


@dataclass(frozen=True)
class DesktopSyncCenterCard:
    card_id: str
    kind: str
    level: str
    title: str
    body: str
    badge_count: int
    actions: list[DesktopSyncPanelAction]


@dataclass(frozen=True)
class DesktopSyncCenterModel:
    cards: list[DesktopSyncCenterCard]
    panel: DesktopSyncPanelModel
    summary: DesktopVaultSummary
    recent_activity: DesktopSyncActivityFeed


@dataclass(frozen=True)
class DesktopSyncShellSnapshot:
    generated_at_ms: int
    vault_id: str
    device_id: str
    vault_root: Path
    sync_center: DesktopSyncCenterModel
    activity_feed: DesktopSyncActivityFeed


@dataclass(frozen=True)
class DesktopSyncActionSnapshotResult:
    execution: DesktopSyncActionExecutionResult
    snapshot: DesktopSyncShellSnapshot


@dataclass(frozen=True)
class DesktopSyncActionExecutionResult:
    action: DesktopSyncPanelAction
    source: str
    status: str
    payload: object | None
    message: Optional[str] = None


@dataclass(frozen=True)
class DesktopSyncActivityRecord:
    activity_id: str
    occurred_at_ms: int
    level: str
    action_id: str
    command: str
    status: str
    source: str
    message: Optional[str] = None


@dataclass(frozen=True)
class DesktopSyncActivityFeed:
    records: list[DesktopSyncActivityRecord]
    total_count: int


@dataclass(frozen=True)
class DesktopPullApplySessionResult:
    pull: PullSyncSessionResult
    plan: DesktopPullApplyPlan
    staged: DesktopPullApplyStagingResult
    execution: DesktopPullApplyExecutionResult
    finalized: Optional[DesktopPullApplyFinalizeResult] = None


def inspect_vault_package(package_path: Path) -> DesktopVaultPackageInspection:
    resolved_package_path = package_path.resolve()
    if not resolved_package_path.exists() or not resolved_package_path.is_file():
        raise FileNotFoundError(f"migration package not found: {resolved_package_path}")
    with ZipFile(resolved_package_path, "r") as archive:
        package_document, archive_entries = _read_migration_package(archive)
    package_entries = sorted(archive_entries)
    return DesktopVaultPackageInspection(
        package_path=resolved_package_path,
        vault_id=package_document.vault_id,
        package_entries=package_entries,
        includes_ai_raw=any(path.startswith(".ai/raw/") for path in package_entries),
        includes_conflict_orphans=any(
            path.startswith(f"{CONFLICT_ORPHANS_DIRNAME}/") for path in package_entries
        ),
        has_unresolved_conflicts=(
            _has_conflict_copy_records(package_document)
            or any(path.startswith(f"{CONFLICT_ORPHANS_DIRNAME}/") for path in package_entries)
        ),
    )


@dataclass(frozen=True)
class DesktopSyncService:
    workspace: DesktopVaultWorkspace
    blob_crypto_provider: DesktopBlobCryptoProvider
    file_id_builder: Callable[[str], str]
    detected_submit_plan_hook: Optional[Callable[[DesktopTrackedChangeCommitPlan], None]] = None
    ai_opener: Optional[UrlopenLike] = None

    @property
    def config(self) -> DesktopSyncHttpConfig:
        return self.workspace.config

    @property
    def vault_id(self) -> str:
        return self.workspace.vault_id

    def ensure_initialized(self, *, now_ms: Optional[int] = None) -> DesktopWorkspaceSnapshot:
        return self.workspace.ensure_initialized(now_ms=now_ms)

    def load_snapshot(self) -> DesktopWorkspaceSnapshot:
        return self.workspace.load_snapshot()

    def import_existing_workspace_files_if_empty(self) -> DesktopWorkspaceFilesSnapshot:
        snapshot = self.load_snapshot()
        importable_files = sorted(
            _iter_existing_workspace_import_files(self.workspace.vault_root),
            key=lambda item: item[0],
        )
        import_paths = [relative_path for relative_path, _ in importable_files]
        if not _should_replace_local_import_filemap(snapshot.document, import_paths):
            change_set = detect_local_workspace_changes(
                self.workspace.vault_root,
                snapshot.document,
            )
            untracked_changes = [change for change in change_set.changes if change.kind == "untracked"]
            if not untracked_changes:
                return self.list_workspace_files()

            records = list(snapshot.document.files)
            existing_file_ids = {record.file_id for record in records}
            latest_updated_at = snapshot.document.updated_at
            for change in untracked_changes:
                relative_path = change.path
                file_id = self.file_id_builder(relative_path)
                while file_id in existing_file_ids:
                    file_id = str(uuid4())
                existing_file_ids.add(file_id)
                disk_path = _resolve_workspace_file_path(self.workspace.vault_root, relative_path)
                payload_size = disk_path.stat().st_size
                mtime_ms = disk_path.stat().st_mtime_ns // 1_000_000
                latest_updated_at = max(latest_updated_at, mtime_ms)
                mime_type = _infer_imported_workspace_mime_type(relative_path)
                records.append(
                    FileRecord(
                        file_id=file_id,
                        path=relative_path,
                        type=change.file_type,
                        status="active",
                        updated_at=mtime_ms,
                        meta={
                            "size": payload_size,
                            "mtime": mtime_ms,
                            **({} if mime_type is None else {"mime_type": mime_type}),
                        },
                    )
                )

            write_filemap_atomic(
                self.workspace.paths.filemap_path,
                snapshot.document.replace_files(records, updated_at=latest_updated_at),
            )
            self.rebuild_workspace_search_index()
            return self.list_workspace_files()

        records: list[FileRecord] = []
        for relative_path, disk_path in importable_files:
            payload_size = disk_path.stat().st_size
            mtime_ms = disk_path.stat().st_mtime_ns // 1_000_000
            mime_type = _infer_imported_workspace_mime_type(relative_path)
            records.append(
                FileRecord(
                    file_id=self.file_id_builder(relative_path),
                    path=relative_path,
                    type=_infer_imported_workspace_file_type(relative_path),
                    status="active",
                    updated_at=mtime_ms,
                    meta={
                        "size": payload_size,
                        "mtime": mtime_ms,
                        **({} if mime_type is None else {"mime_type": mime_type}),
                    },
                )
            )

        if not records:
            return self.list_workspace_files()

        updated_at = max(snapshot.document.updated_at, *(record.updated_at for record in records))
        write_filemap_atomic(
            self.workspace.paths.filemap_path,
            snapshot.document.replace_files(records, updated_at=updated_at),
        )
        self.rebuild_workspace_search_index()
        return self.list_workspace_files()

    def list_workspace_files(self) -> DesktopWorkspaceFilesSnapshot:
        snapshot = self.load_snapshot()
        files: list[DesktopWorkspaceFileEntry] = []
        for record in snapshot.document.sorted_files():
            disk_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            exists_on_disk = disk_path.exists() and disk_path.is_file()
            size_bytes = disk_path.stat().st_size if exists_on_disk else None
            files.append(
                DesktopWorkspaceFileEntry(
                    file_id=record.file_id,
                    path=record.path,
                    type=record.type,
                    status=record.status,
                    updated_at=record.updated_at,
                    exists_on_disk=exists_on_disk,
                    size_bytes=size_bytes,
                    content_hash=record.content_hash,
                    last_known_revision=record.last_known_revision,
                    conflict_source_file_id=record.conflict_source_file_id,
                )
            )
        return DesktopWorkspaceFilesSnapshot(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            files=files,
            total_count=len(files),
            active_count=sum(1 for item in files if item.status == "active"),
            missing_count=sum(1 for item in files if item.status == "active" and not item.exists_on_disk),
        )

    def load_workspace_file_content(self, file_id: str) -> DesktopWorkspaceFileContent:
        snapshot = self.load_snapshot()
        record = next((item for item in snapshot.document.files if item.file_id == file_id), None)
        if record is None:
            raise KeyError(f"file_id not found in workspace filemap: {file_id}")
        if record.status != "active":
            raise ValueError(f"workspace file is not active: {file_id}")
        content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
        if not content_path.exists() or not content_path.is_file():
            raise FileNotFoundError(f"workspace file content not found: {file_id}")
        payload = content_path.read_bytes()
        if len(payload) > _WORKSPACE_FILE_CONTENT_MAX_BYTES:
            raise ValueError(f"workspace file is too large to render: {file_id}")
        text, encoding = _decode_workspace_text(payload, file_id=file_id)
        stat = content_path.stat()
        return DesktopWorkspaceFileContent(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            file_id=record.file_id,
            path=record.path,
            type=record.type,
            status=record.status,
            updated_at=stat.st_mtime_ns // 1_000_000,
            size_bytes=len(payload),
            content_hash=_compute_content_hash(payload),
            tracked_content_hash=record.content_hash,
            text=text,
            encoding=encoding,
        )

    def load_workspace_file_blob(self, file_id: str) -> DesktopWorkspaceFileBlob:
        snapshot = self.load_snapshot()
        record = next((item for item in snapshot.document.files if item.file_id == file_id), None)
        if record is None:
            raise KeyError(f"file_id not found in workspace filemap: {file_id}")
        if record.status != "active":
            raise ValueError(f"workspace file is not active: {file_id}")
        content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
        if not content_path.exists() or not content_path.is_file():
            raise FileNotFoundError(f"workspace file content not found: {file_id}")
        payload = content_path.read_bytes()
        if len(payload) > _WORKSPACE_FILE_BLOB_MAX_BYTES:
            raise ValueError(f"workspace file is too large to preview: {file_id}")
        meta = record.meta or {}
        mime_type = meta.get("mime_type") if isinstance(meta.get("mime_type"), str) else None
        return DesktopWorkspaceFileBlob(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            file_id=record.file_id,
            path=record.path,
            type=record.type,
            status=record.status,
            size_bytes=len(payload),
            content_hash=_compute_content_hash(payload),
            content_base64=base64.b64encode(payload).decode("ascii"),
            mime_type=mime_type or _infer_imported_workspace_mime_type(record.path),
        )

    def _upsert_workspace_search_index_for_record(self, record: FileRecord) -> None:
        if record.status != "active" or not _is_search_indexable_type(record.type):
            self._delete_workspace_search_index_for_file(record.file_id)
            return
        content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
        if not content_path.exists() or not content_path.is_file():
            self._delete_workspace_search_index_for_file(record.file_id)
            return
        try:
            payload = content_path.read_bytes()
            if len(payload) > _WORKSPACE_FILE_CONTENT_MAX_BYTES:
                self._delete_workspace_search_index_for_file(record.file_id)
                return
            text, _ = _decode_workspace_text(payload, file_id=record.file_id)
        except (OSError, ValueError):
            self._delete_workspace_search_index_for_file(record.file_id)
            return
        with closing(self.workspace._open_connection()) as connection:
            upsert_search_index_entry(
                connection,
                self.vault_id,
                file_id=record.file_id,
                path=record.path,
                content=text,
            )

    def _delete_workspace_search_index_for_file(self, file_id: str) -> None:
        with closing(self.workspace._open_connection()) as connection:
            delete_search_index_entry(connection, self.vault_id, file_id=file_id)

    def _rewrite_workspace_note_links_for_rename(
        self,
        *,
        old_title: str,
        new_title: str,
        renamed_file_id: str,
    ) -> None:
        snapshot = self.load_snapshot()
        for record in snapshot.document.sorted_files():
            if record.status != "active" or record.type != "note" or record.file_id == renamed_file_id:
                continue
            content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            if not content_path.exists() or not content_path.is_file():
                continue
            try:
                payload = content_path.read_bytes()
                if len(payload) > _WORKSPACE_FILE_CONTENT_MAX_BYTES:
                    continue
                text, encoding = _decode_workspace_text(payload, file_id=record.file_id)
            except (OSError, ValueError):
                continue
            rewritten, rewrite_count = _rewrite_wiki_links(text, old_title, new_title)
            if rewrite_count == 0:
                continue
            _write_bytes_atomic(content_path, rewritten.encode(encoding))
            self._upsert_workspace_search_index_for_record(record)

    def write_workspace_file_content(self, file_id: str, text: str) -> DesktopWorkspaceFileContent:
        snapshot = self.load_snapshot()
        record = next((item for item in snapshot.document.files if item.file_id == file_id), None)
        if record is None:
            raise KeyError(f"file_id not found in workspace filemap: {file_id}")
        if record.status != "active":
            raise ValueError(f"workspace file is not active: {file_id}")
        content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
        if not content_path.exists() or not content_path.is_file():
            raise FileNotFoundError(f"workspace file content not found: {file_id}")
        _, encoding = _decode_workspace_text(content_path.read_bytes(), file_id=file_id)
        _write_bytes_atomic(content_path, text.encode(encoding))
        self.clear_workspace_file_draft(file_id)
        self._upsert_workspace_search_index_for_record(record)
        return self.load_workspace_file_content(file_id)

    def _upsert_generated_ai_file_records(
        self,
        *,
        document: FileMapDocument,
        generated_paths: Iterable[str],
        written_paths: Iterable[str],
        updated_at: int,
    ) -> FileMapDocument:
        generated_path_set = set(generated_paths)
        written_path_set = set(written_paths)
        records: list[FileRecord] = []
        seen_paths: set[str] = set()
        for record in document.files:
            if record.path not in generated_path_set:
                records.append(record)
                continue
            seen_paths.add(record.path)
            if record.path not in written_path_set:
                records.append(record)
                continue
            disk_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            payload_size = disk_path.stat().st_size if disk_path.exists() and disk_path.is_file() else 0
            records.append(
                FileRecord(
                    file_id=record.file_id,
                    path=record.path,
                    type=_infer_imported_workspace_file_type(record.path),
                    status="active",
                    updated_at=updated_at,
                    content_hash=None,
                    last_known_revision=record.last_known_revision,
                    meta={
                        "ai_generated": True,
                        "size": payload_size,
                        "mtime": updated_at,
                        "mime_type": "text/markdown",
                    },
                )
            )

        existing_file_ids = {record.file_id for record in records}
        for path in sorted(generated_path_set - seen_paths):
            file_id = self.file_id_builder(path)
            while file_id in existing_file_ids:
                file_id = str(uuid4())
            disk_path = _resolve_workspace_file_path(self.workspace.vault_root, path)
            payload_size = disk_path.stat().st_size if disk_path.exists() and disk_path.is_file() else 0
            records.append(
                FileRecord(
                    file_id=file_id,
                    path=path,
                    type=_infer_imported_workspace_file_type(path),
                    status="active",
                    updated_at=updated_at,
                    meta={
                        "ai_generated": True,
                        "size": payload_size,
                        "mtime": updated_at,
                        "mime_type": "text/markdown",
                    },
                )
            )
            existing_file_ids.add(file_id)

        return document.replace_files(records, updated_at=updated_at)

    def compile_ai_wiki(self, *, now_ms: Optional[int] = None) -> DesktopAiWikiCompileResult:
        from ai_core import SourceNote, compile_ai_wiki

        compiled_at = now_ms if now_ms is not None else _current_time_ms()
        generated_at = datetime.fromtimestamp(compiled_at / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        snapshot = self.load_snapshot()
        source_notes: list[SourceNote] = []
        for record in snapshot.document.sorted_files():
            if record.status != "active" or record.type != "note":
                continue
            content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            if not content_path.exists() or not content_path.is_file():
                continue
            payload = content_path.read_bytes()
            if len(payload) > _WORKSPACE_FILE_CONTENT_MAX_BYTES:
                continue
            text, _ = _decode_workspace_text(payload, file_id=record.file_id)
            source_notes.append(
                SourceNote(
                    file_id=record.file_id,
                    path=record.path,
                    text=text,
                    content_hash=_compute_content_hash(payload),
                )
            )

        compiled = compile_ai_wiki(source_notes, generated_at=generated_at)
        generated_paths = [compiled.index_path, *(artifact.path for artifact in compiled.artifacts)]
        written_paths: list[str] = []
        skipped_pages: list[DesktopAiWikiSkippedPage] = []
        _write_bytes_atomic(
            _resolve_workspace_file_path(self.workspace.vault_root, compiled.index_path),
            compiled.index_text.encode("utf-8"),
        )
        written_paths.append(compiled.index_path)
        for artifact in compiled.artifacts:
            artifact_path = _resolve_workspace_file_path(self.workspace.vault_root, artifact.path)
            existing_fields = (
                _parse_markdown_frontmatter(artifact_path.read_text(encoding="utf-8"))
                if artifact_path.exists() and artifact_path.is_file()
                else {}
            )
            if existing_fields.get("locked", "").lower() == "true":
                skipped_pages.append(
                    DesktopAiWikiSkippedPage(
                        path=artifact.path,
                        reason="locked",
                        source_path=artifact.source_path,
                    )
                )
                continue
            if existing_fields.get("user_edited", "").lower() == "true":
                skipped_pages.append(
                    DesktopAiWikiSkippedPage(
                        path=artifact.path,
                        reason="user_edited",
                        source_path=artifact.source_path,
                    )
                )
                continue
            if existing_fields.get("source_content_hash") == artifact.source_content_hash:
                skipped_pages.append(
                    DesktopAiWikiSkippedPage(
                        path=artifact.path,
                        reason="unchanged",
                        source_path=artifact.source_path,
                    )
                )
                continue
            _write_bytes_atomic(artifact_path, artifact.text.encode("utf-8"))
            written_paths.append(artifact.path)

        log_path = _resolve_workspace_file_path(self.workspace.vault_root, ".ai/log.md")
        log_lines = [
            f"## {generated_at}",
            "",
            f"- Sources: {compiled.source_count}",
            f"- Pages: {compiled.artifact_count}",
            f"- Written: {len(written_paths)}",
            f"- Skipped: {len(skipped_pages)}",
            "",
        ]
        if skipped_pages:
            log_lines.append("### Skipped")
            log_lines.append("")
            log_lines.extend(
                f"- `{item.path}`: {item.reason}{'' if item.source_path is None else f' ({item.source_path})'}"
                for item in skipped_pages
            )
            log_lines.append("")
        existing_log = log_path.read_text(encoding="utf-8") if log_path.exists() and log_path.is_file() else "# AI Compile Log\n\n"
        _write_bytes_atomic(log_path, (existing_log.rstrip() + "\n\n" + "\n".join(log_lines)).encode("utf-8"))

        document = self._upsert_generated_ai_file_records(
            document=snapshot.document,
            generated_paths=generated_paths,
            written_paths=written_paths,
            updated_at=compiled_at,
        )
        write_filemap_atomic(self.workspace.paths.filemap_path, document)
        self.rebuild_workspace_search_index()
        files_snapshot = self.list_workspace_files()
        return DesktopAiWikiCompileResult(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            generated_at=generated_at,
            source_count=compiled.source_count,
            artifact_count=compiled.artifact_count,
            written_count=len(written_paths),
            skipped_count=len(skipped_pages),
            index_path=compiled.index_path,
            artifacts=[
                DesktopAiWikiArtifact(
                    title=artifact.title,
                    path=artifact.path,
                    source_file_id=artifact.source_file_id,
                    source_path=artifact.source_path,
                    source_content_hash=artifact.source_content_hash,
                )
                for artifact in compiled.artifacts
            ],
            skipped=skipped_pages,
            files=files_snapshot,
        )

    def answer_ai_wiki(self, question: str, *, limit: int = 5) -> DesktopAiWikiAnswerResult:
        from ai_core import AiWikiPage, answer_ai_wiki

        snapshot = self.load_snapshot()
        pages: list[AiWikiPage] = []
        for record in snapshot.document.sorted_files():
            if record.status != "active" or record.type != "ai_wiki":
                continue
            content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            if not content_path.exists() or not content_path.is_file():
                continue
            payload = content_path.read_bytes()
            if len(payload) > _WORKSPACE_FILE_CONTENT_MAX_BYTES:
                continue
            text, _ = _decode_workspace_text(payload, file_id=record.file_id)
            frontmatter = _parse_markdown_frontmatter(text)
            pages.append(
                AiWikiPage(
                    file_id=record.file_id,
                    path=record.path,
                    title=frontmatter.get("title") or _note_title_from_path(record.path),
                    text=text,
                )
            )

        answer = answer_ai_wiki(question, pages, limit=limit)
        model_status = answer.model_status
        answer_text = answer.answer
        provider_config = self._load_configured_ai_provider()
        if provider_config is not None and answer.citations:
            try:
                answer_text = self._answer_ai_wiki_with_provider(
                    provider_config,
                    question=answer.question,
                    citations=[
                        DesktopAiWikiAnswerCitation(
                            file_id=citation.file_id,
                            path=citation.path,
                            title=citation.title,
                            excerpt=citation.excerpt,
                            score=citation.score,
                        )
                        for citation in answer.citations
                    ],
                )
                model_status = f"{provider_config.provider_api}:{provider_config.model}"
            except Exception:
                model_status = "openai_compatible_error_fallback"
        elif provider_config is not None and not answer.citations:
            model_status = f"{provider_config.provider_api}:{provider_config.model}:no_citations"
        return DesktopAiWikiAnswerResult(
            schema_version=answer.schema_version,
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            question=answer.question,
            answer=answer_text,
            citation_count=answer.citation_count,
            citations=[
                DesktopAiWikiAnswerCitation(
                    file_id=citation.file_id,
                    path=citation.path,
                    title=citation.title,
                    excerpt=citation.excerpt,
                    score=citation.score,
                )
                for citation in answer.citations
            ],
            model_status=model_status,
        )

    def run_ai_context_task(
        self,
        *,
        context_type: str,
        instruction: str,
        file_ids: Optional[Iterable[str]] = None,
        folder_path: Optional[str] = None,
        recursive: bool = True,
        max_files: Optional[int] = None,
        max_chars_per_file: Optional[int] = None,
        max_total_chars: Optional[int] = None,
    ) -> DesktopAiContextTaskResult:
        normalized_instruction = instruction.strip()
        if not normalized_instruction:
            raise ValueError("AI context task instruction is required")
        limits = self._resolve_ai_context_limits(
            max_files=max_files,
            max_chars_per_file=max_chars_per_file,
            max_total_chars=max_total_chars,
        )
        source_records = self._resolve_ai_context_records(
            context_type=context_type,
            file_ids=file_ids,
            folder_path=folder_path,
            recursive=recursive,
            max_files=limits["max_files"],
        )
        sources, skipped_count, included_chars, selection_truncated = self._load_ai_context_sources(
            source_records,
            instruction=normalized_instruction,
            max_chars_per_file=limits["max_chars_per_file"],
            max_total_chars=limits["max_total_chars"],
        )
        included_file_count = len({source.file_id for source in sources})
        truncated = skipped_count > 0 or selection_truncated
        truncation = DesktopAiContextTaskTruncation(
            max_files=limits["max_files"],
            max_chars_per_file=limits["max_chars_per_file"],
            max_total_chars=limits["max_total_chars"],
            included_file_count=included_file_count,
            skipped_file_count=skipped_count,
            included_chars=included_chars,
            truncated=truncated,
            note=(
                "Temporary configurable guardrail for v1; not a product standard. "
                "Future versions should derive the budget from the selected model context window."
            ),
        )
        if not sources:
            model_status = "no_context_sources"
            answer_text = "No eligible Markdown documents were added to the AI context."
        else:
            provider_config = self._load_configured_ai_provider()
            citations = [
                DesktopAiWikiAnswerCitation(
                    file_id=source.file_id,
                    path=source.path,
                    title=source.title,
                    excerpt=source.excerpt,
                    score=index,
                )
                for index, source in enumerate(sources, start=1)
            ]
            if provider_config is None:
                model_status = "not_configured_context_preview"
                answer_text = self._local_ai_context_fallback_answer(normalized_instruction, sources)
            else:
                answer_text = self._answer_ai_wiki_with_provider(
                    provider_config,
                    question=normalized_instruction,
                    citations=citations,
                )
                model_status = f"{provider_config.provider_api}:{provider_config.model}"
        return DesktopAiContextTaskResult(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            context_type=context_type,
            instruction=normalized_instruction,
            answer=answer_text,
            source_count=len(sources),
            sources=sources,
            model_status=model_status,
            truncation=truncation,
        )

    def preview_ai_writeback(
        self,
        request: Mapping[str, Any],
        *,
        now_ms: Optional[int] = None,
    ) -> DesktopAiWritebackPreview:
        parsed = self._parse_ai_writeback_request(request, require_confirmation=False)
        now = now_ms if now_ms is not None else _current_time_ms()
        if parsed["mode"] == "insert_current_note":
            return self._preview_ai_writeback_insert_current_note(parsed, now_ms=now)
        if parsed["mode"] == "create_note":
            return self._preview_ai_writeback_create_note(parsed, now_ms=now)
        raise ValueError(f"ai_writeback_invalid_request: unsupported AI writeback mode: {parsed['mode']}")

    def apply_ai_writeback(
        self,
        request: Mapping[str, Any],
        *,
        now_ms: Optional[int] = None,
    ) -> DesktopAiWritebackApplyResult:
        parsed = self._parse_ai_writeback_request(request, require_confirmation=True)
        token_payload = _parse_ai_writeback_confirmation_token(parsed["confirmation_token"])
        now = now_ms if now_ms is not None else _current_time_ms()
        if now > token_payload["expires_at_ms"]:
            raise ValueError("ai_writeback_missing_confirmation: confirmation token expired")
        if token_payload["mode"] != parsed["mode"] or token_payload["idempotency_key"] != parsed["idempotency_key"]:
            raise ValueError("ai_writeback_missing_confirmation: confirmation token does not match request")
        if parsed["mode"] == "insert_current_note":
            return self._apply_ai_writeback_insert_current_note(parsed, token_payload)
        if parsed["mode"] == "create_note":
            return self._apply_ai_writeback_create_note(parsed, token_payload, now_ms=now)
        raise ValueError(f"ai_writeback_invalid_request: unsupported AI writeback mode: {parsed['mode']}")

    def _preview_ai_writeback_insert_current_note(
        self,
        parsed: Mapping[str, Any],
        *,
        now_ms: int,
    ) -> DesktopAiWritebackPreview:
        target_key = f"current_note:{parsed['file_id']}"
        _, target_path, base_text, protected_texts = self._load_ai_writeback_current_note_base(parsed["file_id"])
        self._ensure_ai_writeback_target_unlocked(protected_texts)
        rendered_markdown = self._render_ai_writeback_insert_current_note_block(
            parsed["answer_markdown"],
            parsed["sources"],
            now_ms=now_ms,
        )
        after_text = self._append_ai_writeback_block(base_text, rendered_markdown)
        before_hash = _compute_content_hash(base_text.encode("utf-8"))
        after_hash = _compute_content_hash(after_text.encode("utf-8"))
        expires_at_ms = now_ms + _AI_WRITEBACK_PREVIEW_TTL_MS
        request_hash = self._ai_writeback_request_hash(parsed, target_key=target_key)
        confirmation_token = _build_ai_writeback_confirmation_token(
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            mode=parsed["mode"],
            target_key=target_key,
            before_hash=before_hash,
            after_hash=after_hash,
            request_hash=request_hash,
            idempotency_key=parsed["idempotency_key"],
            expires_at_ms=expires_at_ms,
        )
        warnings = []
        if not parsed["sources"]:
            warnings.append("source_free_output")
        return DesktopAiWritebackPreview(
            schema_version="v1",
            mode=parsed["mode"],
            target_path=target_path,
            before_hash=before_hash,
            after_hash=after_hash,
            confirmation_token=confirmation_token,
            expires_at_ms=expires_at_ms,
            rendered_markdown=rendered_markdown,
            diff=DesktopAiWritebackDiff(
                format="unified",
                text=self._unified_ai_writeback_diff(base_text, after_text, target_path=target_path),
            ),
            warnings=warnings,
        )

    def _preview_ai_writeback_create_note(
        self,
        parsed: Mapping[str, Any],
        *,
        now_ms: int,
    ) -> DesktopAiWritebackPreview:
        target_path = self._resolve_ai_writeback_create_note_target_path(parsed, now_ms=now_ms)
        target_key = f"new_note:{target_path}"
        rendered_markdown = self._render_ai_writeback_create_note_text(parsed, target_path=target_path, now_ms=now_ms)
        after_hash = _compute_content_hash(rendered_markdown.encode("utf-8"))
        expires_at_ms = now_ms + _AI_WRITEBACK_PREVIEW_TTL_MS
        request_hash = self._ai_writeback_request_hash(parsed, target_key=target_key)
        confirmation_token = _build_ai_writeback_confirmation_token(
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            mode=parsed["mode"],
            target_key=target_key,
            before_hash=None,
            after_hash=after_hash,
            request_hash=request_hash,
            idempotency_key=parsed["idempotency_key"],
            expires_at_ms=expires_at_ms,
        )
        warnings = []
        if not parsed["sources"]:
            warnings.append("source_free_output")
        return DesktopAiWritebackPreview(
            schema_version="v1",
            mode=parsed["mode"],
            target_path=target_path,
            before_hash=None,
            after_hash=after_hash,
            confirmation_token=confirmation_token,
            expires_at_ms=expires_at_ms,
            rendered_markdown=rendered_markdown,
            diff=DesktopAiWritebackDiff(
                format="unified",
                text=self._unified_ai_writeback_diff("", rendered_markdown, target_path=target_path),
            ),
            warnings=warnings,
        )

    def _verify_ai_writeback_confirmation(
        self,
        parsed: Mapping[str, Any],
        token_payload: Mapping[str, Any],
        *,
        target_key: str,
    ) -> str:
        request_hash = self._ai_writeback_request_hash(parsed, target_key=target_key)
        if (
            token_payload["target_key"] != target_key
            or token_payload["request_hash"] != request_hash
        ):
            raise ValueError("ai_writeback_missing_confirmation: confirmation token does not match request")
        expected_signature = hashlib.sha256(
            _stable_json_text(
                _ai_writeback_signature_payload(
                    vault_id=self.vault_id,
                    device_id=self.config.device_id,
                    mode=parsed["mode"],
                    target_key=target_key,
                    before_hash=token_payload["before_hash"],
                    after_hash=token_payload["after_hash"],
                    request_hash=request_hash,
                    idempotency_key=parsed["idempotency_key"],
                    expires_at_ms=token_payload["expires_at_ms"],
                )
            ).encode("utf-8")
        ).hexdigest()
        if token_payload["signature"] != expected_signature:
            raise ValueError("ai_writeback_missing_confirmation: confirmation token signature mismatch")
        return request_hash

    def _apply_ai_writeback_insert_current_note(
        self,
        parsed: Mapping[str, Any],
        token_payload: Mapping[str, Any],
    ) -> DesktopAiWritebackApplyResult:
        target_key = f"current_note:{parsed['file_id']}"
        self._verify_ai_writeback_confirmation(parsed, token_payload, target_key=target_key)
        _, target_path, base_text, protected_texts = self._load_ai_writeback_current_note_base(parsed["file_id"])
        current_hash = _compute_content_hash(base_text.encode("utf-8"))
        if current_hash == token_payload["after_hash"]:
            return DesktopAiWritebackApplyResult(
                schema_version="v1",
                mode=parsed["mode"],
                status="already_applied",
                file_id=parsed["file_id"],
                path=target_path,
                content_hash=current_hash,
                wrote_draft=False,
                wrote_file=False,
                search_index_refreshed=False,
                requires_user_save=True,
            )

        self._ensure_ai_writeback_target_unlocked(protected_texts)
        if current_hash != token_payload["before_hash"]:
            raise ValueError("ai_writeback_base_changed: target draft or file changed after preview")

        preview_now_ms = int(token_payload["expires_at_ms"]) - _AI_WRITEBACK_PREVIEW_TTL_MS
        rendered_markdown = self._render_ai_writeback_insert_current_note_block(
            parsed["answer_markdown"],
            parsed["sources"],
            now_ms=preview_now_ms,
        )
        after_text = self._append_ai_writeback_block(base_text, rendered_markdown)
        after_hash = _compute_content_hash(after_text.encode("utf-8"))
        if after_hash != token_payload["after_hash"]:
            raise ValueError("ai_writeback_missing_confirmation: confirmation token does not match rendered output")

        self.write_workspace_file_draft(parsed["file_id"], after_text)
        return DesktopAiWritebackApplyResult(
            schema_version="v1",
            mode=parsed["mode"],
            status="applied",
            file_id=parsed["file_id"],
            path=target_path,
            content_hash=after_hash,
            wrote_draft=True,
            wrote_file=False,
            search_index_refreshed=False,
            requires_user_save=True,
        )

    def _apply_ai_writeback_create_note(
        self,
        parsed: Mapping[str, Any],
        token_payload: Mapping[str, Any],
        *,
        now_ms: int,
    ) -> DesktopAiWritebackApplyResult:
        target_key = str(token_payload["target_key"])
        if not target_key.startswith("new_note:"):
            raise ValueError("ai_writeback_missing_confirmation: confirmation token target mismatch")
        target_path = target_key.removeprefix("new_note:")
        if _normalize_ai_writeback_new_note_path(target_path) != target_path:
            raise ValueError("ai_writeback_missing_confirmation: confirmation token target mismatch")
        self._verify_ai_writeback_confirmation(parsed, token_payload, target_key=target_key)
        preview_now_ms = int(token_payload["expires_at_ms"]) - _AI_WRITEBACK_PREVIEW_TTL_MS
        rendered_markdown = self._render_ai_writeback_create_note_text(
            parsed,
            target_path=target_path,
            now_ms=preview_now_ms,
        )
        after_hash = _compute_content_hash(rendered_markdown.encode("utf-8"))
        if after_hash != token_payload["after_hash"]:
            raise ValueError("ai_writeback_missing_confirmation: confirmation token does not match rendered output")
        content_path = _resolve_workspace_file_path(self.workspace.vault_root, target_path)
        if content_path.exists():
            if content_path.is_file() and _compute_content_hash(content_path.read_bytes()) == after_hash:
                record = self._workspace_file_record_for_path(target_path)
                if record is not None:
                    return DesktopAiWritebackApplyResult(
                        schema_version="v1",
                        mode=parsed["mode"],
                        status="already_applied",
                        file_id=record.file_id,
                        path=target_path,
                        content_hash=after_hash,
                        wrote_draft=False,
                        wrote_file=False,
                        search_index_refreshed=False,
                        requires_user_save=False,
                    )
            raise ValueError("ai_writeback_base_changed: target note path changed after preview")
        created = self.create_workspace_note(
            target_path,
            text=rendered_markdown,
            now_ms=now_ms,
        )
        return DesktopAiWritebackApplyResult(
            schema_version="v1",
            mode=parsed["mode"],
            status="applied",
            file_id=created.file.file_id,
            path=target_path,
            content_hash=after_hash,
            wrote_draft=False,
            wrote_file=True,
            search_index_refreshed=True,
            requires_user_save=False,
        )

    def _parse_ai_writeback_request(self, request: Mapping[str, Any], *, require_confirmation: bool) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise ValueError("ai_writeback_invalid_request: request must be an object")
        if request.get("schema_version") != "v1":
            raise ValueError("ai_writeback_invalid_request: schema_version must be v1")
        mode = request.get("mode")
        if mode not in {"insert_current_note", "create_note"}:
            raise ValueError(f"ai_writeback_invalid_request: unsupported AI writeback mode: {mode}")
        idempotency_key = request.get("idempotency_key")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("ai_writeback_invalid_request: idempotency_key is required")
        answer_markdown = request.get("answer_markdown")
        if not isinstance(answer_markdown, str) or not answer_markdown.strip():
            raise ValueError("ai_writeback_invalid_request: answer_markdown is required")
        target = request.get("target")
        if not isinstance(target, Mapping):
            raise ValueError("ai_writeback_invalid_request: target is required")
        sources = self._parse_ai_writeback_sources(request.get("sources", []))
        confirmation_token = request.get("confirmation_token")
        if require_confirmation and (not isinstance(confirmation_token, str) or not confirmation_token.strip()):
            raise ValueError("ai_writeback_missing_confirmation: confirmation_token is required")
        instruction = request.get("instruction")
        parsed = {
            "schema_version": "v1",
            "mode": mode,
            "idempotency_key": idempotency_key.strip(),
            "answer_markdown": answer_markdown.strip(),
            "instruction": instruction if isinstance(instruction, str) else "",
            "sources": sources,
            "confirmation_token": confirmation_token.strip() if isinstance(confirmation_token, str) else "",
        }
        if mode == "insert_current_note":
            if target.get("type") != "current_note":
                raise ValueError("ai_writeback_invalid_request: target.type must be current_note")
            if target.get("insert_position") != "append":
                raise ValueError("ai_writeback_invalid_request: insert_position must be append")
            file_id = target.get("file_id")
            if not isinstance(file_id, str) or not file_id:
                raise ValueError("ai_writeback_invalid_request: target.file_id is required")
            parsed["file_id"] = file_id
            return parsed
        if target.get("type") != "new_note":
            raise ValueError("ai_writeback_invalid_request: target.type must be new_note")
        target_path = target.get("target_path")
        title = target.get("title")
        folder_path = target.get("folder_path")
        parsed["target_path"] = target_path if isinstance(target_path, str) and target_path.strip() else None
        parsed["title"] = title if isinstance(title, str) and title.strip() else None
        parsed["folder_path"] = folder_path if isinstance(folder_path, str) and folder_path.strip() else None
        return parsed

    def _parse_ai_writeback_sources(self, value: Any) -> list[DesktopAiWritebackSource]:
        if not isinstance(value, list):
            raise ValueError("ai_writeback_invalid_request: sources must be an array")
        sources: list[DesktopAiWritebackSource] = []
        for item in value:
            if not isinstance(item, Mapping):
                raise ValueError("ai_writeback_invalid_request: sources must contain objects")
            file_id = item.get("file_id")
            path = item.get("path")
            if not isinstance(file_id, str) or not file_id or not isinstance(path, str) or not path:
                raise ValueError("ai_writeback_invalid_request: each source requires file_id and path")
            title = item.get("title")
            excerpt = item.get("excerpt")
            sources.append(
                DesktopAiWritebackSource(
                    file_id=file_id,
                    path=path,
                    title=title if isinstance(title, str) and title else None,
                    excerpt=excerpt if isinstance(excerpt, str) and excerpt else None,
                )
            )
        return sources

    def _load_ai_writeback_current_note_base(self, file_id: str) -> tuple[FileRecord, str, str, list[str]]:
        snapshot = self.load_snapshot()
        record = next((item for item in snapshot.document.files if item.file_id == file_id), None)
        if record is None:
            raise KeyError(f"file_id not found in workspace filemap: {file_id}")
        if record.status != "active":
            raise ValueError(f"workspace file is not active: {file_id}")
        if record.type != "note":
            raise ValueError("ai_writeback_invalid_request: insert_current_note target must be a note")
        content = self.load_workspace_file_content(file_id)
        draft = self.load_workspace_file_draft(file_id)
        base_text = draft.text if draft.has_draft and draft.text is not None else content.text
        protected_texts = [content.text]
        if draft.has_draft and draft.text is not None and draft.text != content.text:
            protected_texts.append(draft.text)
        return record, record.path, base_text, protected_texts

    def _workspace_file_record_for_path(self, path: str) -> Optional[FileRecord]:
        snapshot = self.load_snapshot()
        return next((record for record in snapshot.document.files if record.status == "active" and record.path == path), None)

    def _resolve_ai_writeback_create_note_target_path(self, parsed: Mapping[str, Any], *, now_ms: int) -> str:
        requested_path = parsed.get("target_path")
        if isinstance(requested_path, str) and requested_path:
            normalized = _normalize_ai_writeback_new_note_path(requested_path)
        else:
            title = parsed.get("title") if isinstance(parsed.get("title"), str) else ""
            if not title:
                title = parsed.get("instruction") if isinstance(parsed.get("instruction"), str) else ""
            safe_title = _safe_ai_writeback_note_title(title or "AI Note")
            folder = parsed.get("folder_path") if isinstance(parsed.get("folder_path"), str) else ""
            normalized_folder = _normalize_ai_context_folder_path(folder or "AI Notes")
            if not normalized_folder:
                normalized_folder = "AI Notes"
            date_prefix = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            normalized = _normalize_ai_writeback_new_note_path(f"{normalized_folder}/{date_prefix} {safe_title}.md")
        return self._next_available_ai_writeback_note_path(normalized)

    def _next_available_ai_writeback_note_path(self, normalized_path: str) -> str:
        snapshot = self.load_snapshot()
        active_paths = {record.path for record in snapshot.document.files if record.status != "deleted"}
        base_path = PurePosixPath(normalized_path)
        stem = base_path.stem
        suffix = base_path.suffix or ".md"
        parent = base_path.parent
        index = 1
        while True:
            candidate = normalized_path if index == 1 else (parent / f"{stem}-{index}{suffix}").as_posix()
            content_path = _resolve_workspace_file_path(self.workspace.vault_root, candidate)
            if candidate not in active_paths and not content_path.exists():
                return candidate
            index += 1

    def _ensure_ai_writeback_target_unlocked(self, texts: Iterable[str]) -> None:
        if any(_markdown_frontmatter_flag(text, "locked") for text in texts):
            raise ValueError("ai_writeback_target_locked: target has locked frontmatter")

    def _ai_writeback_request_hash(self, parsed: Mapping[str, Any], *, target_key: str) -> str:
        payload = {
            "schema_version": "v1",
            "mode": parsed["mode"],
            "target_key": target_key,
            "idempotency_key": parsed["idempotency_key"],
            "instruction": parsed["instruction"],
            "answer_markdown": parsed["answer_markdown"],
            "file_id": parsed.get("file_id"),
            "target_path": parsed.get("target_path"),
            "title": parsed.get("title"),
            "folder_path": parsed.get("folder_path"),
            "sources": [
                {
                    "file_id": source.file_id,
                    "path": source.path,
                    "title": source.title,
                    "excerpt": source.excerpt,
                }
                for source in parsed["sources"]
            ],
        }
        return hashlib.sha256(_stable_json_text(payload).encode("utf-8")).hexdigest()

    def _render_ai_writeback_insert_current_note_block(
        self,
        answer_markdown: str,
        sources: list[DesktopAiWritebackSource],
        *,
        now_ms: int,
    ) -> str:
        generated_at = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        lines = [
            f"## AI Generated - {generated_at}",
            "",
            answer_markdown.strip(),
        ]
        if sources:
            lines.extend(self._render_ai_writeback_source_lines(sources))
        return "\n".join(lines).rstrip() + "\n"

    def _render_ai_writeback_create_note_text(
        self,
        parsed: Mapping[str, Any],
        *,
        target_path: str,
        now_ms: int,
    ) -> str:
        created_at = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        title = parsed.get("title") if isinstance(parsed.get("title"), str) and parsed.get("title") else ""
        title = title or _note_title_from_path(target_path)
        sources = parsed["sources"]
        lines = [
            "---",
            "type: ai_generated_note",
            f"created_at: {created_at}",
            f"source_count: {len(sources)}",
            "user_edited: false",
            "locked: false",
            "---",
            "",
            f"# {title}",
            "",
            parsed["answer_markdown"].strip(),
        ]
        if sources:
            lines.extend(self._render_ai_writeback_source_lines(sources))
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_ai_writeback_source_lines(sources: list[DesktopAiWritebackSource]) -> list[str]:
        lines = ["", "### Sources", ""]
        for source in sources:
            title = source.title or _note_title_from_path(source.path)
            lines.append(f"- [[{title}]] (`{source.path}`)")
        return lines

    @staticmethod
    def _append_ai_writeback_block(base_text: str, rendered_markdown: str) -> str:
        if not base_text:
            return rendered_markdown
        separator = "" if base_text.endswith("\n\n") else ("\n" if base_text.endswith("\n") else "\n\n")
        return base_text + separator + rendered_markdown

    @staticmethod
    def _unified_ai_writeback_diff(before_text: str, after_text: str, *, target_path: str) -> str:
        return "".join(
            difflib.unified_diff(
                before_text.splitlines(keepends=True),
                after_text.splitlines(keepends=True),
                fromfile=f"a/{target_path}",
                tofile=f"b/{target_path}",
            )
        )

    def _resolve_ai_context_limits(
        self,
        *,
        max_files: Optional[int],
        max_chars_per_file: Optional[int],
        max_total_chars: Optional[int],
    ) -> dict[str, int]:
        return {
            "max_files": self._positive_int_setting("NOTEAPP_AI_CONTEXT_MAX_FILES", max_files, _AI_CONTEXT_DEFAULT_MAX_FILES),
            "max_chars_per_file": self._positive_int_setting(
                "NOTEAPP_AI_CONTEXT_MAX_CHARS_PER_FILE",
                max_chars_per_file,
                _AI_CONTEXT_DEFAULT_MAX_CHARS_PER_FILE,
            ),
            "max_total_chars": self._positive_int_setting(
                "NOTEAPP_AI_CONTEXT_MAX_TOTAL_CHARS",
                max_total_chars,
                _AI_CONTEXT_DEFAULT_MAX_TOTAL_CHARS,
            ),
        }

    @staticmethod
    def _positive_int_setting(env_name: str, explicit_value: Optional[int], default_value: int) -> int:
        if explicit_value is not None and explicit_value > 0:
            return explicit_value
        raw_value = os.environ.get(env_name, "").strip()
        if raw_value:
            try:
                parsed = int(raw_value)
            except ValueError:
                parsed = default_value
            if parsed > 0:
                return parsed
        return default_value

    def _resolve_ai_context_records(
        self,
        *,
        context_type: str,
        file_ids: Optional[Iterable[str]],
        folder_path: Optional[str],
        recursive: bool,
        max_files: int,
    ) -> list[FileRecord]:
        snapshot = self.load_snapshot()
        active_notes = [
            record
            for record in snapshot.document.sorted_files()
            if record.status == "active"
            and record.type == "note"
            and not _is_hidden_workspace_context_path(record.path)
        ]
        if context_type == "selected_files":
            requested_ids = list(dict.fromkeys(file_ids or []))
            by_id = {record.file_id: record for record in active_notes}
            return [by_id[file_id] for file_id in requested_ids if file_id in by_id][:max_files]
        if context_type == "folder":
            normalized_folder = _normalize_ai_context_folder_path(folder_path or "")
            return [
                record
                for record in active_notes
                if _record_belongs_to_ai_context_folder(record.path, normalized_folder, recursive=recursive)
            ][:max_files]
        raise ValueError(f"unsupported AI context type: {context_type}")

    def _load_ai_context_sources(
        self,
        records: list[FileRecord],
        *,
        instruction: str,
        max_chars_per_file: int,
        max_total_chars: int,
    ) -> tuple[list[DesktopAiContextTaskSource], int, int, bool]:
        chunks: list[_DesktopAiContextChunk] = []
        available_chunk_counts: dict[str, int] = {}
        for file_order, record in enumerate(records):
            content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            if not content_path.exists() or not content_path.is_file():
                continue
            payload = content_path.read_bytes()
            if len(payload) > _WORKSPACE_FILE_CONTENT_MAX_BYTES:
                continue
            text, _ = _decode_workspace_text(payload, file_id=record.file_id)
            normalized_text = _normalize_ai_context_text(text)
            if not normalized_text:
                continue
            chunk_texts = _split_ai_context_chunks(normalized_text, max_chars_per_chunk=max_chars_per_file)
            title = _note_title_from_path(record.path)
            original_chars = len(normalized_text)
            available_chunk_counts[record.file_id] = len(chunk_texts)
            for chunk_index, chunk_text in enumerate(chunk_texts):
                chunks.append(
                    _DesktopAiContextChunk(
                        file_id=record.file_id,
                        path=record.path,
                        title=title,
                        file_order=file_order,
                        chunk_index=chunk_index,
                        chunk_count=len(chunk_texts),
                        text=chunk_text,
                        original_chars=original_chars,
                    )
                )
        instruction_terms = _extract_ai_context_search_terms(instruction)
        ranked_chunks = sorted(
            chunks,
            key=lambda chunk: self._ai_context_chunk_sort_key(chunk, instruction_terms=instruction_terms),
        )
        sources: list[DesktopAiContextTaskSource] = []
        included_chars = 0
        included_file_ids: set[str] = set()
        selected_chunk_counts: dict[str, int] = {}
        selection_truncated = False
        for chunk in ranked_chunks:
            if included_chars >= max_total_chars:
                break
            remaining_chars = max_total_chars - included_chars
            excerpt = chunk.text[:remaining_chars]
            if not excerpt.strip():
                continue
            if len(excerpt) < len(chunk.text):
                selection_truncated = True
            included_chars += len(excerpt)
            included_file_ids.add(chunk.file_id)
            selected_chunk_counts[chunk.file_id] = selected_chunk_counts.get(chunk.file_id, 0) + 1
            title = chunk.title
            if chunk.chunk_count > 1:
                title = f"{title} [part {chunk.chunk_index + 1}/{chunk.chunk_count}]"
            sources.append(
                DesktopAiContextTaskSource(
                    file_id=chunk.file_id,
                    path=chunk.path,
                    title=title,
                    excerpt=excerpt,
                    included_chars=len(excerpt),
                    original_chars=chunk.original_chars,
                    truncated=len(excerpt) < len(chunk.text),
                )
            )
        skipped_count = max(0, len(records) - len(included_file_ids))
        selection_truncated = selection_truncated or any(
            selected_chunk_counts.get(file_id, 0) < available_chunk_counts.get(file_id, 0)
            for file_id in included_file_ids
        )
        return sources, skipped_count, included_chars, selection_truncated

    def _ai_context_chunk_sort_key(
        self,
        chunk: _DesktopAiContextChunk,
        *,
        instruction_terms: list[str],
    ) -> tuple[int, int, int, int, str]:
        body_text = chunk.text
        body_text_lower = body_text.lower()
        title_text = chunk.title
        title_text_lower = title_text.lower()
        heading_text = _ai_context_heading_text(body_text)
        heading_text_lower = heading_text.lower()
        score = 0
        hits = 0
        for term in instruction_terms:
            haystack = body_text_lower if term.isascii() else body_text
            title_haystack = title_text_lower if term.isascii() else title_text
            heading_haystack = heading_text_lower if term.isascii() else heading_text
            match_count = haystack.count(term)
            if match_count <= 0:
                if term in title_haystack:
                    score += max(4, len(term))
                    hits += 1
                continue
            hits += 1
            term_weight = min(len(term), 8)
            score += term_weight * min(match_count, 3)
            if term in title_haystack:
                score += max(4, term_weight)
            if term in heading_haystack:
                score += max(4, term_weight)
        if chunk.chunk_index == 0:
            score += 1
        return (-score, -hits, chunk.chunk_index, chunk.file_order, chunk.path)

    @staticmethod
    def _local_ai_context_fallback_answer(
        instruction: str,
        sources: list[DesktopAiContextTaskSource],
    ) -> str:
        lines = [
            "AI provider is not configured, so this is a local context preview.",
            "",
            f"Instruction: {instruction}",
            "",
            "Context sources:",
        ]
        for index, source in enumerate(sources, start=1):
            lines.append(f"{index}. {source.title} ({source.path})")
        lines.extend(
            [
                "",
                "Configure an AI Key in Settings > AI and click Test to generate a model answer from these sources.",
            ]
        )
        return "\n".join(lines)

    def _load_configured_ai_provider(self) -> Optional[DesktopAiProviderConfig]:
        return (
            _load_ai_provider_config_from_settings(self.workspace.paths.settings_path)
            or _load_ai_provider_config()
        )

    def check_ai_provider_health(self) -> DesktopAiProviderHealthResult:
        provider_config = self._load_configured_ai_provider()
        if provider_config is None:
            return DesktopAiProviderHealthResult(
                schema_version="v1",
                vault_id=self.vault_id,
                device_id=self.config.device_id,
                vault_root=self.workspace.vault_root,
                configured=False,
                status="not_configured",
                provider_api=None,
                base_url=None,
                model_id=None,
                message="AI provider is not configured. Choose a model and enter an AI Key in Settings.",
            )

        try:
            self._answer_ai_wiki_with_provider(
                provider_config,
                question="Health check. Reply with OK only.",
                citations=[
                    DesktopAiWikiAnswerCitation(
                        file_id="health-check",
                        path=".ai/health-check.md",
                        title="Health Check",
                        excerpt="This is a connectivity check for the configured AI provider.",
                        score=1,
                    )
                ],
            )
        except Exception as exc:
            return DesktopAiProviderHealthResult(
                schema_version="v1",
                vault_id=self.vault_id,
                device_id=self.config.device_id,
                vault_root=self.workspace.vault_root,
                configured=True,
                status="error",
                provider_api=provider_config.provider_api,
                base_url=provider_config.base_url,
                model_id=provider_config.model,
                message=str(exc),
            )

        return DesktopAiProviderHealthResult(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            configured=True,
            status="available",
            provider_api=provider_config.provider_api,
            base_url=provider_config.base_url,
            model_id=provider_config.model,
            message="AI provider responded successfully.",
        )

    def _answer_ai_wiki_with_provider(
        self,
        config: DesktopAiProviderConfig,
        *,
        question: str,
        citations: list[DesktopAiWikiAnswerCitation],
    ) -> str:
        if config.provider_api == "anthropic-messages":
            return self._answer_ai_wiki_with_anthropic_provider(config, question=question, citations=citations)
        if config.provider_api == "google-generative-ai":
            return self._answer_ai_wiki_with_google_provider(config, question=question, citations=citations)
        return self._answer_ai_wiki_with_openai_provider(config, question=question, citations=citations)

    def _ai_wiki_provider_prompt(
        self,
        *,
        question: str,
        citations: list[DesktopAiWikiAnswerCitation],
    ) -> str:
        context = "\n\n".join(
            f"[{index}] {citation.title}\nPath: {citation.path}\nExcerpt: {citation.excerpt}"
            for index, citation in enumerate(citations, start=1)
        )
        return f"Question:\n{question}\n\nCitations:\n{context}"

    def _answer_ai_wiki_with_openai_provider(
        self,
        config: DesktopAiProviderConfig,
        *,
        question: str,
        citations: list[DesktopAiWikiAnswerCitation],
    ) -> str:
        payload = {
            "model": config.model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You answer questions for a local-first personal knowledge base. "
                        "Use only the provided citations. If the citations are insufficient, say so. "
                        "Cite sources inline as [1], [2]."
                    ),
                },
                {
                    "role": "user",
                    "content": self._ai_wiki_provider_prompt(question=question, citations=citations),
                },
            ],
        }
        request = Request(
            f"{config.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "pkb-desktop-ai/0.1",
            },
            method="POST",
        )
        opener = self.ai_opener if self.ai_opener is not None else _default_ai_urlopen
        with closing(opener(request, config.timeout_seconds)) as response:
            status_code = response.getcode()
            body = response.read()
        if status_code < 200 or status_code >= 300:
            raise ValueError(f"AI provider returned unexpected status: {status_code}")
        payload = json.loads(body.decode("utf-8")) if body else {}
        if not isinstance(payload, dict):
            raise ValueError("AI provider response must be an object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("AI provider response missing choices")
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ValueError("AI provider choice must be an object")
        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("AI provider choice missing message")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("AI provider returned an empty answer")
        return content.strip()

    def _answer_ai_wiki_with_anthropic_provider(
        self,
        config: DesktopAiProviderConfig,
        *,
        question: str,
        citations: list[DesktopAiWikiAnswerCitation],
    ) -> str:
        payload = {
            "model": config.model,
            "max_tokens": 1200,
            "temperature": 0.2,
            "system": (
                "You answer questions for a local-first personal knowledge base. "
                "Use only the provided citations. If the citations are insufficient, say so. "
                "Cite sources inline as [1], [2]."
            ),
            "messages": [
                {
                    "role": "user",
                    "content": self._ai_wiki_provider_prompt(question=question, citations=citations),
                }
            ],
        }
        request = Request(
            f"{config.base_url.rstrip('/')}/v1/messages",
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "pkb-desktop-ai/0.1",
                "anthropic-version": "2023-06-01",
                "x-api-key": config.api_key,
            },
            method="POST",
        )
        payload = self._read_ai_json_response(request, config.timeout_seconds)
        content = payload.get("content")
        if not isinstance(content, list) or not content:
            raise ValueError("AI provider response missing content")
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                text_parts.append(item["text"])
        answer = "\n".join(part.strip() for part in text_parts if part.strip()).strip()
        if not answer:
            raise ValueError("AI provider returned an empty answer")
        return answer

    def _answer_ai_wiki_with_google_provider(
        self,
        config: DesktopAiProviderConfig,
        *,
        question: str,
        citations: list[DesktopAiWikiAnswerCitation],
    ) -> str:
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "You answer questions for a local-first personal knowledge base. "
                                "Use only the provided citations. If the citations are insufficient, say so. "
                                "Cite sources inline as [1], [2].\n\n"
                                + self._ai_wiki_provider_prompt(question=question, citations=citations)
                            ),
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
            },
        }
        request = Request(
            f"{config.base_url.rstrip('/')}/v1beta/models/{config.model}:generateContent",
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "pkb-desktop-ai/0.1",
                "x-goog-api-key": config.api_key,
            },
            method="POST",
        )
        payload = self._read_ai_json_response(request, config.timeout_seconds)
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("AI provider response missing candidates")
        first = candidates[0]
        if not isinstance(first, dict):
            raise ValueError("AI provider candidate must be an object")
        content = first.get("content")
        if not isinstance(content, dict):
            raise ValueError("AI provider candidate missing content")
        parts = content.get("parts")
        if not isinstance(parts, list):
            raise ValueError("AI provider candidate missing parts")
        text_parts = [item.get("text") for item in parts if isinstance(item, dict) and isinstance(item.get("text"), str)]
        answer = "\n".join(part.strip() for part in text_parts if part and part.strip()).strip()
        if not answer:
            raise ValueError("AI provider returned an empty answer")
        return answer

    def _read_ai_json_response(self, request: Request, timeout_seconds: float) -> dict[str, object]:
        opener = self.ai_opener if self.ai_opener is not None else _default_ai_urlopen
        with closing(opener(request, timeout_seconds)) as response:
            status_code = response.getcode()
            body = response.read()
        if status_code < 200 or status_code >= 300:
            raise ValueError(f"AI provider returned unexpected status: {status_code}")
        payload = json.loads(body.decode("utf-8")) if body else {}
        if not isinstance(payload, dict):
            raise ValueError("AI provider response must be an object")
        return payload

    def _workspace_file_entry_for_id(
        self,
        files_snapshot: DesktopWorkspaceFilesSnapshot,
        file_id: str,
    ) -> DesktopWorkspaceFileEntry:
        entry = next((item for item in files_snapshot.files if item.file_id == file_id), None)
        if entry is None:
            raise KeyError(f"file_id not found in workspace files snapshot: {file_id}")
        return entry

    def _workspace_trash_path_for_record(self, record: FileRecord) -> Path:
        return (
            self.workspace.paths.root
            / NOTEAPP_DIRNAME
            / _WORKSPACE_TRASH_DIRNAME
            / _safe_deleted_file_name(record.file_id, record.updated_at, record.path)
        )

    def _is_workspace_trash_purged(self, record: FileRecord) -> bool:
        return isinstance(record.meta, dict) and _WORKSPACE_TRASH_PURGED_META_KEY in record.meta

    def _mark_workspace_trash_purged(self, record: FileRecord, purged_at: int) -> FileRecord:
        meta = dict(record.meta or {})
        meta[_WORKSPACE_TRASH_PURGED_META_KEY] = purged_at
        return replace(record, meta=meta)

    def list_workspace_trash(self) -> DesktopWorkspaceTrashSnapshot:
        snapshot = self.load_snapshot()
        items: list[DesktopWorkspaceTrashItem] = []
        for record in snapshot.document.sorted_files():
            if record.status != "deleted":
                continue
            if self._is_workspace_trash_purged(record):
                continue
            trash_path = self._workspace_trash_path_for_record(record)
            exists_in_trash = trash_path.exists() and trash_path.is_file()
            items.append(
                DesktopWorkspaceTrashItem(
                    file_id=record.file_id,
                    path=record.path,
                    type=record.type,
                    deleted_at=record.updated_at,
                    trash_path=trash_path,
                    exists_in_trash=exists_in_trash,
                    size_bytes=trash_path.stat().st_size if exists_in_trash else None,
                )
            )
        return DesktopWorkspaceTrashSnapshot(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            trash_root=self.workspace.paths.root / NOTEAPP_DIRNAME / _WORKSPACE_TRASH_DIRNAME,
            items=items,
            total_count=len(items),
        )

    def restore_workspace_trash_item(
        self,
        file_id: str,
        *,
        now_ms: Optional[int] = None,
    ) -> DesktopWorkspaceFileMutationResult:
        snapshot = self.load_snapshot()
        record = next((item for item in snapshot.document.files if item.file_id == file_id), None)
        if record is None:
            raise KeyError(f"file_id not found in workspace filemap: {file_id}")
        if record.status != "deleted":
            raise ValueError(f"workspace file is not deleted: {file_id}")
        trash_path = self._workspace_trash_path_for_record(record)
        if not trash_path.exists() or not trash_path.is_file():
            raise FileNotFoundError(f"workspace trash file not found: {file_id}")
        restore_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
        if restore_path.exists():
            raise FileExistsError(f"workspace restore path already exists: {record.path}")
        restored_at = now_ms if now_ms is not None else _current_time_ms()
        restored_files = [
            (
                FileRecord(
                    file_id=item.file_id,
                    path=item.path,
                    type=item.type,
                    status="active",
                    updated_at=restored_at,
                    content_hash=item.content_hash,
                    last_known_revision=item.last_known_revision,
                    meta=item.meta,
                )
                if item.file_id == file_id
                else item
            )
            for item in snapshot.document.files
        ]
        restore_path.parent.mkdir(parents=True, exist_ok=True)
        trash_path.replace(restore_path)
        write_filemap_atomic(
            self.workspace.paths.filemap_path,
            snapshot.document.replace_files(restored_files, updated_at=restored_at),
        )
        restored_record = next(item for item in restored_files if item.file_id == file_id)
        self._upsert_workspace_search_index_for_record(restored_record)
        tombstones = [item for item in load_tombstone_ledger(self.workspace.paths.ledger_path) if item.file_id != file_id]
        rewrite_tombstone_ledger(self.workspace.paths.ledger_path, tombstones)
        files_snapshot = self.list_workspace_files()
        return DesktopWorkspaceFileMutationResult(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            operation="restore",
            file=self._workspace_file_entry_for_id(files_snapshot, file_id),
            files=files_snapshot,
        )

    def purge_workspace_trash_item(
        self,
        file_id: str,
        *,
        now_ms: Optional[int] = None,
    ) -> DesktopWorkspaceTrashSnapshot:
        snapshot = self.load_snapshot()
        record = next((item for item in snapshot.document.files if item.file_id == file_id), None)
        if record is None:
            raise KeyError(f"file_id not found in workspace filemap: {file_id}")
        if record.status != "deleted":
            raise ValueError(f"workspace file is not deleted: {file_id}")
        self._workspace_trash_path_for_record(record).unlink(missing_ok=True)
        purged_at = now_ms if now_ms is not None else _current_time_ms()
        purged_files = [
            self._mark_workspace_trash_purged(item, purged_at) if item.file_id == file_id else item
            for item in snapshot.document.files
        ]
        write_filemap_atomic(
            self.workspace.paths.filemap_path,
            snapshot.document.replace_files(purged_files, updated_at=purged_at),
        )
        return self.list_workspace_trash()

    def empty_workspace_trash(self, *, now_ms: Optional[int] = None) -> DesktopWorkspaceTrashSnapshot:
        snapshot = self.load_snapshot()
        purged_at = now_ms if now_ms is not None else _current_time_ms()
        purged_files: list[FileRecord] = []
        for record in snapshot.document.files:
            if record.status == "deleted" and not self._is_workspace_trash_purged(record):
                self._workspace_trash_path_for_record(record).unlink(missing_ok=True)
                purged_files.append(self._mark_workspace_trash_purged(record, purged_at))
            else:
                purged_files.append(record)
        write_filemap_atomic(
            self.workspace.paths.filemap_path,
            snapshot.document.replace_files(purged_files, updated_at=purged_at),
        )
        return self.list_workspace_trash()

    def create_workspace_note(
        self,
        path: str,
        *,
        text: str = "",
        now_ms: Optional[int] = None,
    ) -> DesktopWorkspaceFileMutationResult:
        normalized_path = _normalize_workspace_note_path(path)
        snapshot = self.load_snapshot()
        file_id = self.file_id_builder(normalized_path)
        existing_file_ids = {record.file_id for record in snapshot.document.files}
        while file_id in existing_file_ids:
            file_id = str(uuid4())
        content_path = _resolve_workspace_file_path(self.workspace.vault_root, normalized_path)
        if content_path.exists():
            raise FileExistsError(f"workspace note path already exists on disk: {normalized_path}")
        payload = text.encode("utf-8")
        created_at = now_ms if now_ms is not None else _current_time_ms()
        _write_bytes_atomic(content_path, payload)
        document = add_file(
            snapshot.document,
            file_id=file_id,
            path=normalized_path,
            type="note",
            updated_at=created_at,
            content_hash=None,
            last_known_revision=None,
        )
        write_filemap_atomic(self.workspace.paths.filemap_path, document)
        self._upsert_workspace_search_index_for_record(
            FileRecord(
                file_id=file_id,
                path=normalized_path,
                type="note",
                status="active",
                updated_at=created_at,
            )
        )
        files_snapshot = self.list_workspace_files()
        return DesktopWorkspaceFileMutationResult(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            operation="create",
            file=self._workspace_file_entry_for_id(files_snapshot, file_id),
            files=files_snapshot,
        )

    def create_workspace_attachment(
        self,
        file_name: str,
        payload: bytes,
        *,
        now_ms: Optional[int] = None,
    ) -> DesktopWorkspaceFileMutationResult:
        if not payload:
            raise ValueError("workspace attachment payload must be non-empty")
        snapshot = self.load_snapshot()
        normalized_path = _next_available_workspace_attachment_path(
            snapshot.document,
            file_name,
            self.workspace.vault_root,
        )
        file_id = self.file_id_builder(normalized_path)
        existing_file_ids = {record.file_id for record in snapshot.document.files}
        while file_id in existing_file_ids:
            file_id = str(uuid4())
        created_at = now_ms if now_ms is not None else _current_time_ms()
        content_path = _resolve_workspace_file_path(self.workspace.vault_root, normalized_path)
        _write_bytes_atomic(content_path, payload)
        mime_type = _infer_imported_workspace_mime_type(normalized_path)
        records = list(snapshot.document.files)
        records.append(
            FileRecord(
                file_id=file_id,
                path=normalized_path,
                type="attachment",
                status="active",
                updated_at=created_at,
                meta={
                    "size": len(payload),
                    "mtime": created_at,
                    **({} if mime_type is None else {"mime_type": mime_type}),
                },
            )
        )
        write_filemap_atomic(
            self.workspace.paths.filemap_path,
            snapshot.document.replace_files(records, updated_at=created_at),
        )
        files_snapshot = self.list_workspace_files()
        return DesktopWorkspaceFileMutationResult(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            operation="create_attachment",
            file=self._workspace_file_entry_for_id(files_snapshot, file_id),
            files=files_snapshot,
        )

    def rename_workspace_note(
        self,
        file_id: str,
        new_path: str,
        *,
        now_ms: Optional[int] = None,
    ) -> DesktopWorkspaceFileMutationResult:
        snapshot = self.load_snapshot()
        record = next((item for item in snapshot.document.files if item.file_id == file_id), None)
        if record is None:
            raise KeyError(f"file_id not found in workspace filemap: {file_id}")
        if record.status != "active":
            raise ValueError(f"workspace file is not active: {file_id}")
        if record.type != "note":
            raise ValueError(f"workspace file is not a note: {file_id}")
        normalized_path = _normalize_workspace_note_rename_path(record.path, new_path)
        source_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
        target_path = _resolve_workspace_file_path(self.workspace.vault_root, normalized_path)
        if not source_path.exists() or not source_path.is_file():
            raise FileNotFoundError(f"workspace file content not found: {file_id}")
        if target_path.exists() and target_path.resolve() != source_path.resolve():
            raise FileExistsError(f"workspace note path already exists on disk: {normalized_path}")
        updated_at = now_ms if now_ms is not None else _current_time_ms()
        old_title = _note_title_from_path(record.path)
        new_title = _note_title_from_path(normalized_path)
        document = rename_file(
            snapshot.document,
            file_id=file_id,
            new_path=normalized_path,
            updated_at=updated_at,
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.replace(target_path)
        if source_path.exists() and source_path.resolve() != target_path.resolve():
            raise RuntimeError(f"workspace rename left source file in place: {record.path}")
        if not target_path.exists() or not target_path.is_file():
            raise RuntimeError(f"workspace rename did not create target file: {normalized_path}")
        write_filemap_atomic(self.workspace.paths.filemap_path, document)
        if old_title.lower() != new_title.lower():
            self._rewrite_workspace_note_links_for_rename(
                old_title=old_title,
                new_title=new_title,
                renamed_file_id=file_id,
            )
        self._upsert_workspace_search_index_for_record(
            FileRecord(
                file_id=file_id,
                path=normalized_path,
                type=record.type,
                status=record.status,
                updated_at=updated_at,
            )
        )
        files_snapshot = self.list_workspace_files()
        return DesktopWorkspaceFileMutationResult(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            operation="rename",
            file=self._workspace_file_entry_for_id(files_snapshot, file_id),
            files=files_snapshot,
        )

    def move_workspace_note(
        self,
        file_id: str,
        target_path: str,
        *,
        now_ms: Optional[int] = None,
    ) -> DesktopWorkspaceFileMutationResult:
        snapshot = self.load_snapshot()
        record = next((item for item in snapshot.document.files if item.file_id == file_id), None)
        if record is None:
            raise KeyError(f"file_id not found in workspace filemap: {file_id}")
        if record.status != "active":
            raise ValueError(f"workspace file is not active: {file_id}")
        if record.type not in {"note", "attachment"}:
            raise ValueError(f"workspace file is not movable: {file_id}")
        normalized_path = _normalize_workspace_note_move_path(target_path)
        source_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
        target_file_path = _resolve_workspace_file_path(self.workspace.vault_root, normalized_path)
        if not source_path.exists() or not source_path.is_file():
            raise FileNotFoundError(f"workspace file content not found: {file_id}")

        source_resolved = source_path.resolve()
        target_resolved = target_file_path.resolve()
        if target_file_path.exists() and target_resolved != source_resolved:
            raise FileExistsError(f"workspace note path already exists on disk: {normalized_path}")

        updated_at = now_ms if now_ms is not None else _current_time_ms()
        old_title = _note_title_from_path(record.path)
        new_title = _note_title_from_path(normalized_path)
        document = rename_file(
            snapshot.document,
            file_id=file_id,
            new_path=normalized_path,
            updated_at=updated_at,
        )

        if target_resolved != source_resolved:
            target_file_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.replace(target_file_path)
            if source_path.exists():
                raise RuntimeError(f"workspace move left source file in place: {record.path}")
            if not target_file_path.exists() or not target_file_path.is_file():
                raise RuntimeError(f"workspace move did not create target file: {normalized_path}")

        write_filemap_atomic(self.workspace.paths.filemap_path, document)
        if old_title.lower() != new_title.lower():
            self._rewrite_workspace_note_links_for_rename(
                old_title=old_title,
                new_title=new_title,
                renamed_file_id=file_id,
            )
        self._upsert_workspace_search_index_for_record(
            FileRecord(
                file_id=file_id,
                path=normalized_path,
                type=record.type,
                status=record.status,
                updated_at=updated_at,
            )
        )
        self.rebuild_workspace_note_links()
        files_snapshot = self.list_workspace_files()
        return DesktopWorkspaceFileMutationResult(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            operation="move",
            file=self._workspace_file_entry_for_id(files_snapshot, file_id),
            files=files_snapshot,
        )

    def delete_workspace_note(
        self,
        file_id: str,
        *,
        now_ms: Optional[int] = None,
    ) -> DesktopWorkspaceFileMutationResult:
        snapshot = self.load_snapshot()
        record = next((item for item in snapshot.document.files if item.file_id == file_id), None)
        if record is None:
            raise KeyError(f"file_id not found in workspace filemap: {file_id}")
        if record.status != "active":
            raise ValueError(f"workspace file is not active: {file_id}")
        if record.type not in {"note", "attachment"}:
            raise ValueError(f"workspace file is not deletable: {file_id}")
        deleted_at = now_ms if now_ms is not None else _current_time_ms()
        local_delete_sequence = snapshot.state.local_delete_sequence + 1
        document, tombstone = mark_deleted(
            snapshot.document,
            file_id=file_id,
            deleted_at=deleted_at,
            local_delete_seq=local_delete_sequence,
            deleted_revision=None,
            deleted_by_device=self.config.device_id,
        )
        content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
        trash_path = (
            self.workspace.paths.root
            / NOTEAPP_DIRNAME
            / _WORKSPACE_TRASH_DIRNAME
            / _safe_deleted_file_name(file_id, deleted_at, record.path)
        )
        if content_path.exists() and content_path.is_file():
            trash_path.parent.mkdir(parents=True, exist_ok=True)
            content_path.replace(trash_path)
        self.clear_workspace_file_draft(file_id)
        write_filemap_atomic(self.workspace.paths.filemap_path, document)
        self._delete_workspace_search_index_for_file(file_id)
        append_tombstone(self.workspace.paths.ledger_path, tombstone)
        with closing(self.workspace._open_connection()) as connection:
            upsert_vault_state(
                connection,
                replace(snapshot.state, local_delete_sequence=local_delete_sequence),
            )
        files_snapshot = self.list_workspace_files()
        return DesktopWorkspaceFileMutationResult(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            operation="delete",
            file=self._workspace_file_entry_for_id(files_snapshot, file_id),
            files=files_snapshot,
        )

    def load_workspace_file_draft(self, file_id: str) -> DesktopWorkspaceFileDraft:
        snapshot = self.load_snapshot()
        record = next((item for item in snapshot.document.files if item.file_id == file_id), None)
        if record is None:
            raise KeyError(f"file_id not found in workspace filemap: {file_id}")
        draft_path = self.workspace.paths.root / NOTEAPP_DIRNAME / "drafts" / _safe_draft_file_name(file_id)
        if not draft_path.exists() or not draft_path.is_file():
            return DesktopWorkspaceFileDraft(
                schema_version="v1",
                vault_id=self.vault_id,
                device_id=self.config.device_id,
                vault_root=self.workspace.vault_root,
                file_id=file_id,
                path=record.path,
                has_draft=False,
                draft_path=draft_path,
            )
        payload = draft_path.read_bytes()
        if len(payload) > _WORKSPACE_FILE_CONTENT_MAX_BYTES:
            raise ValueError(f"workspace draft is too large to render: {file_id}")
        text = payload.decode("utf-8")
        stat = draft_path.stat()
        return DesktopWorkspaceFileDraft(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            file_id=file_id,
            path=record.path,
            has_draft=True,
            draft_path=draft_path,
            updated_at=stat.st_mtime_ns // 1_000_000,
            size_bytes=len(payload),
            text=text,
        )

    def write_workspace_file_draft(self, file_id: str, text: str) -> DesktopWorkspaceFileDraft:
        snapshot = self.load_snapshot()
        record = next((item for item in snapshot.document.files if item.file_id == file_id), None)
        if record is None:
            raise KeyError(f"file_id not found in workspace filemap: {file_id}")
        if record.status != "active":
            raise ValueError(f"workspace file is not active: {file_id}")
        content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
        if not content_path.exists() or not content_path.is_file():
            raise FileNotFoundError(f"workspace file content not found: {file_id}")
        draft_path = self.workspace.paths.root / NOTEAPP_DIRNAME / "drafts" / _safe_draft_file_name(file_id)
        _write_bytes_atomic(draft_path, text.encode("utf-8"))
        return self.load_workspace_file_draft(file_id)

    def clear_workspace_file_draft(self, file_id: str) -> DesktopWorkspaceFileDraft:
        snapshot = self.load_snapshot()
        record = next((item for item in snapshot.document.files if item.file_id == file_id), None)
        if record is None:
            raise KeyError(f"file_id not found in workspace filemap: {file_id}")
        draft_path = self.workspace.paths.root / NOTEAPP_DIRNAME / "drafts" / _safe_draft_file_name(file_id)
        draft_path.unlink(missing_ok=True)
        return DesktopWorkspaceFileDraft(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            file_id=file_id,
            path=record.path,
            has_draft=False,
            draft_path=draft_path,
        )

    def rebuild_workspace_search_index(self) -> DesktopWorkspaceSearchSnapshot:
        snapshot = self.load_snapshot()
        entries: list[dict[str, str]] = []
        for record in snapshot.document.sorted_files():
            if record.status != "active" or record.type != "note":
                continue
            content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            if not content_path.exists() or not content_path.is_file():
                continue
            try:
                payload = content_path.read_bytes()
                if len(payload) > _WORKSPACE_FILE_CONTENT_MAX_BYTES:
                    continue
                text, _ = _decode_workspace_text(payload, file_id=record.file_id)
            except (OSError, ValueError):
                continue
            entries.append(
                {
                    "file_id": record.file_id,
                    "path": record.path,
                    "content": text,
                }
            )
        with closing(self.workspace._open_connection()) as connection:
            replace_search_index_entries(connection, self.vault_id, entries)
        return DesktopWorkspaceSearchSnapshot(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            query="",
            total_count=0,
            results=[],
        )

    def search_workspace(self, query: str, *, limit: int = 20) -> DesktopWorkspaceSearchSnapshot:
        normalized_query = query.strip()
        if not normalized_query:
            return DesktopWorkspaceSearchSnapshot(
                schema_version="v1",
                vault_id=self.vault_id,
                device_id=self.config.device_id,
                vault_root=self.workspace.vault_root,
                query=query,
                total_count=0,
                results=[],
            )
        with closing(self.workspace._open_connection()) as connection:
            rows = search_index(connection, self.vault_id, normalized_query, limit=limit)
        results = [
            DesktopWorkspaceSearchResult(
                file_id=row["file_id"],
                path=row["path"],
                title=_search_result_title(row["path"]),
                snippet=row["snippet"] or row["path"],
            )
            for row in rows
        ]
        return DesktopWorkspaceSearchSnapshot(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            query=normalized_query,
            total_count=len(results),
            results=results,
        )

    def rebuild_workspace_note_links(self) -> None:
        snapshot = self.load_snapshot()
        active_notes = [
            record
            for record in snapshot.document.sorted_files()
            if record.status == "active" and record.type == "note"
        ]
        target_by_alias: dict[str, FileRecord] = {}
        for record in active_notes:
            aliases = {
                record.path,
                PurePosixPath(record.path).as_posix(),
                _note_title_from_path(record.path),
            }
            suffix = PurePosixPath(record.path).suffix
            if suffix:
                aliases.add(record.path[: -len(suffix)])
            for alias in aliases:
                normalized = alias.strip().lower()
                if normalized:
                    target_by_alias.setdefault(normalized, record)

        entries: list[dict[str, object]] = []
        for record in active_notes:
            content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            if not content_path.exists() or not content_path.is_file():
                continue
            try:
                payload = content_path.read_bytes()
                if len(payload) > _WORKSPACE_FILE_CONTENT_MAX_BYTES:
                    continue
                text, _ = _decode_workspace_text(payload, file_id=record.file_id)
            except (OSError, ValueError):
                continue
            for ordinal, match in enumerate(_WIKI_LINK_PATTERN.finditer(text)):
                link_text = match.group(1).strip()
                target_key = _normalize_wiki_link_target(link_text).lower()
                target = target_by_alias.get(target_key)
                if target is None and target_key and "." not in PurePosixPath(target_key).name:
                    target = target_by_alias.get(f"{target_key}.md")
                entries.append(
                    {
                        "source_file_id": record.file_id,
                        "source_path": record.path,
                        "link_text": link_text,
                        "target_file_id": None if target is None else target.file_id,
                        "target_path": None if target is None else target.path,
                        "ordinal": ordinal,
                    }
                )

        with closing(self.workspace._open_connection()) as connection:
            replace_note_links(connection, self.vault_id, entries)

    def load_workspace_note_links(self, file_id: str) -> DesktopWorkspaceNoteLinksSnapshot:
        snapshot = self.load_snapshot()
        record = next((item for item in snapshot.document.files if item.file_id == file_id), None)
        if record is None:
            raise KeyError(f"file_id not found in workspace filemap: {file_id}")
        self.rebuild_workspace_note_links()
        with closing(self.workspace._open_connection()) as connection:
            outgoing_rows = list_note_links_for_file(connection, self.vault_id, file_id)
            backlink_rows = list_note_backlinks_for_file(connection, self.vault_id, file_id)

        def build_link(row) -> DesktopWorkspaceNoteLink:
            return DesktopWorkspaceNoteLink(
                source_file_id=row["source_file_id"],
                source_path=row["source_path"],
                link_text=row["link_text"],
                target_file_id=row["target_file_id"],
                target_path=row["target_path"],
                ordinal=row["ordinal"],
            )

        outgoing = [build_link(row) for row in outgoing_rows]
        backlinks = [build_link(row) for row in backlink_rows]
        return DesktopWorkspaceNoteLinksSnapshot(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            file_id=record.file_id,
            path=record.path,
            outgoing=outgoing,
            backlinks=backlinks,
            outgoing_count=len(outgoing),
            backlink_count=len(backlinks),
        )

    def load_local_settings_snapshot(self) -> DesktopLocalSettingsSnapshot:
        settings_path = self.workspace.paths.settings_path
        source = "default"
        payload: dict[str, Any] = {}
        if settings_path.exists():
            raw_payload = json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(raw_payload, dict):
                raise ValueError(f"local settings must contain an object: {settings_path}")
            payload = raw_payload
            source = "file"

        appearance_payload = payload.get("appearance", {})
        if not isinstance(appearance_payload, dict):
            appearance_payload = {}
        ai_payload = payload.get("ai", {})
        if not isinstance(ai_payload, dict):
            ai_payload = {}

        theme = appearance_payload.get("theme")
        local_model_status = ai_payload.get("local_model_status")
        embedding_status = ai_payload.get("embedding_status")
        provider_api = ai_payload.get("provider_api")
        base_url = ai_payload.get("base_url")
        model_id = ai_payload.get("model_id")
        api_key = ai_payload.get("api_key")
        normalized_api_key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
        default_ai_provider_api = _local_settings_default_ai_provider_api()
        default_ai_base_url = _local_settings_default_ai_base_url()
        default_ai_model_id = _local_settings_default_ai_model_id()

        return DesktopLocalSettingsSnapshot(
            schema_version="v1",
            source=source,
            settings_path=settings_path,
            vault_root=self.workspace.vault_root,
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            sync=DesktopLocalSyncSettings(
                base_url=self.config.base_url,
                bearer_token_configured=self.config.bearer_token is not None,
                request_timeout_seconds=self.config.request_timeout_seconds,
                blob_timeout_seconds=self.config.blob_timeout_seconds,
                user_agent=self.config.user_agent,
            ),
            appearance=DesktopLocalAppearanceSettings(
                theme=theme if isinstance(theme, str) and theme in _LOCAL_SETTINGS_THEMES else "dark",
            ),
            ai=DesktopLocalAiSettings(
                local_model_status=(
                    local_model_status
                    if isinstance(local_model_status, str) and local_model_status
                    else "not_configured"
                ),
                embedding_status=(
                    embedding_status
                    if isinstance(embedding_status, str) and embedding_status
                    else "not_configured"
                ),
                provider_api=(
                    provider_api
                    if isinstance(provider_api, str) and provider_api in _LOCAL_SETTINGS_AI_PROVIDER_APIS
                    else default_ai_provider_api
                ),
                base_url=base_url if isinstance(base_url, str) and base_url else default_ai_base_url,
                model_id=model_id if isinstance(model_id, str) and model_id else default_ai_model_id,
                api_key_configured=normalized_api_key is not None,
                api_key=normalized_api_key,
            ),
            crypto=_local_crypto_settings_from_status(
                load_desktop_crypto_status(
                    vault_id=self.vault_id,
                    vault_root=self.workspace.vault_root,
                )
            ),
        )

    def write_local_settings(self, payload: Mapping[str, Any]) -> DesktopLocalSettingsSnapshot:
        normalized = _normalize_local_settings_payload(payload)
        if "api_key" not in normalized["ai"] and self.workspace.paths.settings_path.exists():
            existing_payload = json.loads(self.workspace.paths.settings_path.read_text(encoding="utf-8"))
            if isinstance(existing_payload, dict):
                existing_ai = existing_payload.get("ai")
                if isinstance(existing_ai, dict):
                    existing_api_key = existing_ai.get("api_key")
                    if isinstance(existing_api_key, str) and existing_api_key.strip():
                        normalized["ai"]["api_key"] = existing_api_key.strip()
        _write_json_atomic(self.workspace.paths.settings_path, normalized)
        return self.load_local_settings_snapshot()

    def _load_current_vault_key(self) -> Optional[bytes]:
        vault_key = load_desktop_vault_key(self.vault_id)
        if vault_key is not None:
            return vault_key
        provider_vault_id = getattr(self.blob_crypto_provider, "vault_id", None)
        provider_vault_key = getattr(self.blob_crypto_provider, "vault_key", None)
        if provider_vault_id == self.vault_id and isinstance(provider_vault_key, bytes):
            return provider_vault_key
        return None

    def export_crypto_recovery_package(
        self,
        *,
        recovery_phrase: str,
        created_at: Optional[int] = None,
        memory_kib: Optional[int] = None,
        iterations: Optional[int] = None,
    ) -> DesktopRecoveryPackageExport:
        vault_key = self._load_current_vault_key()
        if vault_key is None:
            raise ValueError("recovery package export requires an unlocked e2ee-v1 vault key")
        kwargs: dict[str, Any] = {}
        if memory_kib is not None:
            kwargs["memory_kib"] = memory_kib
        if iterations is not None:
            kwargs["iterations"] = iterations
        return export_recovery_package(
            vault_id=self.vault_id,
            vault_key=vault_key,
            recovery_phrase=recovery_phrase,
            created_at=_current_time_ms() if created_at is None else created_at,
            **kwargs,
        )

    def import_crypto_recovery_package(
        self,
        package_payload: Mapping[str, Any],
        *,
        recovery_phrase: str,
    ) -> DesktopCryptoRecoveryImportResult:
        recovery, vault_key = import_recovery_package(
            package_payload,
            recovery_phrase=recovery_phrase,
            expected_vault_id=self.vault_id,
        )
        store_desktop_vault_key(self.vault_id, vault_key)
        return DesktopCryptoRecoveryImportResult(
            recovery=recovery,
            crypto=_local_crypto_settings_from_status(
                load_desktop_crypto_status(
                    vault_id=self.vault_id,
                    vault_root=self.workspace.vault_root,
                )
            ),
        )

    def pull_reconcile(self, *, rewritten_at: int) -> PullReconcileSessionResult:
        return self.workspace.pull_reconcile(rewritten_at=rewritten_at)

    def pull_and_ack(self, *, rewritten_at: int) -> PullSyncSessionResult:
        return self._pull_and_ack_with_snapshot(rewritten_at=rewritten_at)[1]

    def pull_and_plan_apply(self, *, rewritten_at: int) -> DesktopPullApplyPlanResult:
        before_snapshot, pull = self._pull_and_ack_with_snapshot(rewritten_at=rewritten_at)
        return DesktopPullApplyPlanResult(
            pull=pull,
            plan=self._build_pull_apply_plan(before_snapshot, pull),
        )

    def pull_and_apply(self, *, rewritten_at: int) -> DesktopPullApplySessionResult:
        return self._pull_and_apply(rewritten_at=rewritten_at, reject_blocking_paths=False)

    def pull_and_apply_nonblocking(self, *, rewritten_at: int) -> DesktopPullApplySessionResult:
        return self._pull_and_apply(rewritten_at=rewritten_at, reject_blocking_paths=True)

    def _pull_and_apply(
        self,
        *,
        rewritten_at: int,
        reject_blocking_paths: bool,
    ) -> DesktopPullApplySessionResult:
        before_snapshot, pull = self._pull_and_ack_with_snapshot(rewritten_at=rewritten_at)
        plan = self._build_pull_apply_plan(before_snapshot, pull)
        if reject_blocking_paths and plan.blocking_paths:
            raise ValueError(
                "pull apply requires a later two-phase materialization boundary for blocking paths: "
                + ", ".join(plan.blocking_paths)
            )
        resolved = self.download_and_decrypt_pull_required_blobs(
            pull,
            apply_plan=plan,
        )
        staged = self.stage_pull_required_plaintext_for_apply(
            resolved,
            started_at=rewritten_at,
            apply_plan=plan,
        )
        execution = self.apply_staged_pull_plan(
            plan,
            staged,
            materialized_at=rewritten_at,
        )
        finalized = self.finalize_applied_pull_plan(
            plan,
            execution,
            staged,
            finalized_at=rewritten_at,
        )
        return DesktopPullApplySessionResult(
            pull=pull,
            plan=plan,
            staged=staged,
            execution=execution,
            finalized=finalized,
        )

    def _pull_and_ack_with_snapshot(
        self,
        *,
        rewritten_at: int,
    ) -> tuple[DesktopWorkspaceSnapshot, PullSyncSessionResult]:
        with closing(self.workspace._open_connection()) as connection:
            snapshot = self.workspace._load_snapshot_from_connection(connection)
            self._require_no_active_sync_apply_journal(connection, operation="pull")
            if snapshot.state.commit_in_progress:
                raise ValueError("pull cannot start while commit_in_progress is true")
            pull = self.workspace.runtime.session.pull_and_ack(
                connection,
                filemap_path=self.workspace.paths.filemap_path,
                ledger_path=self.workspace.paths.ledger_path,
                current_document=snapshot.document,
                current_state=snapshot.state,
                local_tombstones=snapshot.tombstones,
                rewritten_at=rewritten_at,
            )
        return snapshot, pull

    def resume_commit_recovery(self, *, normalized_at: int) -> CommitRecoverySessionResult:
        return self.workspace.resume_commit_recovery(normalized_at=normalized_at)

    def resume_pull_apply_recovery(self, *, normalized_at: int) -> DesktopPullApplyRecoveryResult:
        with closing(self.workspace._open_connection()) as connection:
            journal = load_sync_apply_journal(connection, self.vault_id)
            if journal is None:
                isolated_paths = self._isolate_unjournaled_pull_apply_staging()
                removed_plan_path = self._cleanup_pull_apply_plan_file()
                if isolated_paths or removed_plan_path is not None:
                    with closing(self.workspace._open_connection()) as refresh_connection:
                        state = load_vault_state(refresh_connection, self.vault_id)
                        if state is None:
                            raise KeyError(f"vault_state not found: {self.vault_id}")
                        stale_state = apply_manifest_summary_stale(state)
                        upsert_vault_state(refresh_connection, stale_state)
                    return DesktopPullApplyRecoveryResult(
                        mode="orphaned",
                        requires_full_pull=True,
                        journal_phase=None,
                        state=stale_state,
                        removed_staging_paths=[],
                        isolated_staging_paths=isolated_paths,
                        removed_plan_path=removed_plan_path,
                    )
                return DesktopPullApplyRecoveryResult(
                    mode="idle",
                    requires_full_pull=False,
                    journal_phase=None,
                    state=None,
                    removed_staging_paths=[],
                    isolated_staging_paths=[],
                    removed_plan_path=None,
                )
            materialized_snapshot = None
            if journal.phase == "materializing":
                materialized_snapshot = self.workspace._load_snapshot_from_connection(connection)

        if journal.phase in {"staging", "materializing"}:
            try:
                plan = self._load_pull_apply_plan_file()
            except (KeyError, TypeError, UnicodeDecodeError, ValueError):
                if (
                    journal.phase == "materializing"
                    and self._materializing_workspace_matches_document(materialized_snapshot)
                ):
                    return self._finalize_materializing_pull_apply_recovery(
                        journal,
                        normalized_at=normalized_at,
                    )
                return self._degrade_pull_apply_recovery(journal, normalized_at=normalized_at)
            if plan is not None:
                try:
                    self._validate_recovery_pull_apply_plan(plan, journal=journal)
                except ValueError:
                    if (
                        journal.phase == "materializing"
                        and self._materializing_workspace_matches_document(materialized_snapshot)
                    ):
                        return self._finalize_materializing_pull_apply_recovery(
                            journal,
                            normalized_at=normalized_at,
                        )
                    return self._degrade_pull_apply_recovery(journal, normalized_at=normalized_at)
                staged = DesktopPullApplyStagingResult(journal=journal, written_staging_paths={})
                try:
                    execution = self.apply_staged_pull_plan(
                        plan,
                        staged,
                        materialized_at=normalized_at,
                    )
                    finalized = self.finalize_applied_pull_plan(
                        plan,
                        execution,
                        staged,
                        finalized_at=normalized_at,
                    )
                except (FileNotFoundError, KeyError, ValueError):
                    if (
                        journal.phase == "materializing"
                        and self._materializing_workspace_matches_document(materialized_snapshot)
                    ):
                        return self._finalize_materializing_pull_apply_recovery(
                            journal,
                            normalized_at=normalized_at,
                        )
                    return self._degrade_pull_apply_recovery(journal, normalized_at=normalized_at)
                return DesktopPullApplyRecoveryResult(
                    mode="replayed",
                    requires_full_pull=False if finalized is None else finalized.state.last_manifest_summary_status != "valid",
                    journal_phase=journal.phase,
                    state=None if finalized is None else finalized.state,
                    removed_staging_paths=[] if finalized is None else finalized.removed_staging_paths,
                    isolated_staging_paths=[],
                    removed_plan_path=None if finalized is None else finalized.removed_plan_path,
                )
        if journal.phase in {"filemap_rewrite", "finalizing"}:
            with closing(self.workspace._open_connection()) as connection:
                finalizing_journal = (
                    journal
                    if journal.phase == "finalizing"
                    else replace(journal, phase="finalizing", updated_at=normalized_at)
                )
                if finalizing_journal != journal:
                    upsert_sync_apply_journal(connection, finalizing_journal)
                state = recover_sync_apply_finalizing_state(connection, self.vault_id)
            removed = self._cleanup_pull_apply_staging_artifacts()
            return DesktopPullApplyRecoveryResult(
                mode="finalized",
                requires_full_pull=state.last_manifest_summary_status != "valid",
                journal_phase=finalizing_journal.phase,
                state=state,
                removed_staging_paths=removed,
                isolated_staging_paths=[],
                removed_plan_path=self._cleanup_pull_apply_plan_file(),
            )
        if journal.phase == "preparing":
            return self._degrade_pull_apply_recovery(journal, normalized_at=normalized_at)
        if journal.phase == "materializing":
            if not self._materializing_workspace_matches_document(materialized_snapshot):
                return self._degrade_pull_apply_recovery(journal, normalized_at=normalized_at)
            return self._finalize_materializing_pull_apply_recovery(
                journal,
                normalized_at=normalized_at,
            )
        if journal.phase == "staging":
            return self._degrade_pull_apply_recovery(journal, normalized_at=normalized_at)
        raise ValueError(f"pull apply recovery is not supported for journal phase: {journal.phase}")

    def download_blobs(self, blob_ids: Iterable[str]) -> BlobDownloadSessionResult:
        return self.workspace.download_blobs(blob_ids)

    def list_file_versions(
        self,
        *,
        file_id: str,
        limit: int = 50,
        cursor: Optional[str] = None,
        include_pinned: bool = True,
    ) -> FileVersionListExecutionResult:
        return self.workspace.list_file_versions(
            file_id=file_id,
            limit=limit,
            cursor=cursor,
            include_pinned=include_pinned,
        )

    def update_file_version(
        self,
        *,
        version_id: str,
        version_label: Optional[str] = None,
        change_note: Optional[str] = None,
        is_pinned: Optional[bool] = None,
    ) -> FileVersionUpdateExecutionResult:
        return self.workspace.update_file_version(
            version_id=version_id,
            version_label=version_label,
            change_note=change_note,
            is_pinned=is_pinned,
        )

    def list_vault_devices(self) -> VaultDeviceListExecutionResult:
        return self.workspace.list_vault_devices()

    def heartbeat_vault_device(self) -> VaultDeviceHeartbeatExecutionResult:
        return self.workspace.heartbeat_vault_device()

    def revoke_device(self, *, device_id: str) -> dict[str, object]:
        return self.workspace.revoke_device(device_id=device_id)

    def _load_file_version_record(self, *, file_id: str, version_id: str) -> FileVersionRecord:
        cursor: Optional[str] = None
        seen_cursors: set[str] = set()
        while True:
            result = self.list_file_versions(
                file_id=file_id,
                limit=100,
                cursor=cursor,
                include_pinned=True,
            )
            for version in result.response.versions:
                if version.version_id == version_id:
                    if version.file_id != file_id:
                        raise ValueError(
                            f"file version {version_id} belongs to file_id {version.file_id}, not {file_id}"
                        )
                    return version
            next_cursor = result.response.next_cursor
            if next_cursor is None:
                break
            if next_cursor in seen_cursors:
                raise ValueError(f"file version pagination cursor repeated: {next_cursor}")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise KeyError(f"file version not found for file_id {file_id}: {version_id}")

    def _download_and_decrypt_file_version_payload(self, version: FileVersionRecord) -> bytes:
        download = self.download_blobs([version.blob_id])
        encrypted_payload = download.downloaded_blobs.get(version.blob_id)
        if encrypted_payload is None:
            raise KeyError(f"downloaded blob payload not found: {version.blob_id}")
        payload = self.blob_crypto_provider.decrypt_payload(
            encrypted_payload,
            content_hash=version.content_hash,
        )
        if len(payload) != version.size:
            raise ValueError(
                "file version plaintext size mismatch: "
                f"expected {version.size}, got {len(payload)}"
            )
        return payload

    def load_file_version_content(
        self,
        *,
        file_id: str,
        version_id: str,
        include_text: bool = True,
    ) -> DesktopFileVersionContent:
        version = self._load_file_version_record(file_id=file_id, version_id=version_id)
        payload = self._download_and_decrypt_file_version_payload(version)
        if len(payload) > _WORKSPACE_FILE_BLOB_MAX_BYTES:
            raise ValueError(f"file version is too large to preview: {version_id}")
        text = None
        encoding = None
        if include_text and len(payload) <= _WORKSPACE_FILE_CONTENT_MAX_BYTES:
            with suppress(ValueError):
                text, encoding = _decode_workspace_text(payload, file_id=file_id)
        return DesktopFileVersionContent(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            version=version,
            size_bytes=len(payload),
            content_hash=_compute_content_hash(payload),
            content_base64=base64.b64encode(payload).decode("ascii"),
            text=text,
            encoding=encoding,
        )

    def diff_file_version_with_current(
        self,
        *,
        file_id: str,
        version_id: str,
        context_lines: int = 3,
    ) -> DesktopFileVersionDiff:
        if context_lines < 0:
            raise ValueError("context_lines must be non-negative")
        snapshot = self.load_snapshot()
        record = next((item for item in snapshot.document.files if item.file_id == file_id), None)
        if record is None:
            raise KeyError(f"file_id not found in workspace filemap: {file_id}")
        if record.status != "active":
            raise ValueError(f"workspace file is not active: {file_id}")
        content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
        if not content_path.exists() or not content_path.is_file():
            raise FileNotFoundError(f"workspace file content not found: {file_id}")

        version = self._load_file_version_record(file_id=file_id, version_id=version_id)
        version_payload = self._download_and_decrypt_file_version_payload(version)
        current_payload = content_path.read_bytes()
        current_hash = _compute_content_hash(current_payload)
        version_hash = _compute_content_hash(version_payload)

        try:
            version_text, _ = _decode_workspace_text(version_payload, file_id=file_id)
            current_text, _ = _decode_workspace_text(current_payload, file_id=file_id)
        except ValueError:
            return DesktopFileVersionDiff(
                schema_version="v1",
                vault_id=self.vault_id,
                device_id=self.config.device_id,
                vault_root=self.workspace.vault_root,
                version=version,
                current_file_id=file_id,
                current_path=record.path,
                current_content_hash=current_hash,
                version_content_hash=version_hash,
                is_binary=True,
                diff_text="",
            )

        diff_lines = list(
            difflib.unified_diff(
                version_text.splitlines(),
                current_text.splitlines(),
                fromfile=f"{version.path_at_revision}@r{version.revision}",
                tofile=f"{record.path}@current",
                n=context_lines,
                lineterm="",
            )
        )
        diff_text = "\n".join(diff_lines)
        if diff_text:
            diff_text += "\n"
        return DesktopFileVersionDiff(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            version=version,
            current_file_id=file_id,
            current_path=record.path,
            current_content_hash=current_hash,
            version_content_hash=version_hash,
            is_binary=False,
            diff_text=diff_text,
        )

    def restore_file_version(
        self,
        *,
        file_id: str,
        version_id: str,
        created_at: int,
        commit_intent_id: Optional[str] = None,
        cleanup_normalized_at: Optional[int] = None,
        version_label: Optional[str] = None,
        change_note: Optional[str] = None,
        is_pinned: bool = False,
    ) -> DesktopFileVersionRestoreResult:
        snapshot = self.load_snapshot()
        record = next((item for item in snapshot.document.files if item.file_id == file_id), None)
        if record is None:
            raise KeyError(f"file_id not found in workspace filemap: {file_id}")
        if record.status != "active":
            raise ValueError(f"workspace file is not active: {file_id}")

        version = self._load_file_version_record(file_id=file_id, version_id=version_id)
        payload = self._download_and_decrypt_file_version_payload(version)
        content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
        _write_bytes_atomic(content_path, payload)
        updated_snapshot = self.load_snapshot()
        updated_document = self._document_with_current_blob_metadata(
            updated_snapshot.document,
            content_by_file_id={file_id: payload},
        )
        content_by_file_id = self.load_workspace_content_for_document(updated_document)
        directive_note = change_note or f"restored from {version.version_id} at revision {version.revision}"
        prepared = self._prepare_commit_with_snapshot(
            replace(updated_snapshot, document=updated_document),
            created_at=created_at,
            content_by_file_id=content_by_file_id,
            commit_intent_id=commit_intent_id,
            file_version_directives=[
                FileVersionCommitDirective(
                    file_id=file_id,
                    source="restore",
                    version_label=version_label,
                    change_note=directive_note,
                    is_pinned=is_pinned,
                )
            ],
        )
        resolved_cleanup_at = created_at if cleanup_normalized_at is None else cleanup_normalized_at
        commit = self._submit_prepared_commit(
            prepared,
            resolved_cleanup_at=resolved_cleanup_at,
        )
        if commit.network.commit.status == "committed":
            self.clear_workspace_file_draft(file_id)
            self._upsert_workspace_search_index_for_record(record)
        return DesktopFileVersionRestoreResult(
            schema_version="v1",
            vault_id=self.vault_id,
            device_id=self.config.device_id,
            vault_root=self.workspace.vault_root,
            version=version,
            file_id=file_id,
            path=record.path,
            restored_content_hash=_compute_content_hash(payload),
            commit=commit,
        )

    def build_pull_required_blob_plan(
        self,
        pull: PullSyncSessionResult,
        *,
        apply_plan: Optional[DesktopPullApplyPlan] = None,
    ) -> DesktopPullRequiredBlobPlan:
        applied = pull.pull.reconcile.applied
        if applied is None:
            return DesktopPullRequiredBlobPlan(
                vault_id=self.vault_id,
                revision=pull.pull.head.head_revision,
                files=[],
                blob_ids=[],
            )
        manifest = pull.pull.manifest
        if manifest is None:
            if applied.required_blob_ids:
                raise ValueError("pull result is missing manifest for required blob planning")
            return DesktopPullRequiredBlobPlan(
                vault_id=self.vault_id,
                revision=pull.pull.head.head_revision,
                files=[],
                blob_ids=[],
            )

        required_blob_id_set = set(applied.required_blob_ids)
        manifest_entry_by_file_id = {
            entry.file_id: entry
            for entry in manifest.sorted_files()
        }
        if apply_plan is not None:
            for move in apply_plan.moves:
                entry = manifest_entry_by_file_id.get(move.file_id)
                if entry is None:
                    raise ValueError(f"pull manifest is missing move target file_id: {move.file_id}")
                source_path = _resolve_workspace_file_path(self.workspace.vault_root, move.source_path)
                if not source_path.exists() or not source_path.is_file():
                    required_blob_id_set.add(entry.blob_id)
                    continue
                actual_hash = _compute_content_hash(source_path.read_bytes())
                if actual_hash != move.content_hash:
                    required_blob_id_set.add(entry.blob_id)
        files: list[DesktopPullRequiredBlobFile] = []
        blob_ids: list[str] = []
        seen_blob_ids: set[str] = set()
        content_hash_by_blob_id: dict[str, str] = {}
        manifest_blob_ids: set[str] = set()

        for entry in manifest.sorted_files():
            manifest_blob_ids.add(entry.blob_id)
            if entry.blob_id not in required_blob_id_set:
                continue
            previous_content_hash = content_hash_by_blob_id.get(entry.blob_id)
            if previous_content_hash is not None and previous_content_hash != entry.content_hash:
                raise ValueError(
                    f"required blob_id maps to multiple content hashes: {entry.blob_id}"
                )
            content_hash_by_blob_id[entry.blob_id] = entry.content_hash
            files.append(
                DesktopPullRequiredBlobFile(
                    file_id=entry.file_id,
                    path=entry.path,
                    type=entry.type,
                    blob_id=entry.blob_id,
                    content_hash=entry.content_hash,
                )
            )
            if entry.blob_id not in seen_blob_ids:
                seen_blob_ids.add(entry.blob_id)
                blob_ids.append(entry.blob_id)

        missing_blob_ids = sorted(required_blob_id_set - manifest_blob_ids)
        if missing_blob_ids:
            raise ValueError(
                "required blob_ids are missing from pull manifest: " + ", ".join(missing_blob_ids)
            )

        return DesktopPullRequiredBlobPlan(
            vault_id=manifest.vault_id,
            revision=manifest.revision,
            files=files,
            blob_ids=blob_ids,
        )

    def download_and_decrypt_pull_required_blobs(
        self,
        pull: PullSyncSessionResult,
        *,
        apply_plan: Optional[DesktopPullApplyPlan] = None,
    ) -> DesktopPullRequiredBlobResult:
        plan = self.build_pull_required_blob_plan(
            pull,
            apply_plan=apply_plan,
        )
        download = None
        downloaded_blobs: dict[str, bytes] = {}
        if plan.blob_ids:
            download = self.download_blobs(plan.blob_ids)
            downloaded_blobs = dict(download.downloaded_blobs)

        plaintext_by_blob_id: dict[str, bytes] = {}
        plaintext_by_file_id: dict[str, bytes] = {}
        for item in plan.files:
            plaintext = plaintext_by_blob_id.get(item.blob_id)
            if plaintext is None:
                if item.blob_id not in downloaded_blobs:
                    raise KeyError(f"downloaded blob payload not found: {item.blob_id}")
                plaintext = self.blob_crypto_provider.decrypt_payload(
                    downloaded_blobs[item.blob_id],
                    content_hash=item.content_hash,
                )
                plaintext_by_blob_id[item.blob_id] = plaintext
            plaintext_by_file_id[item.file_id] = plaintext

        return DesktopPullRequiredBlobResult(
            pull=pull,
            plan=plan,
            download=download,
            plaintext_by_file_id=plaintext_by_file_id,
        )

    def materialize_pull_required_plaintext(
        self,
        resolved: DesktopPullRequiredBlobResult,
        output_root: Path,
    ) -> dict[str, Path]:
        written_paths: dict[str, Path] = {}
        for item in resolved.plan.files:
            if item.file_id not in resolved.plaintext_by_file_id:
                raise KeyError(f"plaintext payload not found for file_id: {item.file_id}")
            output_path = _resolve_workspace_file_path(output_root, item.path)
            _write_bytes_atomic(output_path, resolved.plaintext_by_file_id[item.file_id])
            written_paths[item.file_id] = output_path
        return written_paths

    def stage_pull_required_plaintext_for_apply(
        self,
        resolved: DesktopPullRequiredBlobResult,
        *,
        started_at: int,
        apply_plan: Optional[DesktopPullApplyPlan] = None,
    ) -> DesktopPullApplyStagingResult:
        manifest = resolved.pull.pull.manifest
        if manifest is None:
            raise ValueError("pull manifest is required to stage required plaintext")
        if manifest.vault_id != resolved.plan.vault_id:
            raise ValueError("pull manifest vault_id does not match required blob plan")
        if manifest.revision != resolved.plan.revision:
            raise ValueError("pull manifest revision does not match required blob plan")
        if apply_plan is not None:
            if apply_plan.vault_id != resolved.plan.vault_id:
                raise ValueError("pull apply plan vault_id does not match required blob plan")
            if apply_plan.revision != resolved.plan.revision:
                raise ValueError("pull apply plan revision does not match required blob plan")

        staged_files: list[tuple[str, Path, bytes]] = []
        for item in resolved.plan.files:
            plaintext = resolved.plaintext_by_file_id.get(item.file_id)
            if plaintext is None:
                raise KeyError(f"plaintext payload not found for file_id: {item.file_id}")
            staged_files.append(
                (
                    item.file_id,
                    _resolve_pull_apply_staging_path(self.workspace.vault_root, item.file_id),
                    plaintext,
                )
            )
        should_create_journal = bool(staged_files)
        if apply_plan is not None and (apply_plan.writes or apply_plan.moves or apply_plan.deletes):
            should_create_journal = True
        if not should_create_journal:
            return DesktopPullApplyStagingResult(
                journal=None,
                written_staging_paths={},
            )

        journal = SyncApplyJournalRecord(
            vault_id=resolved.plan.vault_id,
            journal_id=str(uuid4()),
            target_revision=resolved.plan.revision,
            target_manifest_hash=manifest.summary_hash,
            phase="preparing",
            ops_hash=apply_plan.ops_hash if apply_plan is not None else _build_pull_apply_ops_hash(resolved.plan),
            created_at=started_at,
            updated_at=started_at,
        )
        written_staging_paths: dict[str, Path] = {}

        with closing(self.workspace._open_connection()) as connection:
            self._require_no_active_sync_apply_journal(connection, operation="stage pull apply")
            upsert_sync_apply_journal(connection, journal)
            try:
                if apply_plan is not None:
                    self._write_pull_apply_plan_file(apply_plan)
                for file_id, staging_path, plaintext in staged_files:
                    _write_bytes_atomic(staging_path, plaintext)
                    written_staging_paths[file_id] = staging_path
                journal = replace(journal, phase="staging", updated_at=started_at)
                upsert_sync_apply_journal(connection, journal)
            except Exception:
                for staging_path in written_staging_paths.values():
                    staging_path.unlink(missing_ok=True)
                with suppress(Exception):
                    clear_sync_apply_journal(connection, resolved.plan.vault_id)
                with suppress(Exception):
                    self._cleanup_pull_apply_plan_file()
                raise

        return DesktopPullApplyStagingResult(
            journal=journal,
            written_staging_paths=written_staging_paths,
        )

    def _allocate_pull_conflict_copy_path(
        self,
        *,
        original_relative_path: str,
        materialized_at: int,
    ) -> Path:
        original_path = _resolve_workspace_file_path(self.workspace.vault_root, original_relative_path)
        conflict_date = datetime.fromtimestamp(materialized_at / 1000, tz=timezone.utc).date()
        if original_path.parent.exists():
            return allocate_conflict_copy_path(
                original_path,
                conflict_date=conflict_date,
                device_name=self.config.device_id,
            )

        orphan_root = self.workspace.vault_root / CONFLICT_ORPHANS_DIRNAME
        orphan_root.mkdir(parents=True, exist_ok=True)
        return allocate_conflict_copy_path(
            orphan_root / original_path.name,
            conflict_date=conflict_date,
            device_name=self.config.device_id,
        )

    def _preserve_dirty_pull_conflict_copy(
        self,
        connection,
        *,
        source_file_id: str,
        live_path: Path,
        original_relative_path: str,
        expected_content_hash: Optional[str],
        materialized_at: int,
    ) -> Optional[Path]:
        if expected_content_hash is None:
            return None
        if not live_path.exists() or not live_path.is_file():
            return None

        payload = live_path.read_bytes()
        actual_hash = _compute_content_hash(payload)
        if actual_hash == expected_content_hash:
            return None

        current_document = load_filemap(self.workspace.paths.filemap_path)
        for record in current_document.files:
            if record.status != "conflict_copy":
                continue
            if record.conflict_source_file_id != source_file_id:
                continue
            if record.content_hash != actual_hash:
                continue
            conflict_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            if not conflict_path.exists() or not conflict_path.is_file():
                _write_bytes_atomic(conflict_path, payload)
            else:
                existing_hash = _compute_content_hash(conflict_path.read_bytes())
                if existing_hash != actual_hash:
                    _write_bytes_atomic(conflict_path, payload)
            state = load_vault_state(connection, self.vault_id)
            if state is None:
                raise KeyError(f"vault_state not found: {self.vault_id}")
            if not state.has_unresolved_conflicts:
                upsert_vault_state(connection, replace(state, has_unresolved_conflicts=True))
            return conflict_path

        conflict_path = self._allocate_pull_conflict_copy_path(
            original_relative_path=original_relative_path,
            materialized_at=materialized_at,
        )
        _write_bytes_atomic(conflict_path, payload)

        conflict_relative_path = _relative_vault_path(self.workspace.vault_root, conflict_path)
        updated_document = register_conflict_copy(
            current_document,
            source_file_id=source_file_id,
            conflict_file_id=self.file_id_builder(
                f"pull-conflict:{source_file_id}:{conflict_relative_path}:{materialized_at}"
            ),
            conflict_path=conflict_relative_path,
            updated_at=materialized_at,
            content_hash=actual_hash,
        )
        write_filemap_atomic(self.workspace.paths.filemap_path, updated_document)

        state = load_vault_state(connection, self.vault_id)
        if state is None:
            raise KeyError(f"vault_state not found: {self.vault_id}")
        if not state.has_unresolved_conflicts:
            upsert_vault_state(connection, replace(state, has_unresolved_conflicts=True))
        return conflict_path

    def _promote_unresolved_conflict_state_if_needed(
        self,
        snapshot: DesktopWorkspaceSnapshot,
    ) -> DesktopWorkspaceSnapshot:
        if snapshot.state.has_unresolved_conflicts:
            return snapshot
        if (
            not _has_conflict_copy_records(snapshot.document)
            and not _has_conflict_orphan_files(self.workspace.vault_root)
        ):
            return snapshot

        updated_state = replace(snapshot.state, has_unresolved_conflicts=True)
        with closing(self.workspace._open_connection()) as connection:
            upsert_vault_state(connection, updated_state)
        return DesktopWorkspaceSnapshot(
            document=snapshot.document,
            state=updated_state,
            tombstones=snapshot.tombstones,
        )

    def apply_staged_pull_plan(
        self,
        plan: DesktopPullApplyPlan,
        staged: DesktopPullApplyStagingResult,
        *,
        materialized_at: int,
    ) -> DesktopPullApplyExecutionResult:
        journal = staged.journal
        if journal is None:
            return DesktopPullApplyExecutionResult(
                journal=None,
                written_paths={},
                moved_paths={},
                deleted_paths=[],
            )

        written_paths: dict[str, Path] = {}
        moved_paths: dict[str, Path] = {}
        deleted_paths: list[Path] = []
        blocking_paths = set(plan.blocking_paths)

        with closing(self.workspace._open_connection()) as connection:
            current_journal = load_sync_apply_journal(connection, self.vault_id)
            if current_journal is None:
                raise KeyError(f"sync_apply_journal not found: {self.vault_id}")
            if current_journal.target_revision != plan.revision:
                raise ValueError("sync_apply_journal target_revision does not match pull apply plan")
            if current_journal.vault_id != plan.vault_id:
                raise ValueError("sync_apply_journal vault_id does not match pull apply plan")

            materializing_journal = replace(
                current_journal,
                phase="materializing",
                ops_hash=plan.ops_hash,
                updated_at=materialized_at,
            )
            upsert_sync_apply_journal(connection, materializing_journal)

            for item in plan.moves:
                if item.source_path not in blocking_paths and item.target_path not in blocking_paths:
                    continue
                source_path = _resolve_workspace_file_path(self.workspace.vault_root, item.source_path)
                move_staging_path = _resolve_pull_apply_staging_path(self.workspace.vault_root, item.file_id)
                target_path = _resolve_workspace_file_path(self.workspace.vault_root, item.target_path)
                if source_path.exists():
                    payload = source_path.read_bytes()
                    actual_hash = _compute_content_hash(payload)
                    if actual_hash != item.content_hash:
                        self._preserve_dirty_pull_conflict_copy(
                            connection,
                            source_file_id=item.file_id,
                            live_path=source_path,
                            original_relative_path=item.source_path,
                            expected_content_hash=item.content_hash,
                            materialized_at=materialized_at,
                        )
                        if target_path.exists():
                            if not target_path.is_file():
                                raise ValueError(f"pull apply target path is not a file: {item.target_path}")
                            target_hash = _compute_content_hash(target_path.read_bytes())
                            if target_hash == item.content_hash:
                                source_path.unlink(missing_ok=True)
                                continue
                        if not move_staging_path.exists() or not move_staging_path.is_file():
                            raise FileNotFoundError(
                                f"staged pull payload required for dirty move conflict: {item.file_id}"
                            )
                        staged_hash = _compute_content_hash(move_staging_path.read_bytes())
                        if staged_hash != item.content_hash:
                            raise ValueError(
                                f"staged pull payload hash mismatch for file_id {item.file_id}: "
                                f"expected {item.content_hash}, got {staged_hash}"
                            )
                        source_path.unlink(missing_ok=True)
                        continue
                    move_staging_path.parent.mkdir(parents=True, exist_ok=True)
                    source_path.replace(move_staging_path)
                    continue
                if move_staging_path.exists():
                    continue
                if target_path.exists():
                    payload = target_path.read_bytes()
                    actual_hash = _compute_content_hash(payload)
                    if actual_hash != item.content_hash:
                        raise ValueError(
                            f"materialized move target hash mismatch for file_id {item.file_id}: "
                            f"expected {item.content_hash}, got {actual_hash}"
                        )
                    continue
                raise FileNotFoundError(f"pull apply source path not found: {item.source_path}")

            for item in plan.deletes:
                if item.path not in blocking_paths:
                    continue
                delete_path = _resolve_workspace_file_path(self.workspace.vault_root, item.path)
                self._preserve_dirty_pull_conflict_copy(
                    connection,
                    source_file_id=item.file_id,
                    live_path=delete_path,
                    original_relative_path=item.path,
                    expected_content_hash=item.expected_content_hash,
                    materialized_at=materialized_at,
                )
                if not delete_path.exists():
                    continue
                delete_path.unlink(missing_ok=True)
                deleted_paths.append(delete_path)

            for item in plan.writes:
                output_path = _resolve_workspace_file_path(self.workspace.vault_root, item.target_path)
                staging_path = _resolve_workspace_file_path(self.workspace.vault_root, item.staging_path)
                if not staging_path.exists() or not staging_path.is_file():
                    if output_path.exists():
                        if not output_path.is_file():
                            raise ValueError(f"pull apply target path is not a file: {item.target_path}")
                        payload = output_path.read_bytes()
                        actual_hash = _compute_content_hash(payload)
                        if actual_hash == item.content_hash:
                            written_paths[item.file_id] = output_path
                            continue
                    raise FileNotFoundError(f"staged pull payload not found: {item.staging_path}")
                payload = staging_path.read_bytes()
                actual_hash = _compute_content_hash(payload)
                if actual_hash != item.content_hash:
                    raise ValueError(
                        f"staged pull payload hash mismatch for file_id {item.file_id}: "
                        f"expected {item.content_hash}, got {actual_hash}"
                    )
                if item.previous_path == item.target_path:
                    self._preserve_dirty_pull_conflict_copy(
                        connection,
                        source_file_id=item.file_id,
                        live_path=output_path,
                        original_relative_path=item.target_path,
                        expected_content_hash=item.expected_previous_content_hash,
                        materialized_at=materialized_at,
                    )
                _write_bytes_atomic(output_path, payload)
                written_paths[item.file_id] = output_path

            for item in plan.moves:
                if item.source_path in blocking_paths or item.target_path in blocking_paths:
                    source_path = _resolve_pull_apply_staging_path(self.workspace.vault_root, item.file_id)
                else:
                    source_path = _resolve_workspace_file_path(self.workspace.vault_root, item.source_path)
                target_path = _resolve_workspace_file_path(self.workspace.vault_root, item.target_path)
                if source_path.exists():
                    payload = source_path.read_bytes()
                    actual_hash = _compute_content_hash(payload)
                    if actual_hash != item.content_hash:
                        if item.source_path in blocking_paths or item.target_path in blocking_paths:
                            raise ValueError(
                                f"blocking move staging hash mismatch for file_id {item.file_id}: "
                                f"expected {item.content_hash}, got {actual_hash}"
                            )
                        self._preserve_dirty_pull_conflict_copy(
                            connection,
                            source_file_id=item.file_id,
                            live_path=source_path,
                            original_relative_path=item.source_path,
                            expected_content_hash=item.content_hash,
                            materialized_at=materialized_at,
                        )
                        if target_path.exists():
                            if not target_path.is_file():
                                raise ValueError(f"pull apply target path is not a file: {item.target_path}")
                            target_hash = _compute_content_hash(target_path.read_bytes())
                            if target_hash == item.content_hash:
                                source_path.unlink(missing_ok=True)
                                moved_paths[item.file_id] = target_path
                                continue
                        staged_source_path = _resolve_pull_apply_staging_path(
                            self.workspace.vault_root,
                            item.file_id,
                        )
                        if not staged_source_path.exists() or not staged_source_path.is_file():
                            raise FileNotFoundError(
                                f"staged pull payload required for dirty move conflict: {item.file_id}"
                            )
                        staged_payload = staged_source_path.read_bytes()
                        staged_hash = _compute_content_hash(staged_payload)
                        if staged_hash != item.content_hash:
                            raise ValueError(
                                f"staged pull payload hash mismatch for file_id {item.file_id}: "
                                f"expected {item.content_hash}, got {staged_hash}"
                            )
                        _write_bytes_atomic(target_path, staged_payload)
                        source_path.unlink(missing_ok=True)
                        moved_paths[item.file_id] = target_path
                        continue
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    source_path.replace(target_path)
                    moved_paths[item.file_id] = target_path
                    continue
                if target_path.exists():
                    payload = target_path.read_bytes()
                    actual_hash = _compute_content_hash(payload)
                    if actual_hash != item.content_hash:
                        raise ValueError(
                            f"materialized move target hash mismatch for file_id {item.file_id}: "
                            f"expected {item.content_hash}, got {actual_hash}"
                        )
                    moved_paths[item.file_id] = target_path
                    continue
                raise FileNotFoundError(f"pull apply source path not found: {item.source_path}")

            for item in plan.deletes:
                if item.path in blocking_paths:
                    continue
                delete_path = _resolve_workspace_file_path(self.workspace.vault_root, item.path)
                self._preserve_dirty_pull_conflict_copy(
                    connection,
                    source_file_id=item.file_id,
                    live_path=delete_path,
                    original_relative_path=item.path,
                    expected_content_hash=item.expected_content_hash,
                    materialized_at=materialized_at,
                )
                if not delete_path.exists():
                    continue
                delete_path.unlink(missing_ok=True)
                deleted_paths.append(delete_path)

        return DesktopPullApplyExecutionResult(
            journal=materializing_journal,
            written_paths=written_paths,
            moved_paths=moved_paths,
            deleted_paths=deleted_paths,
        )

    def finalize_applied_pull_plan(
        self,
        plan: DesktopPullApplyPlan,
        execution: DesktopPullApplyExecutionResult,
        staged: DesktopPullApplyStagingResult,
        *,
        finalized_at: int,
    ) -> Optional[DesktopPullApplyFinalizeResult]:
        if execution.journal is None:
            return None

        with closing(self.workspace._open_connection()) as connection:
            current_journal = load_sync_apply_journal(connection, self.vault_id)
            if current_journal is None:
                raise KeyError(f"sync_apply_journal not found: {self.vault_id}")
            if current_journal.vault_id != plan.vault_id:
                raise ValueError("sync_apply_journal vault_id does not match pull apply plan")
            if current_journal.target_revision != plan.revision:
                raise ValueError("sync_apply_journal target_revision does not match pull apply plan")
            if current_journal.phase != "materializing":
                raise ValueError("sync_apply_journal must be in materializing phase before finalization")

            filemap_rewrite_journal = replace(current_journal, phase="filemap_rewrite", updated_at=finalized_at)
            upsert_sync_apply_journal(connection, filemap_rewrite_journal)
            finalizing_journal = replace(filemap_rewrite_journal, phase="finalizing", updated_at=finalized_at)
            upsert_sync_apply_journal(connection, finalizing_journal)
            state = recover_sync_apply_finalizing_state(connection, self.vault_id)

        removed_staging_paths = self._cleanup_pull_apply_staging_artifacts()
        removed_plan_path = self._cleanup_pull_apply_plan_file()

        return DesktopPullApplyFinalizeResult(
            state=state,
            removed_staging_paths=removed_staging_paths,
            removed_plan_path=removed_plan_path,
        )

    def _build_pull_apply_plan(
        self,
        before_snapshot: DesktopWorkspaceSnapshot,
        pull: PullSyncSessionResult,
    ) -> DesktopPullApplyPlan:
        manifest = pull.pull.manifest
        if manifest is None:
            raise ValueError("pull manifest is required to build pull apply plan")

        before_active_by_file_id = {
            record.file_id: record
            for record in before_snapshot.document.files
            if record.status == "active"
        }
        target_file_ids = {entry.file_id for entry in manifest.files}

        writes: list[DesktopPullApplyWriteFile] = []
        moves: list[DesktopPullApplyMoveFile] = []
        deletes: list[DesktopPullApplyDeleteFile] = []

        for entry in manifest.sorted_files():
            before_record = before_active_by_file_id.get(entry.file_id)
            if before_record is None or before_record.content_hash != entry.content_hash:
                writes.append(
                    DesktopPullApplyWriteFile(
                        file_id=entry.file_id,
                        target_path=entry.path,
                        staging_path=_build_pull_apply_staging_relative_path(entry.file_id),
                        type=entry.type,
                        content_hash=entry.content_hash,
                        previous_path=None if before_record is None else before_record.path,
                        expected_previous_content_hash=(
                            None if before_record is None else before_record.content_hash
                        ),
                    )
                )
                if before_record is not None and before_record.path != entry.path:
                    deletes.append(
                        DesktopPullApplyDeleteFile(
                            file_id=entry.file_id,
                            path=before_record.path,
                            reason="replaced_old_path",
                            expected_content_hash=before_record.content_hash,
                        )
                    )
                continue

            if before_record.path != entry.path:
                moves.append(
                    DesktopPullApplyMoveFile(
                        file_id=entry.file_id,
                        source_path=before_record.path,
                        target_path=entry.path,
                        type=entry.type,
                        content_hash=entry.content_hash,
                    )
                )

        for record in before_active_by_file_id.values():
            if record.file_id in target_file_ids:
                continue
            deletes.append(
                DesktopPullApplyDeleteFile(
                    file_id=record.file_id,
                    path=record.path,
                    reason="deleted",
                    expected_content_hash=record.content_hash,
                )
            )

        source_paths = {item.source_path for item in moves}
        source_paths.update(item.path for item in deletes)
        target_paths = {item.target_path for item in writes}
        target_paths.update(item.target_path for item in moves)
        blocking_paths = sorted(target_paths & source_paths)

        plan = DesktopPullApplyPlan(
            vault_id=manifest.vault_id,
            revision=manifest.revision,
            writes=writes,
            moves=moves,
            deletes=deletes,
            blocking_paths=blocking_paths,
            ops_hash="",
        )
        return replace(plan, ops_hash=_build_pull_apply_plan_ops_hash(plan))

    def detect_local_changes(self) -> DesktopWorkspaceChangeSet:
        return self.workspace.detect_local_changes()

    def load_worker_state(self) -> DesktopSyncWorkerStateRecord:
        return self.workspace.load_worker_state()

    def load_worker_health(self) -> DesktopSyncWorkerHealth:
        return self.workspace.load_worker_health()

    def _record_sync_activity(
        self,
        *,
        occurred_at_ms: int,
        action: DesktopSyncPanelAction,
        source: str,
        status: str,
        message: Optional[str],
    ) -> DesktopSyncActivityRecord:
        level = "success" if status == "executed" else ("warning" if status in {"blocked", "disabled"} else "danger")
        record = DesktopSyncActivityRecord(
            activity_id=str(uuid4()),
            occurred_at_ms=occurred_at_ms,
            level=level,
            action_id=action.action_id,
            command=action.command,
            status=status,
            source=source,
            message=message,
        )
        _append_jsonl_record(
            self.workspace.paths.sync_activity_log_path,
            {
                "activity_id": record.activity_id,
                "occurred_at_ms": record.occurred_at_ms,
                "level": record.level,
                "action_id": record.action_id,
                "command": record.command,
                "status": record.status,
                "source": record.source,
                "message": record.message,
            },
        )
        return record

    def list_sync_activity(self, *, limit: int = 20) -> DesktopSyncActivityFeed:
        all_payloads = _load_jsonl_records(self.workspace.paths.sync_activity_log_path)
        if limit <= 0:
            payloads = []
        elif len(all_payloads) <= limit:
            payloads = all_payloads
        else:
            payloads = all_payloads[-limit:]
        records = [
            DesktopSyncActivityRecord(
                activity_id=str(payload["activity_id"]),
                occurred_at_ms=int(payload["occurred_at_ms"]),
                level=str(payload["level"]),
                action_id=str(payload["action_id"]),
                command=str(payload["command"]),
                status=str(payload["status"]),
                source=str(payload["source"]),
                message=None if payload.get("message") is None else str(payload["message"]),
            )
            for payload in payloads
        ]
        return DesktopSyncActivityFeed(
            records=records,
            total_count=len(all_payloads),
        )

    def _build_panel_action(
        self,
        *,
        action_id: str,
        label: str,
        command: str,
        argv: Optional[list[str]] = None,
        enabled: bool = True,
        emphasis: str = "normal",
        reason: Optional[str] = None,
        requires_confirmation: bool = False,
    ) -> DesktopSyncPanelAction:
        return DesktopSyncPanelAction(
            action_id=action_id,
            label=label,
            enabled=enabled,
            emphasis=emphasis,
            command=command,
            argv=[] if argv is None else list(argv),
            reason=reason,
            requires_confirmation=requires_confirmation,
        )

    def summarize_vault(self) -> DesktopVaultSummary:
        conflicts = self.list_conflicts()
        changes = self.detect_local_changes()
        try:
            worker_health = self.load_worker_health()
        except FileNotFoundError:
            worker_health = None

        with closing(self.workspace._open_connection()) as connection:
            sync_apply_journal = load_sync_apply_journal(connection, self.vault_id)
            commit_journal = load_commit_intent_journal(connection, self.vault_id)

        state = conflicts.state
        blocking_reasons: list[str] = []
        if sync_apply_journal is not None:
            blocking_reasons.append(f"sync_apply_journal:{sync_apply_journal.phase}")
        if commit_journal is not None:
            blocking_reasons.append(f"commit_intent_journal:{commit_journal.status}")
        if state.commit_in_progress:
            blocking_reasons.append("commit_in_progress")
        if conflicts.actual_has_unresolved_conflicts or state.has_unresolved_conflicts:
            blocking_reasons.append("unresolved_conflicts")
        requires_full_pull = state.last_manifest_summary_status != "valid" or state.last_manifest_summary is None
        if requires_full_pull:
            blocking_reasons.append("requires_full_pull")

        return DesktopVaultSummary(
            state=state,
            changes=changes,
            conflicts=conflicts,
            worker_health=worker_health,
            commit_gate=DesktopCommitGateStatus(
                can_submit_commit=not blocking_reasons,
                blocking_reasons=blocking_reasons,
                requires_full_pull=requires_full_pull,
                has_active_commit_journal=commit_journal is not None,
                has_active_sync_apply_journal=sync_apply_journal is not None,
            ),
        )

    def build_sync_panel_model(self, *, now_ms: Optional[int] = None) -> DesktopSyncPanelModel:
        resolved_now_ms = (
            int(datetime.now(timezone.utc).timestamp() * 1000)
            if now_ms is None
            else now_ms
        )
        summary = self.summarize_vault()
        conflict_badge_count = len(summary.conflicts.conflict_copies) + len(summary.conflicts.conflict_orphans)
        change_badge_count = summary.changes.change_count
        secondary_actions = [
            self._build_panel_action(
                action_id="show-vault-summary",
                label="Open Summary",
                command="vault-summary",
            ),
            self._build_panel_action(
                action_id="list-conflicts",
                label="Show Conflicts",
                command="list-conflicts",
                enabled=conflict_badge_count > 0,
                reason=None if conflict_badge_count > 0 else "No unresolved conflicts",
            ),
            self._build_panel_action(
                action_id="worker-health",
                label="Worker Health",
                command="worker-health",
                enabled=summary.worker_health is not None,
                reason=None if summary.worker_health is not None else "No worker state recorded yet",
            ),
        ]
        if conflict_badge_count > 0:
            secondary_actions.append(
                self._build_panel_action(
                    action_id="resolve-conflicts-all",
                    label="Resolve All Local Artifacts",
                    command="resolve-conflicts",
                    argv=["--resolved-at", str(resolved_now_ms), "--all"],
                    enabled=True,
                    emphasis="warning",
                    reason="Deletes all listed local conflict-copy artifacts after user confirmation.",
                    requires_confirmation=True,
                )
            )

        if summary.commit_gate.has_active_sync_apply_journal:
            return DesktopSyncPanelModel(
                level="danger",
                headline="Sync recovery required",
                detail="A pull/apply journal is still active. Resume recovery before new sync or commit work.",
                conflict_badge_count=conflict_badge_count,
                change_badge_count=change_badge_count,
                primary_action=self._build_panel_action(
                    action_id="recover-pull-apply",
                    label="Resume Pull Recovery",
                    command="recover-pull-apply",
                    argv=["--normalized-at", str(resolved_now_ms)],
                    emphasis="primary",
                ),
                secondary_actions=secondary_actions,
                summary=summary,
            )
        if summary.commit_gate.has_active_commit_journal or summary.state.commit_in_progress:
            return DesktopSyncPanelModel(
                level="danger",
                headline="Commit recovery required",
                detail="A previous commit is still in progress or awaiting recovery confirmation.",
                conflict_badge_count=conflict_badge_count,
                change_badge_count=change_badge_count,
                primary_action=self._build_panel_action(
                    action_id="recover",
                    label="Resume Commit Recovery",
                    command="recover",
                    argv=["--normalized-at", str(resolved_now_ms)],
                    emphasis="primary",
                ),
                secondary_actions=secondary_actions,
                summary=summary,
            )
        if conflict_badge_count > 0:
            return DesktopSyncPanelModel(
                level="warning",
                headline=f"{conflict_badge_count} unresolved conflict artifacts",
                detail="Resolve local conflict copies before the next commit can be submitted.",
                conflict_badge_count=conflict_badge_count,
                change_badge_count=change_badge_count,
                primary_action=self._build_panel_action(
                    action_id="list-conflicts",
                    label="Review Conflicts",
                    command="list-conflicts",
                    emphasis="primary",
                ),
                secondary_actions=secondary_actions,
                summary=summary,
            )
        if summary.commit_gate.requires_full_pull:
            return DesktopSyncPanelModel(
                level="warning",
                headline="Full pull required",
                detail="The local manifest baseline is stale. Run pull/reconcile before creating a new commit.",
                conflict_badge_count=conflict_badge_count,
                change_badge_count=change_badge_count,
                primary_action=self._build_panel_action(
                    action_id="pull",
                    label="Run Pull",
                    command="pull",
                    argv=["--rewritten-at", str(resolved_now_ms)],
                    emphasis="primary",
                ),
                secondary_actions=secondary_actions,
                summary=summary,
            )
        if summary.worker_health is not None and summary.worker_health.status != "healthy":
            return DesktopSyncPanelModel(
                level="warning",
                headline="Background sync needs attention",
                detail="The latest worker run did not finish cleanly. Review worker health before relying on background sync.",
                conflict_badge_count=conflict_badge_count,
                change_badge_count=change_badge_count,
                primary_action=self._build_panel_action(
                    action_id="worker-health",
                    label="Inspect Worker Health",
                    command="worker-health",
                    emphasis="primary",
                ),
                secondary_actions=secondary_actions,
                summary=summary,
            )
        if change_badge_count > 0:
            return DesktopSyncPanelModel(
                level="info",
                headline=f"{change_badge_count} local changes pending",
                detail="Local edits are ready for the next submit or sync cycle.",
                conflict_badge_count=conflict_badge_count,
                change_badge_count=change_badge_count,
                primary_action=self._build_panel_action(
                    action_id="submit-detected-commit",
                    label="Submit Local Changes",
                    command="submit-detected-commit",
                    argv=["--created-at", str(resolved_now_ms)],
                    enabled=summary.commit_gate.can_submit_commit,
                    emphasis="primary",
                    reason=None if summary.commit_gate.can_submit_commit else ", ".join(summary.commit_gate.blocking_reasons),
                ),
                secondary_actions=secondary_actions,
                summary=summary,
            )
        return DesktopSyncPanelModel(
            level="success",
            headline="Vault is in sync",
            detail="No unresolved conflicts, no pending local changes, and no blocked sync state detected.",
            conflict_badge_count=0,
            change_badge_count=0,
            primary_action=self._build_panel_action(
                action_id="pull",
                label="Check For Remote Changes",
                command="pull",
                argv=["--rewritten-at", str(resolved_now_ms)],
                emphasis="primary",
            ),
            secondary_actions=secondary_actions,
            summary=summary,
        )

    def build_sync_center_model(self, *, now_ms: Optional[int] = None) -> DesktopSyncCenterModel:
        resolved_now_ms = (
            int(datetime.now(timezone.utc).timestamp() * 1000)
            if now_ms is None
            else now_ms
        )
        panel = self.build_sync_panel_model(now_ms=resolved_now_ms)
        summary = panel.summary
        recent_activity = self.list_sync_activity(limit=5)
        cards: list[DesktopSyncCenterCard] = []
        conflict_badge_count = panel.conflict_badge_count
        change_badge_count = panel.change_badge_count

        if summary.commit_gate.has_active_sync_apply_journal:
            cards.append(
                DesktopSyncCenterCard(
                    card_id="pull-recovery",
                    kind="recovery",
                    level="danger",
                    title="Pull/apply recovery is blocking the vault",
                    body="A sync apply journal is still active. Resume pull recovery before any new sync or commit work.",
                    badge_count=1,
                    actions=[panel.primary_action],
                )
            )
        elif summary.commit_gate.has_active_commit_journal or summary.state.commit_in_progress:
            cards.append(
                DesktopSyncCenterCard(
                    card_id="commit-recovery",
                    kind="recovery",
                    level="danger",
                    title="Commit recovery is blocking the vault",
                    body="A previous commit still needs recovery confirmation before the next submit can start.",
                    badge_count=1,
                    actions=[panel.primary_action],
                )
            )

        if conflict_badge_count > 0:
            cards.append(
                DesktopSyncCenterCard(
                    card_id="conflicts",
                    kind="conflicts",
                    level="warning",
                    title=f"{conflict_badge_count} unresolved local conflict artifacts",
                    body="Conflict copies and orphan conflict files must be reviewed or cleared before commit submission can reopen.",
                    badge_count=conflict_badge_count,
                    actions=[
                        self._build_panel_action(
                            action_id="list-conflicts",
                            label="Review Conflicts",
                            command="list-conflicts",
                            emphasis="primary",
                        ),
                        self._build_panel_action(
                            action_id="resolve-conflicts-all",
                            label="Resolve All Local Artifacts",
                            command="resolve-conflicts",
                            argv=panel.secondary_actions[-1].argv if panel.secondary_actions else [],
                            enabled=any(action.action_id == "resolve-conflicts-all" for action in panel.secondary_actions),
                            emphasis="warning",
                            reason="Deletes all listed local conflict-copy artifacts after user confirmation.",
                            requires_confirmation=True,
                        ),
                    ],
                )
            )

        if summary.commit_gate.requires_full_pull:
            cards.append(
                DesktopSyncCenterCard(
                    card_id="baseline",
                    kind="baseline",
                    level="warning",
                    title="Local sync baseline must be rebuilt",
                    body="The current manifest summary is stale. Run a full pull/reconcile before creating a new commit.",
                    badge_count=1,
                    actions=[
                        self._build_panel_action(
                            action_id="pull",
                            label="Run Pull",
                            command="pull",
                            argv=["--rewritten-at", str(resolved_now_ms)],
                            emphasis="primary",
                        )
                    ],
                )
            )

        if change_badge_count > 0:
            cards.append(
                DesktopSyncCenterCard(
                    card_id="local-changes",
                    kind="changes",
                    level="info",
                    title=f"{change_badge_count} local changes are waiting",
                    body="Tracked modifications, missing files, or untracked files are ready for the next submit or sync cycle.",
                    badge_count=change_badge_count,
                    actions=[
                        self._build_panel_action(
                            action_id="detect-local-changes",
                            label="Inspect Local Changes",
                            command="detect-local-changes",
                        ),
                        self._build_panel_action(
                            action_id="submit-detected-commit",
                            label="Submit Local Changes",
                            command="submit-detected-commit",
                            argv=["--created-at", str(resolved_now_ms)],
                            enabled=summary.commit_gate.can_submit_commit,
                            emphasis="primary",
                            reason=None if summary.commit_gate.can_submit_commit else ", ".join(summary.commit_gate.blocking_reasons),
                        ),
                    ],
                )
            )

        if summary.worker_health is not None and summary.worker_health.status != "healthy":
            cards.append(
                DesktopSyncCenterCard(
                    card_id="worker-health",
                    kind="background-sync",
                    level="warning",
                    title="Background sync worker needs attention",
                    body="The latest worker run reported failures or stopped early. Review health before relying on background sync.",
                    badge_count=summary.worker_health.failure_count,
                    actions=[
                        self._build_panel_action(
                            action_id="worker-health",
                            label="Inspect Worker Health",
                            command="worker-health",
                            emphasis="primary",
                        )
                    ],
                )
            )

        if not cards:
            cards.append(
                DesktopSyncCenterCard(
                    card_id="healthy",
                    kind="overview",
                    level="success",
                    title="Vault sync center is clear",
                    body="No unresolved conflicts, no blocked recovery state, and no pending local changes were detected.",
                    badge_count=0,
                    actions=[
                        self._build_panel_action(
                            action_id="pull",
                            label="Check For Remote Changes",
                            command="pull",
                            argv=panel.primary_action.argv,
                            emphasis="primary",
                        )
                    ],
                )
            )

        if recent_activity.total_count > 0:
            latest_record = recent_activity.records[-1]
            cards.append(
                DesktopSyncCenterCard(
                    card_id="activity",
                    kind="activity",
                    level=_resolve_activity_feed_level(recent_activity),
                    title=f"{recent_activity.total_count} recent sync actions recorded",
                    body=(
                        f"Latest action `{latest_record.action_id}` finished with status "
                        f"`{latest_record.status}` from `{latest_record.source}`."
                        if latest_record.message is None
                        else (
                            f"Latest action `{latest_record.action_id}` finished with status "
                            f"`{latest_record.status}` from `{latest_record.source}`: "
                            f"{latest_record.message}"
                        )
                    ),
                    badge_count=recent_activity.total_count,
                    actions=[
                        self._build_panel_action(
                            action_id="sync-activity",
                            label="Open Activity Feed",
                            command="sync-activity",
                            argv=["--limit", "20"],
                            emphasis="primary",
                        )
                    ],
                )
            )

        return DesktopSyncCenterModel(
            cards=cards,
            panel=panel,
            summary=summary,
            recent_activity=recent_activity,
        )

    def build_sync_shell_snapshot(
        self,
        *,
        now_ms: Optional[int] = None,
        activity_limit: int = 20,
    ) -> DesktopSyncShellSnapshot:
        resolved_now_ms = (
            int(datetime.now(timezone.utc).timestamp() * 1000)
            if now_ms is None
            else now_ms
        )
        sync_center = self.build_sync_center_model(now_ms=resolved_now_ms)
        return DesktopSyncShellSnapshot(
            generated_at_ms=resolved_now_ms,
            vault_id=self.workspace.vault_id,
            device_id=self.workspace.config.device_id,
            vault_root=self.workspace.vault_root,
            sync_center=sync_center,
            activity_feed=self.list_sync_activity(limit=activity_limit),
        )

    def _find_sync_action(
        self,
        action_id: str,
        *,
        now_ms: Optional[int] = None,
    ) -> tuple[DesktopSyncPanelAction, str]:
        center = self.build_sync_center_model(now_ms=now_ms)
        for card in center.cards:
            for action in card.actions:
                if action.action_id == action_id:
                    return action, f"card:{card.card_id}"
        for action in [center.panel.primary_action, *center.panel.secondary_actions]:
            if action.action_id == action_id:
                return action, "panel"
        raise KeyError(f"sync action not found: {action_id}")

    def execute_sync_action(
        self,
        action_id: str,
        *,
        now_ms: Optional[int] = None,
    ) -> DesktopSyncActionExecutionResult:
        resolved_now_ms = (
            int(datetime.now(timezone.utc).timestamp() * 1000)
            if now_ms is None
            else now_ms
        )
        action, source = self._find_sync_action(action_id, now_ms=resolved_now_ms)
        if not action.enabled:
            result = DesktopSyncActionExecutionResult(
                action=action,
                source=source,
                status="disabled",
                payload=None,
                message=action.reason or "action is currently disabled",
            )
            self._record_sync_activity(
                occurred_at_ms=resolved_now_ms,
                action=action,
                source=source,
                status=result.status,
                message=result.message,
            )
            return result

        try:
            payload = self._execute_supported_sync_action(action, resolved_now_ms=resolved_now_ms)
        except KeyError:
            result = DesktopSyncActionExecutionResult(
                action=action,
                source=source,
                status="unsupported",
                payload=None,
                message=f"unsupported sync action command: {action.command}",
            )
            self._record_sync_activity(
                occurred_at_ms=resolved_now_ms,
                action=action,
                source=source,
                status=result.status,
                message=result.message,
            )
            return result
        except Exception as error:
            self._record_sync_activity(
                occurred_at_ms=resolved_now_ms,
                action=action,
                source=source,
                status="failed",
                message=f"{type(error).__name__}: {error}",
            )
            raise
        status, message = self._resolve_sync_action_execution_status(action, payload)
        result = DesktopSyncActionExecutionResult(
            action=action,
            source=source,
            status=status,
            payload=payload,
            message=message,
        )
        self._record_sync_activity(
            occurred_at_ms=resolved_now_ms,
            action=action,
            source=source,
            status=result.status,
            message=result.message,
        )
        return result

    def _resolve_sync_action_execution_status(
        self,
        action: DesktopSyncPanelAction,
        payload: object | None,
    ) -> tuple[str, Optional[str]]:
        if action.command != "submit-detected-commit" or not isinstance(payload, DesktopCommitSessionResult):
            return "executed", None
        if payload.network.commit.status != "conflict":
            return "executed", None

        conflict = payload.network.commit.conflict
        if conflict is None:
            return "blocked", "submit conflict; run pull before retrying"
        return (
            "blocked",
            (
                f"submit conflict: {conflict.code}; "
                f"remote head revision {conflict.current_head_revision}; "
                "run pull before retrying"
            ),
        )

    def execute_sync_action_and_snapshot(
        self,
        action_id: str,
        *,
        now_ms: Optional[int] = None,
        activity_limit: int = 20,
    ) -> DesktopSyncActionSnapshotResult:
        resolved_now_ms = (
            int(datetime.now(timezone.utc).timestamp() * 1000)
            if now_ms is None
            else now_ms
        )
        execution = self.execute_sync_action(action_id, now_ms=resolved_now_ms)
        snapshot = self.build_sync_shell_snapshot(
            now_ms=resolved_now_ms,
            activity_limit=activity_limit,
        )
        return DesktopSyncActionSnapshotResult(
            execution=execution,
            snapshot=snapshot,
        )

    def _execute_supported_sync_action(
        self,
        action: DesktopSyncPanelAction,
        *,
        resolved_now_ms: int,
    ) -> object | None:
        if action.command == "vault-summary":
            return self.summarize_vault()
        if action.command == "list-conflicts":
            return self.list_conflicts()
        if action.command == "worker-health":
            return self.load_worker_health()
        if action.command == "detect-local-changes":
            return self.detect_local_changes()
        if action.command == "sync-activity":
            return self.list_sync_activity(limit=_resolve_sync_activity_limit(action.argv))
        if action.command == "resolve-conflicts":
            return self.resolve_conflicts(
                resolved_at=resolved_now_ms,
                resolve_all="--all" in action.argv,
            )
        if action.command == "recover-pull-apply":
            return self.resume_pull_apply_recovery(normalized_at=resolved_now_ms)
        if action.command == "recover":
            return self.resume_commit_recovery(normalized_at=resolved_now_ms)
        if action.command == "pull":
            return self.pull_and_apply(rewritten_at=resolved_now_ms)
        if action.command == "submit-detected-commit":
            return self.submit_detected_changes_if_needed(
                created_at=resolved_now_ms,
                cleanup_normalized_at=resolved_now_ms,
            )
        raise KeyError(action.command)

    def list_conflicts(self) -> DesktopConflictStatus:
        snapshot = self._promote_unresolved_conflict_state_if_needed(self.load_snapshot())
        conflict_copies: list[DesktopConflictArtifact] = []
        for record in sorted(
            (item for item in snapshot.document.files if item.status == "conflict_copy"),
            key=lambda item: (item.path, item.file_id),
        ):
            conflict_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            conflict_copies.append(
                DesktopConflictArtifact(
                    kind="conflict_copy",
                    file_id=record.file_id,
                    path=record.path,
                    exists_on_disk=conflict_path.exists() and conflict_path.is_file(),
                    conflict_source_file_id=record.conflict_source_file_id,
                    content_hash=record.content_hash,
                )
            )

        orphan_root = self.workspace.vault_root / CONFLICT_ORPHANS_DIRNAME
        conflict_orphans: list[DesktopConflictArtifact] = []
        if orphan_root.exists():
            for path in sorted(
                (item for item in orphan_root.rglob("*") if item.is_file()),
                key=lambda item: str(item.relative_to(orphan_root)),
            ):
                conflict_orphans.append(
                    DesktopConflictArtifact(
                        kind="conflict_orphan",
                        path=_relative_vault_path(self.workspace.vault_root, path),
                        exists_on_disk=True,
                    )
                )

        return DesktopConflictStatus(
            state=snapshot.state,
            actual_has_unresolved_conflicts=bool(conflict_copies or conflict_orphans),
            conflict_copies=conflict_copies,
            conflict_orphans=conflict_orphans,
        )

    def resolve_conflicts(
        self,
        *,
        resolved_at: int,
        conflict_file_ids: Optional[Iterable[str]] = None,
        orphan_relative_paths: Optional[Iterable[str]] = None,
        resolve_all: bool = False,
    ) -> DesktopConflictResolutionResult:
        requested_conflict_file_ids = list(dict.fromkeys(conflict_file_ids or []))
        requested_orphan_paths = list(dict.fromkeys(orphan_relative_paths or []))
        if resolve_all:
            status = self.list_conflicts()
            requested_conflict_file_ids = [item.file_id for item in status.conflict_copies if item.file_id is not None]
            requested_orphan_paths = [item.path for item in status.conflict_orphans]
        if not requested_conflict_file_ids and not requested_orphan_paths:
            raise ValueError("resolve-conflicts requires at least one conflict file_id or orphan path")

        with closing(self.workspace._open_connection()) as connection:
            self._require_no_active_sync_apply_journal(connection, operation="resolve-conflicts")
            snapshot = self.workspace._load_snapshot_from_connection(connection)
            if snapshot.state.commit_in_progress:
                raise ValueError("resolve-conflicts cannot start while commit_in_progress is true")

            current_document = snapshot.document
            file_by_id = {record.file_id: record for record in current_document.files}
            removed_conflict_paths: dict[str, Path] = {}
            skipped_conflict_file_ids: list[str] = []
            updated_document = current_document
            filemap_changed = False

            for file_id in requested_conflict_file_ids:
                record = file_by_id.get(file_id)
                if record is None:
                    skipped_conflict_file_ids.append(file_id)
                    continue
                if record.status != "conflict_copy":
                    raise ValueError(f"workspace file is not a conflict_copy: {file_id}")
                conflict_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
                if conflict_path.exists() and not conflict_path.is_file():
                    raise ValueError(f"conflict_copy path is not a file: {record.path}")
                conflict_path.unlink(missing_ok=True)
                removed_conflict_paths[file_id] = conflict_path
                updated_document = remove_conflict_copy(
                    updated_document,
                    file_id=file_id,
                    updated_at=resolved_at,
                )
                filemap_changed = True

            removed_orphan_paths: list[Path] = []
            skipped_orphan_paths: list[str] = []
            for relative_path in requested_orphan_paths:
                orphan_path = _resolve_conflict_orphan_path(self.workspace.vault_root, relative_path)
                if not orphan_path.exists():
                    skipped_orphan_paths.append(relative_path)
                    continue
                if not orphan_path.is_file():
                    raise ValueError(f"conflict orphan path is not a file: {relative_path}")
                orphan_path.unlink(missing_ok=True)
                removed_orphan_paths.append(orphan_path)

            if filemap_changed:
                write_filemap_atomic(self.workspace.paths.filemap_path, updated_document)

            has_unresolved_conflicts = (
                _has_conflict_copy_records(updated_document)
                or _has_conflict_orphan_files(self.workspace.vault_root)
            )
            updated_state = snapshot.state
            if snapshot.state.has_unresolved_conflicts != has_unresolved_conflicts:
                updated_state = replace(
                    snapshot.state,
                    has_unresolved_conflicts=has_unresolved_conflicts,
                )
                upsert_vault_state(connection, updated_state)

        return DesktopConflictResolutionResult(
            state=updated_state,
            removed_conflict_paths=removed_conflict_paths,
            removed_orphan_paths=removed_orphan_paths,
            skipped_conflict_file_ids=skipped_conflict_file_ids,
            skipped_orphan_paths=skipped_orphan_paths,
        )

    def export_vault_package(
        self,
        package_path: Path,
        *,
        include_ai_raw: bool = False,
    ) -> DesktopVaultExportResult:
        resolved_package_path = package_path.resolve()
        try:
            resolved_package_path.relative_to(self.workspace.vault_root.resolve())
        except ValueError:
            pass
        else:
            raise ValueError("export-vault output package must be outside the vault root")
        if resolved_package_path.exists() and resolved_package_path.is_dir():
            raise ValueError("export-vault output package path must be a file")

        with closing(self.workspace._open_connection()) as connection:
            self._require_no_active_sync_apply_journal(connection, operation="export-vault")
            snapshot = self.workspace._load_snapshot_from_connection(connection)
            if snapshot.state.commit_in_progress:
                raise ValueError("export-vault cannot start while commit_in_progress is true")
            commit_journal = load_commit_intent_journal(connection, self.vault_id)
            if commit_journal is not None:
                raise ValueError("export-vault cannot start while commit_intent_journal is active")

        export_files = _list_migration_export_files(
            self.workspace.vault_root,
            include_ai_raw=include_ai_raw,
            package_path=resolved_package_path,
        )
        export_paths = [relative_path for relative_path, _ in export_files]
        missing_required = sorted(_MIGRATION_REQUIRED_FILES - set(export_paths))
        if missing_required:
            raise ValueError(
                "export-vault cannot build a complete migration package; missing required files: "
                + ", ".join(missing_required)
            )

        resolved_package_path.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(resolved_package_path, "w", compression=ZIP_DEFLATED) as archive:
            for relative_path, source_path in export_files:
                archive.write(source_path, arcname=relative_path)

        return DesktopVaultExportResult(
            package_path=resolved_package_path,
            vault_id=self.vault_id,
            exported_paths=export_paths,
            included_ai_raw=any(path.startswith(".ai/raw/") for path in export_paths),
            included_conflict_orphans=any(path.startswith(f"{CONFLICT_ORPHANS_DIRNAME}/") for path in export_paths),
        )

    def import_vault_package(
        self,
        package_path: Path,
    ) -> DesktopVaultImportResult:
        resolved_package_path = package_path.resolve()
        if not resolved_package_path.exists() or not resolved_package_path.is_file():
            raise FileNotFoundError(f"migration package not found: {resolved_package_path}")

        if self.workspace.vault_root.exists():
            if not self.workspace.vault_root.is_dir():
                raise ValueError("import-vault target root must be a directory")
            if any(self.workspace.vault_root.iterdir()):
                raise ValueError("import-vault requires an empty vault root")
        if self.workspace.paths.db_path.exists():
            raise ValueError("import-vault requires an empty local state database path")

        inspection = inspect_vault_package(resolved_package_path)
        if inspection.vault_id != self.vault_id:
            raise ValueError(
                "migration package vault_id does not match desktop config: "
                f"expected {self.vault_id}, got {inspection.vault_id}"
            )

        with ZipFile(resolved_package_path, "r") as archive:
            package_document, archive_entries = _read_migration_package(archive)
            self.workspace.vault_root.mkdir(parents=True, exist_ok=True)
            for relative_path in sorted(archive_entries):
                target_path = _resolve_workspace_file_path(self.workspace.vault_root, relative_path)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                _write_bytes_atomic(target_path, archive.read(archive_entries[relative_path]))

        tombstones = load_tombstone_ledger(self.workspace.paths.ledger_path)
        local_delete_sequence = max((record.local_delete_seq for record in tombstones), default=0)
        has_unresolved_conflicts = (
            _has_conflict_copy_records(package_document)
            or _has_conflict_orphan_files(self.workspace.vault_root)
        )

        with closing(self.workspace._open_connection()) as connection:
            imported_state = replace(
                load_vault_state(connection, self.vault_id)
                or VaultStateRecord(
                    vault_id=self.vault_id,
                    last_applied_revision=0,
                    remote_head_revision=0,
                    acked_revision=0,
                    pending_ack_to_server=[],
                    commit_in_progress=False,
                    last_manifest_summary=None,
                    last_manifest_summary_status="stale",
                    local_delete_sequence=local_delete_sequence,
                    has_unresolved_conflicts=has_unresolved_conflicts,
                    meta={"device_id": self.config.device_id},
                ),
                last_applied_revision=0,
                remote_head_revision=0,
                acked_revision=0,
                pending_ack_to_server=[],
                commit_in_progress=False,
                last_manifest_summary=None,
                last_manifest_summary_status="stale",
                local_delete_sequence=local_delete_sequence,
                has_unresolved_conflicts=has_unresolved_conflicts,
            )
            upsert_vault_state(connection, imported_state)

        imported_paths = sorted(archive_entries)
        return DesktopVaultImportResult(
            package_path=resolved_package_path,
            vault_id=self.vault_id,
            imported_paths=imported_paths,
            restored_ai_raw=any(path.startswith(".ai/raw/") for path in imported_paths),
            restored_conflict_orphans=any(
                path.startswith(f"{CONFLICT_ORPHANS_DIRNAME}/") for path in imported_paths
            ),
            state=imported_state,
        )

    def load_workspace_content(self, file_ids: Iterable[str]) -> dict[str, bytes]:
        snapshot = self.load_snapshot()
        file_by_id = {record.file_id: record for record in snapshot.document.files}
        selected_records = []

        for file_id in file_ids:
            record = file_by_id.get(file_id)
            if record is None:
                raise KeyError(f"file_id not found in workspace filemap: {file_id}")
            if record.status != "active":
                raise ValueError(f"workspace file is not active: {file_id}")
            selected_records.append(record)

        return self._load_workspace_content_for_records(selected_records)

    def _build_expected_source_version_token(self, record) -> Optional[str]:
        meta = record.meta or {}
        expected_token = meta.get("source_version_token")
        if isinstance(expected_token, str) and expected_token:
            return expected_token
        if record.content_hash is None:
            return None
        expected_mtime = meta.get("mtime")
        expected_size = meta.get("size")
        if (
            isinstance(expected_mtime, int)
            and expected_mtime >= 0
            and isinstance(expected_size, int)
            and expected_size >= 0
        ):
            return f"mtime:{expected_mtime}:size:{expected_size}:hash:{record.content_hash}"
        return f"updated_at:{record.updated_at}:hash:{record.content_hash}"

    def _load_workspace_content_for_records(self, records: Iterable[object]) -> dict[str, bytes]:
        drifted_file_ids: list[str] = []
        content_by_file_id: dict[str, bytes] = {}

        for record in records:
            content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            if not content_path.exists() or not content_path.is_file():
                drifted_file_ids.append(record.file_id)
                continue
            payload = content_path.read_bytes()
            content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
            mtime_ms = content_path.stat().st_mtime_ns // 1_000_000
            size_bytes = len(payload)
            expected_token = self._build_expected_source_version_token(record)
            current_token = f"mtime:{mtime_ms}:size:{size_bytes}:hash:{content_hash}"
            if (
                expected_token is None
                or content_hash != record.content_hash
                or current_token != expected_token
            ):
                drifted_file_ids.append(record.file_id)
                continue
            content_by_file_id[record.file_id] = payload

        if drifted_file_ids:
            raise ValueError(
                "workspace snapshot drift detected: " + ", ".join(sorted(drifted_file_ids))
            )
        return content_by_file_id

    def _document_with_current_blob_metadata(
        self,
        document: FileMapDocument,
        *,
        content_by_file_id: Mapping[str, bytes],
    ) -> FileMapDocument:
        updated_files: list[FileRecord] = []
        latest_updated_at = document.updated_at
        for record in document.files:
            payload = content_by_file_id.get(record.file_id)
            if record.status != "active" or payload is None:
                updated_files.append(record)
                continue

            content_hash = _compute_content_hash(payload)
            content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            stat = content_path.stat() if content_path.exists() and content_path.is_file() else None
            size_bytes = len(payload)
            mtime_ms = stat.st_mtime_ns // 1_000_000 if stat is not None else record.updated_at
            mime_type = (
                (record.meta or {}).get("mime_type")
                if isinstance((record.meta or {}).get("mime_type"), str)
                else _infer_imported_workspace_mime_type(record.path)
            )
            meta = dict(record.meta or {})
            meta.update(
                {
                    "blob_id": self.blob_crypto_provider.build_blob_id(content_hash),
                    "size": size_bytes,
                    "mtime": mtime_ms,
                    "source_version_token": _build_source_version_token(
                        content_hash=content_hash,
                        size_bytes=size_bytes,
                        mtime_ms=mtime_ms,
                    ),
                }
            )
            if mime_type is not None:
                meta["mime_type"] = mime_type
            updated_files.append(
                FileRecord(
                    file_id=record.file_id,
                    path=record.path,
                    type=record.type,
                    status=record.status,
                    updated_at=mtime_ms,
                    content_hash=content_hash,
                    last_known_revision=record.last_known_revision,
                    conflict_source_file_id=record.conflict_source_file_id,
                    meta=meta,
                )
            )
            latest_updated_at = max(latest_updated_at, mtime_ms)
        return document.replace_files(updated_files, updated_at=latest_updated_at)

    def load_workspace_content_for_document(
        self,
        document: FileMapDocument,
    ) -> dict[str, bytes]:
        return self._load_workspace_content_for_records(
            record for record in document.files if record.status == "active"
        )

    def build_tracked_change_commit_plan(self) -> DesktopTrackedChangeCommitPlan:
        snapshot = self.load_snapshot()
        return build_tracked_change_commit_plan(
            self.workspace.vault_root,
            snapshot.document,
            tombstones=snapshot.tombstones,
            current_local_delete_sequence=snapshot.state.local_delete_sequence,
            deleted_by_device=self.config.device_id,
            file_id_builder=self.file_id_builder,
            blob_id_builder=self.blob_crypto_provider.build_blob_id,
        )

    def _submit_tracked_change_plan(
        self,
        snapshot: DesktopWorkspaceSnapshot,
        plan: DesktopTrackedChangeCommitPlan,
        *,
        created_at: int,
        commit_intent_id: Optional[str] = None,
        cleanup_normalized_at: Optional[int] = None,
    ) -> DesktopCommitSessionResult:
        prepared = self._prepare_commit_with_snapshot(
            DesktopWorkspaceSnapshot(
                document=plan.document,
                state=replace(
                    snapshot.state,
                    local_delete_sequence=plan.local_delete_sequence,
                ),
                tombstones=plan.tombstones,
            ),
            created_at=created_at,
            content_by_file_id=plan.content_by_file_id,
            commit_intent_id=commit_intent_id,
        )
        resolved_cleanup_at = created_at if cleanup_normalized_at is None else cleanup_normalized_at

        try:
            network = self.workspace.runtime.session.submit_commit(
                prepared.submission,
                snapshot_table=prepared.snapshot_table,
            )
        except Exception:
            cleanup = self.cleanup_failed_commit(normalized_at=resolved_cleanup_at)
            raise

        if network.commit.status != "committed":
            cleanup = self.cleanup_failed_commit(normalized_at=resolved_cleanup_at)
            if network.commit.status == "conflict":
                cleanup = self._mark_commit_conflict_requires_pull(
                    cleanup,
                    conflict=network.commit.conflict,
                )
            return DesktopCommitSessionResult(
                prepared=prepared,
                network=network,
                cleanup=cleanup,
            )

        response = network.commit.response
        if response is None:
            raise ValueError("committed submit_commit result must include response")

        finalized = self._finalize_successful_commit_submission(
            prepared,
            committed_revision=response.new_revision,
            rewritten_at=resolved_cleanup_at,
        )
        return DesktopCommitSessionResult(
            prepared=prepared,
            network=network,
            finalized=finalized,
        )

    def cleanup_failed_commit(self, *, normalized_at: int) -> DesktopCommitCleanupResult:
        with closing(self.workspace._open_connection()) as connection:
            state = cleanup_failed_commit_submission(
                connection,
                self.vault_id,
                normalized_at=normalized_at,
            )
        return DesktopCommitCleanupResult(
            state=state,
            removed_staging_paths=cleanup_commit_staging_artifacts(self.workspace.vault_root),
        )

    def _mark_commit_conflict_requires_pull(
        self,
        cleanup: DesktopCommitCleanupResult,
        *,
        conflict,
    ) -> DesktopCommitCleanupResult:
        if conflict is None:
            return cleanup
        updated_state = replace(
            cleanup.state,
            remote_head_revision=max(cleanup.state.remote_head_revision, conflict.current_head_revision),
            last_manifest_summary=None,
            last_manifest_summary_status="stale",
        )
        with closing(self.workspace._open_connection()) as connection:
            upsert_vault_state(connection, updated_state)
        return replace(cleanup, state=updated_state)

    def prepare_commit(
        self,
        *,
        created_at: int,
        content_by_file_id: Mapping[str, bytes],
        encrypted_blob_by_file_id: Optional[Mapping[str, bytes]] = None,
        commit_intent_id: Optional[str] = None,
    ) -> DesktopPreparedCommit:
        snapshot = self.load_snapshot()
        return self._prepare_commit_with_snapshot(
            (
                replace(
                    snapshot,
                    document=self._document_with_current_blob_metadata(
                        snapshot.document,
                        content_by_file_id=content_by_file_id,
                    ),
                )
                if encrypted_blob_by_file_id is None
                else snapshot
            ),
            created_at=created_at,
            content_by_file_id=content_by_file_id,
            encrypted_blob_by_file_id=encrypted_blob_by_file_id,
            commit_intent_id=commit_intent_id,
        )

    def _prepare_commit_with_snapshot(
        self,
        snapshot: DesktopWorkspaceSnapshot,
        *,
        created_at: int,
        content_by_file_id: Mapping[str, bytes],
        encrypted_blob_by_file_id: Optional[Mapping[str, bytes]] = None,
        commit_intent_id: Optional[str] = None,
        file_version_directives: Optional[Iterable[FileVersionCommitDirective]] = None,
    ) -> DesktopPreparedCommit:
        snapshot = self._promote_unresolved_conflict_state_if_needed(snapshot)
        resolved_commit_intent_id = commit_intent_id or str(uuid4())
        resolved_encrypted_blob_by_file_id = (
            self.blob_crypto_provider.build_encrypted_blob_map(content_by_file_id)
            if encrypted_blob_by_file_id is None
            else dict(encrypted_blob_by_file_id)
        )

        with closing(self.workspace._open_connection()) as connection:
            self._require_no_active_sync_apply_journal(connection, operation="commit")
            submission = prepare_commit_submission(
                connection,
                state=snapshot.state,
                document=snapshot.document,
                tombstones=snapshot.tombstones,
                commit_intent_id=resolved_commit_intent_id,
                created_by_device=self.config.device_id,
                created_at=created_at,
            )
            if file_version_directives is not None:
                submission = replace(
                    submission,
                    file_version_directives=list(file_version_directives),
                )

            try:
                snapshot_materialization = materialize_content_snapshot_plan(
                    self.workspace.vault_root,
                    plan=submission.snapshot_plan,
                    document=snapshot.document,
                    content_by_file_id=content_by_file_id,
                )
                blob_staging_materialization = materialize_blob_staging_plan(
                    self.workspace.vault_root,
                    plan=submission.snapshot_plan,
                    snapshot_materialization=snapshot_materialization,
                    encrypted_blob_by_file_id=resolved_encrypted_blob_by_file_id,
                )
                snapshot_table = build_commit_snapshot_table(
                    submission.snapshot_plan,
                    snapshot_materialization=snapshot_materialization,
                    blob_staging_materialization=blob_staging_materialization,
                )
            except Exception:
                with suppress(Exception):
                    cleanup_failed_commit_submission(
                        connection,
                        self.vault_id,
                        normalized_at=created_at,
                    )
                cleanup_commit_staging_artifacts(self.workspace.vault_root)
                raise

        return DesktopPreparedCommit(
            snapshot=snapshot,
            submission=submission,
            snapshot_materialization=snapshot_materialization,
            blob_staging_materialization=blob_staging_materialization,
            snapshot_table=snapshot_table,
        )

    def _require_no_active_sync_apply_journal(self, connection, *, operation: str) -> None:
        journal = load_sync_apply_journal(connection, self.vault_id)
        if journal is None:
            return
        raise ValueError(
            f"{operation} is blocked while sync_apply_journal is active: "
            f"{journal.journal_id} ({journal.phase})"
        )

    def _write_pull_apply_plan_file(self, plan: DesktopPullApplyPlan) -> None:
        _write_bytes_atomic(
            self.workspace.paths.sync_apply_plan_path,
            _serialize_pull_apply_plan(plan),
        )

    def _load_pull_apply_plan_file(self) -> Optional[DesktopPullApplyPlan]:
        path = self.workspace.paths.sync_apply_plan_path
        if not path.exists() or not path.is_file():
            return None
        return _deserialize_pull_apply_plan(path.read_bytes())

    def _cleanup_pull_apply_plan_file(self) -> Optional[Path]:
        path = self.workspace.paths.sync_apply_plan_path
        if not path.exists():
            return None
        path.unlink(missing_ok=True)
        return path

    def _validate_recovery_pull_apply_plan(
        self,
        plan: DesktopPullApplyPlan,
        *,
        journal: SyncApplyJournalRecord,
    ) -> None:
        if plan.vault_id != journal.vault_id:
            raise ValueError("recovery pull apply plan vault_id does not match sync_apply_journal")
        if plan.revision != journal.target_revision:
            raise ValueError("recovery pull apply plan revision does not match sync_apply_journal")
        if plan.ops_hash != journal.ops_hash:
            raise ValueError("recovery pull apply plan ops_hash does not match sync_apply_journal")

    def _materializing_workspace_matches_document(
        self,
        snapshot: Optional[DesktopWorkspaceSnapshot],
    ) -> bool:
        return snapshot is not None and self._workspace_matches_document(snapshot.document)

    def _finalize_materializing_pull_apply_recovery(
        self,
        journal: SyncApplyJournalRecord,
        *,
        normalized_at: int,
    ) -> DesktopPullApplyRecoveryResult:
        with closing(self.workspace._open_connection()) as connection:
            upsert_sync_apply_journal(
                connection,
                replace(journal, phase="finalizing", updated_at=normalized_at),
            )
            state = recover_sync_apply_finalizing_state(connection, self.vault_id)
        removed = self._cleanup_pull_apply_staging_artifacts()
        return DesktopPullApplyRecoveryResult(
            mode="finalized",
            requires_full_pull=state.last_manifest_summary_status != "valid",
            journal_phase="materializing",
            state=state,
            removed_staging_paths=removed,
            isolated_staging_paths=[],
            removed_plan_path=self._cleanup_pull_apply_plan_file(),
        )

    def _degrade_pull_apply_recovery(
        self,
        journal: SyncApplyJournalRecord,
        *,
        normalized_at: int,
    ) -> DesktopPullApplyRecoveryResult:
        with closing(self.workspace._open_connection()) as connection:
            state = load_vault_state(connection, self.vault_id)
            if state is None:
                raise KeyError(f"vault_state not found: {self.vault_id}")
            degraded_state = apply_manifest_summary_stale(state)
            upsert_vault_state(connection, degraded_state)
            clear_sync_apply_journal(connection, self.vault_id)
        isolated_paths = isolate_staging_orphans(self.workspace.vault_root)
        return DesktopPullApplyRecoveryResult(
            mode="degraded",
            requires_full_pull=True,
            journal_phase=journal.phase,
            state=degraded_state,
            removed_staging_paths=[],
            isolated_staging_paths=isolated_paths,
            removed_plan_path=self._cleanup_pull_apply_plan_file(),
        )

    def _isolate_unjournaled_pull_apply_staging(self) -> list[Path]:
        staging_root = self.workspace.vault_root / STAGING_DIRNAME
        if not staging_root.exists():
            return []
        moved_paths: list[Path] = []
        for staging_path in sorted(
            (
                path
                for path in staging_root.rglob("*")
                if path.is_file() and path.name.endswith(".staging") and not path.name.endswith(".blob.staging")
            ),
            key=lambda item: str(item.relative_to(staging_root)),
        ):
            moved_paths.append(move_staging_orphan(self.workspace.vault_root, staging_path))
        return moved_paths

    def _workspace_matches_document(self, document) -> bool:
        for record in document.files:
            if record.status != "active":
                continue
            content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            if not content_path.exists() or not content_path.is_file():
                return False
            if _compute_content_hash(content_path.read_bytes()) != record.content_hash:
                return False
        return True

    def _cleanup_pull_apply_staging_artifacts(self) -> list[Path]:
        staging_root = self.workspace.vault_root / STAGING_DIRNAME
        if not staging_root.exists():
            return []
        removed_paths: list[Path] = []
        for staging_path in sorted(
            (
                path
                for path in staging_root.rglob("*")
                if path.is_file() and path.name.endswith(".staging") and not path.name.endswith(".blob.staging")
            ),
            key=lambda item: str(item.relative_to(staging_root)),
        ):
            staging_path.unlink(missing_ok=True)
            removed_paths.append(staging_path)
        return removed_paths

    def _finalize_successful_commit_submission(
        self,
        prepared: DesktopPreparedCommit,
        *,
        committed_revision: int,
        rewritten_at: int,
    ) -> CommitFinalizeCleanupResult:
        current_tombstones = load_tombstone_ledger(self.workspace.paths.ledger_path)
        with closing(self.workspace._open_connection()) as connection:
            finalized = finalize_commit_submission_cleanup(
                connection,
                vault_root=self.workspace.vault_root,
                ledger_path=self.workspace.paths.ledger_path,
                manifest=prepared.submission.manifest,
                local_tombstones=current_tombstones,
                committed_revision=committed_revision,
            )
        updated_document = rebuild_filemap_from_manifest(
            prepared.snapshot.document,
            finalized.finalized.manifest,
            local_tombstones=finalized.finalized.tombstones,
            rewritten_at=rewritten_at,
        )
        write_filemap_atomic(self.workspace.paths.filemap_path, updated_document)
        return finalized

    def _submit_prepared_commit(
        self,
        prepared: DesktopPreparedCommit,
        *,
        resolved_cleanup_at: int,
    ) -> DesktopCommitSessionResult:
        try:
            network = self.workspace.runtime.session.submit_commit(
                prepared.submission,
                snapshot_table=prepared.snapshot_table,
            )
        except Exception:
            self.cleanup_failed_commit(normalized_at=resolved_cleanup_at)
            raise

        if network.commit.status != "committed":
            cleanup = self.cleanup_failed_commit(normalized_at=resolved_cleanup_at)
            if network.commit.status == "conflict":
                cleanup = self._mark_commit_conflict_requires_pull(
                    cleanup,
                    conflict=network.commit.conflict,
                )
            return DesktopCommitSessionResult(
                prepared=prepared,
                network=network,
                cleanup=cleanup,
            )

        response = network.commit.response
        if response is None:
            raise ValueError("committed submit_commit result must include response")

        finalized = self._finalize_successful_commit_submission(
            prepared,
            committed_revision=response.new_revision,
            rewritten_at=resolved_cleanup_at,
        )
        return DesktopCommitSessionResult(
            prepared=prepared,
            network=network,
            finalized=finalized,
        )

    def submit_detected_changes(
        self,
        *,
        created_at: int,
        commit_intent_id: Optional[str] = None,
        cleanup_normalized_at: Optional[int] = None,
    ) -> DesktopCommitSessionResult:
        snapshot = self.load_snapshot()
        plan = build_tracked_change_commit_plan(
            self.workspace.vault_root,
            snapshot.document,
            tombstones=snapshot.tombstones,
            current_local_delete_sequence=snapshot.state.local_delete_sequence,
            deleted_by_device=self.config.device_id,
            file_id_builder=self.file_id_builder,
            blob_id_builder=self.blob_crypto_provider.build_blob_id,
        )
        if self.detected_submit_plan_hook is not None:
            self.detected_submit_plan_hook(plan)
        refreshed_content_by_file_id = self.load_workspace_content_for_document(plan.document)
        plan = replace(
            plan,
            content_by_file_id=refreshed_content_by_file_id,
        )
        return self._submit_tracked_change_plan(
            snapshot,
            plan,
            created_at=created_at,
            commit_intent_id=commit_intent_id,
            cleanup_normalized_at=cleanup_normalized_at,
        )

    def submit_detected_changes_if_needed(
        self,
        *,
        created_at: int,
        commit_intent_id: Optional[str] = None,
        cleanup_normalized_at: Optional[int] = None,
    ) -> Optional[DesktopCommitSessionResult]:
        snapshot = self.load_snapshot()
        change_set = detect_local_workspace_changes(
            self.workspace.vault_root,
            snapshot.document,
        )
        has_pending_tombstones = any(record.deleted_revision is None for record in snapshot.tombstones)
        if not change_set.changes and not has_pending_tombstones:
            return None
        plan = build_tracked_change_commit_plan(
            self.workspace.vault_root,
            snapshot.document,
            change_set,
            tombstones=snapshot.tombstones,
            current_local_delete_sequence=snapshot.state.local_delete_sequence,
            deleted_by_device=self.config.device_id,
            file_id_builder=self.file_id_builder,
            blob_id_builder=self.blob_crypto_provider.build_blob_id,
        )
        if self.detected_submit_plan_hook is not None:
            self.detected_submit_plan_hook(plan)
        refreshed_content_by_file_id = self.load_workspace_content_for_document(plan.document)
        plan = replace(
            plan,
            content_by_file_id=refreshed_content_by_file_id,
        )
        return self._submit_tracked_change_plan(
            snapshot,
            plan,
            created_at=created_at,
            commit_intent_id=commit_intent_id,
            cleanup_normalized_at=cleanup_normalized_at,
        )

    def submit_commit(
        self,
        *,
        created_at: int,
        content_by_file_id: Mapping[str, bytes],
        encrypted_blob_by_file_id: Optional[Mapping[str, bytes]] = None,
        commit_intent_id: Optional[str] = None,
        cleanup_normalized_at: Optional[int] = None,
        file_version_directives: Optional[Iterable[FileVersionCommitDirective]] = None,
    ) -> DesktopCommitSessionResult:
        snapshot = self.load_snapshot()
        prepared = self._prepare_commit_with_snapshot(
            (
                replace(
                    snapshot,
                    document=self._document_with_current_blob_metadata(
                        snapshot.document,
                        content_by_file_id=content_by_file_id,
                    ),
                )
                if encrypted_blob_by_file_id is None
                else snapshot
            ),
            created_at=created_at,
            content_by_file_id=content_by_file_id,
            encrypted_blob_by_file_id=encrypted_blob_by_file_id,
            commit_intent_id=commit_intent_id,
            file_version_directives=file_version_directives,
        )
        resolved_cleanup_at = created_at if cleanup_normalized_at is None else cleanup_normalized_at
        return self._submit_prepared_commit(
            prepared,
            resolved_cleanup_at=resolved_cleanup_at,
        )

    def submit_workspace_commit(
        self,
        *,
        created_at: int,
        file_ids: Iterable[str],
        encrypted_blob_by_file_id: Optional[Mapping[str, bytes]] = None,
        commit_intent_id: Optional[str] = None,
        cleanup_normalized_at: Optional[int] = None,
        file_version_directives: Optional[Iterable[FileVersionCommitDirective]] = None,
    ) -> DesktopCommitSessionResult:
        snapshot = self.load_snapshot()
        requested_file_ids = list(file_ids)
        file_by_id = {record.file_id: record for record in snapshot.document.files}
        selected_records = []
        for file_id in requested_file_ids:
            record = file_by_id.get(file_id)
            if record is None:
                raise KeyError(f"file_id not found in workspace filemap: {file_id}")
            if record.status != "active":
                raise ValueError(f"workspace file is not active: {file_id}")
            selected_records.append(record)
        content_by_file_id = self._load_workspace_content_for_records(selected_records)
        prepared = self._prepare_commit_with_snapshot(
            (
                replace(
                    snapshot,
                    document=self._document_with_current_blob_metadata(
                        snapshot.document,
                        content_by_file_id=content_by_file_id,
                    ),
                )
                if encrypted_blob_by_file_id is None
                else snapshot
            ),
            created_at=created_at,
            content_by_file_id=content_by_file_id,
            encrypted_blob_by_file_id=encrypted_blob_by_file_id,
            commit_intent_id=commit_intent_id,
            file_version_directives=file_version_directives,
        )
        resolved_cleanup_at = created_at if cleanup_normalized_at is None else cleanup_normalized_at
        return self._submit_prepared_commit(
            prepared,
            resolved_cleanup_at=resolved_cleanup_at,
        )


def build_desktop_sync_service(
    config: DesktopSyncHttpConfig,
    vault_root: Path,
    *,
    db_path: Optional[Path] = None,
    api_opener: Optional[UrlopenLike] = None,
    blob_opener: Optional[UrlopenLike] = None,
    ai_opener: Optional[UrlopenLike] = None,
    blob_crypto_provider: Optional[DesktopBlobCryptoProvider] = None,
    file_id_builder: Optional[Callable[[str], str]] = None,
    allow_placeholder_crypto: bool = False,
) -> DesktopSyncService:
    from .change_detection import build_generated_file_id

    resolved_blob_crypto_provider = (
        _build_default_blob_crypto_provider(
            config,
            allow_placeholder_crypto=allow_placeholder_crypto,
        )
        if blob_crypto_provider is None
        else blob_crypto_provider
    )

    return DesktopSyncService(
        workspace=build_desktop_vault_workspace(
            config,
            vault_root,
            db_path=db_path,
            api_opener=api_opener,
            blob_opener=blob_opener,
        ),
        blob_crypto_provider=resolved_blob_crypto_provider,
        file_id_builder=build_generated_file_id if file_id_builder is None else file_id_builder,
        ai_opener=ai_opener,
    )
