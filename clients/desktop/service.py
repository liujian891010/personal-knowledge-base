from __future__ import annotations

from contextlib import closing, suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional
from uuid import uuid4

from vault_core import (
    BlobDownloadSessionResult,
    BlobStagingMaterializationResult,
    CommitFinalizeCleanupResult,
    CommitRecoverySessionResult,
    CommitSnapshotTable,
    CommitSubmissionBundle,
    CommitSubmissionExecutionResult,
    ContentSnapshotMaterializationResult,
    PullReconcileSessionResult,
    PullSyncSessionResult,
    TombstoneRecord,
    VaultStateRecord,
    build_commit_snapshot_table,
    cleanup_commit_staging_artifacts,
    cleanup_failed_commit_submission,
    finalize_commit_submission_cleanup,
    load_tombstone_ledger,
    materialize_blob_staging_plan,
    materialize_content_snapshot_plan,
    prepare_commit_submission,
)
from vault_core.sync_http import UrlopenLike

from .change_detection import (
    DesktopTrackedChangeCommitPlan,
    DesktopWorkspaceChangeSet,
    build_tracked_change_commit_plan,
    detect_local_workspace_changes,
)
from .crypto import DesktopBlobCryptoProvider, build_placeholder_blob_crypto_provider
from .sync_runtime import DesktopSyncHttpConfig
from .worker_state import DesktopSyncWorkerHealth, DesktopSyncWorkerStateRecord
from .workspace import (
    DesktopVaultWorkspace,
    DesktopWorkspaceSnapshot,
    build_desktop_vault_workspace,
)


def _resolve_workspace_file_path(vault_root: Path, relative_path: str) -> Path:
    path = Path(relative_path)
    if path.anchor or path.drive or ".." in path.parts:
        raise ValueError(f"workspace file path is not safe: {relative_path!r}")
    return vault_root / path


@dataclass(frozen=True)
class DesktopPreparedCommit:
    snapshot: DesktopWorkspaceSnapshot
    submission: CommitSubmissionBundle
    snapshot_materialization: ContentSnapshotMaterializationResult
    blob_staging_materialization: BlobStagingMaterializationResult
    snapshot_table: CommitSnapshotTable


@dataclass(frozen=True)
class DesktopCommitCleanupResult:
    state: VaultStateRecord
    removed_staging_paths: list[Path]


@dataclass(frozen=True)
class DesktopCommitSessionResult:
    prepared: DesktopPreparedCommit
    network: CommitSubmissionExecutionResult
    finalized: Optional[CommitFinalizeCleanupResult] = None
    cleanup: Optional[DesktopCommitCleanupResult] = None


