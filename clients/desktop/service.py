from __future__ import annotations

import hashlib
import json
from contextlib import closing, suppress
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Mapping, Optional
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from vault_core import (
    allocate_conflict_copy_path,
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
    FileMapDocument,
    isolate_staging_orphans,
    load_commit_intent_journal,
    load_filemap,
    load_sync_apply_journal,
    load_tombstone_ledger,
    load_vault_state,
    materialize_blob_staging_plan,
    move_staging_orphan,
    materialize_content_snapshot_plan,
    prepare_commit_submission,
    register_conflict_copy,
    remove_conflict_copy,
    recover_sync_apply_finalizing_state,
    upsert_vault_state,
    upsert_sync_apply_journal,
    write_filemap_atomic,
)
from vault_core.constants import (
    CONFLICT_ORPHANS_DIRNAME,
    FILEMAP_FILENAME,
    NOTEAPP_DIRNAME,
    STAGING_DIRNAME,
    STAGING_ORPHANS_DIRNAME,
    TOMBSTONE_LEDGER_FILENAME,
    VAULTINFO_FILENAME,
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


def _append_jsonl_record(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _load_jsonl_records(path: Path) -> list[dict[str, object]]:
    if not path.exists() or not path.is_file():
        return []
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


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
                "previous_path": item.previous_path,
                "expected_previous_content_hash": item.expected_previous_content_hash,
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
                "expected_content_hash": item.expected_content_hash,
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


def _relative_vault_path(vault_root: Path, path: Path) -> str:
    return path.relative_to(vault_root).as_posix()


def _has_conflict_orphan_files(vault_root: Path) -> bool:
    orphan_root = vault_root / CONFLICT_ORPHANS_DIRNAME
    if not orphan_root.exists():
        return False
    return any(path.is_file() for path in orphan_root.rglob("*"))


def _has_conflict_copy_records(document) -> bool:
    return any(record.status == "conflict_copy" for record in document.files)


def _resolve_conflict_orphan_path(vault_root: Path, relative_path: str) -> Path:
    orphan_path = _resolve_workspace_file_path(vault_root, relative_path)
    orphan_root = (vault_root / CONFLICT_ORPHANS_DIRNAME).resolve()
    try:
        orphan_path.resolve().relative_to(orphan_root)
    except ValueError as exc:
        raise ValueError(f"conflict orphan path is not inside {CONFLICT_ORPHANS_DIRNAME}: {relative_path}") from exc
    return orphan_path


_MIGRATION_EXPORT_EXCLUDED_FILES = {
    f"{NOTEAPP_DIRNAME}/filemap.json.tmp",
    f"{NOTEAPP_DIRNAME}/state.sqlite3",
    f"{NOTEAPP_DIRNAME}/sync-apply-plan.json",
    f"{NOTEAPP_DIRNAME}/sync-worker-state.json",
    ".ai/log.md",
}
_MIGRATION_EXPORT_EXCLUDED_PREFIXES = (
    f"{NOTEAPP_DIRNAME}/drafts/",
    f"{STAGING_DIRNAME}/",
    f"{STAGING_ORPHANS_DIRNAME}/",
)
_MIGRATION_REQUIRED_FILES = {
    VAULTINFO_FILENAME,
    f"{NOTEAPP_DIRNAME}/{FILEMAP_FILENAME}",
    f"{NOTEAPP_DIRNAME}/{TOMBSTONE_LEDGER_FILENAME}",
}


def _normalize_migration_relative_path(relative_path: str) -> str:
    path = PurePosixPath(relative_path)
    if not relative_path or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"migration package path is not safe: {relative_path!r}")
    return path.as_posix()


def _should_skip_migration_export_path(relative_path: str, *, include_ai_raw: bool) -> bool:
    normalized = _normalize_migration_relative_path(relative_path)
    if normalized in _MIGRATION_EXPORT_EXCLUDED_FILES:
        return True
    if any(normalized.startswith(prefix) for prefix in _MIGRATION_EXPORT_EXCLUDED_PREFIXES):
        return True
    if normalized.startswith(".ai/raw/") and not include_ai_raw:
        return True
    return False


def _is_forbidden_migration_import_path(relative_path: str) -> bool:
    normalized = _normalize_migration_relative_path(relative_path)
    if normalized in _MIGRATION_EXPORT_EXCLUDED_FILES:
        return True
    if any(normalized.startswith(prefix) for prefix in _MIGRATION_EXPORT_EXCLUDED_PREFIXES):
        return True
    return False


def _list_migration_export_files(
    vault_root: Path,
    *,
    include_ai_raw: bool,
    package_path: Path,
) -> list[tuple[str, Path]]:
    package_target = package_path.resolve()
    export_files: list[tuple[str, Path]] = []
    for path in sorted(
        (item for item in vault_root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(vault_root).as_posix(),
    ):
        if path.resolve() == package_target:
            continue
        relative_path = path.relative_to(vault_root).as_posix()
        if _should_skip_migration_export_path(relative_path, include_ai_raw=include_ai_raw):
            continue
        export_files.append((relative_path, path))
    return export_files


def _read_migration_package(archive: ZipFile) -> tuple[FileMapDocument, dict[str, str]]:
    archive_entries: dict[str, str] = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        normalized = _normalize_migration_relative_path(info.filename)
        if _is_forbidden_migration_import_path(normalized):
            raise ValueError(f"migration package contains unsupported runtime entry: {normalized}")
        existing = archive_entries.get(normalized)
        if existing is not None and existing != info.filename:
            raise ValueError(f"migration package contains duplicate entry: {normalized}")
        archive_entries[normalized] = info.filename

    missing_required = sorted(_MIGRATION_REQUIRED_FILES - set(archive_entries))
    if missing_required:
        raise ValueError("migration package is missing required files: " + ", ".join(missing_required))

    filemap_payload = archive.read(archive_entries[f"{NOTEAPP_DIRNAME}/{FILEMAP_FILENAME}"])
    return FileMapDocument.from_dict(json.loads(filemap_payload.decode("utf-8"))), archive_entries


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
    previous_path: Optional[str] = None
    expected_previous_content_hash: Optional[str] = None


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
    expected_content_hash: Optional[str] = None


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
    requires_full_pull: bool
    journal_phase: Optional[str]
    state: Optional[VaultStateRecord]
    removed_staging_paths: list[Path]
    isolated_staging_paths: Optional[list[Path]] = None
    removed_plan_path: Optional[Path] = None


@dataclass(frozen=True)
class DesktopConflictResolutionResult:
    state: VaultStateRecord
    removed_conflict_paths: dict[str, Path]
    removed_orphan_paths: list[Path]
    skipped_conflict_file_ids: list[str]
    skipped_orphan_paths: list[str]


@dataclass(frozen=True)
class DesktopConflictArtifact:
    kind: str
    path: str
    exists_on_disk: bool
    file_id: Optional[str] = None
    conflict_source_file_id: Optional[str] = None
    content_hash: Optional[str] = None


@dataclass(frozen=True)
class DesktopConflictStatus:
    state: VaultStateRecord
    actual_has_unresolved_conflicts: bool
    conflict_copies: list[DesktopConflictArtifact]
    conflict_orphans: list[DesktopConflictArtifact]


@dataclass(frozen=True)
class DesktopVaultExportResult:
    package_path: Path
    vault_id: str
    exported_paths: list[str]
    included_ai_raw: bool
    included_conflict_orphans: bool


@dataclass(frozen=True)
class DesktopVaultPackageInspection:
    package_path: Path
    vault_id: str
    package_entries: list[str]
    includes_ai_raw: bool
    includes_conflict_orphans: bool
    has_unresolved_conflicts: bool


@dataclass(frozen=True)
class DesktopVaultImportResult:
    package_path: Path
    vault_id: str
    imported_paths: list[str]
    restored_ai_raw: bool
    restored_conflict_orphans: bool
    state: VaultStateRecord


@dataclass(frozen=True)
class DesktopCommitGateStatus:
    can_submit_commit: bool
    blocking_reasons: list[str]
    requires_full_pull: bool
    has_active_commit_journal: bool
    has_active_sync_apply_journal: bool


@dataclass(frozen=True)
class DesktopVaultSummary:
    state: VaultStateRecord
    changes: DesktopWorkspaceChangeSet
    conflicts: DesktopConflictStatus
    worker_health: Optional[DesktopSyncWorkerHealth]
    commit_gate: DesktopCommitGateStatus


@dataclass(frozen=True)
class DesktopSyncPanelAction:
    action_id: str
    label: str
    enabled: bool
    emphasis: str
    command: str
    argv: list[str]
    reason: Optional[str] = None
    requires_confirmation: bool = False


@dataclass(frozen=True)
class DesktopSyncPanelModel:
    level: str
    headline: str
    detail: str
    conflict_badge_count: int
    change_badge_count: int
    primary_action: DesktopSyncPanelAction
    secondary_actions: list[DesktopSyncPanelAction]
    summary: DesktopVaultSummary


@dataclass(frozen=True)
class DesktopSyncCenterCard:
    card_id: str
    kind: str
    level: str
    title: str
    body: str
    badge_count: int
    actions: list[DesktopSyncPanelAction]


@dataclass(frozen=True)
class DesktopSyncCenterModel:
    cards: list[DesktopSyncCenterCard]
    panel: DesktopSyncPanelModel
    summary: DesktopVaultSummary
    recent_activity: DesktopSyncActivityFeed


@dataclass(frozen=True)
class DesktopSyncActionExecutionResult:
    action: DesktopSyncPanelAction
    source: str
    status: str
    payload: object | None
    message: Optional[str] = None


@dataclass(frozen=True)
class DesktopSyncActivityRecord:
    activity_id: str
    occurred_at_ms: int
    level: str
    action_id: str
    command: str
    status: str
    source: str
    message: Optional[str] = None


@dataclass(frozen=True)
class DesktopSyncActivityFeed:
    records: list[DesktopSyncActivityRecord]
    total_count: int


@dataclass(frozen=True)
class DesktopPullApplySessionResult:
    pull: PullSyncSessionResult
    plan: DesktopPullApplyPlan
    staged: DesktopPullApplyStagingResult
    execution: DesktopPullApplyExecutionResult
    finalized: Optional[DesktopPullApplyFinalizeResult] = None


def inspect_vault_package(package_path: Path) -> DesktopVaultPackageInspection:
    resolved_package_path = package_path.resolve()
    if not resolved_package_path.exists() or not resolved_package_path.is_file():
        raise FileNotFoundError(f"migration package not found: {resolved_package_path}")
    with ZipFile(resolved_package_path, "r") as archive:
        package_document, archive_entries = _read_migration_package(archive)
    package_entries = sorted(archive_entries)
    return DesktopVaultPackageInspection(
        package_path=resolved_package_path,
        vault_id=package_document.vault_id,
        package_entries=package_entries,
        includes_ai_raw=any(path.startswith(".ai/raw/") for path in package_entries),
        includes_conflict_orphans=any(
            path.startswith(f"{CONFLICT_ORPHANS_DIRNAME}/") for path in package_entries
        ),
        has_unresolved_conflicts=(
            _has_conflict_copy_records(package_document)
            or any(path.startswith(f"{CONFLICT_ORPHANS_DIRNAME}/") for path in package_entries)
        ),
    )


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
        resolved = self.download_and_decrypt_pull_required_blobs(
            pull,
            apply_plan=plan,
        )
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
                isolated_paths = self._isolate_unjournaled_pull_apply_staging()
                removed_plan_path = self._cleanup_pull_apply_plan_file()
                if isolated_paths or removed_plan_path is not None:
                    with closing(self.workspace._open_connection()) as refresh_connection:
                        state = load_vault_state(refresh_connection, self.vault_id)
                        if state is None:
                            raise KeyError(f"vault_state not found: {self.vault_id}")
                        stale_state = apply_manifest_summary_stale(state)
                        upsert_vault_state(refresh_connection, stale_state)
                    return DesktopPullApplyRecoveryResult(
                        mode="orphaned",
                        requires_full_pull=True,
                        journal_phase=None,
                        state=stale_state,
                        removed_staging_paths=[],
                        isolated_staging_paths=isolated_paths,
                        removed_plan_path=removed_plan_path,
                    )
                return DesktopPullApplyRecoveryResult(
                    mode="idle",
                    requires_full_pull=False,
                    journal_phase=None,
                    state=None,
                    removed_staging_paths=[],
                    isolated_staging_paths=[],
                    removed_plan_path=None,
                )
            materialized_snapshot = None
            if journal.phase == "materializing":
                materialized_snapshot = self.workspace._load_snapshot_from_connection(connection)

        if journal.phase in {"staging", "materializing"}:
            try:
                plan = self._load_pull_apply_plan_file()
            except (KeyError, TypeError, UnicodeDecodeError, ValueError):
                if (
                    journal.phase == "materializing"
                    and self._materializing_workspace_matches_document(materialized_snapshot)
                ):
                    return self._finalize_materializing_pull_apply_recovery(
                        journal,
                        normalized_at=normalized_at,
                    )
                return self._degrade_pull_apply_recovery(journal, normalized_at=normalized_at)
            if plan is not None:
                try:
                    self._validate_recovery_pull_apply_plan(plan, journal=journal)
                except ValueError:
                    if (
                        journal.phase == "materializing"
                        and self._materializing_workspace_matches_document(materialized_snapshot)
                    ):
                        return self._finalize_materializing_pull_apply_recovery(
                            journal,
                            normalized_at=normalized_at,
                        )
                    return self._degrade_pull_apply_recovery(journal, normalized_at=normalized_at)
                staged = DesktopPullApplyStagingResult(journal=journal, written_staging_paths={})
                try:
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
                except (FileNotFoundError, KeyError, ValueError):
                    if (
                        journal.phase == "materializing"
                        and self._materializing_workspace_matches_document(materialized_snapshot)
                    ):
                        return self._finalize_materializing_pull_apply_recovery(
                            journal,
                            normalized_at=normalized_at,
                        )
                    return self._degrade_pull_apply_recovery(journal, normalized_at=normalized_at)
                return DesktopPullApplyRecoveryResult(
                    mode="replayed",
                    requires_full_pull=False if finalized is None else finalized.state.last_manifest_summary_status != "valid",
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
                requires_full_pull=state.last_manifest_summary_status != "valid",
                journal_phase=finalizing_journal.phase,
                state=state,
                removed_staging_paths=removed,
                isolated_staging_paths=[],
                removed_plan_path=self._cleanup_pull_apply_plan_file(),
            )
        if journal.phase == "preparing":
            return self._degrade_pull_apply_recovery(journal, normalized_at=normalized_at)
        if journal.phase == "materializing":
            if not self._materializing_workspace_matches_document(materialized_snapshot):
                return self._degrade_pull_apply_recovery(journal, normalized_at=normalized_at)
            return self._finalize_materializing_pull_apply_recovery(
                journal,
                normalized_at=normalized_at,
            )
        if journal.phase == "staging":
            return self._degrade_pull_apply_recovery(journal, normalized_at=normalized_at)
        raise ValueError(f"pull apply recovery is not supported for journal phase: {journal.phase}")

    def download_blobs(self, blob_ids: Iterable[str]) -> BlobDownloadSessionResult:
        return self.workspace.download_blobs(blob_ids)

    def build_pull_required_blob_plan(
        self,
        pull: PullSyncSessionResult,
        *,
        apply_plan: Optional[DesktopPullApplyPlan] = None,
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
        manifest_entry_by_file_id = {
            entry.file_id: entry
            for entry in manifest.sorted_files()
        }
        if apply_plan is not None:
            for move in apply_plan.moves:
                entry = manifest_entry_by_file_id.get(move.file_id)
                if entry is None:
                    raise ValueError(f"pull manifest is missing move target file_id: {move.file_id}")
                source_path = _resolve_workspace_file_path(self.workspace.vault_root, move.source_path)
                if not source_path.exists() or not source_path.is_file():
                    required_blob_id_set.add(entry.blob_id)
                    continue
                actual_hash = _compute_content_hash(source_path.read_bytes())
                if actual_hash != move.content_hash:
                    required_blob_id_set.add(entry.blob_id)
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
        *,
        apply_plan: Optional[DesktopPullApplyPlan] = None,
    ) -> DesktopPullRequiredBlobResult:
        plan = self.build_pull_required_blob_plan(
            pull,
            apply_plan=apply_plan,
        )
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

    def _allocate_pull_conflict_copy_path(
        self,
        *,
        original_relative_path: str,
        materialized_at: int,
    ) -> Path:
        original_path = _resolve_workspace_file_path(self.workspace.vault_root, original_relative_path)
        conflict_date = datetime.fromtimestamp(materialized_at / 1000, tz=timezone.utc).date()
        if original_path.parent.exists():
            return allocate_conflict_copy_path(
                original_path,
                conflict_date=conflict_date,
                device_name=self.config.device_id,
            )

        orphan_root = self.workspace.vault_root / CONFLICT_ORPHANS_DIRNAME
        orphan_root.mkdir(parents=True, exist_ok=True)
        return allocate_conflict_copy_path(
            orphan_root / original_path.name,
            conflict_date=conflict_date,
            device_name=self.config.device_id,
        )

    def _preserve_dirty_pull_conflict_copy(
        self,
        connection,
        *,
        source_file_id: str,
        live_path: Path,
        original_relative_path: str,
        expected_content_hash: Optional[str],
        materialized_at: int,
    ) -> Optional[Path]:
        if expected_content_hash is None:
            return None
        if not live_path.exists() or not live_path.is_file():
            return None

        payload = live_path.read_bytes()
        actual_hash = _compute_content_hash(payload)
        if actual_hash == expected_content_hash:
            return None

        current_document = load_filemap(self.workspace.paths.filemap_path)
        for record in current_document.files:
            if record.status != "conflict_copy":
                continue
            if record.conflict_source_file_id != source_file_id:
                continue
            if record.content_hash != actual_hash:
                continue
            conflict_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            if not conflict_path.exists() or not conflict_path.is_file():
                _write_bytes_atomic(conflict_path, payload)
            else:
                existing_hash = _compute_content_hash(conflict_path.read_bytes())
                if existing_hash != actual_hash:
                    _write_bytes_atomic(conflict_path, payload)
            state = load_vault_state(connection, self.vault_id)
            if state is None:
                raise KeyError(f"vault_state not found: {self.vault_id}")
            if not state.has_unresolved_conflicts:
                upsert_vault_state(connection, replace(state, has_unresolved_conflicts=True))
            return conflict_path

        conflict_path = self._allocate_pull_conflict_copy_path(
            original_relative_path=original_relative_path,
            materialized_at=materialized_at,
        )
        _write_bytes_atomic(conflict_path, payload)

        conflict_relative_path = _relative_vault_path(self.workspace.vault_root, conflict_path)
        updated_document = register_conflict_copy(
            current_document,
            source_file_id=source_file_id,
            conflict_file_id=self.file_id_builder(
                f"pull-conflict:{source_file_id}:{conflict_relative_path}:{materialized_at}"
            ),
            conflict_path=conflict_relative_path,
            updated_at=materialized_at,
            content_hash=actual_hash,
        )
        write_filemap_atomic(self.workspace.paths.filemap_path, updated_document)

        state = load_vault_state(connection, self.vault_id)
        if state is None:
            raise KeyError(f"vault_state not found: {self.vault_id}")
        if not state.has_unresolved_conflicts:
            upsert_vault_state(connection, replace(state, has_unresolved_conflicts=True))
        return conflict_path

    def _promote_unresolved_conflict_state_if_needed(
        self,
        snapshot: DesktopWorkspaceSnapshot,
    ) -> DesktopWorkspaceSnapshot:
        if snapshot.state.has_unresolved_conflicts:
            return snapshot
        if (
            not _has_conflict_copy_records(snapshot.document)
            and not _has_conflict_orphan_files(self.workspace.vault_root)
        ):
            return snapshot

        updated_state = replace(snapshot.state, has_unresolved_conflicts=True)
        with closing(self.workspace._open_connection()) as connection:
            upsert_vault_state(connection, updated_state)
        return DesktopWorkspaceSnapshot(
            document=snapshot.document,
            state=updated_state,
            tombstones=snapshot.tombstones,
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
                target_path = _resolve_workspace_file_path(self.workspace.vault_root, item.target_path)
                if source_path.exists():
                    payload = source_path.read_bytes()
                    actual_hash = _compute_content_hash(payload)
                    if actual_hash != item.content_hash:
                        self._preserve_dirty_pull_conflict_copy(
                            connection,
                            source_file_id=item.file_id,
                            live_path=source_path,
                            original_relative_path=item.source_path,
                            expected_content_hash=item.content_hash,
                            materialized_at=materialized_at,
                        )
                        if target_path.exists():
                            if not target_path.is_file():
                                raise ValueError(f"pull apply target path is not a file: {item.target_path}")
                            target_hash = _compute_content_hash(target_path.read_bytes())
                            if target_hash == item.content_hash:
                                source_path.unlink(missing_ok=True)
                                continue
                        if not move_staging_path.exists() or not move_staging_path.is_file():
                            raise FileNotFoundError(
                                f"staged pull payload required for dirty move conflict: {item.file_id}"
                            )
                        staged_hash = _compute_content_hash(move_staging_path.read_bytes())
                        if staged_hash != item.content_hash:
                            raise ValueError(
                                f"staged pull payload hash mismatch for file_id {item.file_id}: "
                                f"expected {item.content_hash}, got {staged_hash}"
                            )
                        source_path.unlink(missing_ok=True)
                        continue
                    move_staging_path.parent.mkdir(parents=True, exist_ok=True)
                    source_path.replace(move_staging_path)
                    continue
                if move_staging_path.exists():
                    continue
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
                self._preserve_dirty_pull_conflict_copy(
                    connection,
                    source_file_id=item.file_id,
                    live_path=delete_path,
                    original_relative_path=item.path,
                    expected_content_hash=item.expected_content_hash,
                    materialized_at=materialized_at,
                )
                if not delete_path.exists():
                    continue
                delete_path.unlink(missing_ok=True)
                deleted_paths.append(delete_path)

            for item in plan.writes:
                output_path = _resolve_workspace_file_path(self.workspace.vault_root, item.target_path)
                staging_path = _resolve_workspace_file_path(self.workspace.vault_root, item.staging_path)
                if not staging_path.exists() or not staging_path.is_file():
                    if output_path.exists():
                        if not output_path.is_file():
                            raise ValueError(f"pull apply target path is not a file: {item.target_path}")
                        payload = output_path.read_bytes()
                        actual_hash = _compute_content_hash(payload)
                        if actual_hash == item.content_hash:
                            written_paths[item.file_id] = output_path
                            continue
                    raise FileNotFoundError(f"staged pull payload not found: {item.staging_path}")
                payload = staging_path.read_bytes()
                actual_hash = _compute_content_hash(payload)
                if actual_hash != item.content_hash:
                    raise ValueError(
                        f"staged pull payload hash mismatch for file_id {item.file_id}: "
                        f"expected {item.content_hash}, got {actual_hash}"
                    )
                if item.previous_path == item.target_path:
                    self._preserve_dirty_pull_conflict_copy(
                        connection,
                        source_file_id=item.file_id,
                        live_path=output_path,
                        original_relative_path=item.target_path,
                        expected_content_hash=item.expected_previous_content_hash,
                        materialized_at=materialized_at,
                    )
                _write_bytes_atomic(output_path, payload)
                written_paths[item.file_id] = output_path

            for item in plan.moves:
                if item.source_path in blocking_paths or item.target_path in blocking_paths:
                    source_path = _resolve_pull_apply_staging_path(self.workspace.vault_root, item.file_id)
                else:
                    source_path = _resolve_workspace_file_path(self.workspace.vault_root, item.source_path)
                target_path = _resolve_workspace_file_path(self.workspace.vault_root, item.target_path)
                if source_path.exists():
                    payload = source_path.read_bytes()
                    actual_hash = _compute_content_hash(payload)
                    if actual_hash != item.content_hash:
                        if item.source_path in blocking_paths or item.target_path in blocking_paths:
                            raise ValueError(
                                f"blocking move staging hash mismatch for file_id {item.file_id}: "
                                f"expected {item.content_hash}, got {actual_hash}"
                            )
                        self._preserve_dirty_pull_conflict_copy(
                            connection,
                            source_file_id=item.file_id,
                            live_path=source_path,
                            original_relative_path=item.source_path,
                            expected_content_hash=item.content_hash,
                            materialized_at=materialized_at,
                        )
                        if target_path.exists():
                            if not target_path.is_file():
                                raise ValueError(f"pull apply target path is not a file: {item.target_path}")
                            target_hash = _compute_content_hash(target_path.read_bytes())
                            if target_hash == item.content_hash:
                                source_path.unlink(missing_ok=True)
                                moved_paths[item.file_id] = target_path
                                continue
                        staged_source_path = _resolve_pull_apply_staging_path(
                            self.workspace.vault_root,
                            item.file_id,
                        )
                        if not staged_source_path.exists() or not staged_source_path.is_file():
                            raise FileNotFoundError(
                                f"staged pull payload required for dirty move conflict: {item.file_id}"
                            )
                        staged_payload = staged_source_path.read_bytes()
                        staged_hash = _compute_content_hash(staged_payload)
                        if staged_hash != item.content_hash:
                            raise ValueError(
                                f"staged pull payload hash mismatch for file_id {item.file_id}: "
                                f"expected {item.content_hash}, got {staged_hash}"
                            )
                        _write_bytes_atomic(target_path, staged_payload)
                        source_path.unlink(missing_ok=True)
                        moved_paths[item.file_id] = target_path
                        continue
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
                self._preserve_dirty_pull_conflict_copy(
                    connection,
                    source_file_id=item.file_id,
                    live_path=delete_path,
                    original_relative_path=item.path,
                    expected_content_hash=item.expected_content_hash,
                    materialized_at=materialized_at,
                )
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
                        previous_path=None if before_record is None else before_record.path,
                        expected_previous_content_hash=(
                            None if before_record is None else before_record.content_hash
                        ),
                    )
                )
                if before_record is not None and before_record.path != entry.path:
                    deletes.append(
                        DesktopPullApplyDeleteFile(
                            file_id=entry.file_id,
                            path=before_record.path,
                            reason="replaced_old_path",
                            expected_content_hash=before_record.content_hash,
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
                    expected_content_hash=record.content_hash,
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

    def _record_sync_activity(
        self,
        *,
        occurred_at_ms: int,
        action: DesktopSyncPanelAction,
        source: str,
        status: str,
        message: Optional[str],
    ) -> DesktopSyncActivityRecord:
        level = "success" if status == "executed" else ("warning" if status == "disabled" else "danger")
        record = DesktopSyncActivityRecord(
            activity_id=str(uuid4()),
            occurred_at_ms=occurred_at_ms,
            level=level,
            action_id=action.action_id,
            command=action.command,
            status=status,
            source=source,
            message=message,
        )
        _append_jsonl_record(
            self.workspace.paths.sync_activity_log_path,
            {
                "activity_id": record.activity_id,
                "occurred_at_ms": record.occurred_at_ms,
                "level": record.level,
                "action_id": record.action_id,
                "command": record.command,
                "status": record.status,
                "source": record.source,
                "message": record.message,
            },
        )
        return record

    def list_sync_activity(self, *, limit: int = 20) -> DesktopSyncActivityFeed:
        all_payloads = _load_jsonl_records(self.workspace.paths.sync_activity_log_path)
        if limit <= 0:
            payloads = []
        elif len(all_payloads) <= limit:
            payloads = all_payloads
        else:
            payloads = all_payloads[-limit:]
        records = [
            DesktopSyncActivityRecord(
                activity_id=str(payload["activity_id"]),
                occurred_at_ms=int(payload["occurred_at_ms"]),
                level=str(payload["level"]),
                action_id=str(payload["action_id"]),
                command=str(payload["command"]),
                status=str(payload["status"]),
                source=str(payload["source"]),
                message=None if payload.get("message") is None else str(payload["message"]),
            )
            for payload in payloads
        ]
        return DesktopSyncActivityFeed(
            records=records,
            total_count=len(all_payloads),
        )

    def _build_panel_action(
        self,
        *,
        action_id: str,
        label: str,
        command: str,
        argv: Optional[list[str]] = None,
        enabled: bool = True,
        emphasis: str = "normal",
        reason: Optional[str] = None,
        requires_confirmation: bool = False,
    ) -> DesktopSyncPanelAction:
        return DesktopSyncPanelAction(
            action_id=action_id,
            label=label,
            enabled=enabled,
            emphasis=emphasis,
            command=command,
            argv=[] if argv is None else list(argv),
            reason=reason,
            requires_confirmation=requires_confirmation,
        )

    def summarize_vault(self) -> DesktopVaultSummary:
        conflicts = self.list_conflicts()
        changes = self.detect_local_changes()
        try:
            worker_health = self.load_worker_health()
        except FileNotFoundError:
            worker_health = None

        with closing(self.workspace._open_connection()) as connection:
            sync_apply_journal = load_sync_apply_journal(connection, self.vault_id)
            commit_journal = load_commit_intent_journal(connection, self.vault_id)

        state = conflicts.state
        blocking_reasons: list[str] = []
        if sync_apply_journal is not None:
            blocking_reasons.append(f"sync_apply_journal:{sync_apply_journal.phase}")
        if commit_journal is not None:
            blocking_reasons.append(f"commit_intent_journal:{commit_journal.status}")
        if state.commit_in_progress:
            blocking_reasons.append("commit_in_progress")
        if conflicts.actual_has_unresolved_conflicts or state.has_unresolved_conflicts:
            blocking_reasons.append("unresolved_conflicts")
        requires_full_pull = state.last_manifest_summary_status != "valid" or state.last_manifest_summary is None
        if requires_full_pull:
            blocking_reasons.append("requires_full_pull")

        return DesktopVaultSummary(
            state=state,
            changes=changes,
            conflicts=conflicts,
            worker_health=worker_health,
            commit_gate=DesktopCommitGateStatus(
                can_submit_commit=not blocking_reasons,
                blocking_reasons=blocking_reasons,
                requires_full_pull=requires_full_pull,
                has_active_commit_journal=commit_journal is not None,
                has_active_sync_apply_journal=sync_apply_journal is not None,
            ),
        )

    def build_sync_panel_model(self, *, now_ms: Optional[int] = None) -> DesktopSyncPanelModel:
        resolved_now_ms = (
            int(datetime.now(timezone.utc).timestamp() * 1000)
            if now_ms is None
            else now_ms
        )
        summary = self.summarize_vault()
        conflict_badge_count = len(summary.conflicts.conflict_copies) + len(summary.conflicts.conflict_orphans)
        change_badge_count = summary.changes.change_count
        secondary_actions = [
            self._build_panel_action(
                action_id="show-vault-summary",
                label="Open Summary",
                command="vault-summary",
            ),
            self._build_panel_action(
                action_id="list-conflicts",
                label="Show Conflicts",
                command="list-conflicts",
                enabled=conflict_badge_count > 0,
                reason=None if conflict_badge_count > 0 else "No unresolved conflicts",
            ),
            self._build_panel_action(
                action_id="worker-health",
                label="Worker Health",
                command="worker-health",
                enabled=summary.worker_health is not None,
                reason=None if summary.worker_health is not None else "No worker state recorded yet",
            ),
        ]
        if conflict_badge_count > 0:
            secondary_actions.append(
                self._build_panel_action(
                    action_id="resolve-conflicts-all",
                    label="Resolve All Local Artifacts",
                    command="resolve-conflicts",
                    argv=["--resolved-at", str(resolved_now_ms), "--all"],
                    enabled=True,
                    emphasis="warning",
                    reason="Deletes all listed local conflict-copy artifacts after user confirmation.",
                    requires_confirmation=True,
                )
            )

        if summary.commit_gate.has_active_sync_apply_journal:
            return DesktopSyncPanelModel(
                level="danger",
                headline="Sync recovery required",
                detail="A pull/apply journal is still active. Resume recovery before new sync or commit work.",
                conflict_badge_count=conflict_badge_count,
                change_badge_count=change_badge_count,
                primary_action=self._build_panel_action(
                    action_id="recover-pull-apply",
                    label="Resume Pull Recovery",
                    command="recover-pull-apply",
                    argv=["--normalized-at", str(resolved_now_ms)],
                    emphasis="primary",
                ),
                secondary_actions=secondary_actions,
                summary=summary,
            )
        if summary.commit_gate.has_active_commit_journal or summary.state.commit_in_progress:
            return DesktopSyncPanelModel(
                level="danger",
                headline="Commit recovery required",
                detail="A previous commit is still in progress or awaiting recovery confirmation.",
                conflict_badge_count=conflict_badge_count,
                change_badge_count=change_badge_count,
                primary_action=self._build_panel_action(
                    action_id="recover",
                    label="Resume Commit Recovery",
                    command="recover",
                    argv=["--normalized-at", str(resolved_now_ms)],
                    emphasis="primary",
                ),
                secondary_actions=secondary_actions,
                summary=summary,
            )
        if conflict_badge_count > 0:
            return DesktopSyncPanelModel(
                level="warning",
                headline=f"{conflict_badge_count} unresolved conflict artifacts",
                detail="Resolve local conflict copies before the next commit can be submitted.",
                conflict_badge_count=conflict_badge_count,
                change_badge_count=change_badge_count,
                primary_action=self._build_panel_action(
                    action_id="list-conflicts",
                    label="Review Conflicts",
                    command="list-conflicts",
                    emphasis="primary",
                ),
                secondary_actions=secondary_actions,
                summary=summary,
            )
        if summary.commit_gate.requires_full_pull:
            return DesktopSyncPanelModel(
                level="warning",
                headline="Full pull required",
                detail="The local manifest baseline is stale. Run pull/reconcile before creating a new commit.",
                conflict_badge_count=conflict_badge_count,
                change_badge_count=change_badge_count,
                primary_action=self._build_panel_action(
                    action_id="pull",
                    label="Run Pull",
                    command="pull",
                    argv=["--rewritten-at", str(resolved_now_ms)],
                    emphasis="primary",
                ),
                secondary_actions=secondary_actions,
                summary=summary,
            )
        if summary.worker_health is not None and summary.worker_health.status != "healthy":
            return DesktopSyncPanelModel(
                level="warning",
                headline="Background sync needs attention",
                detail="The latest worker run did not finish cleanly. Review worker health before relying on background sync.",
                conflict_badge_count=conflict_badge_count,
                change_badge_count=change_badge_count,
                primary_action=self._build_panel_action(
                    action_id="worker-health",
                    label="Inspect Worker Health",
                    command="worker-health",
                    emphasis="primary",
                ),
                secondary_actions=secondary_actions,
                summary=summary,
            )
        if change_badge_count > 0:
            return DesktopSyncPanelModel(
                level="info",
                headline=f"{change_badge_count} local changes pending",
                detail="Local edits are ready for the next submit or sync cycle.",
                conflict_badge_count=conflict_badge_count,
                change_badge_count=change_badge_count,
                primary_action=self._build_panel_action(
                    action_id="submit-detected-commit",
                    label="Submit Local Changes",
                    command="submit-detected-commit",
                    argv=["--created-at", str(resolved_now_ms)],
                    enabled=summary.commit_gate.can_submit_commit,
                    emphasis="primary",
                    reason=None if summary.commit_gate.can_submit_commit else ", ".join(summary.commit_gate.blocking_reasons),
                ),
                secondary_actions=secondary_actions,
                summary=summary,
            )
        return DesktopSyncPanelModel(
            level="success",
            headline="Vault is in sync",
            detail="No unresolved conflicts, no pending local changes, and no blocked sync state detected.",
            conflict_badge_count=0,
            change_badge_count=0,
            primary_action=self._build_panel_action(
                action_id="pull",
                label="Check For Remote Changes",
                command="pull",
                argv=["--rewritten-at", str(resolved_now_ms)],
                emphasis="primary",
            ),
            secondary_actions=secondary_actions,
            summary=summary,
        )

    def build_sync_center_model(self, *, now_ms: Optional[int] = None) -> DesktopSyncCenterModel:
        resolved_now_ms = (
            int(datetime.now(timezone.utc).timestamp() * 1000)
            if now_ms is None
            else now_ms
        )
        panel = self.build_sync_panel_model(now_ms=resolved_now_ms)
        summary = panel.summary
        cards: list[DesktopSyncCenterCard] = []
        conflict_badge_count = panel.conflict_badge_count
        change_badge_count = panel.change_badge_count

        if summary.commit_gate.has_active_sync_apply_journal:
            cards.append(
                DesktopSyncCenterCard(
                    card_id="pull-recovery",
                    kind="recovery",
                    level="danger",
                    title="Pull/apply recovery is blocking the vault",
                    body="A sync apply journal is still active. Resume pull recovery before any new sync or commit work.",
                    badge_count=1,
                    actions=[panel.primary_action],
                )
            )
        elif summary.commit_gate.has_active_commit_journal or summary.state.commit_in_progress:
            cards.append(
                DesktopSyncCenterCard(
                    card_id="commit-recovery",
                    kind="recovery",
                    level="danger",
                    title="Commit recovery is blocking the vault",
                    body="A previous commit still needs recovery confirmation before the next submit can start.",
                    badge_count=1,
                    actions=[panel.primary_action],
                )
            )

        if conflict_badge_count > 0:
            cards.append(
                DesktopSyncCenterCard(
                    card_id="conflicts",
                    kind="conflicts",
                    level="warning",
                    title=f"{conflict_badge_count} unresolved local conflict artifacts",
                    body="Conflict copies and orphan conflict files must be reviewed or cleared before commit submission can reopen.",
                    badge_count=conflict_badge_count,
                    actions=[
                        self._build_panel_action(
                            action_id="list-conflicts",
                            label="Review Conflicts",
                            command="list-conflicts",
                            emphasis="primary",
                        ),
                        self._build_panel_action(
                            action_id="resolve-conflicts-all",
                            label="Resolve All Local Artifacts",
                            command="resolve-conflicts",
                            argv=panel.secondary_actions[-1].argv if panel.secondary_actions else [],
                            enabled=any(action.action_id == "resolve-conflicts-all" for action in panel.secondary_actions),
                            emphasis="warning",
                            reason="Deletes all listed local conflict-copy artifacts after user confirmation.",
                            requires_confirmation=True,
                        ),
                    ],
                )
            )

        if summary.commit_gate.requires_full_pull:
            cards.append(
                DesktopSyncCenterCard(
                    card_id="baseline",
                    kind="baseline",
                    level="warning",
                    title="Local sync baseline must be rebuilt",
                    body="The current manifest summary is stale. Run a full pull/reconcile before creating a new commit.",
                    badge_count=1,
                    actions=[
                        self._build_panel_action(
                            action_id="pull",
                            label="Run Pull",
                            command="pull",
                            argv=["--rewritten-at", str(resolved_now_ms)],
                            emphasis="primary",
                        )
                    ],
                )
            )

        if change_badge_count > 0:
            cards.append(
                DesktopSyncCenterCard(
                    card_id="local-changes",
                    kind="changes",
                    level="info",
                    title=f"{change_badge_count} local changes are waiting",
                    body="Tracked modifications, missing files, or untracked files are ready for the next submit or sync cycle.",
                    badge_count=change_badge_count,
                    actions=[
                        self._build_panel_action(
                            action_id="detect-local-changes",
                            label="Inspect Local Changes",
                            command="detect-local-changes",
                        ),
                        self._build_panel_action(
                            action_id="submit-detected-commit",
                            label="Submit Local Changes",
                            command="submit-detected-commit",
                            argv=["--created-at", str(resolved_now_ms)],
                            enabled=summary.commit_gate.can_submit_commit,
                            emphasis="primary",
                            reason=None if summary.commit_gate.can_submit_commit else ", ".join(summary.commit_gate.blocking_reasons),
                        ),
                    ],
                )
            )

        if summary.worker_health is not None and summary.worker_health.status != "healthy":
            cards.append(
                DesktopSyncCenterCard(
                    card_id="worker-health",
                    kind="background-sync",
                    level="warning",
                    title="Background sync worker needs attention",
                    body="The latest worker run reported failures or stopped early. Review health before relying on background sync.",
                    badge_count=summary.worker_health.failure_count,
                    actions=[
                        self._build_panel_action(
                            action_id="worker-health",
                            label="Inspect Worker Health",
                            command="worker-health",
                            emphasis="primary",
                        )
                    ],
                )
            )

        if not cards:
            cards.append(
                DesktopSyncCenterCard(
                    card_id="healthy",
                    kind="overview",
                    level="success",
                    title="Vault sync center is clear",
                    body="No unresolved conflicts, no blocked recovery state, and no pending local changes were detected.",
                    badge_count=0,
                    actions=[
                        self._build_panel_action(
                            action_id="pull",
                            label="Check For Remote Changes",
                            command="pull",
                            argv=panel.primary_action.argv,
                            emphasis="primary",
                        )
                    ],
                )
            )

        return DesktopSyncCenterModel(
            cards=cards,
            panel=panel,
            summary=summary,
            recent_activity=self.list_sync_activity(limit=5),
        )

    def _find_sync_action(
        self,
        action_id: str,
        *,
        now_ms: Optional[int] = None,
    ) -> tuple[DesktopSyncPanelAction, str]:
        center = self.build_sync_center_model(now_ms=now_ms)
        for card in center.cards:
            for action in card.actions:
                if action.action_id == action_id:
                    return action, f"card:{card.card_id}"
        for action in [center.panel.primary_action, *center.panel.secondary_actions]:
            if action.action_id == action_id:
                return action, "panel"
        raise KeyError(f"sync action not found: {action_id}")

    def execute_sync_action(
        self,
        action_id: str,
        *,
        now_ms: Optional[int] = None,
    ) -> DesktopSyncActionExecutionResult:
        resolved_now_ms = (
            int(datetime.now(timezone.utc).timestamp() * 1000)
            if now_ms is None
            else now_ms
        )
        action, source = self._find_sync_action(action_id, now_ms=resolved_now_ms)
        if not action.enabled:
            result = DesktopSyncActionExecutionResult(
                action=action,
                source=source,
                status="disabled",
                payload=None,
                message=action.reason or "action is currently disabled",
            )
            self._record_sync_activity(
                occurred_at_ms=resolved_now_ms,
                action=action,
                source=source,
                status=result.status,
                message=result.message,
            )
            return result

        payload: object | None
        if action.command == "vault-summary":
            payload = self.summarize_vault()
        elif action.command == "list-conflicts":
            payload = self.list_conflicts()
        elif action.command == "worker-health":
            payload = self.load_worker_health()
        elif action.command == "detect-local-changes":
            payload = self.detect_local_changes()
        elif action.command == "resolve-conflicts":
            payload = self.resolve_conflicts(
                resolved_at=resolved_now_ms,
                resolve_all="--all" in action.argv,
            )
        elif action.command == "recover-pull-apply":
            payload = self.resume_pull_apply_recovery(normalized_at=resolved_now_ms)
        elif action.command == "recover":
            payload = self.resume_commit_recovery(normalized_at=resolved_now_ms)
        elif action.command == "pull":
            payload = self.pull_and_ack(rewritten_at=resolved_now_ms)
        elif action.command == "submit-detected-commit":
            payload = self.submit_detected_changes_if_needed(
                created_at=resolved_now_ms,
                cleanup_normalized_at=resolved_now_ms,
            )
        else:
            result = DesktopSyncActionExecutionResult(
                action=action,
                source=source,
                status="unsupported",
                payload=None,
                message=f"unsupported sync action command: {action.command}",
            )
            self._record_sync_activity(
                occurred_at_ms=resolved_now_ms,
                action=action,
                source=source,
                status=result.status,
                message=result.message,
            )
            return result

        result = DesktopSyncActionExecutionResult(
            action=action,
            source=source,
            status="executed",
            payload=payload,
            message=None,
        )
        self._record_sync_activity(
            occurred_at_ms=resolved_now_ms,
            action=action,
            source=source,
            status=result.status,
            message=result.message,
        )
        return result

    def list_conflicts(self) -> DesktopConflictStatus:
        snapshot = self._promote_unresolved_conflict_state_if_needed(self.load_snapshot())
        conflict_copies: list[DesktopConflictArtifact] = []
        for record in sorted(
            (item for item in snapshot.document.files if item.status == "conflict_copy"),
            key=lambda item: (item.path, item.file_id),
        ):
            conflict_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
            conflict_copies.append(
                DesktopConflictArtifact(
                    kind="conflict_copy",
                    file_id=record.file_id,
                    path=record.path,
                    exists_on_disk=conflict_path.exists() and conflict_path.is_file(),
                    conflict_source_file_id=record.conflict_source_file_id,
                    content_hash=record.content_hash,
                )
            )

        orphan_root = self.workspace.vault_root / CONFLICT_ORPHANS_DIRNAME
        conflict_orphans: list[DesktopConflictArtifact] = []
        if orphan_root.exists():
            for path in sorted(
                (item for item in orphan_root.rglob("*") if item.is_file()),
                key=lambda item: str(item.relative_to(orphan_root)),
            ):
                conflict_orphans.append(
                    DesktopConflictArtifact(
                        kind="conflict_orphan",
                        path=_relative_vault_path(self.workspace.vault_root, path),
                        exists_on_disk=True,
                    )
                )

        return DesktopConflictStatus(
            state=snapshot.state,
            actual_has_unresolved_conflicts=bool(conflict_copies or conflict_orphans),
            conflict_copies=conflict_copies,
            conflict_orphans=conflict_orphans,
        )

    def resolve_conflicts(
        self,
        *,
        resolved_at: int,
        conflict_file_ids: Optional[Iterable[str]] = None,
        orphan_relative_paths: Optional[Iterable[str]] = None,
        resolve_all: bool = False,
    ) -> DesktopConflictResolutionResult:
        requested_conflict_file_ids = list(dict.fromkeys(conflict_file_ids or []))
        requested_orphan_paths = list(dict.fromkeys(orphan_relative_paths or []))
        if resolve_all:
            status = self.list_conflicts()
            requested_conflict_file_ids = [item.file_id for item in status.conflict_copies if item.file_id is not None]
            requested_orphan_paths = [item.path for item in status.conflict_orphans]
        if not requested_conflict_file_ids and not requested_orphan_paths:
            raise ValueError("resolve-conflicts requires at least one conflict file_id or orphan path")

        with closing(self.workspace._open_connection()) as connection:
            self._require_no_active_sync_apply_journal(connection, operation="resolve-conflicts")
            snapshot = self.workspace._load_snapshot_from_connection(connection)
            if snapshot.state.commit_in_progress:
                raise ValueError("resolve-conflicts cannot start while commit_in_progress is true")

            current_document = snapshot.document
            file_by_id = {record.file_id: record for record in current_document.files}
            removed_conflict_paths: dict[str, Path] = {}
            skipped_conflict_file_ids: list[str] = []
            updated_document = current_document
            filemap_changed = False

            for file_id in requested_conflict_file_ids:
                record = file_by_id.get(file_id)
                if record is None:
                    skipped_conflict_file_ids.append(file_id)
                    continue
                if record.status != "conflict_copy":
                    raise ValueError(f"workspace file is not a conflict_copy: {file_id}")
                conflict_path = _resolve_workspace_file_path(self.workspace.vault_root, record.path)
                if conflict_path.exists() and not conflict_path.is_file():
                    raise ValueError(f"conflict_copy path is not a file: {record.path}")
                conflict_path.unlink(missing_ok=True)
                removed_conflict_paths[file_id] = conflict_path
                updated_document = remove_conflict_copy(
                    updated_document,
                    file_id=file_id,
                    updated_at=resolved_at,
                )
                filemap_changed = True

            removed_orphan_paths: list[Path] = []
            skipped_orphan_paths: list[str] = []
            for relative_path in requested_orphan_paths:
                orphan_path = _resolve_conflict_orphan_path(self.workspace.vault_root, relative_path)
                if not orphan_path.exists():
                    skipped_orphan_paths.append(relative_path)
                    continue
                if not orphan_path.is_file():
                    raise ValueError(f"conflict orphan path is not a file: {relative_path}")
                orphan_path.unlink(missing_ok=True)
                removed_orphan_paths.append(orphan_path)

            if filemap_changed:
                write_filemap_atomic(self.workspace.paths.filemap_path, updated_document)

            has_unresolved_conflicts = (
                _has_conflict_copy_records(updated_document)
                or _has_conflict_orphan_files(self.workspace.vault_root)
            )
            updated_state = snapshot.state
            if snapshot.state.has_unresolved_conflicts != has_unresolved_conflicts:
                updated_state = replace(
                    snapshot.state,
                    has_unresolved_conflicts=has_unresolved_conflicts,
                )
                upsert_vault_state(connection, updated_state)

        return DesktopConflictResolutionResult(
            state=updated_state,
            removed_conflict_paths=removed_conflict_paths,
            removed_orphan_paths=removed_orphan_paths,
            skipped_conflict_file_ids=skipped_conflict_file_ids,
            skipped_orphan_paths=skipped_orphan_paths,
        )

    def export_vault_package(
        self,
        package_path: Path,
        *,
        include_ai_raw: bool = False,
    ) -> DesktopVaultExportResult:
        resolved_package_path = package_path.resolve()
        try:
            resolved_package_path.relative_to(self.workspace.vault_root.resolve())
        except ValueError:
            pass
        else:
            raise ValueError("export-vault output package must be outside the vault root")
        if resolved_package_path.exists() and resolved_package_path.is_dir():
            raise ValueError("export-vault output package path must be a file")

        with closing(self.workspace._open_connection()) as connection:
            self._require_no_active_sync_apply_journal(connection, operation="export-vault")
            snapshot = self.workspace._load_snapshot_from_connection(connection)
            if snapshot.state.commit_in_progress:
                raise ValueError("export-vault cannot start while commit_in_progress is true")
            commit_journal = load_commit_intent_journal(connection, self.vault_id)
            if commit_journal is not None:
                raise ValueError("export-vault cannot start while commit_intent_journal is active")

        export_files = _list_migration_export_files(
            self.workspace.vault_root,
            include_ai_raw=include_ai_raw,
            package_path=resolved_package_path,
        )
        export_paths = [relative_path for relative_path, _ in export_files]
        missing_required = sorted(_MIGRATION_REQUIRED_FILES - set(export_paths))
        if missing_required:
            raise ValueError(
                "export-vault cannot build a complete migration package; missing required files: "
                + ", ".join(missing_required)
            )

        resolved_package_path.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(resolved_package_path, "w", compression=ZIP_DEFLATED) as archive:
            for relative_path, source_path in export_files:
                archive.write(source_path, arcname=relative_path)

        return DesktopVaultExportResult(
            package_path=resolved_package_path,
            vault_id=self.vault_id,
            exported_paths=export_paths,
            included_ai_raw=any(path.startswith(".ai/raw/") for path in export_paths),
            included_conflict_orphans=any(path.startswith(f"{CONFLICT_ORPHANS_DIRNAME}/") for path in export_paths),
        )

    def import_vault_package(
        self,
        package_path: Path,
    ) -> DesktopVaultImportResult:
        resolved_package_path = package_path.resolve()
        if not resolved_package_path.exists() or not resolved_package_path.is_file():
            raise FileNotFoundError(f"migration package not found: {resolved_package_path}")

        if self.workspace.vault_root.exists():
            if not self.workspace.vault_root.is_dir():
                raise ValueError("import-vault target root must be a directory")
            if any(self.workspace.vault_root.iterdir()):
                raise ValueError("import-vault requires an empty vault root")
        if self.workspace.paths.db_path.exists():
            raise ValueError("import-vault requires an empty local state database path")

        inspection = inspect_vault_package(resolved_package_path)
        if inspection.vault_id != self.vault_id:
            raise ValueError(
                "migration package vault_id does not match desktop config: "
                f"expected {self.vault_id}, got {inspection.vault_id}"
            )

        with ZipFile(resolved_package_path, "r") as archive:
            package_document, archive_entries = _read_migration_package(archive)
            self.workspace.vault_root.mkdir(parents=True, exist_ok=True)
            for relative_path in sorted(archive_entries):
                target_path = _resolve_workspace_file_path(self.workspace.vault_root, relative_path)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                _write_bytes_atomic(target_path, archive.read(archive_entries[relative_path]))

        tombstones = load_tombstone_ledger(self.workspace.paths.ledger_path)
        local_delete_sequence = max((record.local_delete_seq for record in tombstones), default=0)
        has_unresolved_conflicts = (
            _has_conflict_copy_records(package_document)
            or _has_conflict_orphan_files(self.workspace.vault_root)
        )

        with closing(self.workspace._open_connection()) as connection:
            imported_state = replace(
                load_vault_state(connection, self.vault_id)
                or VaultStateRecord(
                    vault_id=self.vault_id,
                    last_applied_revision=0,
                    remote_head_revision=0,
                    acked_revision=0,
                    pending_ack_to_server=[],
                    commit_in_progress=False,
                    last_manifest_summary=None,
                    last_manifest_summary_status="stale",
                    local_delete_sequence=local_delete_sequence,
                    has_unresolved_conflicts=has_unresolved_conflicts,
                    meta={"device_id": self.config.device_id},
                ),
                last_applied_revision=0,
                remote_head_revision=0,
                acked_revision=0,
                pending_ack_to_server=[],
                commit_in_progress=False,
                last_manifest_summary=None,
                last_manifest_summary_status="stale",
                local_delete_sequence=local_delete_sequence,
                has_unresolved_conflicts=has_unresolved_conflicts,
            )
            upsert_vault_state(connection, imported_state)

        imported_paths = sorted(archive_entries)
        return DesktopVaultImportResult(
            package_path=resolved_package_path,
            vault_id=self.vault_id,
            imported_paths=imported_paths,
            restored_ai_raw=any(path.startswith(".ai/raw/") for path in imported_paths),
            restored_conflict_orphans=any(
                path.startswith(f"{CONFLICT_ORPHANS_DIRNAME}/") for path in imported_paths
            ),
            state=imported_state,
        )

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
        snapshot = self._promote_unresolved_conflict_state_if_needed(snapshot)
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

    def _materializing_workspace_matches_document(
        self,
        snapshot: Optional[DesktopWorkspaceSnapshot],
    ) -> bool:
        return snapshot is not None and self._workspace_matches_document(snapshot.document)

    def _finalize_materializing_pull_apply_recovery(
        self,
        journal: SyncApplyJournalRecord,
        *,
        normalized_at: int,
    ) -> DesktopPullApplyRecoveryResult:
        with closing(self.workspace._open_connection()) as connection:
            upsert_sync_apply_journal(
                connection,
                replace(journal, phase="finalizing", updated_at=normalized_at),
            )
            state = recover_sync_apply_finalizing_state(connection, self.vault_id)
        removed = self._cleanup_pull_apply_staging_artifacts()
        return DesktopPullApplyRecoveryResult(
            mode="finalized",
            requires_full_pull=state.last_manifest_summary_status != "valid",
            journal_phase="materializing",
            state=state,
            removed_staging_paths=removed,
            isolated_staging_paths=[],
            removed_plan_path=self._cleanup_pull_apply_plan_file(),
        )

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
            requires_full_pull=True,
            journal_phase=journal.phase,
            state=degraded_state,
            removed_staging_paths=[],
            isolated_staging_paths=isolated_paths,
            removed_plan_path=self._cleanup_pull_apply_plan_file(),
        )

    def _isolate_unjournaled_pull_apply_staging(self) -> list[Path]:
        staging_root = self.workspace.vault_root / STAGING_DIRNAME
        if not staging_root.exists():
            return []
        moved_paths: list[Path] = []
        for staging_path in sorted(
            (
                path
                for path in staging_root.rglob("*")
                if path.is_file() and path.name.endswith(".staging") and not path.name.endswith(".blob.staging")
            ),
            key=lambda item: str(item.relative_to(staging_root)),
        ):
            moved_paths.append(move_staging_orphan(self.workspace.vault_root, staging_path))
        return moved_paths

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
