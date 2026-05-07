from __future__ import annotations

from typing import Iterable, List, Optional

from .models import CommitIntentJournalRecord, SyncApplyJournalRecord, TombstoneRecord, VaultStateRecord


def _sorted_unique_revisions(revisions: Iterable[int]) -> List[int]:
    return sorted(set(revisions))


def should_block_new_commit(
    state: VaultStateRecord,
    *,
    has_active_commit_journal: bool = False,
) -> bool:
    if has_active_commit_journal:
        return True
    if state.commit_in_progress:
        return True
    if state.has_unresolved_conflicts:
        return True
    if state.last_manifest_summary_status != "valid":
        return True
    return state.last_manifest_summary is None


def requires_full_pull(
    state: VaultStateRecord,
    *,
    observed_head_revision: Optional[int] = None,
) -> bool:
    if state.last_manifest_summary_status != "valid":
        return True
    if state.last_manifest_summary is None:
        return True
    if observed_head_revision is None:
        return False
    return observed_head_revision > state.last_applied_revision


def apply_manifest_summary_stale(state: VaultStateRecord) -> VaultStateRecord:
    return VaultStateRecord(
        vault_id=state.vault_id,
        last_applied_revision=state.last_applied_revision,
        remote_head_revision=state.remote_head_revision,
        acked_revision=state.acked_revision,
        pending_ack_to_server=list(state.pending_ack_to_server),
        commit_in_progress=state.commit_in_progress,
        last_manifest_summary=None,
        last_manifest_summary_status="stale",
        local_delete_sequence=state.local_delete_sequence,
        has_unresolved_conflicts=state.has_unresolved_conflicts,
        schema_version=state.schema_version,
        meta=state.meta,
    )


def apply_sync_finalizing_recovery(
    state: VaultStateRecord,
    journal: SyncApplyJournalRecord,
) -> VaultStateRecord:
    pending_ack = _sorted_unique_revisions([*state.pending_ack_to_server, journal.target_revision])
    return VaultStateRecord(
        vault_id=state.vault_id,
        last_applied_revision=max(state.last_applied_revision, journal.target_revision),
        remote_head_revision=max(state.remote_head_revision, journal.target_revision),
        acked_revision=max(state.acked_revision, journal.target_revision),
        pending_ack_to_server=pending_ack,
        commit_in_progress=state.commit_in_progress,
        last_manifest_summary=journal.target_manifest_hash,
        last_manifest_summary_status="valid",
        local_delete_sequence=state.local_delete_sequence,
        has_unresolved_conflicts=state.has_unresolved_conflicts,
        schema_version=state.schema_version,
        meta=state.meta,
    )


def apply_prepared_commit_recovery(state: VaultStateRecord) -> VaultStateRecord:
    return VaultStateRecord(
        vault_id=state.vault_id,
        last_applied_revision=state.last_applied_revision,
        remote_head_revision=state.remote_head_revision,
        acked_revision=state.acked_revision,
        pending_ack_to_server=list(state.pending_ack_to_server),
        commit_in_progress=False,
        last_manifest_summary=state.last_manifest_summary,
        last_manifest_summary_status=state.last_manifest_summary_status,
        local_delete_sequence=state.local_delete_sequence,
        has_unresolved_conflicts=state.has_unresolved_conflicts,
        schema_version=state.schema_version,
        meta=state.meta,
    )


def apply_submitted_commit_match_recovery(
    state: VaultStateRecord,
    *,
    matched_revision: int,
    observed_head_revision: int,
    matched_manifest_summary: Optional[str],
) -> VaultStateRecord:
    if matched_manifest_summary is None:
        last_manifest_summary = None
        last_manifest_summary_status = "stale"
    else:
        last_manifest_summary = matched_manifest_summary
        last_manifest_summary_status = "valid"

    return VaultStateRecord(
        vault_id=state.vault_id,
        last_applied_revision=max(state.last_applied_revision, matched_revision),
        remote_head_revision=max(state.remote_head_revision, observed_head_revision),
        acked_revision=max(state.acked_revision, matched_revision),
        pending_ack_to_server=list(state.pending_ack_to_server),
        commit_in_progress=False,
        last_manifest_summary=last_manifest_summary,
        last_manifest_summary_status=last_manifest_summary_status,
        local_delete_sequence=state.local_delete_sequence,
        has_unresolved_conflicts=state.has_unresolved_conflicts,
        schema_version=state.schema_version,
        meta=state.meta,
    )


def apply_submitted_commit_miss_recovery(state: VaultStateRecord) -> VaultStateRecord:
    return apply_prepared_commit_recovery(state)


def normalize_commit_journal_for_recovery(
    journal: CommitIntentJournalRecord,
    *,
    normalized_at: int,
) -> CommitIntentJournalRecord:
    if journal.status != "acknowledged":
        return journal
    return CommitIntentJournalRecord(
        vault_id=journal.vault_id,
        commit_intent_id=journal.commit_intent_id,
        intent_manifest_hash=journal.intent_manifest_hash,
        base_revision=journal.base_revision,
        created_by_device=journal.created_by_device,
        status="submitted",
        intent_delete_seq_upper_bound=journal.intent_delete_seq_upper_bound,
        created_at=journal.created_at,
        updated_at=normalized_at,
    )


def select_pending_tombstones_for_commit(
    tombstones: Iterable[TombstoneRecord],
    journal: CommitIntentJournalRecord,
) -> List[TombstoneRecord]:
    selected = []
    for record in tombstones:
        if record.deleted_revision is not None:
            continue
        if journal.intent_delete_seq_upper_bound is not None:
            if record.local_delete_seq <= journal.intent_delete_seq_upper_bound:
                selected.append(record)
        else:
            if record.deleted_at <= journal.created_at:
                selected.append(record)
    return selected
