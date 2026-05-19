from __future__ import annotations

import base64
import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .crypto import E2EE_CRYPTO_SCHEME, E2EE_VAULT_KEY_BYTES


CRYPTO_STATUS_SCHEMA_VERSION = "crypto-v1"
CRYPTO_KEY_EPOCH = 1
_SERVICE_NAME = "noteapp.e2ee.vault-key"


@dataclass(frozen=True)
class DesktopCryptoStatus:
    schema_version: str
    vault_id: str
    crypto_scheme: str
    key_epoch: int
    unlocked: bool
    key_available: bool
    storage_provider: str
    key_ref: str
    message: str
    error: Optional[str] = None


def _status(
    *,
    vault_id: str,
    crypto_scheme: str,
    unlocked: bool,
    key_available: bool,
    storage_provider: str,
    key_ref: str,
    message: str,
    error: Optional[str] = None,
) -> DesktopCryptoStatus:
    return DesktopCryptoStatus(
        schema_version=CRYPTO_STATUS_SCHEMA_VERSION,
        vault_id=vault_id,
        crypto_scheme=crypto_scheme,
        key_epoch=CRYPTO_KEY_EPOCH,
        unlocked=unlocked,
        key_available=key_available,
        storage_provider=storage_provider,
        key_ref=key_ref,
        message=message,
        error=error,
    )


def _service_account(vault_id: str) -> str:
    if not vault_id.strip():
        raise ValueError("vault_id must be non-empty")
    return f"vault:{vault_id}"


def _insecure_store_enabled() -> bool:
    return os.environ.get("NOTEAPP_ALLOW_INSECURE_CRYPTO_STORE") == "true"


def _insecure_store_root() -> Path:
    root = os.environ.get("NOTEAPP_CRYPTO_STORE_DIR")
    if not root:
        raise RuntimeError("NOTEAPP_CRYPTO_STORE_DIR is required for insecure test crypto storage")
    return Path(root)


def _insecure_store_path(vault_id: str) -> Path:
    safe = base64.urlsafe_b64encode(vault_id.encode("utf-8")).decode("ascii").rstrip("=")
    return _insecure_store_root() / f"{safe}.json"


def _windows_store_root() -> Path:
    root = os.environ.get("NOTEAPP_CRYPTO_STORE_DIR")
    if root:
        return Path(root)
    appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("LOCALAPPDATA or APPDATA is required for Windows DPAPI crypto storage")
    return Path(appdata) / "NoteApp" / "crypto-keys"


def _windows_store_path(vault_id: str) -> Path:
    safe = base64.urlsafe_b64encode(vault_id.encode("utf-8")).decode("ascii").rstrip("=")
    return _windows_store_root() / f"{safe}.json"


def _dpapi_entropy(vault_id: str) -> bytes:
    return f"noteapp:e2ee:v1:{vault_id}".encode("utf-8")


def _windows_dpapi_protect(payload: bytes, *, vault_id: str) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    payload_buffer = ctypes.create_string_buffer(payload)
    payload_blob = DATA_BLOB(len(payload), ctypes.cast(payload_buffer, ctypes.POINTER(ctypes.c_char)))
    entropy = _dpapi_entropy(vault_id)
    entropy_buffer = ctypes.create_string_buffer(entropy)
    entropy_blob = DATA_BLOB(len(entropy), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_char)))
    output_blob = DATA_BLOB()
    description = "NoteApp e2ee vault key"

    ok = crypt32.CryptProtectData(
        ctypes.byref(payload_blob),
        description,
        ctypes.byref(entropy_blob),
        None,
        None,
        0,
        ctypes.byref(output_blob),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _windows_dpapi_unprotect(payload: bytes, *, vault_id: str) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    payload_buffer = ctypes.create_string_buffer(payload)
    payload_blob = DATA_BLOB(len(payload), ctypes.cast(payload_buffer, ctypes.POINTER(ctypes.c_char)))
    entropy = _dpapi_entropy(vault_id)
    entropy_buffer = ctypes.create_string_buffer(entropy)
    entropy_blob = DATA_BLOB(len(entropy), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_char)))
    output_blob = DATA_BLOB()

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(payload_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0,
        ctypes.byref(output_blob),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    try:
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _read_vault_key_from_env() -> Optional[bytes]:
    encoded = os.environ.get("NOTEAPP_VAULT_KEY_BASE64")
    if encoded:
        return base64.b64decode(encoded.encode("ascii"), validate=True)
    encoded_hex = os.environ.get("NOTEAPP_VAULT_KEY_HEX")
    if encoded_hex:
        return bytes.fromhex(encoded_hex)
    return None


def _validate_vault_key(vault_key: bytes) -> bytes:
    if len(vault_key) != E2EE_VAULT_KEY_BYTES:
        raise ValueError(f"vault key must decode to {E2EE_VAULT_KEY_BYTES} bytes")
    return vault_key


def _load_insecure_vault_key(vault_id: str) -> Optional[bytes]:
    path = _insecure_store_path(vault_id)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"crypto key file must contain an object: {path}")
    encoded = payload.get("vault_key_base64")
    if not isinstance(encoded, str):
        raise ValueError(f"crypto key file is missing vault_key_base64: {path}")
    return _validate_vault_key(base64.b64decode(encoded.encode("ascii"), validate=True))


