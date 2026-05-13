from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, List

from .models import TombstoneRecord


def _serialize_line(record: TombstoneRecord) -> str:
    return json.dumps(
        record.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
    ) + "\n"


def rewrite_tombstone_ledger(ledger_path: Path, records: Iterable[TombstoneRecord]) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = ledger_path.with_suffix(ledger_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(_serialize_line(record))
        handle.flush()
        os.fsync(handle.fileno())
    tmp_path.replace(ledger_path)


def append_tombstone(ledger_path: Path, record: TombstoneRecord) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(_serialize_line(record))
        handle.flush()
        os.fsync(handle.fileno())


def load_tombstone_ledger(ledger_path: Path, rewrite_legacy: bool = True) -> List[TombstoneRecord]:
    if not ledger_path.exists():
        return []

    records: List[TombstoneRecord] = []
    needs_rewrite = False
    max_seq = 0

    for raw_line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        payload = json.loads(line)
        local_delete_seq = payload.get("local_delete_seq")
        if local_delete_seq is None:
            max_seq += 1
            payload["local_delete_seq"] = max_seq
            needs_rewrite = True
        else:
            if not isinstance(local_delete_seq, int) or local_delete_seq < 0:
                raise ValueError("local_delete_seq must be a non-negative integer when present")
            max_seq = max(max_seq, local_delete_seq)

        records.append(TombstoneRecord.from_dict(payload))

    if needs_rewrite and rewrite_legacy:
        rewrite_tombstone_ledger(ledger_path, records)

    return records
