from __future__ import annotations

from dataclasses import dataclass

from .models import VaultStateRecord
from .recovery import requires_full_pull


@dataclass(frozen=True)
class ReconcilePlan:
    observed_head_revision: int
    target_revision: int
    should_download_manifest: bool
    requires_full_pull: bool
    can_use_summary_shortcut: bool


def plan_pull_reconcile(
    state: VaultStateRecord,
    *,
    observed_head_revision: int,
) -> ReconcilePlan:
    full_pull = requires_full_pull(state, observed_head_revision=observed_head_revision)
    should_download_manifest = full_pull or observed_head_revision > state.last_applied_revision
    return ReconcilePlan(
        observed_head_revision=observed_head_revision,
        target_revision=observed_head_revision,
        should_download_manifest=should_download_manifest,
        requires_full_pull=full_pull,
        can_use_summary_shortcut=should_download_manifest and not full_pull,
    )
