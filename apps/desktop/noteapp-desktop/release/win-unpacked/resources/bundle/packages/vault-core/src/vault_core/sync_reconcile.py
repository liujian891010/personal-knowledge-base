from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .models import FileMapDocument, ManifestRecord, TombstoneRecord, VaultStateRecord
from .sync_apply import AppliedManifestResult, apply_pulled_manifest
from .sync_plan import ReconcilePlan, plan_pull_reconcile


@dataclass(frozen=True)
class ReconcileResult:
    plan: ReconcilePlan
    state: VaultStateRecord
    applied: Optional[AppliedManifestResult] = None


def execute_pull_reconcile(
    connection: sqlite3.Connection,
    *,
    filemap_path: Path,
    ledger_path: Path,
    current_document: FileMapDocument,
    current_state: VaultStateRecord,
    local_tombstones: Iterable[TombstoneRecord],
    observed_head_revision: int,
    rewritten_at: int,
    manifest: Optional[ManifestRecord] = None,
) -> ReconcileResult:
    plan = plan_pull_reconcile(
        current_state,
        observed_head_revision=observed_head_revision,
    )

    if not plan.should_download_manifest:
        return ReconcileResult(
            plan=plan,
            state=current_state,
            applied=None,
        )

    if manifest is None:
        raise ValueError("manifest is required when reconcile plan requires a download")
    if manifest.vault_id != current_state.vault_id:
        raise ValueError("manifest vault_id does not match current state")
    if manifest.revision != plan.target_revision:
        raise ValueError("manifest revision does not match reconcile target_revision")

    applied = apply_pulled_manifest(
        connection,
        filemap_path=filemap_path,
        ledger_path=ledger_path,
        current_document=current_document,
        manifest=manifest,
        local_tombstones=local_tombstones,
        rewritten_at=rewritten_at,
    )
    return ReconcileResult(
        plan=plan,
        state=applied.state,
        applied=applied,
    )
