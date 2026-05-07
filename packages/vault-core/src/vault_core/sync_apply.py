from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from .convergence import ManifestConvergenceResult, persist_manifest_convergence
from .ledger import rewrite_tombstone_ledger
from .manifest import compute_manifest_summary_hash
from .models import FileMapDocument, ManifestRecord, TombstoneRecord, VaultStateRecord
from .recovery import apply_committed_tombstones, apply_manifest_reconciled_state, requires_full_pull
from .sqlite_store import (
    load_commit_intent_journal,
    load_vault_state,
    recover_submitted_commit_from_manifest,
    recover_submitted_commit_match,
    upsert_vault_state,
)


@dataclass(frozen=True)
class AppliedManifestResult:
    convergence: ManifestConvergenceResult
    state: VaultStateRecord


@dataclass(frozen=True)
class SubmittedRecoveryResult:
    state: VaultStateRecord
    tombstones: List[TombstoneRecord]
    requires_full_pull: bool


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


def recover_submitted_commit_flow(
    connection: sqlite3.Connection,
    *,
    ledger_path: Path,
    vault_id: str,
    local_tombstones: Iterable[TombstoneRecord],
    matched_revision: int,
    observed_head_revision: int,
    normalized_at: int,
    matched_manifest: Optional[ManifestRecord] = None,
) -> SubmittedRecoveryResult:
    journal = load_commit_intent_journal(connection, vault_id)
    if journal is None:
        raise KeyError(f"commit_intent_journal not found: {vault_id}")

    updated_tombstones = apply_committed_tombstones(
        list(local_tombstones),
        journal,
        committed_revision=matched_revision,
    )
    if matched_manifest is None:
        state = recover_submitted_commit_match(
            connection,
            vault_id,
            matched_revision=matched_revision,
            observed_head_revision=observed_head_revision,
            matched_manifest_summary=None,
            normalized_at=normalized_at,
        )
    else:
        state = recover_submitted_commit_from_manifest(
            connection,
            vault_id,
            matched_manifest=matched_manifest,
            matched_revision=matched_revision,
            observed_head_revision=observed_head_revision,
            normalized_at=normalized_at,
        )

    rewrite_tombstone_ledger(ledger_path, updated_tombstones)
    return SubmittedRecoveryResult(
        state=state,
        tombstones=updated_tombstones,
        requires_full_pull=requires_full_pull(state, observed_head_revision=observed_head_revision),
    )
