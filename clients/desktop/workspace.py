from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from vault_core import (
    BlobDownloadSessionResult,
    CommitRecoverySessionResult,
    FileMapDocument,
    PullReconcileSessionResult,
    PullSyncSessionResult,
    TombstoneRecord,
    VaultStateRecord,
    bootstrap_database,
    initialize_vault,
    initialize_vault_state,
    load_filemap,
    load_tombstone_ledger,
    load_vault_state,
    open_database,
)
from vault_core.constants import FILEMAP_FILENAME, NOTEAPP_DIRNAME, TOMBSTONE_LEDGER_FILENAME
from vault_core.sync_http import UrlopenLike

from .change_detection import DesktopWorkspaceChangeSet, detect_local_workspace_changes
from .sync_runtime import (
    DesktopSyncHttpConfig,
    DesktopSyncRuntime,
    build_desktop_sync_runtime,
)
from .worker_state import (
    DesktopSyncWorkerHealth,
    DesktopSyncWorkerStateRecord,
    build_desktop_sync_worker_health,
    load_desktop_sync_worker_state,
)

STATE_DB_FILENAME = "state.sqlite3"
SYNC_WORKER_STATE_FILENAME = "sync-worker-state.json"


def _require_matching_vault(expected_vault_id: str, actual_vault_id: str, label: str) -> None:
    if actual_vault_id != expected_vault_id:
        raise ValueError(
            f"{label} vault_id does not match desktop config: "
            f"expected {expected_vault_id}, got {actual_vault_id}"
        )


@dataclass(frozen=True)
class DesktopVaultPaths:
    root: Path
    filemap_path: Path
    ledger_path: Path
    db_path: Path
    worker_state_path: Path

    @classmethod
    def from_root(
        cls,
        root: Path,
        *,
        db_path: Optional[Path] = None,
    ) -> "DesktopVaultPaths":
        noteapp_root = root / NOTEAPP_DIRNAME
        return cls(
            root=root,
            filemap_path=noteapp_root / FILEMAP_FILENAME,
            ledger_path=noteapp_root / TOMBSTONE_LEDGER_FILENAME,
            db_path=noteapp_root / STATE_DB_FILENAME if db_path is None else db_path,
            worker_state_path=noteapp_root / SYNC_WORKER_STATE_FILENAME,
        )


@dataclass(frozen=True)
class DesktopWorkspaceSnapshot:
    document: FileMapDocument
    state: VaultStateRecord
    tombstones: list[TombstoneRecord]


@dataclass(frozen=True)
class DesktopVaultWorkspace:
    runtime: DesktopSyncRuntime
    paths: DesktopVaultPaths

    @property
    def config(self) -> DesktopSyncHttpConfig:
        return self.runtime.config

    @property
    def vault_id(self) -> str:
        return self.runtime.vault_id

    @property
    def vault_root(self) -> Path:
        return self.paths.root

    def ensure_initialized(self, *, now_ms: Optional[int] = None) -> DesktopWorkspaceSnapshot:
        if self.paths.filemap_path.exists():
            document = load_filemap(self.paths.filemap_path)
        else:
            document = initialize_vault(
                self.paths.root,
                vault_id=self.vault_id,
                now_ms=now_ms,
            )
        _require_matching_vault(self.vault_id, document.vault_id, "filemap")

        with closing(self._open_connection()) as connection:
            state = initialize_vault_state(
                connection,
                self.vault_id,
                meta={"device_id": self.runtime.device_id},
            )
        return DesktopWorkspaceSnapshot(
            document=document,
            state=state,
            tombstones=load_tombstone_ledger(self.paths.ledger_path),
        )

    def load_snapshot(self) -> DesktopWorkspaceSnapshot:
        with closing(self._open_connection()) as connection:
            return self._load_snapshot_from_connection(connection)

    def pull_reconcile(self, *, rewritten_at: int) -> PullReconcileSessionResult:
        with closing(self._open_connection()) as connection:
            snapshot = self._load_snapshot_from_connection(connection)
            return self.runtime.session.pull_reconcile(
                connection,
                filemap_path=self.paths.filemap_path,
                ledger_path=self.paths.ledger_path,
                current_document=snapshot.document,
                current_state=snapshot.state,
                local_tombstones=snapshot.tombstones,
                rewritten_at=rewritten_at,
            )

    def pull_and_ack(self, *, rewritten_at: int) -> PullSyncSessionResult:
        with closing(self._open_connection()) as connection:
            snapshot = self._load_snapshot_from_connection(connection)
            return self.runtime.session.pull_and_ack(
                connection,
                filemap_path=self.paths.filemap_path,
                ledger_path=self.paths.ledger_path,
                current_document=snapshot.document,
                current_state=snapshot.state,
                local_tombstones=snapshot.tombstones,
                rewritten_at=rewritten_at,
            )

    def resume_commit_recovery(self, *, normalized_at: int) -> CommitRecoverySessionResult:
        with closing(self._open_connection()) as connection:
            snapshot = self._load_snapshot_from_connection(connection)
            return self.runtime.session.resume_commit_recovery(
                connection,
                vault_root=self.paths.root,
                vault_id=self.vault_id,
                normalized_at=normalized_at,
                ledger_path=self.paths.ledger_path,
                local_tombstones=snapshot.tombstones,
            )

    def download_blobs(self, blob_ids: Iterable[str]) -> BlobDownloadSessionResult:
        return self.runtime.session.download_blobs(
            vault_id=self.vault_id,
            blob_ids=blob_ids,
        )

    def detect_local_changes(self) -> DesktopWorkspaceChangeSet:
        return detect_local_workspace_changes(
            self.vault_root,
            self.load_snapshot().document,
        )

    def load_worker_state(self) -> DesktopSyncWorkerStateRecord:
        return load_desktop_sync_worker_state(self.paths.worker_state_path)

    def load_worker_health(self) -> DesktopSyncWorkerHealth:
        return build_desktop_sync_worker_health(self.load_worker_state())

    def _open_connection(self):
        connection = open_database(self.paths.db_path)
        bootstrap_database(connection)
        return connection

    def _load_snapshot_from_connection(self, connection) -> DesktopWorkspaceSnapshot:
        document = load_filemap(self.paths.filemap_path)
        _require_matching_vault(self.vault_id, document.vault_id, "filemap")
        state = load_vault_state(connection, self.vault_id)
        if state is None:
            raise KeyError(f"vault_state not found: {self.vault_id}")
        _require_matching_vault(self.vault_id, state.vault_id, "vault_state")
        return DesktopWorkspaceSnapshot(
            document=document,
            state=state,
            tombstones=load_tombstone_ledger(self.paths.ledger_path),
        )


def build_desktop_vault_workspace(
    config: DesktopSyncHttpConfig,
    vault_root: Path,
    *,
    db_path: Optional[Path] = None,
    api_opener: Optional[UrlopenLike] = None,
    blob_opener: Optional[UrlopenLike] = None,
) -> DesktopVaultWorkspace:
    return DesktopVaultWorkspace(
        runtime=build_desktop_sync_runtime(
            config,
            api_opener=api_opener,
            blob_opener=blob_opener,
        ),
        paths=DesktopVaultPaths.from_root(vault_root, db_path=db_path),
    )
