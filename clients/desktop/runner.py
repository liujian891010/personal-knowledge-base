from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
        pull = self.service.pull_and_ack(rewritten_at=pull_rewritten_at)
        final_snapshot = self.service.load_snapshot()
        return DesktopSyncRunOnceResult(
            initialized=initialized,
            recovery=recovery,
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