def _store_insecure_vault_key(vault_id: str, vault_key: bytes) -> None:
    path = _insecure_store_path(vault_id)
    _write_json_atomic(
        path,
        {
            "schema_version": CRYPTO_STATUS_SCHEMA_VERSION,
            "vault_id": vault_id,
            "crypto_scheme": E2EE_CRYPTO_SCHEME,
            "key_epoch": CRYPTO_KEY_EPOCH,
            "vault_key_base64": base64.b64encode(vault_key).decode("ascii"),
        },
    )


def _delete_insecure_vault_key(vault_id: str) -> None:
    _insecure_store_path(vault_id).unlink(missing_ok=True)


def _load_windows_vault_key(vault_id: str) -> Optional[bytes]:
    path = _windows_store_path(vault_id)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"DPAPI crypto key file must contain an object: {path}")
    encoded = payload.get("protected_key_base64")
    if not isinstance(encoded, str):
        raise ValueError(f"DPAPI crypto key file is missing protected_key_base64: {path}")
    protected = base64.b64decode(encoded.encode("ascii"), validate=True)
    return _validate_vault_key(_windows_dpapi_unprotect(protected, vault_id=vault_id))


def _store_windows_vault_key(vault_id: str, vault_key: bytes) -> None:
    path = _windows_store_path(vault_id)
    protected = _windows_dpapi_protect(vault_key, vault_id=vault_id)
    _write_json_atomic(
        path,
        {
            "schema_version": CRYPTO_STATUS_SCHEMA_VERSION,
            "vault_id": vault_id,
            "crypto_scheme": E2EE_CRYPTO_SCHEME,
            "key_epoch": CRYPTO_KEY_EPOCH,
            "storage_provider": "windows-dpapi",
            "protected_key_base64": base64.b64encode(protected).decode("ascii"),
        },
    )


def _delete_windows_vault_key(vault_id: str) -> None:
    _windows_store_path(vault_id).unlink(missing_ok=True)


def _run_security_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["security", *args],
        check=False,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _load_macos_vault_key(vault_id: str) -> Optional[bytes]:
    result = _run_security_command(
        ["find-generic-password", "-w", "-s", _SERVICE_NAME, "-a", _service_account(vault_id)]
    )
    if result.returncode != 0:
        return None
    return _validate_vault_key(base64.b64decode(result.stdout.strip().encode("ascii"), validate=True))


