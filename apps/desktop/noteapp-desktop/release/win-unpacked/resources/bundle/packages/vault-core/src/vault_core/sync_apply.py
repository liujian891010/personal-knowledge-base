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
    LocalCommitRecoveryResult,
    RevisionMetadata,
    SubmittedConfirmationPlan,
    SubmittedConfirmationResolution,
    plan_commit_recovery,
    plan_submitted_confirmation,
    recover_local_commit_state,
    resolve_submitted_confirmation,
)


@dataclass(frozen=True)
class AppliedManifestResult:
    convergence: ManifestConvergenceResult
    state: VaultStateRecord
    required_blob_ids: List[str]


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


@dataclass(frozen=True)
class CommitRecoveryExecutionResult:
    mode: str
    requires_full_pull: bool = False
    local: Optional[LocalCommitRecoveryResult] = None
    submitted: Optional[SubmittedConfirmationExecutionResult] = None


@dataclass(frozen=True)
class SubmittedConfirmationRemoteState:
    observed_head_revision: int
    head_commit_intent_id: str
    revisions: tuple[RevisionMetadata, ...] = ()
    matched_manifest: Optional[ManifestRecord] = None

    def __post_init__(self) -> None:
        if self.observed_head_revision < 0:
            raise ValueError("observed_head_revision must be a non-negative integer")
        if not self.head_commit_intent_id:
            raise ValueError("head_commit_intent_id must be non-empty")
        object.__setattr__(self, "revisions", tuple(self.revisions))


def _validate_scan_revision_metadata(
    plan: SubmittedConfirmationPlan,
    revisions: Iterable[RevisionMetadata],
) -> None:
    if plan.mode != "scan_range":
        return
    if plan.scan_from_revision is None or plan.scan_to_revision is None:
        raise ValueError("scan_range submitted confirmation plan must define a revision window")
    for record in revisions:
        if record.revision < plan.scan_from_revision or record.revision > plan.scan_to_revision:
            raise ValueError("revision metadata falls outside submitted confirmation scan range")


def _validate_submitted_confirmation_manifest(
    *,
    vault_id: str,
    plan: SubmittedConfirmationPlan,
    resolution: SubmittedConfirmationResolution,
    matched_manifest: Optional[ManifestRecord],
) -> None:
    if matched_manifest is None:
        return
    if matched_manifest.vault_id != vault_id:
        raise ValueError("matched_manifest vault_id does not match submitted confirmation vault")

    if plan.mode == "miss":
        raise ValueError("matched_manifest is not allowed when submitted confirmation misses")
    if plan.mode == "head_match":
        expected_revision = plan.matched_revision
    else:
        if resolution.matched_metadata is None:
            raise ValueError("matched_manifest requires a matched submitted revision")
        expected_revision = resolution.matched_metadata.revision

    if expected_revision is None:
        raise ValueError("submitted confirmation did not resolve a matched revision")
    if matched_manifest.revision != expected_revision:
        raise ValueError("matched_manifest revision does not match submitted confirmation target")


def plan_required_blob_ids(
    current_document: FileMapDocument,
    manifest: ManifestRecord,
) -> List[str]:
    current_active_by_file_id = {
        record.file_id: record
        for record in current_document.files
        if record.status == "active"
    }
    required_blob_ids: List[str] = []
    seen_blob_ids: set[str] = set()

    for entry in manifest.sorted_files():
        current = current_active_by_file_id.get(entry.file_id)
        if current is not None and current.content_hash == entry.content_hash:
            continue
        if entry.blob_id in seen_blob_ids:
            continue
        seen_blob_ids.add(entry.blob_id)
        required_blob_ids.append(entry.blob_id)

    return required_blob_ids


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

    required_blob_ids = plan_required_blob_ids(current_document, manifest)
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
        required_blob_ids=required_blob_ids,
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
    remote_state = SubmittedConfirmationRemoteState(
        observed_head_revision=observed_head_revision,
        head_commit_intent_id=head_commit_intent_id,
        revisions=tuple(revisions),
        matched_manifest=matched_manifest,
    )
    return execute_submitted_commit_confirmation_remote_state(
        connection,
        ledger_path=ledger_path,
        vault_id=vault_id,
        local_tombstones=local_tombstones,
        normalized_at=normalized_at,
        remote_state=remote_state,
    )


