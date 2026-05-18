from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from sync_repository import SQLiteStateRepository, sqlite_path_from_database_url
from sync_store import BlobCapabilityError, CommitConflict, SyncStore, SyncStoreError


def _default_data_dir() -> Path:
    return Path(__file__).resolve().parent / ".data"


def _sqlite_db_path(data_dir: Path) -> Path:
    database_url = os.environ.get("NOTEAPP_SERVER_DATABASE_URL")
    if database_url:
        return sqlite_path_from_database_url(database_url)
    return Path(os.environ.get("NOTEAPP_SERVER_SQLITE_PATH", data_dir / "noteapp-server.sqlite3"))


def create_store_from_env() -> SyncStore:
    data_dir = Path(os.environ.get("NOTEAPP_SERVER_DATA_DIR", _default_data_dir()))
    storage = os.environ.get("NOTEAPP_SERVER_STORAGE", "json").lower()
    if storage in {"sqlite", "db"}:
        return SyncStore(data_dir, repository=SQLiteStateRepository(_sqlite_db_path(data_dir)))
    if storage != "json":
        raise RuntimeError("NOTEAPP_SERVER_STORAGE must be json or sqlite")
    return SyncStore(data_dir)


store = create_store_from_env()
app = FastAPI(title="noteapp-server")


@app.exception_handler(HTTPException)
async def http_exception_to_contract_error(_: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "code" in exc.detail and "message" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": "http_error", "message": str(exc.detail)},
    )


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"code": code, "message": message})


def _status_code_for_store_error(error: SyncStoreError) -> int:
    status_code_by_code = {
        "access_token_expired": 401,
        "blob_not_found": 404,
        "device_forbidden": 403,
        "device_not_found": 404,
        "device_revoked": 403,
        "file_version_not_found": 404,
        "invalid_access_token": 401,
        "invalid_refresh_token": 401,
        "refresh_token_expired": 401,
        "resumable_upload_session_not_found": 404,
        "state_write_conflict": 409,
    }
    return status_code_by_code.get(error.code, 400)


def _store_error_response(error: SyncStoreError) -> JSONResponse:
    return _error_response(_status_code_for_store_error(error), error.code, str(error))


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


@app.post("/devices/register")
def register_device(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return store.register_device(_body_mapping(payload))
    except SyncStoreError as error:
        raise _error(400, error.code, str(error)) from error


@app.post("/auth/login")
def login(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return store.register_device(_body_mapping(payload))
    except SyncStoreError as error:
        raise _error(400, error.code, str(error)) from error


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
        return store.run_tombstone_gc(vault_id, **options)
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
        return JSONResponse(status_code=409, content=conflict.payload)
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
