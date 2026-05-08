from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

from vault_core.sync_http import UrlopenLike

from .service import DesktopSyncService, build_desktop_sync_service
from .sync_runtime import DesktopSyncHttpConfig
from .workspace import DesktopWorkspaceSnapshot


@dataclass(frozen=True)
class DesktopSyncRunOnceResult:
    initialized: DesktopWorkspaceSnapshot
    recovery: object
    pull: object
    final_snapshot: DesktopWorkspaceSnapshot
    pull_apply_recovery: object | None = None


@dataclass(frozen=True)
class DesktopSyncCycleResult:
    initialized: DesktopWorkspaceSnapshot
    recovery: object
    submitted: Optional[object]
    pull: object
    final_snapshot: DesktopWorkspaceSnapshot
    pull_apply_recovery: object | None = None


@dataclass(frozen=True)
class DesktopSyncRunner:
    service: DesktopSyncService

    def run_once(
        self,
        *,
        recovery_normalized_at: int,
        pull_rewritten_at: int,
        init_now_ms: Optional[int] = None,
    ) -> DesktopSyncRunOnceResult:
        initialized = self.service.ensure_initialized(now_ms=init_now_ms)
        recovery = self.service.resume_commit_recovery(
            normalized_at=recovery_normalized_at,
        )
        pull_apply_recovery = self.service.resume_pull_apply_recovery(
            normalized_at=recovery_normalized_at,
        )
        pull = self.service.pull_and_apply(rewritten_at=pull_rewritten_at)
        final_snapshot = self.service.load_snapshot()
        return DesktopSyncRunOnceResult(
            initialized=initialized,
            recovery=recovery,
            pull_apply_recovery=pull_apply_recovery,
            pull=pull,
            final_snapshot=final_snapshot,
        )

    def run_cycle(
        self,
        *,
        recovery_normalized_at: int,
        pull_rewritten_at: int,
        init_now_ms: Optional[int] = None,
        submit_created_at: Optional[int] = None,
        submit_file_ids: Optional[Iterable[str]] = None,
        submit_detected: bool = False,
        encrypted_blob_by_file_id: Optional[Mapping[str, bytes]] = None,
        commit_intent_id: Optional[str] = None,
        cleanup_normalized_at: Optional[int] = None,
    ) -> DesktopSyncCycleResult:
        initialized = self.service.ensure_initialized(now_ms=init_now_ms)
        recovery = self.service.resume_commit_recovery(
            normalized_at=recovery_normalized_at,
        )
        pull_apply_recovery = self.service.resume_pull_apply_recovery(
            normalized_at=recovery_normalized_at,
        )

        resolved_submit_file_ids = None if submit_file_ids is None else list(submit_file_ids)
        if submit_created_at is None:
            if (
                resolved_submit_file_ids
                or encrypted_blob_by_file_id is not None
                or submit_detected
            ):
                raise ValueError(
                    "submit file_ids, submit_detected, and encrypted payloads require submit_created_at"
                )
            submitted = None
        else:
            if submit_detected:
                if resolved_submit_file_ids:
                    raise ValueError("submit_detected cannot be combined with submit_file_ids")
                if encrypted_blob_by_file_id is not None:
                    raise ValueError(
                        "submit_detected cannot be combined with encrypted_blob_by_file_id"
                    )
                submitted = self.service.submit_detected_changes_if_needed(
                    created_at=submit_created_at,
                    commit_intent_id=commit_intent_id,
                    cleanup_normalized_at=cleanup_normalized_at,
                )
            else:
                if not resolved_submit_file_ids:
                    raise ValueError("submit_created_at requires at least one submit file_id")
                submitted = self.service.submit_workspace_commit(
                    created_at=submit_created_at,
                    file_ids=resolved_submit_file_ids,
                    encrypted_blob_by_file_id=encrypted_blob_by_file_id,
                    commit_intent_id=commit_intent_id,
                    cleanup_normalized_at=cleanup_normalized_at,
                )

        pull = self.service.pull_and_apply(rewritten_at=pull_rewritten_at)
        final_snapshot = self.service.load_snapshot()
        return DesktopSyncCycleResult(
            initialized=initialized,
            recovery=recovery,
            pull_apply_recovery=pull_apply_recovery,
            submitted=submitted,
            pull=pull,
            final_snapshot=final_snapshot,
        )


def build_desktop_sync_runner(
    config: DesktopSyncHttpConfig,
    vault_root: Path,
    *,
    db_path: Optional[Path] = None,
    api_opener: Optional[UrlopenLike] = None,
    blob_opener: Optional[UrlopenLike] = None,
) -> DesktopSyncRunner:
    return DesktopSyncRunner(
        service=build_desktop_sync_service(
            config,
            vault_root,
            db_path=db_path,
            api_opener=api_opener,
            blob_opener=blob_opener,
        )
    )
