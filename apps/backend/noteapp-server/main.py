from __future__ import annotations

import os
import json
import logging
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from blob_storage import FileSystemBlobStore, S3CompatibleBlobStore
from sync_repository import SQLiteStateRepository, sqlite_path_from_database_url
from sync_store import BlobCapabilityError, CommitConflict, SyncStore, SyncStoreError


class RuntimeMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[str] = Counter()
        self._status_codes: Counter[str] = Counter()
        self._methods: Counter[str] = Counter()
        self.started_at_ms = 0
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.started_at_ms = _now_ms()
            self._counters = Counter()
            self._status_codes = Counter()
            self._methods = Counter()

    def record_request(self, *, method: str, route_path: str, status_code: int, error_code: Optional[str]) -> None:
        with self._lock:
            self._counters["requests_total"] += 1
            self._status_codes[str(status_code)] += 1
            self._methods[method] += 1
            if status_code >= 400:
                self._counters["errors_total"] += 1
            if route_path == "/vaults/{vault_id}/commits" and method == "POST":
                self._counters["commit_attempts_total"] += 1
                if error_code in {"base_revision_conflict", "manifest_conflict"}:
                    self._counters["commit_conflicts_total"] += 1
            if route_path in {
                "/_capabilities/blobs/{capability_token}",
                "/_capabilities/blobs/resumable-upload/{session_id}",
            } and method == "PUT":
                self._counters["blob_upload_attempts_total"] += 1
                if status_code >= 400:
                    self._counters["blob_upload_failures_total"] += 1
            if error_code == "capability_expired":
                self._counters["capability_expired_total"] += 1
            if error_code == "capability_not_found" and route_path.startswith("/_capabilities/"):
                self._counters["capability_missing_or_revoked_total"] += 1

    def record_capability_revocation_event(self) -> None:
        with self._lock:
            self._counters["capability_revocation_events_total"] += 1

    def record_tombstone_gc(self, reclaimed_count: int) -> None:
        with self._lock:
            self._counters["tombstone_gc_deleted_total"] += max(0, reclaimed_count)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = Counter(self._counters)
            status_codes = dict(self._status_codes)
            methods = dict(self._methods)
            started_at_ms = self.started_at_ms
        requests_total = counters["requests_total"]
        commit_attempts_total = counters["commit_attempts_total"]
        blob_upload_attempts_total = counters["blob_upload_attempts_total"]
        return {
            "ok": True,
            "started_at_ms": started_at_ms,
            "uptime_ms": max(0, _now_ms() - started_at_ms),
            "requests_total": requests_total,
            "errors_total": counters["errors_total"],
            "error_rate": _rate(counters["errors_total"], requests_total),
            "status_codes": status_codes,
            "methods": methods,
            "commit_attempts_total": commit_attempts_total,
            "commit_conflicts_total": counters["commit_conflicts_total"],
            "commit_conflict_rate": _rate(counters["commit_conflicts_total"], commit_attempts_total),
            "blob_upload_attempts_total": blob_upload_attempts_total,
            "blob_upload_failures_total": counters["blob_upload_failures_total"],
            "blob_upload_failure_rate": _rate(counters["blob_upload_failures_total"], blob_upload_attempts_total),
            "capability_expired_total": counters["capability_expired_total"],
            "capability_missing_or_revoked_total": counters["capability_missing_or_revoked_total"],
            "capability_revocation_events_total": counters["capability_revocation_events_total"],
            "tombstone_gc_deleted_total": counters["tombstone_gc_deleted_total"],
        }


def _now_ms() -> int:
    return int(time.time() * 1000)


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


logger = logging.getLogger("noteapp.server")
metrics = RuntimeMetrics()


