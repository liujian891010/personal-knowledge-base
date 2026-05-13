from __future__ import annotations

import json
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Protocol
from urllib.request import Request, urlopen

from .sync_client import (
    SyncBlobDownloader,
    SyncBlobUploader,
    SyncCommitTransport,
    SyncHttpJsonResponse,
)


class HttpResponseLike(Protocol):
    def read(self) -> bytes:
        ...

    def getcode(self) -> int:
        ...

    def close(self) -> None:
        ...


UrlopenLike = Callable[[Request, float], HttpResponseLike]


def _default_urlopen(request: Request, timeout: float) -> HttpResponseLike:
    return urlopen(request, timeout=timeout)


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _read_json_response(opener: UrlopenLike, request: Request, timeout_seconds: float) -> SyncHttpJsonResponse:
    with closing(opener(request, timeout_seconds)) as response:
        body = response.read()
        if body:
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("HTTP JSON response body must be an object")
        else:
            payload = {}
        return SyncHttpJsonResponse(
            status_code=response.getcode(),
            payload=payload,
        )


def _merge_headers(
    base_headers: Mapping[str, str],
    extra_headers: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    headers = dict(base_headers)
    if extra_headers is not None:
        headers.update(extra_headers)
    return headers


@dataclass(frozen=True)
class JsonHttpSyncTransport(SyncCommitTransport):
    base_url: str
    bearer_token: Optional[str] = None
    timeout_seconds: float = 30.0
    user_agent: str = "vault-core-sync-http/0.1"
    opener: UrlopenLike = _default_urlopen

    def __post_init__(self) -> None:
        if not self.base_url:
            raise ValueError("base_url must be non-empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    def _base_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers

    def _json_request(
        self,
        method: str,
        path: str,
        payload: Optional[Mapping[str, object]] = None,
    ) -> SyncHttpJsonResponse:
        body: Optional[bytes] = None
        headers = self._base_headers()
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            _join_url(self.base_url, path),
            data=body,
            headers=headers,
            method=method,
        )
        return _read_json_response(self.opener, request, self.timeout_seconds)

    def get_vault_head(self, vault_id: str) -> SyncHttpJsonResponse:
        return self._json_request("GET", f"/vaults/{vault_id}/head")

    def get_manifest(self, vault_id: str, revision: int) -> SyncHttpJsonResponse:
        return self._json_request("GET", f"/vaults/{vault_id}/manifests/{revision}")

    def post_blob_check(self, vault_id: str, payload: Mapping[str, object]) -> SyncHttpJsonResponse:
        return self._json_request("POST", f"/vaults/{vault_id}/blobs/check", payload)

    def post_blob_upload_init(self, vault_id: str, payload: Mapping[str, object]) -> SyncHttpJsonResponse:
        return self._json_request("POST", f"/vaults/{vault_id}/blobs/upload-init", payload)

    def post_blob_download_init(self, vault_id: str, payload: Mapping[str, object]) -> SyncHttpJsonResponse:
        return self._json_request("POST", f"/vaults/{vault_id}/blobs/download-init", payload)

    def post_create_commit(self, vault_id: str, payload: Mapping[str, object]) -> SyncHttpJsonResponse:
        return self._json_request("POST", f"/vaults/{vault_id}/commits", payload)

    def post_resolve_commit_intent(self, vault_id: str, payload: Mapping[str, object]) -> SyncHttpJsonResponse:
        return self._json_request("POST", f"/vaults/{vault_id}/commits/resolve-intent", payload)

    def post_ack(self, vault_id: str, payload: Mapping[str, object]) -> SyncHttpJsonResponse:
        return self._json_request("POST", f"/vaults/{vault_id}/ack", payload)


@dataclass(frozen=True)
class CapabilityBlobUploader(SyncBlobUploader):
    timeout_seconds: float = 60.0
    opener: UrlopenLike = _default_urlopen
    user_agent: str = "vault-core-sync-http/0.1"

    def upload_blob(self, upload, capability) -> None:
        payload = Path(upload.blob_staging_path).read_bytes()
        headers = _merge_headers(
            {
                "Content-Length": str(len(payload)),
                "User-Agent": self.user_agent,
            },
            capability.headers,
        )
        request = Request(
            capability.upload_url,
            data=payload,
            headers=headers,
            method=capability.method,
        )
        with closing(self.opener(request, self.timeout_seconds)) as response:
            status_code = response.getcode()
            if status_code < 200 or status_code >= 300:
                raise ValueError(f"blob upload returned unexpected status: {status_code}")


@dataclass(frozen=True)
class CapabilityBlobDownloader(SyncBlobDownloader):
    timeout_seconds: float = 60.0
    opener: UrlopenLike = _default_urlopen
    user_agent: str = "vault-core-sync-http/0.1"

    def download_blob(self, capability) -> bytes:
        request = Request(
            capability.download_url,
            headers=_merge_headers(
                {
                    "User-Agent": self.user_agent,
                },
                capability.headers,
            ),
            method="GET",
        )
        with closing(self.opener(request, self.timeout_seconds)) as response:
            status_code = response.getcode()
            if status_code < 200 or status_code >= 300:
                raise ValueError(f"blob download returned unexpected status: {status_code}")
            payload = response.read()
        if len(payload) != capability.encrypted_size:
            raise ValueError("blob download size does not match download capability")
        return payload
