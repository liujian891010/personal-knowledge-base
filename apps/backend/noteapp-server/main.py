from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from sync_store import BlobCapabilityError, CommitConflict, SyncStore, SyncStoreError


def _default_data_dir() -> Path:
    return Path(__file__).resolve().parent / ".data"


store = SyncStore(Path(os.environ.get("NOTEAPP_SERVER_DATA_DIR", _default_data_dir())))
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


def _store_error_response(error: SyncStoreError) -> JSONResponse:
    status_code_by_code = {
        "blob_not_found": 404,
        "device_not_found": 404,
    }
    return _error_response(status_code_by_code.get(error.code, 400), error.code, str(error))


def _bearer_token(request: Request) -> Optional[str]:
    authorization = request.headers.get("authorization")
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _error(401, "invalid_authorization", "Authorization must use a bearer token.")
    return token


def _authorized_device_id(request: Request) -> Optional[str]:
    token = _bearer_token(request)
    if token is None:
        return None
    device_id = store.device_id_for_token(token)
    if device_id is None:
        raise _error(403, "device_revoked", "Device token is invalid or revoked.")
    return device_id


def _body_mapping(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _error(400, "invalid_json_body", "Request body must be a JSON object.")
    return payload


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/devices/register")
def register_device(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return store.register_device(_body_mapping(payload))
    except SyncStoreError as error:
        raise _error(400, error.code, str(error)) from error


@app.delete("/devices/{device_id}")
def delete_device(device_id: str, request: Request) -> Response:
    _authorized_device_id(request)
    if not store.delete_device(device_id):
        raise _error(404, "device_not_found", "Device was not found.")
    return Response(status_code=204)


@app.get("/vaults/{vault_id}/head")
def get_vault_head(vault_id: str, request: Request) -> dict[str, Any]:
    _authorized_device_id(request)
    return store.get_vault_head(vault_id)


@app.get("/vaults/{vault_id}/manifests/{revision}")
def get_manifest(vault_id: str, revision: int, request: Request) -> dict[str, Any]:
    _authorized_device_id(request)
    manifest = store.get_manifest(vault_id, revision)
    if manifest is None:
        raise _error(404, "manifest_not_found", "Manifest revision was not found.")
    return manifest


@app.post("/vaults/{vault_id}/commits")
def create_commit(vault_id: str, payload: dict[str, Any], request: Request) -> JSONResponse:
    device_id = _authorized_device_id(request)
    try:
        result = store.create_commit(vault_id, _body_mapping(payload), auth_device_id=device_id)
        return JSONResponse(status_code=200, content=result)
    except CommitConflict as conflict:
        return JSONResponse(status_code=409, content=conflict.payload)
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/vaults/{vault_id}/commits/resolve-intent")
def resolve_commit_intent(vault_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _authorized_device_id(request)
    try:
        return store.resolve_commit_intent(vault_id, _body_mapping(payload))
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/vaults/{vault_id}/ack")
def ack_revisions(vault_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    device_id = _authorized_device_id(request)
    try:
        return store.ack_revisions(vault_id, _body_mapping(payload), device_id=device_id)
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/vaults/{vault_id}/blobs/check")
def check_blobs(vault_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _authorized_device_id(request)
    try:
        return store.check_blobs(vault_id, _body_mapping(payload))
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/vaults/{vault_id}/blobs/upload-init")
def init_blob_upload(vault_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    device_id = _authorized_device_id(request)
    try:
        return store.init_blob_upload(vault_id, _body_mapping(payload), request_base_url=str(request.base_url), device_id=device_id)
    except SyncStoreError as error:
        return _store_error_response(error)


@app.post("/vaults/{vault_id}/blobs/download-init")
def init_blob_download(vault_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    device_id = _authorized_device_id(request)
    try:
        return store.init_blob_download(vault_id, _body_mapping(payload), request_base_url=str(request.base_url), device_id=device_id)
    except SyncStoreError as error:
        return _store_error_response(error)


@app.put("/_capabilities/blobs/{capability_token}")
async def upload_blob(capability_token: str, request: Request) -> Response:
    try:
        store.complete_blob_upload(capability_token, await request.body())
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
