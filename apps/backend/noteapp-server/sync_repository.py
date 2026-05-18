from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional


REPOSITORY_VERSION_KEY = "_repository_version"
SQLITE_SCHEMA_VERSION = 2


class StateRepositoryConflict(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class JsonStateRepository:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path

    def load(self, default_state: dict[str, Any]) -> dict[str, Any]:
        if not self.state_path.exists():
            state = deepcopy(default_state)
            state[REPOSITORY_VERSION_KEY] = 0
            return state
        with self.state_path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
        state[REPOSITORY_VERSION_KEY] = 0
        return state

    def save(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(_strip_repository_metadata(state), handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        tmp_path.replace(self.state_path)


class SQLiteStateRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        if str(db_path) == ":memory:":
            raise ValueError("The SQLite repository profile requires a file-backed database path.")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def load(self, default_state: dict[str, Any]) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT version, payload_json FROM sync_state WHERE id = ?",
                ("current",),
            ).fetchone()
        if row is None:
            state = deepcopy(default_state)
            state[REPOSITORY_VERSION_KEY] = 0
            return state
        state = json.loads(row["payload_json"])
        state[REPOSITORY_VERSION_KEY] = int(row["version"])
        return state

    def save(self, state: dict[str, Any]) -> None:
        expected_version = state.get(REPOSITORY_VERSION_KEY)
        if expected_version is not None and not isinstance(expected_version, int):
            raise StateRepositoryConflict("state_write_conflict", "Repository version marker must be an integer.")
        self._save_state(state, expected_version=expected_version)

    def import_state(self, state: dict[str, Any], *, force: bool = False) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT version FROM sync_state WHERE id = ?", ("current",)).fetchone()
            if row is not None and not force:
                connection.rollback()
                raise StateRepositoryConflict("sqlite_state_exists", "SQLite state already exists; pass force=True to replace it.")
            next_version = 1 if row is None else int(row["version"]) + 1
            self._write_state_locked(connection, state, version=next_version)
            connection.commit()

    def import_json_file(self, json_state_path: Path, *, force: bool = False) -> None:
        with json_state_path.open("r", encoding="utf-8") as handle:
            self.import_state(json.load(handle), force=force)

    def migrate(self) -> None:
        with self._connection() as connection:
            connection.executescript(SQLITE_SCHEMA)
            applied_at_ms = _now_ms()
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, name, applied_at_ms)
                VALUES (?, ?, ?)
                """,
                (1, "initial_sync_repository_schema", applied_at_ms),
            )
            _ensure_column(connection, "blobs", "object_key", "TEXT")
            _ensure_column(connection, "blobs", "status", "TEXT")
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, name, applied_at_ms)
                VALUES (?, ?, ?)
                """,
                (SQLITE_SCHEMA_VERSION, "object_storage_blob_metadata", applied_at_ms),
            )
            connection.commit()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _save_state(self, state: dict[str, Any], *, expected_version: Optional[int]) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT version FROM sync_state WHERE id = ?", ("current",)).fetchone()
            current_version = None if row is None else int(row["version"])
            if current_version is None:
                if expected_version not in (None, 0):
                    connection.rollback()
                    raise StateRepositoryConflict("state_write_conflict", "State was created by another process.")
                next_version = 1
            else:
                if expected_version is not None and expected_version != current_version:
                    connection.rollback()
                    raise StateRepositoryConflict("state_write_conflict", "State changed before this write could be saved.")
                next_version = current_version + 1
            self._write_state_locked(connection, state, version=next_version)
            state[REPOSITORY_VERSION_KEY] = next_version
            connection.commit()

    def _write_state_locked(self, connection: sqlite3.Connection, state: dict[str, Any], *, version: int) -> None:
        persisted_state = _strip_repository_metadata(state)
        payload_json = _json_payload(persisted_state)
        connection.execute(
            """
            INSERT INTO sync_state(id, version, payload_json, updated_at_ms)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                version = excluded.version,
                payload_json = excluded.payload_json,
                updated_at_ms = excluded.updated_at_ms
            """,
            ("current", version, payload_json, _now_ms()),
        )
        _replace_projection(connection, persisted_state)


