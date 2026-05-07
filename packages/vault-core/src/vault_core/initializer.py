from __future__ import annotations

import json
import uuid
from pathlib import Path
from time import time
from typing import Optional

from .constants import (
    AI_DIRNAME,
    ATTACHMENTS_DIRNAME,
    FILEMAP_FILENAME,
    LOCAL_ONLY_DIRS,
    NOTEAPP_DIRNAME,
    NOTES_DIRNAME,
    SYNCED_DIRS,
    TOMBSTONE_LEDGER_FILENAME,
    VAULTINFO_FILENAME,
)
from .filemap import write_filemap_atomic
from .models import FileMapDocument


def _now_ms() -> int:
    return int(time() * 1000)


def initialize_vault(root: Path, vault_id: Optional[str] = None, now_ms: Optional[int] = None) -> FileMapDocument:
    now_ms = _now_ms() if now_ms is None else now_ms
    vault_id = vault_id or str(uuid.uuid4())

    root.mkdir(parents=True, exist_ok=True)

    for relative_dir in LOCAL_ONLY_DIRS + SYNCED_DIRS:
        (root / relative_dir).mkdir(parents=True, exist_ok=True)

    vaultinfo_path = root / VAULTINFO_FILENAME
    if not vaultinfo_path.exists():
        vaultinfo_path.write_text(
            json.dumps(
                {
                    "schema_version": "v1",
                    "vault_id": vault_id,
                    "created_at": now_ms,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    document = FileMapDocument(
        vault_id=vault_id,
        updated_at=now_ms,
        files=[],
        meta={
            "root_layout": {
                "noteapp_dir": NOTEAPP_DIRNAME,
                "ai_dir": AI_DIRNAME,
                "notes_dir": NOTES_DIRNAME,
                "attachments_dir": ATTACHMENTS_DIRNAME,
            }
        },
    )

    filemap_path = root / NOTEAPP_DIRNAME / FILEMAP_FILENAME
    write_filemap_atomic(filemap_path, document)
    tombstone_ledger_path = root / NOTEAPP_DIRNAME / TOMBSTONE_LEDGER_FILENAME
    if not tombstone_ledger_path.exists():
        tombstone_ledger_path.write_text("", encoding="utf-8")
    return document