@dataclass(frozen=True)
class DesktopSyncService:
    workspace: DesktopVaultWorkspace
    blob_crypto_provider: DesktopBlobCryptoProvider
    file_id_builder: Callable[[str], str]

    @property
    def config(self) -> DesktopSyncHttpConfig:
        return self.workspace.config

    @property
    def vault_id(self) -> str:
        return self.workspace.vault_id

    def ensure_initialized(self, *, now_ms: Optional[int] = None) -> DesktopWorkspaceSnapshot:
        return self.workspace.ensure_initialized(now_ms=now_ms)

    def load_snapshot(self) -> DesktopWorkspaceSnapshot:
        return self.workspace.load_snapshot()

    def pull_reconcile(self, *, rewritten_at: int) -> PullReconcileSessionResult:
        return self.workspace.pull_reconcile(rewritten_at=rewritten_at)

    def pull_and_ack(self, *, rewritten_at: int) -> PullSyncSessionResult:
        return self.workspace.pull_and_ack(rewritten_at=rewritten_at)

    def resume_commit_recovery(self, *, normalized_at: int) -> CommitRecoverySessionResult:
        return self.workspace.resume_commit_recovery(normalized_at=normalized_at)

    def download_blobs(self, blob_ids: Iterable[str]) -> BlobDownloadSessionResult:
        return self.workspace.download_blobs(blob_ids)

    def detect_local_changes(self) -> DesktopWorkspaceChangeSet:
        return self.workspace.detect_local_changes()

    def load_worker_state(self) -> DesktopSyncWorkerStateRecord:
        return self.workspace.load_worker_state()

    def load_worker_health(self) -> DesktopSyncWorkerHealth:
        return self.workspace.load_worker_health()

    def load_workspace_content(self, file_ids: Iterable[str]) -> dict[str, bytes]:
        snapshot = self.load_snapshot()
        file_by_id = {record.file_id: record for record in snapshot.document.files}
        content_by_file_id: dict[str, bytes] = {}

        for file_id in file_ids:
            record = file_by_id.get(file_id)
            if record is None:
                raise KeyError(f"file_id not found in workspace filemap: {file_id}")
            if record.status != "active":
                raise ValueError(f"workspace file is not active: {file_id}")
            content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            content_by_file_id[file_id] = content_path.read_bytes()

        return content_by_file_id

    def build_tracked_change_commit_plan(self) -> DesktopTrackedChangeCommitPlan:
        snapshot = self.load_snapshot()
        return build_tracked_change_commit_plan(
            self.workspace.vault_root,
            snapshot.document,
            tombstones=snapshot.tombstones,
            current_local_delete_sequence=snapshot.state.local_delete_sequence,
            deleted_by_device=self.config.device_id,
            file_id_builder=self.file_id_builder,
            blob_id_builder=self.blob_crypto_provider.build_blob_id,
        )

    def _submit_tracked_change_plan(
        self,
        snapshot: DesktopWorkspaceSnapshot,
        plan: DesktopTrackedChangeCommitPlan,
        *,
        created_at: int,
        commit_intent_id: Optional[str] = None,
        cleanup_normalized_at: Optional[int] = None,
    ) -> DesktopCommitSessionResult:
        prepared = self._prepare_commit_with_snapshot(
            DesktopWorkspaceSnapshot(
                document=plan.document,
                state=replace(
                    snapshot.state,
                    local_delete_sequence=plan.local_delete_sequence,
                ),
                tombstones=plan.tombstones,
            ),
            created_at=created_at,
            content_by_file_id=plan.content_by_file_id,
            commit_intent_id=commit_intent_id,
        )
        resolved_cleanup_at = created_at if cleanup_normalized_at is None else cleanup_normalized_at

        try:
            network = self.workspace.runtime.session.submit_commit(
                prepared.submission,
                snapshot_table=prepared.snapshot_table,
            )
        except Exception:
            cleanup = self.cleanup_failed_commit(normalized_at=resolved_cleanup_at)
            raise

        if network.commit.status != "committed":
            return DesktopCommitSessionResult(
                prepared=prepared,
                network=network,
                cleanup=self.cleanup_failed_commit(normalized_at=resolved_cleanup_at),
            )

        response = network.commit.response
        if response is None:
            raise ValueError("committed submit_commit result must include response")

        current_tombstones = load_tombstone_ledger(self.workspace.paths.ledger_path)
        with closing(self.workspace._open_connection()) as connection:
            finalized = finalize_commit_submission_cleanup(
                connection,
                vault_root=self.workspace.vault_root,
                ledger_path=self.workspace.paths.ledger_path,
                manifest=prepared.submission.manifest,
                local_tombstones=current_tombstones,
                committed_revision=response.new_revision,
            )
        return DesktopCommitSessionResult(
            prepared=prepared,
            network=network,
            finalized=finalized,
        )

    def cleanup_failed_commit(self, *, normalized_at: int) -> DesktopCommitCleanupResult:
        with closing(self.workspace._open_connection()) as connection:
            state = cleanup_failed_commit_submission(
                connection,
                self.vault_id,
                normalized_at=normalized_at,
            )
        return DesktopCommitCleanupResult(
            state=state,
            removed_staging_paths=cleanup_commit_staging_artifacts(self.workspace.vault_root),
        )

    def prepare_commit(
        self,
        *,
        created_at: int,
        content_by_file_id: Mapping[str, bytes],
        encrypted_blob_by_file_id: Optional[Mapping[str, bytes]] = None,
        commit_intent_id: Optional[str] = None,
    ) -> DesktopPreparedCommit:
        return self._prepare_commit_with_snapshot(
            self.load_snapshot(),
            created_at=created_at,
            content_by_file_id=content_by_file_id,
            encrypted_blob_by_file_id=encrypted_blob_by_file_id,
            commit_intent_id=commit_intent_id,
        )

    def _prepare_commit_with_snapshot(
        self,
        snapshot: DesktopWorkspaceSnapshot,
        *,
        created_at: int,
        content_by_file_id: Mapping[str, bytes],
        encrypted_blob_by_file_id: Optional[Mapping[str, bytes]] = None,
        commit_intent_id: Optional[str] = None,
    ) -> DesktopPreparedCommit:
        resolved_commit_intent_id = commit_intent_id or str(uuid4())
        resolved_encrypted_blob_by_file_id = (
            self.blob_crypto_provider.build_encrypted_blob_map(content_by_file_id)
            if encrypted_blob_by_file_id is None
            else dict(encrypted_blob_by_file_id)
        )

        with closing(self.workspace._open_connection()) as connection:
            submission = prepare_commit_submission(
                connection,
                state=snapshot.state,
                document=snapshot.document,
                tombstones=snapshot.tombstones,
                commit_intent_id=resolved_commit_intent_id,
                created_by_device=self.config.device_id,
                created_at=created_at,
            )

            try:
                snapshot_materialization = materialize_content_snapshot_plan(
                    self.workspace.vault_root,
                    plan=submission.snapshot_plan,
                    document=snapshot.document,
                    content_by_file_id=content_by_file_id,
                )
                blob_staging_materialization = materialize_blob_staging_plan(
                    self.workspace.vault_root,
                    plan=submission.snapshot_plan,
                    snapshot_materialization=snapshot_materialization,
                    encrypted_blob_by_file_id=resolved_encrypted_blob_by_file_id,
                )
                snapshot_table = build_commit_snapshot_table(
                    submission.snapshot_plan,
                    snapshot_materialization=snapshot_materialization,
                    blob_staging_materialization=blob_staging_materialization,
                )
            except Exception:
                with suppress(Exception):
                    cleanup_failed_commit_submission(
                        connection,
                        self.vault_id,
                        normalized_at=created_at,
                    )
                cleanup_commit_staging_artifacts(self.workspace.vault_root)
                raise

        return DesktopPreparedCommit(
            snapshot=snapshot,
            submission=submission,
            snapshot_materialization=snapshot_materialization,
            blob_staging_materialization=blob_staging_materialization,
            snapshot_table=snapshot_table,
        )

    def submit_detected_changes(
        self,
        *,
        created_at: int,
        commit_intent_id: Optional[str] = None,
        cleanup_normalized_at: Optional[int] = None,
    ) -> DesktopCommitSessionResult:
        snapshot = self.load_snapshot()
        plan = build_tracked_change_commit_plan(
            self.workspace.vault_root,
            snapshot.document,
            tombstones=snapshot.tombstones,
            current_local_delete_sequence=snapshot.state.local_delete_sequence,
            deleted_by_device=self.config.device_id,
            file_id_builder=self.file_id_builder,
            blob_id_builder=self.blob_crypto_provider.build_blob_id,
        )
        return self._submit_tracked_change_plan(
            snapshot,
            plan,
            created_at=created_at,
            commit_intent_id=commit_intent_id,
            cleanup_normalized_at=cleanup_normalized_at,
        )

    def submit_detected_changes_if_needed(
        self,
        *,
        created_at: int,
        commit_intent_id: Optional[str] = None,
        cleanup_normalized_at: Optional[int] = None,
    ) -> Optional[DesktopCommitSessionResult]:
        snapshot = self.load_snapshot()
        change_set = detect_local_workspace_changes(
            self.workspace.vault_root,
            snapshot.document,
        )
        if not change_set.changes:
            return None
        plan = build_tracked_change_commit_plan(
            self.workspace.vault_root,
            snapshot.document,
            change_set,
            tombstones=snapshot.tombstones,
            current_local_delete_sequence=snapshot.state.local_delete_sequence,
            deleted_by_device=self.config.device_id,
            file_id_builder=self.file_id_builder,
            blob_id_builder=self.blob_crypto_provider.build_blob_id,
        )
        return self._submit_tracked_change_plan(
            snapshot,
            plan,
            created_at=created_at,
            commit_intent_id=commit_intent_id,
            cleanup_normalized_at=cleanup_normalized_at,
        )

    def submit_commit(
        self,
        *,
        created_at: int,
        content_by_file_id: Mapping[str, bytes],
        encrypted_blob_by_file_id: Optional[Mapping[str, bytes]] = None,
        commit_intent_id: Optional[str] = None,
        cleanup_normalized_at: Optional[int] = None,
    ) -> DesktopCommitSessionResult:
        prepared = self.prepare_commit(
            created_at=created_at,
            content_by_file_id=content_by_file_id,
            encrypted_blob_by_file_id=encrypted_blob_by_file_id,
            commit_intent_id=commit_intent_id,
        )
        resolved_cleanup_at = created_at if cleanup_normalized_at is None else cleanup_normalized_at

        try:
            network = self.workspace.runtime.session.submit_commit(
                prepared.submission,
                snapshot_table=prepared.snapshot_table,
            )
        except Exception:
            cleanup = self.cleanup_failed_commit(normalized_at=resolved_cleanup_at)
            raise

        if network.commit.status != "committed":
            return DesktopCommitSessionResult(
                prepared=prepared,
                network=network,
                cleanup=self.cleanup_failed_commit(normalized_at=resolved_cleanup_at),
            )

        response = network.commit.response
        if response is None:
            raise ValueError("committed submit_commit result must include response")

        current_tombstones = load_tombstone_ledger(self.workspace.paths.ledger_path)
        with closing(self.workspace._open_connection()) as connection:
            finalized = finalize_commit_submission_cleanup(
                connection,
                vault_root=self.workspace.vault_root,
                ledger_path=self.workspace.paths.ledger_path,
                manifest=prepared.submission.manifest,
                local_tombstones=current_tombstones,
                committed_revision=response.new_revision,
            )
        return DesktopCommitSessionResult(
            prepared=prepared,
            network=network,
            finalized=finalized,
        )

    def submit_workspace_commit(
        self,
        *,
        created_at: int,
        file_ids: Iterable[str],
        encrypted_blob_by_file_id: Optional[Mapping[str, bytes]] = None,
        commit_intent_id: Optional[str] = None,
        cleanup_normalized_at: Optional[int] = None,
    ) -> DesktopCommitSessionResult:
        requested_file_ids = list(file_ids)
        content_by_file_id = self.load_workspace_content(requested_file_ids)
        return self.submit_commit(
            created_at=created_at,
            content_by_file_id=content_by_file_id,
            encrypted_blob_by_file_id=encrypted_blob_by_file_id,
            commit_intent_id=commit_intent_id,
            cleanup_normalized_at=cleanup_normalized_at,
        )


def build_desktop_sync_service(
    config: DesktopSyncHttpConfig,
    vault_root: Path,
    *,
    db_path: Optional[Path] = None,
    api_opener: Optional[UrlopenLike] = None,
    blob_opener: Optional[UrlopenLike] = None,
    blob_crypto_provider: Optional[DesktopBlobCryptoProvider] = None,
    file_id_builder: Optional[Callable[[str], str]] = None,
) -> DesktopSyncService:
    from .change_detection import build_generated_file_id

    return DesktopSyncService(
        workspace=build_desktop_vault_workspace(
            config,
            vault_root,
            db_path=db_path,
            api_opener=api_opener,
            blob_opener=blob_opener,
        ),
        blob_crypto_provider=(
            build_placeholder_blob_crypto_provider()
            if blob_crypto_provider is None
            else blob_crypto_provider
        ),
        file_id_builder=build_generated_file_id if file_id_builder is None else file_id_builder,
    )
