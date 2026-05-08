from __future__ import annotations

import hashlib
from typing import Mapping


def build_placeholder_blob_id(content_hash: str) -> str:
    digest = hashlib.sha256(
        b"pkb-placeholder-blob-id-v1\x00" + content_hash.encode("utf-8")
    ).hexdigest()
    return f"blob-{digest}"


def build_placeholder_encrypted_blob_payload(payload: bytes) -> bytes:
    """Deterministic placeholder envelope until a real crypto provider lands."""
    tag = hashlib.sha256(b"pkb-placeholder-blob-v1\x00" + payload).digest()[:16]
    return payload + tag


def build_placeholder_encrypted_blob_map(
    content_by_file_id: Mapping[str, bytes],
) -> dict[str, bytes]:
    return {
        file_id: build_placeholder_encrypted_blob_payload(payload)
        for file_id, payload in content_by_file_id.items()
    }
