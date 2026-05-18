from __future__ import annotations

import hashlib
import hmac
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class BlobStorageError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class FileSystemBlobStore:
    def __init__(self, root_dir: Path, *, backend_name: str = "filesystem") -> None:
        self.root_dir = root_dir
        self.backend_name = backend_name
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def put_object(self, object_key: str, payload: bytes) -> None:
        path = self._path_for_key(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def read_object(self, object_key: str) -> bytes:
        path = self._path_for_key(object_key)
        if not path.exists():
            raise BlobStorageError(404, "object_not_found", "Blob object was not found in object storage.")
        return path.read_bytes()

    def read_range(self, object_key: str, *, offset: int, size: int) -> bytes:
        path = self._path_for_key(object_key)
        if not path.exists():
            raise BlobStorageError(404, "object_not_found", "Blob object was not found in object storage.")
        with path.open("rb") as handle:
            handle.seek(offset)
            return handle.read(size)

    def _path_for_key(self, object_key: str) -> Path:
        parts = [part for part in object_key.replace("\\", "/").split("/") if part]
        if not parts or any(part in {".", ".."} for part in parts):
            raise BlobStorageError(400, "invalid_object_key", "Object key is invalid.")
        resolved = (self.root_dir.joinpath(*parts)).resolve()
        root = self.root_dir.resolve()
        if root != resolved and root not in resolved.parents:
            raise BlobStorageError(400, "invalid_object_key", "Object key escapes the storage root.")
        return resolved


class S3CompatibleBlobStore:
    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
    ) -> None:
        if not endpoint or not bucket or not region or not access_key_id or not secret_access_key:
            raise ValueError("S3-compatible object storage requires endpoint, bucket, region, access key, and secret key.")
        self.endpoint = endpoint.rstrip("/")
        self.bucket = bucket
        self.region = region
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.backend_name = "s3"

    def put_object(self, object_key: str, payload: bytes) -> None:
        self._request("PUT", object_key, payload=payload)

    def read_object(self, object_key: str) -> bytes:
        return self._request("GET", object_key)

    def read_range(self, object_key: str, *, offset: int, size: int) -> bytes:
        return self._request("GET", object_key, extra_headers={"Range": f"bytes={offset}-{offset + size - 1}"})

    def _request(
        self,
        method: str,
        object_key: str,
        *,
        payload: bytes = b"",
        extra_headers: Optional[dict[str, str]] = None,
    ) -> bytes:
        url = self._object_url(object_key)
        headers = self._signed_headers(method, url, payload=payload, extra_headers=extra_headers or {})
        request = urllib.request.Request(url, data=payload if method in {"PUT", "POST"} else None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise BlobStorageError(404, "object_not_found", "Blob object was not found in object storage.") from error
            if error.code in {408, 429, 500, 502, 503, 504}:
                raise BlobStorageError(503, "object_storage_unavailable", f"Object storage is temporarily unavailable: HTTP {error.code}.") from error
            raise BlobStorageError(502, "object_storage_error", f"Object storage request failed: HTTP {error.code}.") from error
        except OSError as error:
            raise BlobStorageError(503, "object_storage_unavailable", f"Object storage is temporarily unavailable: {error}.") from error

    def _object_url(self, object_key: str) -> str:
        quoted_bucket = urllib.parse.quote(self.bucket, safe="")
        quoted_key = urllib.parse.quote(object_key, safe="/-_.~")
        return f"{self.endpoint}/{quoted_bucket}/{quoted_key}"

    def _signed_headers(self, method: str, url: str, *, payload: bytes, extra_headers: dict[str, str]) -> dict[str, str]:
        parsed = urllib.parse.urlparse(url)
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(payload).hexdigest()
        headers = {
            "host": parsed.netloc,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            **{key.lower(): value for key, value in extra_headers.items()},
        }
        canonical_headers = "".join(f"{key}:{headers[key].strip()}\n" for key in sorted(headers))
        signed_headers = ";".join(sorted(headers))
        canonical_request = "\n".join(
            [
                method,
                parsed.path or "/",
                parsed.query,
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )
        scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signing_key = self._signing_key(date_stamp)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        headers["authorization"] = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self.access_key_id}/{scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )
        return headers

    def _signing_key(self, date_stamp: str) -> bytes:
        date_key = hmac.new(("AWS4" + self.secret_access_key).encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
        region_key = hmac.new(date_key, self.region.encode("utf-8"), hashlib.sha256).digest()
        service_key = hmac.new(region_key, b"s3", hashlib.sha256).digest()
        return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()
