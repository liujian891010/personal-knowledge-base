from __future__ import annotations

import json
import os
from pathlib import Path

from .constants import FILEMAP_TMP_FILENAME
from .models import FileMapDocument


def _tmp_path(filemap_path: Path) -> Path:
    return filemap_path.with_name(FILEMAP_TMP_FILENAME)


def _serialize(document: FileMapDocument) -> str:
    return json.dumps(
        document.to_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def write_filemap_atomic(filemap_path: Path, document: FileMapDocument) -> None:
    filemap_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _tmp_path(filemap_path)
    payload = _serialize(document)
    with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    tmp_path.replace(filemap_path)


def load_filemap(filemap_path: Path) -> FileMapDocument:
    with filemap_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return FileMapDocument.from_dict(payload)


def recover_filemap(filemap_path: Path) -> bool:
    tmp_path = _tmp_path(filemap_path)
    if not tmp_path.exists():
        return False

    with tmp_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    FileMapDocument.from_dict(payload)

    tmp_path.replace(filemap_path)
    return True
