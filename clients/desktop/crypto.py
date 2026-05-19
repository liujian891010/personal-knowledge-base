from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol


PLACEHOLDER_CRYPTO_SCHEME = "placeholder-v1"
E2EE_CRYPTO_SCHEME = "e2ee-v1"
E2EE_BLOB_ID_PREFIX = "blob-e2ee-v1-"
E2EE_VAULT_KEY_BYTES = 32
XCHACHA20_POLY1305_KEY_BYTES = 32
XCHACHA20_POLY1305_NONCE_BYTES = 24
POLY1305_TAG_BYTES = 16


class DesktopBlobCryptoProvider(Protocol):
    def build_blob_id(self, content_hash: str) -> str:
        ...

    def encrypt_payload(self, payload: bytes) -> bytes:
        ...

    def decrypt_payload(self, encrypted_payload: bytes, *, content_hash: str) -> bytes:
        ...

    def build_encrypted_blob_map(
        self,
        content_by_file_id: Mapping[str, bytes],
    ) -> dict[str, bytes]:
        ...


@dataclass(frozen=True)
class PlaceholderDesktopBlobCryptoProvider:
    def build_blob_id(self, content_hash: str) -> str:
        return build_placeholder_blob_id(content_hash)

    def encrypt_payload(self, payload: bytes) -> bytes:
        return build_placeholder_encrypted_blob_payload(payload)

    def decrypt_payload(self, encrypted_payload: bytes, *, content_hash: str) -> bytes:
        return decrypt_placeholder_encrypted_blob_payload(
            encrypted_payload,
            content_hash=content_hash,
        )

    def build_encrypted_blob_map(
        self,
        content_by_file_id: Mapping[str, bytes],
    ) -> dict[str, bytes]:
        return build_placeholder_encrypted_blob_map(content_by_file_id)


@dataclass(frozen=True)
class MissingVaultKeyDesktopBlobCryptoProvider:
    message: str

    def _raise(self) -> None:
        raise RuntimeError(self.message)

    def build_blob_id(self, content_hash: str) -> str:
        self._raise()

    def encrypt_payload(self, payload: bytes) -> bytes:
        self._raise()

    def decrypt_payload(self, encrypted_payload: bytes, *, content_hash: str) -> bytes:
        self._raise()

    def build_encrypted_blob_map(
        self,
        content_by_file_id: Mapping[str, bytes],
    ) -> dict[str, bytes]:
        self._raise()


def build_placeholder_blob_id(content_hash: str) -> str:
    digest = hashlib.sha256(
        b"pkb-placeholder-blob-id-v1\x00" + content_hash.encode("utf-8")
    ).hexdigest()
    return f"blob-{digest}"


def build_placeholder_encrypted_blob_payload(payload: bytes) -> bytes:
    """Deterministic placeholder envelope until a real crypto provider lands."""
    tag = hashlib.sha256(b"pkb-placeholder-blob-v1\x00" + payload).digest()[:16]
    return payload + tag


def decrypt_placeholder_encrypted_blob_payload(
    encrypted_payload: bytes,
    *,
    content_hash: str,
) -> bytes:
    if len(encrypted_payload) < 16:
        raise ValueError("placeholder encrypted blob is shorter than authentication tag")
    payload = encrypted_payload[:-16]
    tag = encrypted_payload[-16:]
    expected_tag = hashlib.sha256(b"pkb-placeholder-blob-v1\x00" + payload).digest()[:16]
    if tag != expected_tag:
        raise ValueError("placeholder encrypted blob tag mismatch")
    actual_content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
    if actual_content_hash != content_hash:
        raise ValueError(
            "placeholder decrypted content hash mismatch: "
            f"expected {content_hash}, got {actual_content_hash}"
        )
    return payload


def build_placeholder_encrypted_blob_map(
    content_by_file_id: Mapping[str, bytes],
) -> dict[str, bytes]:
    return {
        file_id: build_placeholder_encrypted_blob_payload(payload)
        for file_id, payload in content_by_file_id.items()
    }


def build_placeholder_blob_crypto_provider() -> DesktopBlobCryptoProvider:
    return PlaceholderDesktopBlobCryptoProvider()


def build_missing_vault_key_blob_crypto_provider(message: str) -> DesktopBlobCryptoProvider:
    return MissingVaultKeyDesktopBlobCryptoProvider(message=message)


def _hkdf_sha256(
    *,
    ikm: bytes,
    salt: Optional[bytes],
    info: bytes,
    length: int,
) -> bytes:
    if length <= 0:
        raise ValueError("HKDF output length must be positive")
    resolved_salt = b"\x00" * hashlib.sha256().digest_size if salt is None else salt
    prk = hmac.new(resolved_salt, ikm, hashlib.sha256).digest()
    okm = b""
    previous = b""
    counter = 1
    while len(okm) < length:
        if counter > 255:
            raise ValueError("HKDF output length is too large")
        previous = hmac.new(
            prk,
            previous + info + bytes([counter]),
            hashlib.sha256,
        ).digest()
        okm += previous
        counter += 1
    return okm[:length]