def _store_macos_vault_key(vault_id: str, vault_key: bytes) -> None:
    result = _run_security_command(
        [
            "add-generic-password",
            "-U",
            "-s",
            _SERVICE_NAME,
            "-a",
            _service_account(vault_id),
            "-w",
            base64.b64encode(vault_key).decode("ascii"),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "macOS Keychain write failed")


def _delete_macos_vault_key(vault_id: str) -> None:
    _run_security_command(["delete-generic-password", "-s", _SERVICE_NAME, "-a", _service_account(vault_id)])


def _secret_tool_available() -> bool:
    return shutil.which("secret-tool") is not None


def _load_linux_vault_key(vault_id: str) -> Optional[bytes]:
    if not _secret_tool_available():
        return None
    result = subprocess.run(
        ["secret-tool", "lookup", "service", _SERVICE_NAME, "vault", vault_id],
        check=False,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return _validate_vault_key(base64.b64decode(result.stdout.strip().encode("ascii"), validate=True))


def _store_linux_vault_key(vault_id: str, vault_key: bytes) -> None:
    if not _secret_tool_available():
        raise RuntimeError("secret-tool is required for Linux secure crypto storage")
    result = subprocess.run(
        [
            "secret-tool",
            "store",
            "--label",
            f"NoteApp vault key {vault_id}",
            "service",
            _SERVICE_NAME,
            "vault",
            vault_id,
        ],
        input=base64.b64encode(vault_key).decode("ascii"),
        check=False,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "secret-tool store failed")


def _delete_linux_vault_key(vault_id: str) -> None:
    if not _secret_tool_available():
        return
    subprocess.run(
        ["secret-tool", "clear", "service", _SERVICE_NAME, "vault", vault_id],
        check=False,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _storage_provider() -> str:
    if _insecure_store_enabled():
        return "insecure-file"
    system = platform.system()
    if system == "Windows":
        return "windows-dpapi"
    if system == "Darwin":
        return "macos-keychain"
    if system == "Linux":
        return "linux-secret-tool"
    return "unsupported"


def _key_ref(vault_id: str, provider: str) -> str:
    if provider == "insecure-file":
        return str(_insecure_store_path(vault_id))
    if provider == "windows-dpapi":
        return str(_windows_store_path(vault_id))
    if provider in {"macos-keychain", "linux-secret-tool"}:
        return f"{_SERVICE_NAME}/{_service_account(vault_id)}"
    return ""


def load_desktop_vault_key(vault_id: str) -> Optional[bytes]:
    env_key = _read_vault_key_from_env()
    if env_key is not None:
        return _validate_vault_key(env_key)

    provider = _storage_provider()
    if provider == "insecure-file":
        return _load_insecure_vault_key(vault_id)
    if provider == "windows-dpapi":
        return _load_windows_vault_key(vault_id)
    if provider == "macos-keychain":
        return _load_macos_vault_key(vault_id)
    if provider == "linux-secret-tool":
        return _load_linux_vault_key(vault_id)
    return None


def store_desktop_vault_key(vault_id: str, vault_key: bytes) -> None:
    _validate_vault_key(vault_key)
    provider = _storage_provider()
    if provider == "insecure-file":
        _store_insecure_vault_key(vault_id, vault_key)
        return
    if provider == "windows-dpapi":
        _store_windows_vault_key(vault_id, vault_key)
        return
    if provider == "macos-keychain":
        _store_macos_vault_key(vault_id, vault_key)
        return
    if provider == "linux-secret-tool":
        _store_linux_vault_key(vault_id, vault_key)
        return
    raise RuntimeError(f"secure crypto storage is not supported on {platform.system() or 'this platform'}")


def delete_desktop_vault_key(vault_id: str) -> None:
    provider = _storage_provider()
    if provider == "insecure-file":
        _delete_insecure_vault_key(vault_id)
    elif provider == "windows-dpapi":
        _delete_windows_vault_key(vault_id)
    elif provider == "macos-keychain":
        _delete_macos_vault_key(vault_id)
    elif provider == "linux-secret-tool":
        _delete_linux_vault_key(vault_id)


def load_desktop_crypto_status(*, vault_id: str, vault_root: Optional[Path] = None) -> DesktopCryptoStatus:
    env_key = _read_vault_key_from_env()
    if env_key is not None:
        _validate_vault_key(env_key)
        return _status(
            vault_id=vault_id,
            crypto_scheme=E2EE_CRYPTO_SCHEME,
            unlocked=True,
            key_available=True,
            storage_provider="environment",
            key_ref="NOTEAPP_VAULT_KEY_BASE64/NOTEAPP_VAULT_KEY_HEX",
            message="e2ee-v1 vault key is available from process environment",
        )

    provider = _storage_provider()
    try:
        key = load_desktop_vault_key(vault_id)
    except Exception as exc:
        return _status(
            vault_id=vault_id,
            crypto_scheme=E2EE_CRYPTO_SCHEME,
            unlocked=False,
            key_available=False,
            storage_provider=provider,
            key_ref=_key_ref(vault_id, provider),
            message="crypto key storage is not readable",
            error=str(exc),
        )
    if key is not None:
        return _status(
            vault_id=vault_id,
            crypto_scheme=E2EE_CRYPTO_SCHEME,
            unlocked=True,
            key_available=True,
            storage_provider=provider,
            key_ref=_key_ref(vault_id, provider),
            message="e2ee-v1 vault key is available",
        )
    return _status(
        vault_id=vault_id,
        crypto_scheme=E2EE_CRYPTO_SCHEME,
        unlocked=False,
        key_available=False,
        storage_provider=provider,
        key_ref=_key_ref(vault_id, provider),
        message=(
            "no local e2ee-v1 vault key is available; encrypted operations require unlock "
            "or explicit placeholder-v1 compatibility mode"
        ),
    )
