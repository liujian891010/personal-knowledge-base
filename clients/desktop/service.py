from __future__ import annotations

import hashlib
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
class DesktopPullRequiredBlobFile:
    file_id: str
    path: str
    type: str
    blob_id: str
    content_hash: str


@dataclass(frozen=True)
class DesktopPullRequiredBlobPlan:
    vault_id: str
    revision: int
    files: list[DesktopPullRequiredBlobFile]
    blob_ids: list[str]


@dataclass(frozen=True)
class DesktopPullRequiredBlobResult:
    pull: PullSyncSessionResult
    plan: DesktopPullRequiredBlobPlan
    download: Optional[BlobDownloadSessionResult]
    plaintext_by_file_id: dict[str, bytes]


@dataclass(frozen=True)
class DesktopSyncService:
    workspace: DesktopVaultWorkspace
    blob_crypto_provider: DesktopBlobCryptoProvider
    file_id_builder: Callable[[str], str]
    detected_submit_plan_hook: Optional[Callable[[DesktopTrackedChangeCommitPlan], None]] = None

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

    def build_pull_required_blob_plan(
        self,
        pull: PullSyncSessionResult,
    ) -> DesktopPullRequiredBlobPlan:
        applied = pull.pull.reconcile.applied
        if applied is None:
            return DesktopPullRequiredBlobPlan(
                vault_id=self.vault_id,
                revision=pull.pull.head.head_revision,
                files=[],
                blob_ids=[],
            )
        manifest = pull.pull.manifest
        if manifest is None:
            if applied.required_blob_ids:
                raise ValueError("pull result is missing manifest for required blob planning")
            return DesktopPullRequiredBlobPlan(
                vault_id=self.vault_id,
                revision=pull.pull.head.head_revision,
                files=[],
                blob_ids=[],
            )

        required_blob_id_set = set(applied.required_blob_ids)
        files: list[DesktopPullRequiredBlobFile] = []
        blob_ids: list[str] = []
        seen_blob_ids: set[str] = set()
        content_hash_by_blob_id: dict[str, str] = {}
        manifest_blob_ids: set[str] = set()

        for entry in manifest.sorted_files():
            manifest_blob_ids.add(entry.blob_id)
            if entry.blob_id not in required_blob_id_set:
                continue
            previous_content_hash = content_hash_by_blob_id.get(entry.blob_id)
            if previous_content_hash is not None and previous_content_hash != entry.content_hash:
                raise ValueError(
                    f"required blob_id maps to multiple content hashes: {entry.blob_id}"
                )
            content_hash_by_blob_id[entry.blob_id] = entry.content_hash
            files.append(
                DesktopPullRequiredBlobFile(
                    file_id=entry.file_id,
                    path=entry.path,
                    type=entry.type,
                    blob_id=entry.blob_id,
                    content_hash=entry.content_hash,
                )
            )
            if entry.blob_id not in seen_blob_ids:
                seen_blob_ids.add(entry.blob_id)
                blob_ids.append(entry.blob_id)

        missing_blob_ids = sorted(required_blob_id_set - manifest_blob_ids)
        if missing_blob_ids:
            raise ValueError(
                "required blob_ids are missing from pull manifest: " + ", ".join(missing_blob_ids)
            )

        return DesktopPullRequiredBlobPlan(
            vault_id=manifest.vault_id,
            revision=manifest.revision,
            files=files,
            blob_ids=blob_ids,
        )

    def download_and_decrypt_pull_required_blobs(
        self,
        pull: PullSyncSessionResult,
    ) -> DesktopPullRequiredBlobResult:
        plan = self.build_pull_required_blob_plan(pull)
        download = None
        downloaded_blobs: dict[str, bytes] = {}
        if plan.blob_ids:
            download = self.download_blobs(plan.blob_ids)
            downloaded_blobs = dict(download.downloaded_blobs)

        plaintext_by_blob_id: dict[str, bytes] = {}
        plaintext_by_file_id: dict[str, bytes] = {}
        for item in plan.files:
            plaintext = plaintext_by_blob_id.get(item.blob_id)
            if plaintext is None:
                if item.blob_id not in downloaded_blobs:
                    raise KeyError(f"downloaded blob payload not found: {item.blob_id}")
                plaintext = self.blob_crypto_provider.decrypt_payload(
                    downloaded_blobs[item.blob_id],
                    content_hash=item.content_hash,
                )
                plaintext_by_blob_id[item.blob_id] = plaintext
            plaintext_by_file_id[item.file_id] = plaintext

        return DesktopPullRequiredBlobResult(
            pull=pull,
            plan=plan,
            download=download,
            plaintext_by_file_id=plaintext_by_file_id,
        )

    def detect_local_changes(self) -> DesktopWorkspaceChangeSet:
        return self.workspace.detect_local_changes()

    def load_worker_state(self) -> DesktopSyncWorkerStateRecord:
        return self.workspace.load_worker_state()

    def load_worker_health(self) -> DesktopSyncWorkerHealth:
        return self.workspace.load_worker_health()

    def load_workspace_content(self, file_ids: Iterable[str]) -> dict[str, bytes]:
        snapshot = self.load_snapshot()
        file_by_id = {record.file_id: record for record in snapshot.document.files}
        selected_records = []

        for file_id in file_ids:
            record = file_by_id.get(file_id)
            if record is None:
                raise KeyError(f"file_id not found in workspace filemap: {file_id}")
            if record.status != "active":
                raise ValueError(f"workspace file is not active: {file_id}")
            selected_records.append(record)

        return self._load_workspace_content_for_records(selected_records)

    def _build_expected_source_version_token(self, record) -> Optional[str]:
        meta = record.meta or {}
        expected_token = meta.get("source_version_token")
        if isinstance(expected_token, str) and expected_token:
            return expected_token
        if record.content_hash is None:
            return None
        expected_mtime = meta.get("mtime")
        expected_size = meta.get("size")
        if (
            isinstance(expected_mtime, int)
            and expected_mtime >= 0
            and isinstance(expected_size, int)
            and expected_size >= 0
        ):
            return f"mtime:{expected_mtime}:size:{expected_size}:hash:{record.content_hash}"
        return f"updated_at:{record.updated_at}:hash:{record.content_hash}"

    def _load_workspace_content_for_records(self, records: Iterable[object]) -> dict[str, bytes]:
        drifted_file_ids: list[str] = []
        content_by_file_id: dict[str, bytes] = {}

        for record in records:
            content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            if not content_path.exists() or not content_path.is_file():
                drifted_file_ids.append(record.file_id)
                continue
            payload = content_path.read_bytes()
            content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
            mtime_ms = content_path.stat().st_mtime_ns // 1_000_000
            size_bytes = len(payload)
            expected_token = self._build_expected_source_version_token(record)
            current_token = f"mtime:{mtime_ms}:size:{size_bytes}:hash:{content_hash}"
            if (
                expected_token is None
                or content_hash != record.content_hash
                or current_token != expected_token
            ):
                drifted_file_ids.append(record.file_id)
                continue
            content_by_file_id[record.file_id] = payload

        if drifted_file_ids:
            raise ValueError(
                "workspace snapshot drift detected: " + ", ".join(sorted(drifted_file_ids))
            )
        return content_by_file_id

    def load_workspace_content_for_document(
        self,
        document: FileMapDocument,
    ) -> dict[str, bytes]:
        return self._load_workspace_content_for_records(
            record for record in document.files if record.status == "active"
        )

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

    def _submit_prepared_commit(
        self,
        prepared: DesktopPreparedCommit,
        *,
        resolved_cleanup_at: int,
    ) -> DesktopCommitSessionResult:
        try:
            network = self.workspace.runtime.session.submit_commit(
                prepared.submission,
                snapshot_table=prepared.snapshot_table,
            )
        except Exception:
            self.cleanup_failed_commit(normalized_at=resolved_cleanup_at)
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
        if self.detected_submit_plan_hook is not None:
            self.detected_submit_plan_hook(plan)
        refreshed_content_by_file_id = self.load_workspace_content_for_document(plan.document)
        plan = replace(
            plan,
            content_by_file_id=refreshed_content_by_file_id,
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
        if self.detected_submit_plan_hook is not None:
            self.detected_submit_plan_hook(plan)
        refreshed_content_by_file_id = self.load_workspace_content_for_document(plan.document)
        plan = replace(
            plan,
            content_by_file_id=refreshed_content_by_file_id,
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
        snapshot = self.load_snapshot()
        prepared = self._prepare_commit_with_snapshot(
            snapshot,
            created_at=created_at,
            content_by_file_id=content_by_file_id,
            encrypted_blob_by_file_id=encrypted_blob_by_file_id,
            commit_intent_id=commit_intent_id,
        )
        resolved_cleanup_at = created_at if cleanup_normalized_at is None else cleanup_normalized_at
        return self._submit_prepared_commit(
            prepared,
            resolved_cleanup_at=resolved_cleanup_at,
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
        snapshot = self.load_snapshot()
        requested_file_ids = list(file_ids)
        file_by_id = {record.file_id: record for record in snapshot.document.files}
        selected_records = []
        for file_id in requested_file_ids:
            record = file_by_id.get(file_id)
            if record is None:
                raise KeyError(f"file_id not found in workspace filemap: {file_id}")
            if record.status != "active":
                raise ValueError(f"workspace file is not active: {file_id}")
            selected_records.append(record)
        content_by_file_id = self._load_workspace_content_for_records(selected_records)
        prepared = self._prepare_commit_with_snapshot(
            snapshot,
            created_at=created_at,
            content_by_file_id=content_by_file_id,
            encrypted_blob_by_file_id=encrypted_blob_by_file_id,
            commit_intent_id=commit_intent_id,
        )
        resolved_cleanup_at = created_at if cleanup_normalized_at is None else cleanup_normalized_at
        return self._submit_prepared_commit(
            prepared,
            resolved_cleanup_at=resolved_cleanup_at,
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