def _configure_logging() -> None:
    level_name = os.environ.get("NOTEAPP_SERVER_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


_configure_logging()


def _default_data_dir() -> Path:
    return Path(__file__).resolve().parent / ".data"


def _sqlite_db_path(data_dir: Path) -> Path:
    database_url = os.environ.get("NOTEAPP_SERVER_DATABASE_URL")
    if database_url:
        return sqlite_path_from_database_url(database_url)
    return Path(os.environ.get("NOTEAPP_SERVER_SQLITE_PATH", data_dir / "noteapp-server.sqlite3"))


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def create_blob_store_from_env(data_dir: Path) -> object:
    backend = os.environ.get("NOTEAPP_SERVER_BLOB_STORAGE", "local").lower()
    if backend in {"local", "filesystem"}:
        root = Path(os.environ.get("NOTEAPP_SERVER_OBJECT_STORAGE_DIR", data_dir / "blobs"))
        return FileSystemBlobStore(root, backend_name=backend)
    if backend in {"s3", "oss"}:
        return S3CompatibleBlobStore(
            endpoint=_required_env("NOTEAPP_SERVER_OBJECT_ENDPOINT"),
            bucket=_required_env("NOTEAPP_SERVER_OBJECT_BUCKET"),
            region=_required_env("NOTEAPP_SERVER_OBJECT_REGION"),
            access_key_id=_required_env("NOTEAPP_SERVER_OBJECT_ACCESS_KEY_ID"),
            secret_access_key=_required_env("NOTEAPP_SERVER_OBJECT_SECRET_ACCESS_KEY"),
        )
    raise RuntimeError("NOTEAPP_SERVER_BLOB_STORAGE must be local, filesystem, s3, or oss")


def create_store_from_env() -> SyncStore:
    data_dir = Path(os.environ.get("NOTEAPP_SERVER_DATA_DIR", _default_data_dir()))
    blob_store = create_blob_store_from_env(data_dir)
    storage = os.environ.get("NOTEAPP_SERVER_STORAGE", "json").lower()
    if storage in {"sqlite", "db"}:
        return SyncStore(data_dir, repository=SQLiteStateRepository(_sqlite_db_path(data_dir)), blob_store=blob_store)
    if storage != "json":
        raise RuntimeError("NOTEAPP_SERVER_STORAGE must be json or sqlite")
    return SyncStore(data_dir, blob_store=blob_store)


def _cors_origins_from_env() -> list[str]:
    raw_value = os.environ.get("NOTEAPP_SERVER_CORS_ORIGINS", "").strip()
    if not raw_value:
        return []
    if raw_value == "*":
        return ["*"]
    return [origin.strip() for origin in raw_value.split(",") if origin.strip()]


store = create_store_from_env()
app = FastAPI(title="noteapp-server")
cors_origins = _cors_origins_from_env()
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.exception_handler(HTTPException)
async def http_exception_to_contract_error(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "code" in exc.detail and "message" in exc.detail:
        request.state.error_code = exc.detail["code"]
        return _contract_error_response(exc.status_code, exc.detail["code"], exc.detail["message"])
    request.state.error_code = "http_error"
    return _contract_error_response(exc.status_code, "http_error", str(exc.detail))


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _contract_error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message},
        headers={"x-noteapp-error-code": code},
    )


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return _contract_error_response(status_code, code, message)


def _status_code_for_store_error(error: SyncStoreError) -> int:
    status_code_by_code = {
        "access_token_expired": 401,
        "blob_not_found": 404,
        "device_forbidden": 403,
        "device_not_found": 404,
        "device_revoked": 403,
        "file_version_not_found": 404,
        "invalid_access_token": 401,
        "invalid_credentials": 401,
        "invalid_refresh_token": 401,
        "object_not_found": 404,
        "object_storage_error": 502,
        "object_storage_unavailable": 503,
        "account_disabled": 403,
        "account_exists": 409,
        "password_required": 401,
        "refresh_token_expired": 401,
        "resumable_upload_session_not_found": 404,
        "state_write_conflict": 409,
    }
    return status_code_by_code.get(error.code, 400)


def _store_error_response(error: SyncStoreError) -> JSONResponse:
    return _error_response(_status_code_for_store_error(error), error.code, str(error))


@app.middleware("http")
async def request_observability_middleware(request: Request, call_next: Any) -> Response:
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    status_code = 500
    error_code: Optional[str] = None
    try:
        response = await call_next(request)
        status_code = response.status_code
        error_code = response.headers.get("x-noteapp-error-code") or getattr(request.state, "error_code", None)
        response.headers["x-request-id"] = request_id
        return response
    except Exception as error:
        error_code = getattr(error, "code", "unhandled_exception")
        raise
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        try:
            vault_id = request.path_params.get("vault_id")
        except Exception:
            vault_id = None
        metrics.record_request(
            method=request.method,
            route_path=route_path,
            status_code=status_code,
            error_code=error_code,
        )
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "route": route_path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "user_id": getattr(request.state, "user_id", None),
                    "device_id": getattr(request.state, "device_id", None),
                    "vault_id": vault_id,
                    "error_code": error_code,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )


def _bearer_token(request: Request) -> Optional[str]:
    authorization = request.headers.get("authorization")
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _error(401, "invalid_authorization", "Authorization must use a bearer token.")
    return token


def _required_auth_context(request: Request) -> dict[str, str]:
    token = _bearer_token(request)
    if token is None:
        raise _error(401, "missing_authorization", "Authorization bearer token is required.")
    try:
        context = store.authorize_access_token(token)
    except SyncStoreError as error:
        raise _error(_status_code_for_store_error(error), error.code, str(error)) from error
    request.state.user_id = context["user_id"]
    request.state.device_id = context["device_id"]
    store.touch_device(context["device_id"])
    return context