def sqlite_path_from_database_url(database_url: str) -> Path:
    prefix = "sqlite:///"
    if database_url == "sqlite:///:memory:":
        raise ValueError("The SQLite repository profile requires a file-backed database URL.")
    if not database_url.startswith(prefix):
        raise ValueError("Only sqlite:/// database URLs are supported by the current noteapp-server profile.")
    return Path(database_url[len(prefix) :])


def _now_ms() -> int:
    return int(time.time() * 1000)


def _strip_repository_metadata(state: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(state)
    result.pop(REPOSITORY_VERSION_KEY, None)
    return result


def _json_payload(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _as_int(payload: dict[str, Any], key: str) -> Optional[int]:
    value = payload.get(key)
    return value if isinstance(value, int) else None


def _as_str(payload: dict[str, Any], key: str) -> Optional[str]:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _as_bool_int(payload: dict[str, Any], key: str) -> int:
    return 1 if bool(payload.get(key)) else 0


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def _replace_projection(connection: sqlite3.Connection, state: dict[str, Any]) -> None:
    for table in PROJECTION_TABLES:
        connection.execute(f"DELETE FROM {table}")
    _project_users(connection, state)
    _project_devices(connection, state)
    _project_sessions(connection, state)
    _project_vaults(connection, state)
    _project_blobs(connection, state)
    _project_capabilities(connection, state)
    _project_resumable_upload_sessions(connection, state)


def _project_users(connection: sqlite3.Connection, state: dict[str, Any]) -> None:
    users = state.get("users")
    if isinstance(users, dict):
        for user_id, user in users.items():
            if not isinstance(user_id, str) or not isinstance(user, dict):
                continue
            connection.execute(
                """
                INSERT INTO users(user_id, account_key, display_name, status, created_at_ms, updated_at_ms, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    _as_str(user, "account_key"),
                    _as_str(user, "display_name"),
                    _as_str(user, "status"),
                    _as_int(user, "created_at_ms"),
                    _as_int(user, "updated_at_ms"),
                    _json_payload(user),
                ),
            )

    user_logins = state.get("user_logins")
    if isinstance(user_logins, dict):
        for account_key, user_id in user_logins.items():
            if isinstance(account_key, str) and isinstance(user_id, str):
                connection.execute(
                    "INSERT INTO user_logins(account_key, user_id) VALUES (?, ?)",
                    (account_key, user_id),
                )


def _project_devices(connection: sqlite3.Connection, state: dict[str, Any]) -> None:
    devices = state.get("devices")
    if not isinstance(devices, dict):
        return
    for device_id, device in devices.items():
        if not isinstance(device_id, str) or not isinstance(device, dict):
            continue
        connection.execute(
            """
            INSERT INTO devices(
                device_id, user_id, device_name, platform, app_version, protocol_version,
                session_id, revoked, registered_at_ms, last_seen_at_ms, revoked_at_ms, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                _as_str(device, "user_id"),
                _as_str(device, "device_name"),
                _as_str(device, "platform"),
                _as_str(device, "app_version"),
                _as_str(device, "protocol_version"),
                _as_str(device, "session_id"),
                _as_bool_int(device, "revoked"),
                _as_int(device, "registered_at_ms"),
                _as_int(device, "last_seen_at_ms"),
                _as_int(device, "revoked_at_ms"),
                _json_payload(device),
            ),
        )


def _project_sessions(connection: sqlite3.Connection, state: dict[str, Any]) -> None:
    sessions = state.get("sessions")
    if isinstance(sessions, dict):
        for session_id, session in sessions.items():
            if not isinstance(session_id, str) or not isinstance(session, dict):
                continue
            connection.execute(
                """
                INSERT INTO sessions(
                    session_id, user_id, device_id, access_token, refresh_token,
                    access_expires_at_ms, refresh_expires_at_ms, created_at_ms,
                    updated_at_ms, revoked_at_ms, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    _as_str(session, "user_id"),
                    _as_str(session, "device_id"),
                    _as_str(session, "access_token"),
                    _as_str(session, "refresh_token"),
                    _as_int(session, "access_expires_at_ms"),
                    _as_int(session, "refresh_expires_at_ms"),
                    _as_int(session, "created_at_ms"),
                    _as_int(session, "updated_at_ms"),
                    _as_int(session, "revoked_at_ms"),
                    _json_payload(session),
                ),
            )

    tokens = state.get("tokens")
    if isinstance(tokens, dict):
        for token, session_id in tokens.items():
            if isinstance(token, str) and isinstance(session_id, str):
                connection.execute("INSERT INTO access_tokens(token, session_id) VALUES (?, ?)", (token, session_id))

    refresh_tokens = state.get("refresh_tokens")
    if isinstance(refresh_tokens, dict):
        for token, session_id in refresh_tokens.items():
            if isinstance(token, str) and isinstance(session_id, str):
                connection.execute("INSERT INTO refresh_tokens(token, session_id) VALUES (?, ?)", (token, session_id))


def _project_vaults(connection: sqlite3.Connection, state: dict[str, Any]) -> None:
    vaults = state.get("vaults")
    if not isinstance(vaults, dict):
        return
    for vault_id, vault in vaults.items():
        if not isinstance(vault_id, str) or not isinstance(vault, dict):
            continue
        connection.execute(
            """
            INSERT INTO vaults(vault_id, head_revision, manifest_summary, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                vault_id,
                _as_int(vault, "head_revision"),
                _as_str(vault, "manifest_summary"),
                _json_payload(vault),
            ),
        )
        _project_manifests(connection, vault_id, vault)
        _project_commits(connection, vault_id, vault)
        _project_acks(connection, vault_id, vault)
        _project_tombstone_gc_runs(connection, vault_id, vault)
        _project_file_versions(connection, vault_id, vault)


def _project_manifests(connection: sqlite3.Connection, vault_id: str, vault: dict[str, Any]) -> None:
    manifests = vault.get("manifests")
    if not isinstance(manifests, dict):
        return
    for revision_key, manifest in manifests.items():
        if not isinstance(manifest, dict):
            continue
        revision = _as_int(manifest, "revision")
        if revision is None:
            try:
                revision = int(revision_key)
            except (TypeError, ValueError):
                continue
        connection.execute(
            """
            INSERT INTO manifests(vault_id, revision, summary_hash, created_by_device, created_at_ms, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                vault_id,
                revision,
                _as_str(manifest, "summary_hash"),
                _as_str(manifest, "created_by_device"),
                _as_int(manifest, "created_at"),
                _json_payload(manifest),
            ),
        )


def _project_commits(connection: sqlite3.Connection, vault_id: str, vault: dict[str, Any]) -> None:
    commits = vault.get("commits")
    if not isinstance(commits, dict):
        return
    for commit_intent_id, commit in commits.items():
        if not isinstance(commit_intent_id, str) or not isinstance(commit, dict):
            continue
        connection.execute(
            """
            INSERT INTO commits(vault_id, commit_intent_id, revision, intent_manifest_hash, created_by_device, committed_at_ms, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vault_id,
                commit_intent_id,
                _as_int(commit, "revision"),
                _as_str(commit, "intent_manifest_hash"),
                _as_str(commit, "created_by_device"),
                _as_int(commit, "committed_at_ms"),
                _json_payload(commit),
            ),
        )


def _project_acks(connection: sqlite3.Connection, vault_id: str, vault: dict[str, Any]) -> None:
    acks = vault.get("acks")
    if not isinstance(acks, dict):
        return
    for device_id, acked_revision in acks.items():
        if isinstance(device_id, str) and isinstance(acked_revision, int):
            connection.execute(
                "INSERT INTO vault_acks(vault_id, device_id, acked_revision) VALUES (?, ?, ?)",
                (vault_id, device_id, acked_revision),
            )


def _project_tombstone_gc_runs(connection: sqlite3.Connection, vault_id: str, vault: dict[str, Any]) -> None:
    tombstone_gc = vault.get("tombstone_gc")
    if not isinstance(tombstone_gc, dict):
        return
    runs = tombstone_gc.get("runs")
    if not isinstance(runs, list):
        return
    for index, run in enumerate(runs):
        if isinstance(run, dict):
            connection.execute(
                "INSERT INTO tombstone_gc_runs(vault_id, run_index, new_revision, reason, payload_json) VALUES (?, ?, ?, ?, ?)",
                (vault_id, index, _as_int(run, "new_revision"), _as_str(run, "reason"), _json_payload(run)),
            )


def _project_file_versions(connection: sqlite3.Connection, vault_id: str, vault: dict[str, Any]) -> None:
    file_versions = vault.get("file_versions")
    if not isinstance(file_versions, dict):
        return
    records = file_versions.get("records")
    if not isinstance(records, dict):
        return
    for version_id, record in records.items():
        if not isinstance(version_id, str) or not isinstance(record, dict):
            continue
        connection.execute(
            """
            INSERT INTO file_versions(version_id, vault_id, file_id, revision, blob_id, source, created_at_ms, pinned, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                vault_id,
                _as_str(record, "file_id"),
                _as_int(record, "revision"),
                _as_str(record, "blob_id"),
                _as_str(record, "source"),
                _as_int(record, "created_at"),
                _as_bool_int(record, "pinned"),
                _json_payload(record),
            ),
        )


def _project_blobs(connection: sqlite3.Connection, state: dict[str, Any]) -> None:
    blobs = state.get("blobs")
    if not isinstance(blobs, dict):
        return
    for blob_id, blob in blobs.items():
        if not isinstance(blob_id, str) or not isinstance(blob, dict):
            continue
        connection.execute(
            """
            INSERT INTO blobs(blob_id, filename, object_key, status, encrypted_size, content_hash, encrypted_sha256, uploaded_at_ms, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blob_id,
                _as_str(blob, "filename"),
                _as_str(blob, "object_key"),
                _as_str(blob, "status"),
                _as_int(blob, "encrypted_size"),
                _as_str(blob, "content_hash"),
                _as_str(blob, "encrypted_sha256"),
                _as_int(blob, "uploaded_at_ms"),
                _json_payload(blob),
            ),
        )


def _project_capabilities(connection: sqlite3.Connection, state: dict[str, Any]) -> None:
    capabilities = state.get("capabilities")
    if not isinstance(capabilities, dict):
        return
    for token, capability in capabilities.items():
        if not isinstance(token, str) or not isinstance(capability, dict):
            continue
        connection.execute(
            """
            INSERT INTO capabilities(token, kind, vault_id, device_id, blob_id, expires_at_ms, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token,
                _as_str(capability, "kind"),
                _as_str(capability, "vault_id"),
                _as_str(capability, "device_id"),
                _as_str(capability, "blob_id"),
                _as_int(capability, "expires_at_ms"),
                _json_payload(capability),
            ),
        )


def _project_resumable_upload_sessions(connection: sqlite3.Connection, state: dict[str, Any]) -> None:
    sessions = state.get("resumable_upload_sessions")
    if not isinstance(sessions, dict):
        return
    for session_id, session in sessions.items():
        if not isinstance(session_id, str) or not isinstance(session, dict):
            continue
        connection.execute(
            """
            INSERT INTO resumable_upload_sessions(
                session_id, vault_id, device_id, blob_id, encrypted_size,
                chunk_size, expires_at_ms, created_at_ms, updated_at_ms, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                _as_str(session, "vault_id"),
                _as_str(session, "device_id"),
                _as_str(session, "blob_id"),
                _as_int(session, "encrypted_size"),
                _as_int(session, "chunk_size"),
                _as_int(session, "expires_at_ms"),
                _as_int(session, "created_at_ms"),
                _as_int(session, "updated_at_ms"),
                _json_payload(session),
            ),
        )


PROJECTION_TABLES = [
    "users",
    "user_logins",
    "devices",
    "sessions",
    "access_tokens",
    "refresh_tokens",
    "vaults",
    "manifests",
    "commits",
    "vault_acks",
    "blobs",
    "capabilities",
    "resumable_upload_sessions",
    "file_versions",
    "tombstone_gc_runs",
]


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    account_key TEXT,
    display_name TEXT,
    status TEXT,
    created_at_ms INTEGER,
    updated_at_ms INTEGER,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_logins (
    account_key TEXT PRIMARY KEY,
    user_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    user_id TEXT,
    device_name TEXT,
    platform TEXT,
    app_version TEXT,
    protocol_version TEXT,
    session_id TEXT,
    revoked INTEGER NOT NULL DEFAULT 0,
    registered_at_ms INTEGER,
    last_seen_at_ms INTEGER,
    revoked_at_ms INTEGER,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT,
    device_id TEXT,
    access_token TEXT,
    refresh_token TEXT,
    access_expires_at_ms INTEGER,
    refresh_expires_at_ms INTEGER,
    created_at_ms INTEGER,
    updated_at_ms INTEGER,
    revoked_at_ms INTEGER,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS access_tokens (
    token TEXT PRIMARY KEY,
    session_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token TEXT PRIMARY KEY,
    session_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vaults (
    vault_id TEXT PRIMARY KEY,
    head_revision INTEGER,
    manifest_summary TEXT,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manifests (
    vault_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    summary_hash TEXT,
    created_by_device TEXT,
    created_at_ms INTEGER,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (vault_id, revision)
);

CREATE TABLE IF NOT EXISTS commits (
    vault_id TEXT NOT NULL,
    commit_intent_id TEXT NOT NULL,
    revision INTEGER,
    intent_manifest_hash TEXT,
    created_by_device TEXT,
    committed_at_ms INTEGER,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (vault_id, commit_intent_id)
);

CREATE TABLE IF NOT EXISTS vault_acks (
    vault_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    acked_revision INTEGER NOT NULL,
    PRIMARY KEY (vault_id, device_id)
);

CREATE TABLE IF NOT EXISTS blobs (
    blob_id TEXT PRIMARY KEY,
    filename TEXT,
    object_key TEXT,
    status TEXT,
    encrypted_size INTEGER,
    content_hash TEXT,
    encrypted_sha256 TEXT,
    uploaded_at_ms INTEGER,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capabilities (
    token TEXT PRIMARY KEY,
    kind TEXT,
    vault_id TEXT,
    device_id TEXT,
    blob_id TEXT,
    expires_at_ms INTEGER,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resumable_upload_sessions (
    session_id TEXT PRIMARY KEY,
    vault_id TEXT,
    device_id TEXT,
    blob_id TEXT,
    encrypted_size INTEGER,
    chunk_size INTEGER,
    expires_at_ms INTEGER,
    created_at_ms INTEGER,
    updated_at_ms INTEGER,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS file_versions (
    version_id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL,
    file_id TEXT,
    revision INTEGER,
    blob_id TEXT,
    source TEXT,
    created_at_ms INTEGER,
    pinned INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tombstone_gc_runs (
    vault_id TEXT NOT NULL,
    run_index INTEGER NOT NULL,
    new_revision INTEGER,
    reason TEXT,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (vault_id, run_index)
);

CREATE INDEX IF NOT EXISTS idx_devices_user_id ON devices(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user_device ON sessions(user_id, device_id);
CREATE INDEX IF NOT EXISTS idx_manifests_summary ON manifests(summary_hash);
CREATE INDEX IF NOT EXISTS idx_blobs_content_hash ON blobs(content_hash);
CREATE INDEX IF NOT EXISTS idx_file_versions_file ON file_versions(vault_id, file_id, revision);
"""
