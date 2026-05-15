from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .crypto import (
    E2EE_CRYPTO_SCHEME,
    E2EE_VAULT_KEY_BYTES,
    XCHACHA20_POLY1305_KEY_BYTES,
    XCHACHA20_POLY1305_NONCE_BYTES,
)
from .crypto_store import CRYPTO_KEY_EPOCH


RECOVERY_PACKAGE_SCHEMA_VERSION = "e2ee-recovery-v1"
RECOVERY_EXPORT_SCHEMA_VERSION = "e2ee-recovery-export-v1"
RECOVERY_IMPORT_SCHEMA_VERSION = "e2ee-recovery-import-v1"
RECOVERY_KDF_NAME = "argon2id"
RECOVERY_WRAP_ALG = "xchacha20-poly1305"
RECOVERY_KDF_MEMORY_KIB = 262144
RECOVERY_KDF_ITERATIONS = 3
RECOVERY_KDF_PARALLELISM = 1
RECOVERY_SALT_BYTES = 16
MISSING_RECOVERY_PACKAGE_MESSAGE = (
    "请提供恢复包文件，或使用一台已解锁设备进行本地配对"
)


@dataclass(frozen=True)
class DesktopRecoveryPackageExport:
    schema_version: str
    vault_id: str
    crypto_scheme: str
    key_epoch: int
    created_at: int
    recovery_package: dict[str, Any]
    recovery_package_json: str


@dataclass(frozen=True)
class DesktopRecoveryPackageImport:
    schema_version: str
    vault_id: str
    crypto_scheme: str
    key_epoch: int
    imported: bool
    message: str


def _load_recovery_crypto_bindings() -> tuple[Any, Any, Any]:
    try:
        from nacl import bindings, pwhash
        from nacl.encoding import RawEncoder
    except ImportError as exc:
        raise RuntimeError(
            "PyNaCl is required for e2ee-v1 recovery packages; install PyNaCl>=1.5.0"
        ) from exc

    if not hasattr(pwhash, "argon2id") or not hasattr(pwhash.argon2id, "kdf"):
        raise RuntimeError("PyNaCl build does not expose argon2id kdf")
    required = (
        "crypto_aead_xchacha20poly1305_ietf_encrypt",
        "crypto_aead_xchacha20poly1305_ietf_decrypt",
    )
    if not all(hasattr(bindings, name) for name in required):
        raise RuntimeError("PyNaCl build does not expose XChaCha20-Poly1305 bindings")
    return pwhash, bindings, RawEncoder


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _recovery_checksum(payload_without_checksum: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json_bytes(payload_without_checksum)).hexdigest()


def recovery_package_to_json(package: Mapping[str, Any]) -> str:
    return json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True)


def _validate_vault_id(vault_id: str) -> str:
    if not isinstance(vault_id, str) or not vault_id.strip():
        raise ValueError("vault_id must be non-empty")
    return vault_id


def _validate_recovery_phrase(recovery_phrase: str) -> str:
    if not isinstance(recovery_phrase, str) or not recovery_phrase.strip():
        raise ValueError("recovery phrase must be non-empty")
    return recovery_phrase.strip()


def _validate_vault_key(vault_key: bytes) -> bytes:
    if len(vault_key) != E2EE_VAULT_KEY_BYTES:
        raise ValueError(f"vault key must be {E2EE_VAULT_KEY_BYTES} bytes")
    return vault_key


