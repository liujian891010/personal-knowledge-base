from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DesktopSyncTimePlan:
    base_now_ms: int
    init_now_ms: int
    recovery_normalized_at: int
    submit_created_at: Optional[int]
    cleanup_normalized_at: Optional[int]
    pull_rewritten_at: int


def resolve_desktop_sync_time_plan(
    *,
    base_now_ms: int,
    init_now_ms: Optional[int] = None,
    recovery_normalized_at: Optional[int] = None,
    submit_created_at: Optional[int] = None,
    cleanup_normalized_at: Optional[int] = None,
    pull_rewritten_at: Optional[int] = None,
    submit_requested: bool = False,
) -> DesktopSyncTimePlan:
    resolved_init_now_ms = base_now_ms if init_now_ms is None else init_now_ms
    resolved_recovery_normalized_at = (
        resolved_init_now_ms + 10
        if recovery_normalized_at is None
        else recovery_normalized_at
    )

    if submit_requested:
        resolved_submit_created_at = (
            resolved_recovery_normalized_at + 10
            if submit_created_at is None
            else submit_created_at
        )
        resolved_cleanup_normalized_at = (
            resolved_submit_created_at + 1
            if cleanup_normalized_at is None
            else cleanup_normalized_at
        )
        resolved_pull_rewritten_at = (
            resolved_submit_created_at + 10
            if pull_rewritten_at is None
            else pull_rewritten_at
        )
    else:
        resolved_submit_created_at = submit_created_at
        resolved_cleanup_normalized_at = cleanup_normalized_at
        resolved_pull_rewritten_at = (
            resolved_recovery_normalized_at + 10
            if pull_rewritten_at is None
            else pull_rewritten_at
        )

    return DesktopSyncTimePlan(
        base_now_ms=base_now_ms,
        init_now_ms=resolved_init_now_ms,
        recovery_normalized_at=resolved_recovery_normalized_at,
        submit_created_at=resolved_submit_created_at,
        cleanup_normalized_at=resolved_cleanup_normalized_at,
        pull_rewritten_at=resolved_pull_rewritten_at,
    )
