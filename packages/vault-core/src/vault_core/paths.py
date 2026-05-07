from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from .constants import CONFLICT_ORPHANS_DIRNAME, STAGING_DIRNAME, STAGING_ORPHANS_DIRNAME


def sanitize_device_name(device_name: str, *, max_length: int = 32, allow_spaces: bool = True) -> str:
    truncated = device_name[:max_length]
    sanitized_chars = []
    for char in truncated:
        if char.isascii() and (char.isalnum() or char == "-" or (allow_spaces and char == " ")):
            sanitized_chars.append(char)
        else:
            sanitized_chars.append("-")
    sanitized = "".join(sanitized_chars).strip()
    while "--" in sanitized:
        sanitized = sanitized.replace("--", "-")
    sanitized = sanitized.strip("- ")
    return sanitized or "unknown-device"


def _next_available_path(candidate: Path) -> Path:
    if not candidate.exists():
        return candidate

    suffix = candidate.suffix
    stem = candidate.stem
    counter = 2
    while True:
        numbered = candidate.with_name(f"{stem} {counter}{suffix}")
        if not numbered.exists():
            return numbered
        counter += 1


def allocate_conflict_copy_path(
    original_path: Path,
    *,
    conflict_date: date,
    device_name: str,
) -> Path:
    sanitized_device = sanitize_device_name(device_name)
    suffix = original_path.suffix
    stem = original_path.stem if suffix else original_path.name
    parent = original_path.parent
    base_name = f"{stem} (conflict {conflict_date.isoformat()} {sanitized_device})"
    candidate = parent / f"{base_name}{suffix}"
    return _next_available_path(candidate)


def _ensure_within(child: Path, parent: Path) -> None:
    child.resolve().relative_to(parent.resolve())


def move_staging_orphan(vault_root: Path, staging_path: Path) -> Path:
    staging_root = vault_root / STAGING_DIRNAME
    orphan_root = vault_root / STAGING_ORPHANS_DIRNAME
    _ensure_within(staging_path, staging_root)
    orphan_root.mkdir(parents=True, exist_ok=True)
    target = _next_available_path(orphan_root / staging_path.name)
    staging_path.replace(target)
    return target


def move_conflict_orphan(vault_root: Path, conflict_path: Path, *, preferred_name: Optional[str] = None) -> Path:
    orphan_root = vault_root / CONFLICT_ORPHANS_DIRNAME
    orphan_root.mkdir(parents=True, exist_ok=True)
    name = preferred_name or conflict_path.name
    target = _next_available_path(orphan_root / name)
    conflict_path.replace(target)
    return target
