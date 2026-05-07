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
    recover_submitted_commit_miss,
    recover_submitted_commit_from_manifest,
    recover_submitted_commit_match,
    upsert_vault_state,
)
from .sync_commit import (
    RevisionMetadata,
    SubmittedConfirmationPlan,
    SubmittedConfirmationResolution,
    plan_submitted_confirmation,
    resolve_submitted_confirmation,
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


@dataclass(frozen=True)
class SubmittedConfirmationExecutionResult:
    plan: SubmittedConfirmationPlan
    resolution: SubmittedConfirmationResolution
    recovery: SubmittedRecoveryResult


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


def execute_submitted_commit_confirmation(
    connection: sqlite3.Connection,
    *,
    ledger_path: Path,
    vault_id: str,
    local_tombstones: Iterable[TombstoneRecord],
    observed_head_revision: int,
    head_commit_intent_id: str,
    normalized_at: int,
    revisions: Iterable[RevisionMetadata] = (),
    matched_manifest: Optional[ManifestRecord] = None,
) -> SubmittedConfirmationExecutionResult:
    journal = load_commit_intent_journal(connection, vault_id)
    if journal is None:
        raise KeyError(f"commit_intent_journal not found: {vault_id}")

    plan = plan_submitted_confirmation(
        journal,
        observed_head_revision=observed_head_revision,
        head_commit_intent_id=head_commit_intent_id,
    )
    resolution = resolve_submitted_confirmation(
        plan,
        commit_intent_id=journal.commit_intent_id,
        revisions=revisions,
    )

    if plan.mode == "miss" or (plan.mode == "scan_range" and resolution.matched_metadata is None):
        recovery = SubmittedRecoveryResult(
            state=recover_submitted_commit_miss(
                connection,
                vault_id,
                normalized_at=normalized_at,
            ),
            tombstones=list(local_tombstones),
            requires_full_pull=False,
        )
        return SubmittedConfirmationExecutionResult(
            plan=plan,
            resolution=resolution,
            recovery=recovery,
        )

    matched_revision = observed_head_revision if plan.mode == "head_match" else resolution.matched_metadata.revision
    recovery = recover_submitted_commit_flow(
        connection,
        ledger_path=ledger_path,
        vault_id=vault_id,
        local_tombstones=local_tombstones,
        matched_revision=matched_revision,
        observed_head_revision=observed_head_revision,
        normalized_at=normalized_at,
        matched_manifest=matched_manifest,
    )
    return SubmittedConfirmationExecutionResult(
        plan=plan,
        resolution=resolution,
        recovery=recovery,
    )