def _required_authorized_device_id(request: Request) -> str:
    return _required_auth_context(request)["device_id"]


def _body_mapping(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _error(400, "invalid_json_body", "Request body must be a JSON object.")
    return payload


def _required_int_header(request: Request, name: str) -> int:
    value = request.headers.get(name)
    if value is None:
        raise _error(400, "missing_chunk_header", f"{name} header is required.")
    try:
        parsed = int(value)
    except ValueError as error:
        raise _error(400, "invalid_chunk_header", f"{name} header must be an integer.") from error
    if parsed < 0:
        raise _error(400, "invalid_chunk_header", f"{name} header must be non-negative.")
    return parsed


def _required_string_header(request: Request, name: str) -> str:
    value = request.headers.get(name)
    if not value:
        raise _error(400, "missing_chunk_header", f"{name} header is required.")
    return value


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/health/dependencies")
def health_dependencies(response: Response) -> dict[str, Any]:
    store_diagnostics = store.diagnostics()
    payload = {
        "ok": bool(store_diagnostics.get("ok")),
        "process": {
            "ok": True,
            "pid": os.getpid(),
            "uptime_ms": metrics.snapshot()["uptime_ms"],
            "log_level": os.environ.get("NOTEAPP_SERVER_LOG_LEVEL", "WARNING").upper(),
        },
        **store_diagnostics,
    }
    if not payload["ok"]:
        response.status_code = 503
    return payload


@app.get("/metrics")
def runtime_metrics() -> dict[str, Any]:
    return metrics.snapshot()


@app.post("/devices/register")
def register_device(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return store.register_device(_body_mapping(payload))
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/auth/register")
def register_account(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return store.register_password_account(_body_mapping(payload))
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/auth/login")
def login(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        body = _body_mapping(payload)
        if "password" in body:
            return store.login_password_account(body)
        return store.register_device(body)
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/auth/refresh")
def refresh_token(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return store.refresh_session(_body_mapping(payload))
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/auth/logout")
def logout(request: Request) -> Response:
    token = _bearer_token(request)
    if token is None:
        raise _error(401, "missing_authorization", "Authorization bearer token is required.")
    store.revoke_session_for_token(token)
    return Response(status_code=204)


@app.delete("/devices/{device_id}")
def delete_device(device_id: str, request: Request) -> Response:
    actor_device_id = _required_authorized_device_id(request)
    try:
        if not store.delete_device(device_id, actor_device_id=actor_device_id):
            raise _error(404, "device_not_found", "Device was not found.")
    except SyncStoreError as error:
        raise _error(_status_code_for_store_error(error), error.code, str(error)) from error
    metrics.record_capability_revocation_event()
    return Response(status_code=204)


@app.get("/vaults/{vault_id}/devices")
def list_vault_devices(vault_id: str, request: Request) -> dict[str, Any]:
    device_id = _required_authorized_device_id(request)
    try:
        return store.list_vault_devices(vault_id, current_device_id=device_id)
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/vaults/{vault_id}/devices/heartbeat")
def heartbeat_device(vault_id: str, request: Request) -> dict[str, Any]:
    device_id = _required_authorized_device_id(request)
    try:
        return store.heartbeat_device(vault_id, device_id=device_id)
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/vaults/{vault_id}/tombstones/gc")
def run_tombstone_gc(vault_id: str, request: Request, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    device_id = _required_authorized_device_id(request)
    body = _body_mapping(payload or {})
    options: dict[str, Any] = {"actor_device_id": device_id}
    for key in ("now_ms", "min_retention_ms", "inactive_after_ms"):
        if key not in body:
            continue
        if not isinstance(body[key], int):
            raise _error(400, "invalid_request", f"{key} must be an integer")
        options[key] = body[key]
    try:
        result = store.run_tombstone_gc(vault_id, **options)
        reclaimed_count = result.get("reclaimed_count", 0)
        metrics.record_tombstone_gc(reclaimed_count if isinstance(reclaimed_count, int) else 0)
        return result
    except SyncStoreError as error:
        return _store_error_response(error)


@app.get("/vaults/{vault_id}/head")
def get_vault_head(vault_id: str, request: Request) -> dict[str, Any]:
    _required_authorized_device_id(request)
    return store.get_vault_head(vault_id)


@app.get("/vaults/{vault_id}/manifests/{revision}")
def get_manifest(vault_id: str, revision: int, request: Request) -> dict[str, Any]:
    _required_authorized_device_id(request)
    manifest = store.get_manifest(vault_id, revision)
    if manifest is None:
        raise _error(404, "manifest_not_found", "Manifest revision was not found.")
    return manifest


@app.post("/vaults/{vault_id}/commits")
def create_commit(vault_id: str, payload: dict[str, Any], request: Request) -> JSONResponse:
    device_id = _required_authorized_device_id(request)
    try:
        result = store.create_commit(vault_id, _body_mapping(payload), auth_device_id=device_id)
        return JSONResponse(status_code=200, content=result)
    except CommitConflict as conflict:
        return JSONResponse(
            status_code=409,
            content=conflict.payload,
            headers={"x-noteapp-error-code": conflict.payload["code"]},
        )
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/vaults/{vault_id}/commits/resolve-intent")
def resolve_commit_intent(vault_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _required_authorized_device_id(request)
    try:
        return store.resolve_commit_intent(vault_id, _body_mapping(payload))
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/vaults/{vault_id}/ack")
def ack_revisions(vault_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    device_id = _required_authorized_device_id(request)
    try:
        return store.ack_revisions(vault_id, _body_mapping(payload), device_id=device_id)
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/vaults/{vault_id}/file-versions/list")
def list_file_versions(vault_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _required_authorized_device_id(request)
    try:
        return store.list_file_versions(vault_id, _body_mapping(payload))
    except SyncStoreError as error:
        return _store_error_response(error)


@app.patch("/vaults/{vault_id}/file-versions/{version_id}")
def update_file_version(vault_id: str, version_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _required_authorized_device_id(request)
    try:
        return store.update_file_version(vault_id, version_id, _body_mapping(payload))
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/vaults/{vault_id}/blobs/check")
def check_blobs(vault_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _required_authorized_device_id(request)
    try:
        return store.check_blobs(vault_id, _body_mapping(payload))
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/vaults/{vault_id}/blobs/upload-init")
def init_blob_upload(vault_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    device_id = _required_authorized_device_id(request)
    try:
        return store.init_blob_upload(vault_id, _body_mapping(payload), request_base_url=str(request.base_url), device_id=device_id)
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/vaults/{vault_id}/blobs/resumable-upload-init")
def init_resumable_blob_upload(vault_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    device_id = _required_authorized_device_id(request)
    try:
        return store.init_resumable_blob_upload(
            vault_id,
            _body_mapping(payload),
            request_base_url=str(request.base_url),
            device_id=device_id,
        )
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/vaults/{vault_id}/blobs/resumable-upload-complete")
def complete_resumable_blob_upload(vault_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _required_authorized_device_id(request)
    try:
        return store.complete_resumable_blob_upload(vault_id, _body_mapping(payload))
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/vaults/{vault_id}/blobs/download-init")
def init_blob_download(vault_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    device_id = _required_authorized_device_id(request)
    try:
        return store.init_blob_download(vault_id, _body_mapping(payload), request_base_url=str(request.base_url), device_id=device_id)
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/vaults/{vault_id}/blobs/resumable-download-init")
def init_resumable_blob_download(vault_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    device_id = _required_authorized_device_id(request)
    try:
        return store.init_resumable_blob_download(
            vault_id,
            _body_mapping(payload),
            request_base_url=str(request.base_url),
            device_id=device_id,
        )
    except SyncStoreError as error:
        return _store_error_response(error)


@app.put("/_capabilities/blobs/{capability_token}")
async def upload_blob(capability_token: str, request: Request) -> Response:
    try:
        store.complete_blob_upload(capability_token, await request.body())
        return Response(status_code=204)
    except BlobCapabilityError as error:
        raise _error(error.status_code, error.code, str(error)) from error


@app.put("/_capabilities/blobs/resumable-upload/{session_id}")
async def upload_resumable_blob_chunk(session_id: str, request: Request) -> Response:
    try:
        store.put_resumable_blob_chunk(
            session_id,
            chunk_id=_required_string_header(request, "x-noteapp-chunk-id"),
            offset=_required_int_header(request, "x-noteapp-chunk-offset"),
            size=_required_int_header(request, "x-noteapp-chunk-size"),
            payload=await request.body(),
        )
        return Response(status_code=204)
    except BlobCapabilityError as error:
        raise _error(error.status_code, error.code, str(error)) from error


@app.get("/_capabilities/blobs/{capability_token}")
def download_blob(capability_token: str) -> Response:
    try:
        payload = store.read_blob_download(capability_token)
        return Response(content=payload, media_type="application/octet-stream")
    except BlobCapabilityError as error:
        raise _error(error.status_code, error.code, str(error)) from error


@app.get("/_capabilities/blobs/resumable-download/{capability_token}")
def download_resumable_blob_range(capability_token: str, request: Request) -> Response:
    try:
        payload = store.read_resumable_blob_download(
            capability_token,
            offset=_required_int_header(request, "x-noteapp-range-offset"),
            size=_required_int_header(request, "x-noteapp-range-size"),
        )
        return Response(content=payload, media_type="application/octet-stream")
    except BlobCapabilityError as error:
        raise _error(error.status_code, error.code, str(error)) from error
