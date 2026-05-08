from __future__ import annotations

import hashlib
import json
from contextlib import closing, suppress
from dataclasses import asdict, dataclass, replace
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
    SyncApplyJournalRecord,
    TombstoneRecord,
    VaultStateRecord,
    apply_manifest_summary_stale,
    build_commit_snapshot_table,
    clear_sync_apply_journal,
    cleanup_commit_staging_artifacts,
    cleanup_failed_commit_submission,
    finalize_commit_submission_cleanup,
    isolate_staging_orphans,
    load_sync_apply_journal,
    load_tombstone_ledger,
    load_vault_state,
    materialize_blob_staging_plan,
    materialize_content_snapshot_plan,
    prepare_commit_submission,
    recover_sync_apply_finalizing_state,
    upsert_vault_state,
    upsert_sync_apply_journal,
)
from vault_core.constants import STAGING_DIRNAME
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


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    try:
        temp_path.write_bytes(payload)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _compute_content_hash(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _build_pull_apply_ops_hash(plan: "DesktopPullRequiredBlobPlan") -> str:
    payload = {
        "vault_id": plan.vault_id,
        "revision": plan.revision,
        "files": [
            {
                "file_id": item.file_id,
                "path": item.path,
                "type": item.type,
                "blob_id": item.blob_id,
                "content_hash": item.content_hash,
            }
            for item in plan.files
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _build_pull_apply_plan_ops_hash(plan: "DesktopPullApplyPlan") -> str:
    payload = {
        "vault_id": plan.vault_id,
        "revision": plan.revision,
        "writes": [
            {
                "file_id": item.file_id,
                "target_path": item.target_path,
                "staging_path": item.staging_path,
                "type": item.type,
                "content_hash": item.content_hash,
            }
            for item in plan.writes
        ],
        "moves": [
            {
                "file_id": item.file_id,
                "source_path": item.source_path,
                "target_path": item.target_path,
                "type": item.type,
                "content_hash": item.content_hash,
            }
            for item in plan.moves
        ],
        "deletes": [
            {
                "file_id": item.file_id,
                "path": item.path,
                "reason": item.reason,
            }
            for item in plan.deletes
        ],
        "blocking_paths": plan.blocking_paths,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _build_pull_apply_staging_relative_path(file_id: str) -> str:
    if not file_id or "/" in file_id or "\\" in file_id or file_id in {".", ".."}:
        raise ValueError(f"file_id is not safe for staging: {file_id!r}")
    return f"{STAGING_DIRNAME}/{file_id}.staging"


def _resolve_pull_apply_staging_path(vault_root: Path, file_id: str) -> Path:
    return vault_root / Path(_build_pull_apply_staging_relative_path(file_id))


def _serialize_pull_apply_plan(plan: "DesktopPullApplyPlan") -> bytes:
    return json.dumps(asdict(plan), sort_keys=True, separators=(",", ":")).encode("utf-8")


def _deserialize_pull_apply_plan(payload: bytes) -> "DesktopPullApplyPlan":
    data = json.loads(payload.decode("utf-8"))
    return DesktopPullApplyPlan(
        vault_id=data["vault_id"],
        revision=data["revision"],
        writes=[DesktopPullApplyWriteFile(**item) for item in data.get("writes", [])],
        moves=[DesktopPullApplyMoveFile(**item) for item in data.get("moves", [])],
        deletes=[DesktopPullApplyDeleteFile(**item) for item in data.get("deletes", [])],
        blocking_paths=list(data.get("blocking_paths", [])),
        ops_hash=data["ops_hash"],
    )


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
class DesktopPullApplyStagingResult:
    journal: Optional[SyncApplyJournalRecord]
    written_staging_paths: dict[str, Path]


@dataclass(frozen=True)
class DesktopPullApplyWriteFile:
    file_id: str
    target_path: str
    staging_path: str
    type: str
    content_hash: str


@dataclass(frozen=True)
class DesktopPullApplyMoveFile:
    file_id: str
    source_path: str
    target_path: str
    type: str
    content_hash: str


@dataclass(frozen=True)
class DesktopPullApplyDeleteFile:
    file_id: str
    path: str
    reason: str


@dataclass(frozen=True)
class DesktopPullApplyPlan:
    vault_id: str
    revision: int
    writes: list[DesktopPullApplyWriteFile]
    moves: list[DesktopPullApplyMoveFile]
    deletes: list[DesktopPullApplyDeleteFile]
    blocking_paths: list[str]
    ops_hash: str


@dataclass(frozen=True)
class DesktopPullApplyPlanResult:
    pull: PullSyncSessionResult
    plan: DesktopPullApplyPlan


@dataclass(frozen=True)
class DesktopPullApplyExecutionResult:
    journal: Optional[SyncApplyJournalRecord]
    written_paths: dict[str, Path]
    moved_paths: dict[str, Path]
    deleted_paths: list[Path]


@dataclass(frozen=True)
class DesktopPullApplyFinalizeResult:
    state: VaultStateRecord
    removed_staging_paths: list[Path]
    removed_plan_path: Optional[Path] = None


@dataclass(frozen=True)
class DesktopPullApplyRecoveryResult:
    mode: str
    journal_phase: Optional[str]
    state: Optional[VaultStateRecord]
    removed_staging_paths: list[Path]
    isolated_staging_paths: Optional[list[Path]] = None
    removed_plan_path: Optional[Path] = None


@dataclass(frozen=True)
class DesktopPullApplySessionResult:
    pull: PullSyncSessionResult
    plan: DesktopPullApplyPlan
    staged: DesktopPullApplyStagingResult
    execution: DesktopPullApplyExecutionResult
    finalized: Optional[DesktopPullApplyFinalizeResult] = None


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
        return self._pull_and_ack_with_snapshot(rewritten_at=rewritten_at)[1]

    def pull_and_plan_apply(self, *, rewritten_at: int) -> DesktopPullApplyPlanResult:
        before_snapshot, pull = self._pull_and_ack_with_snapshot(rewritten_at=rewritten_at)
        return DesktopPullApplyPlanResult(
            pull=pull,
            plan=self._build_pull_apply_plan(before_snapshot, pull),
        )

    def pull_and_apply(self, *, rewritten_at: int) -> DesktopPullApplySessionResult:
        return self._pull_and_apply(rewritten_at=rewritten_at, reject_blocking_paths=False)

    def pull_and_apply_nonblocking(self, *, rewritten_at: int) -> DesktopPullApplySessionResult:
        return self._pull_and_apply(rewritten_at=rewritten_at, reject_blocking_paths=True)

    def _pull_and_apply(
        self,
        *,
        rewritten_at: int,
        reject_blocking_paths: bool,
    ) -> DesktopPullApplySessionResult:
        before_snapshot, pull = self._pull_and_ack_with_snapshot(rewritten_at=rewritten_at)
        plan = self._build_pull_apply_plan(before_snapshot, pull)
        if reject_blocking_paths and plan.blocking_paths:
            raise ValueError(
                "pull apply requires a later two-phase materialization boundary for blocking paths: "
                + ", ".join(plan.blocking_paths)
            )
        resolved = self.download_and_decrypt_pull_required_blobs(pull)
        staged = self.stage_pull_required_plaintext_for_apply(
            resolved,
            started_at=rewritten_at,
            apply_plan=plan,
        )
        execution = self.apply_staged_pull_plan(
            plan,
            staged,
            materialized_at=rewritten_at,
        )
        finalized = self.finalize_applied_pull_plan(
            plan,
            execution,
            staged,
            finalized_at=rewritten_at,
        )
        return DesktopPullApplySessionResult(
            pull=pull,
            plan=plan,
            staged=staged,
            execution=execution,
            finalized=finalized,
        )

    def _pull_and_ack_with_snapshot(
        self,
        *,
        rewritten_at: int,
    ) -> tuple[DesktopWorkspaceSnapshot, PullSyncSessionResult]:
        with closing(self.workspace._open_connection()) as connection:
            snapshot = self.workspace._load_snapshot_from_connection(connection)
            self._require_no_active_sync_apply_journal(connection, operation="pull")
            if snapshot.state.commit_in_progress:
                raise ValueError("pull cannot start while commit_in_progress is true")
            pull = self.workspace.runtime.session.pull_and_ack(
                connection,
                filemap_path=self.workspace.paths.filemap_path,
                ledger_path=self.workspace.paths.ledger_path,
                current_document=snapshot.document,
                current_state=snapshot.state,
                local_tombstones=snapshot.tombstones,
                rewritten_at=rewritten_at,
            )
        return snapshot, pull

    def resume_commit_recovery(self, *, normalized_at: int) -> CommitRecoverySessionResult:
        return self.workspace.resume_commit_recovery(normalized_at=normalized_at)

    def resume_pull_apply_recovery(self, *, normalized_at: int) -> DesktopPullApplyRecoveryResult:
        with closing(self.workspace._open_connection()) as connection:
            journal = load_sync_apply_journal(connection, self.vault_id)
            if journal is None:
                return DesktopPullApplyRecoveryResult(
                    mode="idle",
                    journal_phase=None,
                    state=None,
                    removed_staging_paths=[],
                    isolated_staging_paths=[],
                    removed_plan_path=None,
                )
            materialized_snapshot = None
            if journal.phase == "materializing":
                materialized_snapshot = self.workspace._load_snapshot_from_connection(connection)

        plan = self._load_pull_apply_plan_file()
        if plan is not None:
            self._validate_recovery_pull_apply_plan(plan, journal=journal)
            staged = DesktopPullApplyStagingResult(journal=journal, written_staging_paths={})
            execution = self.apply_staged_pull_plan(
                plan,
                staged,
                materialized_at=normalized_at,
            )
            finalized = self.finalize_applied_pull_plan(
                plan,
                execution,
                staged,
                finalized_at=normalized_at,
            )
            return DesktopPullApplyRecoveryResult(
                mode="replayed",
                journal_phase=journal.phase,
                state=None if finalized is None else finalized.state,
                removed_staging_paths=[] if finalized is None else finalized.removed_staging_paths,
                isolated_staging_paths=[],
                removed_plan_path=None if finalized is None else finalized.removed_plan_path,
            )
        if journal.phase in {"filemap_rewrite", "finalizing"}:
            with closing(self.workspace._open_connection()) as connection:
                finalizing_journal = (
                    journal
                    if journal.phase == "finalizing"
                    else replace(journal, phase="finalizing", updated_at=normalized_at)
                )
                if finalizing_journal != journal:
                    upsert_sync_apply_journal(connection, finalizing_journal)
                state = recover_sync_apply_finalizing_state(connection, self.vault_id)
            removed = self._cleanup_pull_apply_staging_artifacts()
            return DesktopPullApplyRecoveryResult(
                mode="finalized",
                journal_phase=finalizing_journal.phase,
                state=state,
                removed_staging_paths=removed,
                isolated_staging_paths=[],
                removed_plan_path=self._cleanup_pull_apply_plan_file(),
            )
        if journal.phase == "materializing":
            if materialized_snapshot is None or not self._workspace_matches_document(materialized_snapshot.document):
                return self._degrade_pull_apply_recovery(journal, normalized_at=normalized_at)
            with closing(self.workspace._open_connection()) as connection:
                upsert_sync_apply_journal(
                    connection,
                    replace(journal, phase="finalizing", updated_at=normalized_at),
                )
                state = recover_sync_apply_finalizing_state(connection, self.vault_id)
            removed = self._cleanup_pull_apply_staging_artifacts()
            return DesktopPullApplyRecoveryResult(
                mode="finalized",
                journal_phase="materializing",
                state=state,
                removed_staging_paths=removed,
                isolated_staging_paths=[],
                removed_plan_path=self._cleanup_pull_apply_plan_file(),
            )
        if journal.phase == "staging":
            return self._degrade_pull_apply_recovery(journal, normalized_at=normalized_at)
        raise ValueError(f"pull apply recovery is not supported for journal phase: {journal.phase}")

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

    def materialize_pull_required_plaintext(
        self,
        resolved: DesktopPullRequiredBlobResult,
        output_root: Path,
    ) -> dict[str, Path]:
        written_paths: dict[str, Path] = {}
        for item in resolved.plan.files:
            if item.file_id not in resolved.plaintext_by_file_id:
                raise KeyError(f"plaintext payload not found for file_id: {item.file_id}")
            output_path = _resolve_workspace_file_path(output_root, item.path)
            _write_bytes_atomic(output_path, resolved.plaintext_by_file_id[item.file_id])
            written_paths[item.file_id] = output_path
        return written_paths

    def stage_pull_required_plaintext_for_apply(
        self,
        resolved: DesktopPullRequiredBlobResult,
        *,
        started_at: int,
        apply_plan: Optional[DesktopPullApplyPlan] = None,
    ) -> DesktopPullApplyStagingResult:
        manifest = resolved.pull.pull.manifest
        if manifest is None:
            raise ValueError("pull manifest is required to stage required plaintext")
        if manifest.vault_id != resolved.plan.vault_id:
            raise ValueError("pull manifest vault_id does not match required blob plan")
        if manifest.revision != resolved.plan.revision:
            raise ValueError("pull manifest revision does not match required blob plan")
        if apply_plan is not None:
            if apply_plan.vault_id != resolved.plan.vault_id:
                raise ValueError("pull apply plan vault_id does not match required blob plan")
            if apply_plan.revision != resolved.plan.revision:
                raise ValueError("pull apply plan revision does not match required blob plan")

        staged_files: list[tuple[str, Path, bytes]] = []
        for item in resolved.plan.files:
            plaintext = resolved.plaintext_by_file_id.get(item.file_id)
            if plaintext is None:
                raise KeyError(f"plaintext payload not found for file_id: {item.file_id}")
            staged_files.append(
                (
                    item.file_id,
                    _resolve_pull_apply_staging_path(self.workspace.vault_root, item.file_id),
                    plaintext,
                )
            )
        should_create_journal = bool(staged_files)
        if apply_plan is not None and (apply_plan.writes or apply_plan.moves or apply_plan.deletes):
            should_create_journal = True
        if not should_create_journal:
            return DesktopPullApplyStagingResult(
                journal=None,
                written_staging_paths={},
            )

        journal = SyncApplyJournalRecord(
            vault_id=resolved.plan.vault_id,
            journal_id=str(uuid4()),
            target_revision=resolved.plan.revision,
            target_manifest_hash=manifest.summary_hash,
            phase="preparing",
            ops_hash=apply_plan.ops_hash if apply_plan is not None else _build_pull_apply_ops_hash(resolved.plan),
            created_at=started_at,
            updated_at=started_at,
        )
        written_staging_paths: dict[str, Path] = {}

        with closing(self.workspace._open_connection()) as connection:
            self._require_no_active_sync_apply_journal(connection, operation="stage pull apply")
            upsert_sync_apply_journal(connection, journal)
            try:
                if apply_plan is not None:
                    self._write_pull_apply_plan_file(apply_plan)
                for file_id, staging_path, plaintext in staged_files:
                    _write_bytes_atomic(staging_path, plaintext)
                    written_staging_paths[file_id] = staging_path
                journal = replace(journal, phase="staging", updated_at=started_at)
                upsert_sync_apply_journal(connection, journal)
            except Exception:
                for staging_path in written_staging_paths.values():
                    staging_path.unlink(missing_ok=True)
                with suppress(Exception):
                    clear_sync_apply_journal(connection, resolved.plan.vault_id)
                with suppress(Exception):
                    self._cleanup_pull_apply_plan_file()
                raise

        return DesktopPullApplyStagingResult(
            journal=journal,
            written_staging_paths=written_staging_paths,
        )

    def apply_staged_pull_plan(
        self,
        plan: DesktopPullApplyPlan,
        staged: DesktopPullApplyStagingResult,
        *,
        materialized_at: int,
    ) -> DesktopPullApplyExecutionResult:
        journal = staged.journal
        if journal is None:
            return DesktopPullApplyExecutionResult(
                journal=None,
                written_paths={},
                moved_paths={},
                deleted_paths=[],
            )

        written_paths: dict[str, Path] = {}
        moved_paths: dict[str, Path] = {}
        deleted_paths: list[Path] = []
        blocking_paths = set(plan.blocking_paths)

        with closing(self.workspace._open_connection()) as connection:
            current_journal = load_sync_apply_journal(connection, self.vault_id)
            if current_journal is None:
                raise KeyError(f"sync_apply_journal not found: {self.vault_id}")
            if current_journal.target_revision != plan.revision:
                raise ValueError("sync_apply_journal target_revision does not match pull apply plan")
            if current_journal.vault_id != plan.vault_id:
                raise ValueError("sync_apply_journal vault_id does not match pull apply plan")

            materializing_journal = replace(
                current_journal,
                phase="materializing",
                ops_hash=plan.ops_hash,
                updated_at=materialized_at,
            )
            upsert_sync_apply_journal(connection, materializing_journal)

            for item in plan.moves:
                if item.source_path not in blocking_paths and item.target_path not in blocking_paths:
                    continue
                source_path = _resolve_workspace_file_path(self.workspace.vault_root, item.source_path)
                move_staging_path = _resolve_pull_apply_staging_path(self.workspace.vault_root, item.file_id)
                if source_path.exists():
                    move_staging_path.parent.mkdir(parents=True, exist_ok=True)
                    source_path.replace(move_staging_path)
                    continue
                if move_staging_path.exists():
                    continue
                target_path = _resolve_workspace_file_path(self.workspace.vault_root, item.target_path)
                if target_path.exists():
                    payload = target_path.read_bytes()
                    actual_hash = _compute_content_hash(payload)
                    if actual_hash != item.content_hash:
                        raise ValueError(
                            f"materialized move target hash mismatch for file_id {item.file_id}: "
                            f"expected {item.content_hash}, got {actual_hash}"
                        )
                    continue
                raise FileNotFoundError(f"pull apply source path not found: {item.source_path}")

            for item in plan.deletes:
                if item.path not in blocking_paths:
                    continue
                delete_path = _resolve_workspace_file_path(self.workspace.vault_root, item.path)
                if not delete_path.exists():
                    continue
                delete_path.unlink(missing_ok=True)
                deleted_paths.append(delete_path)

            for item in plan.writes:
                staging_path = _resolve_workspace_file_path(self.workspace.vault_root, item.staging_path)
                if not staging_path.exists() or not staging_path.is_file():
                    raise FileNotFoundError(f"staged pull payload not found: {item.staging_path}")
                payload = staging_path.read_bytes()
                actual_hash = _compute_content_hash(payload)
                if actual_hash != item.content_hash:
                    raise ValueError(
                        f"staged pull payload hash mismatch for file_id {item.file_id}: "
                        f"expected {item.content_hash}, got {actual_hash}"
                    )
                output_path = _resolve_workspace_file_path(self.workspace.vault_root, item.target_path)
                _write_bytes_atomic(output_path, payload)
                written_paths[item.file_id] = output_path

            for item in plan.moves:
                if item.source_path in blocking_paths or item.target_path in blocking_paths:
                    source_path = _resolve_pull_apply_staging_path(self.workspace.vault_root, item.file_id)
                else:
                    source_path = _resolve_workspace_file_path(self.workspace.vault_root, item.source_path)
                target_path = _resolve_workspace_file_path(self.workspace.vault_root, item.target_path)
                if source_path.exists():
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    source_path.replace(target_path)
                    moved_paths[item.file_id] = target_path
                    continue
                if target_path.exists():
                    payload = target_path.read_bytes()
                    actual_hash = _compute_content_hash(payload)
                    if actual_hash != item.content_hash:
                        raise ValueError(
                            f"materialized move target hash mismatch for file_id {item.file_id}: "
                            f"expected {item.content_hash}, got {actual_hash}"
                        )
                    moved_paths[item.file_id] = target_path
                    continue
                raise FileNotFoundError(f"pull apply source path not found: {item.source_path}")

            for item in plan.deletes:
                if item.path in blocking_paths:
                    continue
                delete_path = _resolve_workspace_file_path(self.workspace.vault_root, item.path)
                if not delete_path.exists():
                    continue
                delete_path.unlink(missing_ok=True)
                deleted_paths.append(delete_path)

        return DesktopPullApplyExecutionResult(
            journal=materializing_journal,
            written_paths=written_paths,
            moved_paths=moved_paths,
            deleted_paths=deleted_paths,
        )

    def finalize_applied_pull_plan(
        self,
        plan: DesktopPullApplyPlan,
        execution: DesktopPullApplyExecutionResult,
        staged: DesktopPullApplyStagingResult,
        *,
        finalized_at: int,
    ) -> Optional[DesktopPullApplyFinalizeResult]:
        if execution.journal is None:
            return None

        with closing(self.workspace._open_connection()) as connection:
            current_journal = load_sync_apply_journal(connection, self.vault_id)
            if current_journal is None:
                raise KeyError(f"sync_apply_journal not found: {self.vault_id}")
            if current_journal.vault_id != plan.vault_id:
                raise ValueError("sync_apply_journal vault_id does not match pull apply plan")
            if current_journal.target_revision != plan.revision:
                raise ValueError("sync_apply_journal target_revision does not match pull apply plan")
            if current_journal.phase != "materializing":
                raise ValueError("sync_apply_journal must be in materializing phase before finalization")

            filemap_rewrite_journal = replace(current_journal, phase="filemap_rewrite", updated_at=finalized_at)
            upsert_sync_apply_journal(connection, filemap_rewrite_journal)
            finalizing_journal = replace(filemap_rewrite_journal, phase="finalizing", updated_at=finalized_at)
            upsert_sync_apply_journal(connection, finalizing_journal)
            state = recover_sync_apply_finalizing_state(connection, self.vault_id)

        removed_staging_paths = self._cleanup_pull_apply_staging_artifacts()
        removed_plan_path = self._cleanup_pull_apply_plan_file()

        return DesktopPullApplyFinalizeResult(
            state=state,
            removed_staging_paths=removed_staging_paths,
            removed_plan_path=removed_plan_path,
        )

    def _build_pull_apply_plan(
        self,
        before_snapshot: DesktopWorkspaceSnapshot,
        pull: PullSyncSessionResult,
    ) -> DesktopPullApplyPlan:
        manifest = pull.pull.manifest
        if manifest is None:
            raise ValueError("pull manifest is required to build pull apply plan")

        before_active_by_file_id = {
            record.file_id: record
            for record in before_snapshot.document.files
            if record.status == "active"
        }
        target_file_ids = {entry.file_id for entry in manifest.files}

        writes: list[DesktopPullApplyWriteFile] = []
        moves: list[DesktopPullApplyMoveFile] = []
        deletes: list[DesktopPullApplyDeleteFile] = []

        for entry in manifest.sorted_files():
            before_record = before_active_by_file_id.get(entry.file_id)
            if before_record is None or before_record.content_hash != entry.content_hash:
                writes.append(
                    DesktopPullApplyWriteFile(
                        file_id=entry.file_id,
                        target_path=entry.path,
                        staging_path=_build_pull_apply_staging_relative_path(entry.file_id),
                        type=entry.type,
                        content_hash=entry.content_hash,
                    )
                )
                if before_record is not None and before_record.path != entry.path:
                    deletes.append(
                        DesktopPullApplyDeleteFile(
                            file_id=entry.file_id,
                            path=before_record.path,
                            reason="replaced_old_path",
                        )
                    )
                continue

            if before_record.path != entry.path:
                moves.append(
                    DesktopPullApplyMoveFile(
                        file_id=entry.file_id,
                        source_path=before_record.path,
                        target_path=entry.path,
                        type=entry.type,
                        content_hash=entry.content_hash,
                    )
                )

        for record in before_active_by_file_id.values():
            if record.file_id in target_file_ids:
                continue
            deletes.append(
                DesktopPullApplyDeleteFile(
                    file_id=record.file_id,
                    path=record.path,
                    reason="deleted",
                )
            )

        source_paths = {item.source_path for item in moves}
        source_paths.update(item.path for item in deletes)
        target_paths = {item.target_path for item in writes}
        target_paths.update(item.target_path for item in moves)
        blocking_paths = sorted(target_paths & source_paths)

        plan = DesktopPullApplyPlan(
            vault_id=manifest.vault_id,
            revision=manifest.revision,
            writes=writes,
            moves=moves,
            deletes=deletes,
            blocking_paths=blocking_paths,
            ops_hash="",
        )
        return replace(plan, ops_hash=_build_pull_apply_plan_ops_hash(plan))

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
            self._require_no_active_sync_apply_journal(connection, operation="commit")
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

    def _require_no_active_sync_apply_journal(self, connection, *, operation: str) -> None:
        journal = load_sync_apply_journal(connection, self.vault_id)
        if journal is None:
            return
        raise ValueError(
            f"{operation} is blocked while sync_apply_journal is active: "
            f"{journal.journal_id} ({journal.phase})"
        )

    def _write_pull_apply_plan_file(self, plan: DesktopPullApplyPlan) -> None:
        _write_bytes_atomic(
            self.workspace.paths.sync_apply_plan_path,
            _serialize_pull_apply_plan(plan),
        )

    def _load_pull_apply_plan_file(self) -> Optional[DesktopPullApplyPlan]:
        path = self.workspace.paths.sync_apply_plan_path
        if not path.exists() or not path.is_file():
            return None
        return _deserialize_pull_apply_plan(path.read_bytes())

    def _cleanup_pull_apply_plan_file(self) -> Optional[Path]:
        path = self.workspace.paths.sync_apply_plan_path
        if not path.exists():
            return None
        path.unlink(missing_ok=True)
        return path

    def _validate_recovery_pull_apply_plan(
        self,
        plan: DesktopPullApplyPlan,
        *,
        journal: SyncApplyJournalRecord,
    ) -> None:
        if plan.vault_id != journal.vault_id:
            raise ValueError("recovery pull apply plan vault_id does not match sync_apply_journal")
        if plan.revision != journal.target_revision:
            raise ValueError("recovery pull apply plan revision does not match sync_apply_journal")
        if plan.ops_hash != journal.ops_hash:
            raise ValueError("recovery pull apply plan ops_hash does not match sync_apply_journal")

    def _degrade_pull_apply_recovery(
        self,
        journal: SyncApplyJournalRecord,
        *,
        normalized_at: int,
    ) -> DesktopPullApplyRecoveryResult:
        with closing(self.workspace._open_connection()) as connection:
            state = load_vault_state(connection, self.vault_id)
            if state is None:
                raise KeyError(f"vault_state not found: {self.vault_id}")
            degraded_state = apply_manifest_summary_stale(state)
            upsert_vault_state(connection, degraded_state)
            clear_sync_apply_journal(connection, self.vault_id)
        isolated_paths = isolate_staging_orphans(self.workspace.vault_root)
        return DesktopPullApplyRecoveryResult(
            mode="degraded",
            journal_phase=journal.phase,
            state=degraded_state,
            removed_staging_paths=[],
            isolated_staging_paths=isolated_paths,
            removed_plan_path=self._cleanup_pull_apply_plan_file(),
        )

    def _workspace_matches_document(self, document) -> bool:
        for record in document.files:
            if record.status != "active":
                continue
            content_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            if not content_path.exists() or not content_path.is_file():
                return False
            if _compute_content_hash(content_path.read_bytes()) != record.content_hash:
                return False
        return True

    def _cleanup_pull_apply_staging_artifacts(self) -> list[Path]:
        staging_root = self.workspace.vault_root / STAGING_DIRNAME
        if not staging_root.exists():
            return []
        removed_paths: list[Path] = []
        for staging_path in sorted(
            (
                path
                for path in staging_root.rglob("*")
                if path.is_file() and path.name.endswith(".staging") and not path.name.endswith(".blob.staging")
            ),
            key=lambda item: str(item.relative_to(staging_root)),
        ):
            staging_path.unlink(missing_ok=True)
            removed_paths.append(staging_path)
        return removed_paths

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