def execute_submitted_commit_confirmation_remote_state(
    connection: sqlite3.Connection,
    *,
    ledger_path: Path,
    vault_id: str,
    local_tombstones: Iterable[TombstoneRecord],
    normalized_at: int,
    remote_state: SubmittedConfirmationRemoteState,
) -> SubmittedConfirmationExecutionResult:
    journal = load_commit_intent_journal(connection, vault_id)
    if journal is None:
        raise KeyError(f"commit_intent_journal not found: {vault_id}")

    plan = plan_submitted_confirmation(
        journal,
        observed_head_revision=remote_state.observed_head_revision,
        head_commit_intent_id=remote_state.head_commit_intent_id,
    )
    _validate_scan_revision_metadata(plan, remote_state.revisions)
    resolution = resolve_submitted_confirmation(
        plan,
        commit_intent_id=journal.commit_intent_id,
        revisions=remote_state.revisions,
    )
    _validate_submitted_confirmation_manifest(
        vault_id=vault_id,
        plan=plan,
        resolution=resolution,
        matched_manifest=remote_state.matched_manifest,
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

    matched_revision = (
        remote_state.observed_head_revision
        if plan.mode == "head_match"
        else resolution.matched_metadata.revision
    )
    recovery = recover_submitted_commit_flow(
        connection,
        ledger_path=ledger_path,
        vault_id=vault_id,
        local_tombstones=local_tombstones,
        matched_revision=matched_revision,
        observed_head_revision=remote_state.observed_head_revision,
        normalized_at=normalized_at,
        matched_manifest=remote_state.matched_manifest,
    )
    return SubmittedConfirmationExecutionResult(
        plan=plan,
        resolution=resolution,
        recovery=recovery,
    )


def resume_commit_recovery(
    connection: sqlite3.Connection,
    *,
    vault_root: Path,
    vault_id: str,
    normalized_at: int,
    ledger_path: Optional[Path] = None,
    local_tombstones: Iterable[TombstoneRecord] = (),
    observed_head_revision: Optional[int] = None,
    head_commit_intent_id: Optional[str] = None,
    revisions: Iterable[RevisionMetadata] = (),
    matched_manifest: Optional[ManifestRecord] = None,
    remote_state: Optional[SubmittedConfirmationRemoteState] = None,
) -> CommitRecoveryExecutionResult:
    state = load_vault_state(connection, vault_id)
    if state is None:
        raise KeyError(f"vault_state not found: {vault_id}")

    journal = load_commit_intent_journal(connection, vault_id)
    plan = plan_commit_recovery(state, journal=journal)
    legacy_revisions = tuple(revisions)

    if plan.mode != "submitted_confirmation":
        local = recover_local_commit_state(
            connection,
            vault_id=vault_id,
            vault_root=vault_root,
        )
        return CommitRecoveryExecutionResult(
            mode=plan.mode,
            requires_full_pull=requires_full_pull(local.state),
            local=local,
            submitted=None,
        )

    if ledger_path is None:
        raise ValueError("ledger_path is required for submitted confirmation recovery")
    if remote_state is not None and (
        observed_head_revision is not None
        or head_commit_intent_id is not None
        or legacy_revisions
        or matched_manifest is not None
    ):
        raise ValueError("remote_state cannot be combined with legacy submitted confirmation inputs")
    if remote_state is None:
        if observed_head_revision is None:
            raise ValueError("observed_head_revision is required for submitted confirmation recovery")
        if head_commit_intent_id is None:
            raise ValueError("head_commit_intent_id is required for submitted confirmation recovery")
        remote_state = SubmittedConfirmationRemoteState(
            observed_head_revision=observed_head_revision,
            head_commit_intent_id=head_commit_intent_id,
            revisions=legacy_revisions,
            matched_manifest=matched_manifest,
        )

    submitted = execute_submitted_commit_confirmation_remote_state(
        connection,
        ledger_path=ledger_path,
        vault_id=vault_id,
        local_tombstones=local_tombstones,
        normalized_at=normalized_at,
        remote_state=remote_state,
    )
    return CommitRecoveryExecutionResult(
        mode=plan.mode,
        requires_full_pull=submitted.recovery.requires_full_pull,
        local=None,
        submitted=submitted,
    )
