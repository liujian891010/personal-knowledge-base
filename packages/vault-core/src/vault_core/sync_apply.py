from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .convergence import ManifestConvergenceResult, persist_manifest_convergence
from .manifest import compute_manifest_summary_hash
from .models import FileMapDocument, ManifestRecord, TombstoneRecord, VaultStateRecord
from .recovery import apply_manifest_reconciled_state
from .sqlite_store import load_vault_state, upsert_vault_state


@dataclass(frozen=True)
class AppliedManifestResult:
    convergence: ManifestConvergenceResult
    state: VaultStateRecord


def apply_pulled_manifest(
    connection: sqlite3.Connection,
    *,
    filemap_path: Path,
    ledger_path: Path,
    current_document: FileMapDocument,
    manifest: ManifestRecord,
    local_tombstones: Iterable[TombstoneRecord],
    rewritten_at: int,
) -> AppliedManifestResult:
    state = load_vault_state(connection, manifest.vault_id)
    if state is None:
        raise KeyError(f"vault_state not found: {manifest.vault_id}")

    convergence = persist_manifest_convergence(
        filemap_path,
        ledger_path,
        current_document,
        manifest,
        local_tombstones=local_tombstones,
        rewritten_at=rewritten_at,
    )
    updated_state = apply_manifest_reconciled_state(
        state,
        target_revision=manifest.revision,
        manifest_summary=compute_manifest_summary_hash(manifest),
    )
    upsert_vault_state(connection, updated_state)
    return AppliedManifestResult(
        convergence=convergence,
        state=updated_state,
    )