def _compute_content_hash(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _derive_e2ee_key(
    *,
    vault_id: str,
    vault_key: bytes,
    purpose: str,
    length: int,
) -> bytes:
    if not vault_id:
        raise ValueError("vault_id must be non-empty")
    if len(vault_key) != E2EE_VAULT_KEY_BYTES:
        raise ValueError(f"vault_key must be {E2EE_VAULT_KEY_BYTES} bytes")
    return _hkdf_sha256(
        ikm=vault_key,
        salt=f"noteapp:e2ee:v1:{vault_id}".encode("utf-8"),
        info=purpose.encode("utf-8"),
        length=length,
    )


def _load_nacl_bindings() -> Any:
    try:
        from nacl import bindings
    except ImportError as exc:
        raise RuntimeError(
            "PyNaCl is required for e2ee-v1 blob encryption; install PyNaCl>=1.5.0"
        ) from exc

    required = (
        "crypto_aead_xchacha20poly1305_ietf_encrypt",
        "crypto_aead_xchacha20poly1305_ietf_decrypt",
    )
    if not all(hasattr(bindings, name) for name in required):
        raise RuntimeError("PyNaCl build does not expose XChaCha20-Poly1305 bindings")
    return bindings


def is_e2ee_crypto_available() -> bool:
    try:
        _load_nacl_bindings()
    except RuntimeError:
        return False
    return True


@dataclass(frozen=True)
class E2EEDesktopBlobCryptoProvider:
    vault_id: str
    vault_key: bytes

    def __post_init__(self) -> None:
        if not self.vault_id:
            raise ValueError("vault_id must be non-empty")
        if len(self.vault_key) != E2EE_VAULT_KEY_BYTES:
            raise ValueError(f"vault_key must be {E2EE_VAULT_KEY_BYTES} bytes")

    def _content_key(self) -> bytes:
        return _derive_e2ee_key(
            vault_id=self.vault_id,
            vault_key=self.vault_key,
            purpose="blob-content",
            length=XCHACHA20_POLY1305_KEY_BYTES,
        )

    def _blob_id_key(self) -> bytes:
        return _derive_e2ee_key(
            vault_id=self.vault_id,
            vault_key=self.vault_key,
            purpose="blob-id",
            length=XCHACHA20_POLY1305_KEY_BYTES,
        )

    def _nonce_key(self) -> bytes:
        return _derive_e2ee_key(
            vault_id=self.vault_id,
            vault_key=self.vault_key,
            purpose="blob-nonce",
            length=XCHACHA20_POLY1305_KEY_BYTES,
        )

    def _nonce_for_content_hash(self, content_hash: str) -> bytes:
        if not content_hash:
            raise ValueError("content_hash must be non-empty")
        return _hkdf_sha256(
            ikm=self._nonce_key(),
            salt=None,
            info=content_hash.encode("utf-8"),
            length=XCHACHA20_POLY1305_NONCE_BYTES,
        )

    def build_blob_id(self, content_hash: str) -> str:
        if not content_hash:
            raise ValueError("content_hash must be non-empty")
        digest = hmac.new(
            self._blob_id_key(),
            content_hash.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return E2EE_BLOB_ID_PREFIX + digest

    def encrypt_payload(self, payload: bytes) -> bytes:
        content_hash = _compute_content_hash(payload)
        bindings = _load_nacl_bindings()
        return bindings.crypto_aead_xchacha20poly1305_ietf_encrypt(
            payload,
            b"",
            self._nonce_for_content_hash(content_hash),
            self._content_key(),
        )

    def decrypt_payload(self, encrypted_payload: bytes, *, content_hash: str) -> bytes:
        bindings = _load_nacl_bindings()
        try:
            payload = bindings.crypto_aead_xchacha20poly1305_ietf_decrypt(
                encrypted_payload,
                b"",
                self._nonce_for_content_hash(content_hash),
                self._content_key(),
            )
        except Exception as exc:
            raise ValueError("e2ee-v1 blob authentication failed") from exc
        actual_content_hash = _compute_content_hash(payload)
        if actual_content_hash != content_hash:
            raise ValueError(
                "e2ee-v1 decrypted content hash mismatch: "
                f"expected {content_hash}, got {actual_content_hash}"
            )
        return payload

    def build_encrypted_blob_map(
        self,
        content_by_file_id: Mapping[str, bytes],
    ) -> dict[str, bytes]:
        return {
            file_id: self.encrypt_payload(payload)
            for file_id, payload in content_by_file_id.items()
        }


def build_e2ee_blob_crypto_provider(
    *,
    vault_id: str,
    vault_key: bytes,
) -> DesktopBlobCryptoProvider:
    return E2EEDesktopBlobCryptoProvider(vault_id=vault_id, vault_key=vault_key)