def _validate_positive_int(value: int, *, name: str) -> int:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _decode_base64_field(value: Any, *, field: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError(f"recovery package {field} must be a non-empty base64 string")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise ValueError(f"recovery package {field} is not valid base64") from exc


def _derive_recovery_wrap_key(
    *,
    recovery_phrase: str,
    salt: bytes,
    memory_kib: int,
    iterations: int,
    parallelism: int,
) -> bytes:
    if parallelism != RECOVERY_KDF_PARALLELISM:
        raise ValueError("recovery package kdf.parallelism is not supported")
    pwhash, _, RawEncoder = _load_recovery_crypto_bindings()
    return pwhash.argon2id.kdf(
        XCHACHA20_POLY1305_KEY_BYTES,
        _validate_recovery_phrase(recovery_phrase).encode("utf-8"),
        salt,
        opslimit=_validate_positive_int(iterations, name="kdf.iterations"),
        memlimit=_validate_positive_int(memory_kib, name="kdf.memory_kib") * 1024,
        encoder=RawEncoder,
    )


def _recovery_aad(vault_id: str) -> bytes:
    return f"noteapp:e2ee-recovery-v1:{vault_id}".encode("utf-8")


def build_recovery_package(
    *,
    vault_id: str,
    vault_key: bytes,
    recovery_phrase: str,
    created_at: int,
    memory_kib: int = RECOVERY_KDF_MEMORY_KIB,
    iterations: int = RECOVERY_KDF_ITERATIONS,
    parallelism: int = RECOVERY_KDF_PARALLELISM,
    salt: Optional[bytes] = None,
    nonce: Optional[bytes] = None,
) -> dict[str, Any]:
    resolved_vault_id = _validate_vault_id(vault_id)
    resolved_vault_key = _validate_vault_key(vault_key)
    resolved_salt = os.urandom(RECOVERY_SALT_BYTES) if salt is None else salt
    resolved_nonce = os.urandom(XCHACHA20_POLY1305_NONCE_BYTES) if nonce is None else nonce
    if len(resolved_salt) != RECOVERY_SALT_BYTES:
        raise ValueError(f"recovery salt must be {RECOVERY_SALT_BYTES} bytes")
    if len(resolved_nonce) != XCHACHA20_POLY1305_NONCE_BYTES:
        raise ValueError(f"recovery nonce must be {XCHACHA20_POLY1305_NONCE_BYTES} bytes")

    _, bindings, _ = _load_recovery_crypto_bindings()
    wrap_key = _derive_recovery_wrap_key(
        recovery_phrase=recovery_phrase,
        salt=resolved_salt,
        memory_kib=memory_kib,
        iterations=iterations,
        parallelism=parallelism,
    )
    ciphertext = bindings.crypto_aead_xchacha20poly1305_ietf_encrypt(
        resolved_vault_key,
        _recovery_aad(resolved_vault_id),
        resolved_nonce,
        wrap_key,
    )
    package: dict[str, Any] = {
        "schema_version": RECOVERY_PACKAGE_SCHEMA_VERSION,
        "vault_id": resolved_vault_id,
        "crypto_scheme": E2EE_CRYPTO_SCHEME,
        "kdf": {
            "name": RECOVERY_KDF_NAME,
            "memory_kib": memory_kib,
            "iterations": iterations,
            "parallelism": parallelism,
            "salt_b64": base64.b64encode(resolved_salt).decode("ascii"),
        },
        "wrap": {
            "alg": RECOVERY_WRAP_ALG,
            "nonce_b64": base64.b64encode(resolved_nonce).decode("ascii"),
            "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
        },
        "created_at": created_at,
    }
    package["checksum"] = _recovery_checksum(package)
    return package


def _recovery_package_body(package: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in package.items() if key != "checksum"}


def validate_recovery_package(package: Mapping[str, Any]) -> None:
    if not isinstance(package, Mapping):
        raise ValueError("recovery package must be a JSON object")
    checksum = package.get("checksum")
    if not isinstance(checksum, str) or not checksum:
        raise ValueError("recovery package checksum is required")
    expected_checksum = _recovery_checksum(_recovery_package_body(package))
    if checksum != expected_checksum:
        raise ValueError("recovery package checksum mismatch")
    if package.get("schema_version") != RECOVERY_PACKAGE_SCHEMA_VERSION:
        raise ValueError("recovery package schema_version is not supported")
    if package.get("crypto_scheme") != E2EE_CRYPTO_SCHEME:
        raise ValueError("recovery package crypto_scheme is not supported")
    kdf = package.get("kdf")
    wrap = package.get("wrap")
    if not isinstance(kdf, Mapping):
        raise ValueError("recovery package kdf must be an object")
    if not isinstance(wrap, Mapping):
        raise ValueError("recovery package wrap must be an object")
    if kdf.get("name") != RECOVERY_KDF_NAME:
        raise ValueError("recovery package kdf.name is not supported")
    if wrap.get("alg") != RECOVERY_WRAP_ALG:
        raise ValueError("recovery package wrap.alg is not supported")


def unwrap_recovery_package(
    package: Mapping[str, Any],
    *,
    recovery_phrase: str,
    expected_vault_id: Optional[str] = None,
) -> bytes:
    validate_recovery_package(package)
    vault_id = package.get("vault_id")
    if not isinstance(vault_id, str) or not vault_id:
        raise ValueError("recovery package vault_id is required")
    if expected_vault_id is not None and vault_id != expected_vault_id:
        raise ValueError(
            "recovery package vault_id does not match desktop config: "
            f"expected {expected_vault_id}, got {vault_id}"
        )

    kdf = package["kdf"]
    wrap = package["wrap"]
    if not isinstance(kdf, Mapping) or not isinstance(wrap, Mapping):
        raise ValueError("recovery package is malformed")
    salt = _decode_base64_field(kdf.get("salt_b64"), field="kdf.salt_b64")
    nonce = _decode_base64_field(wrap.get("nonce_b64"), field="wrap.nonce_b64")
    ciphertext = _decode_base64_field(wrap.get("ciphertext_b64"), field="wrap.ciphertext_b64")
    if len(salt) != RECOVERY_SALT_BYTES:
        raise ValueError(f"recovery package salt must be {RECOVERY_SALT_BYTES} bytes")
    if len(nonce) != XCHACHA20_POLY1305_NONCE_BYTES:
        raise ValueError(f"recovery package nonce must be {XCHACHA20_POLY1305_NONCE_BYTES} bytes")

    _, bindings, _ = _load_recovery_crypto_bindings()
    wrap_key = _derive_recovery_wrap_key(
        recovery_phrase=recovery_phrase,
        salt=salt,
        memory_kib=int(kdf.get("memory_kib", 0)),
        iterations=int(kdf.get("iterations", 0)),
        parallelism=int(kdf.get("parallelism", 0)),
    )
    try:
        vault_key = bindings.crypto_aead_xchacha20poly1305_ietf_decrypt(
            ciphertext,
            _recovery_aad(vault_id),
            nonce,
            wrap_key,
        )
    except Exception as exc:
        raise ValueError("recovery phrase did not decrypt the recovery package") from exc
    return _validate_vault_key(vault_key)


def export_recovery_package(
    *,
    vault_id: str,
    vault_key: bytes,
    recovery_phrase: str,
    created_at: int,
    memory_kib: int = RECOVERY_KDF_MEMORY_KIB,
    iterations: int = RECOVERY_KDF_ITERATIONS,
) -> DesktopRecoveryPackageExport:
    package = build_recovery_package(
        vault_id=vault_id,
        vault_key=vault_key,
        recovery_phrase=recovery_phrase,
        created_at=created_at,
        memory_kib=memory_kib,
        iterations=iterations,
    )
    return DesktopRecoveryPackageExport(
        schema_version=RECOVERY_EXPORT_SCHEMA_VERSION,
        vault_id=vault_id,
        crypto_scheme=E2EE_CRYPTO_SCHEME,
        key_epoch=CRYPTO_KEY_EPOCH,
        created_at=created_at,
        recovery_package=package,
        recovery_package_json=recovery_package_to_json(package),
    )


def import_recovery_package(
    package: Mapping[str, Any],
    *,
    recovery_phrase: str,
    expected_vault_id: str,
) -> tuple[DesktopRecoveryPackageImport, bytes]:
    vault_key = unwrap_recovery_package(
        package,
        recovery_phrase=recovery_phrase,
        expected_vault_id=expected_vault_id,
    )
    return (
        DesktopRecoveryPackageImport(
            schema_version=RECOVERY_IMPORT_SCHEMA_VERSION,
            vault_id=expected_vault_id,
            crypto_scheme=E2EE_CRYPTO_SCHEME,
            key_epoch=CRYPTO_KEY_EPOCH,
            imported=True,
            message="recovery package decrypted; local vault key is ready to store",
        ),
        vault_key,
    )
