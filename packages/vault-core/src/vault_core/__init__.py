from .filemap import load_filemap, recover_filemap, write_filemap_atomic
from .initializer import initialize_vault
from .ledger import append_tombstone, load_tombstone_ledger
from .models import FileMapDocument, FileRecord, TombstoneRecord, VaultStateRecord, WikiTaskRecord
from .models import CommitIntentJournalRecord, SyncApplyJournalRecord
from .operations import add_file, mark_deleted, register_conflict_copy, rename_file
from .paths import allocate_conflict_copy_path, move_conflict_orphan, move_staging_orphan, sanitize_device_name
from .sqlite_store import (
    bootstrap_database,
    clear_commit_intent_journal,
    clear_sync_apply_journal,
    list_file_index,
    load_commit_intent_journal,
    load_sync_apply_journal,
    load_vault_state,
    normalize_legacy_acknowledged_commit_intent,
    open_database,
    replace_active_wiki_task,
    upsert_commit_intent_journal,
    upsert_file_index_entry,
    upsert_sync_apply_journal,
    upsert_vault_state,
)

__all__ = [
    "FileMapDocument",
    "FileRecord",
    "TombstoneRecord",
    "CommitIntentJournalRecord",
    "SyncApplyJournalRecord",
    "VaultStateRecord",
    "WikiTaskRecord",
    "add_file",
    "allocate_conflict_copy_path",
    "append_tombstone",
    "bootstrap_database",
    "clear_commit_intent_journal",
    "clear_sync_apply_journal",
    "initialize_vault",
    "list_file_index",
    "load_commit_intent_journal",
    "load_sync_apply_journal",
    "load_tombstone_ledger",
    "load_filemap",
    "load_vault_state",
    "mark_deleted",
    "move_conflict_orphan",
    "move_staging_orphan",
    "normalize_legacy_acknowledged_commit_intent",
    "open_database",
    "recover_filemap",
    "replace_active_wiki_task",
    "register_conflict_copy",
    "rename_file",
    "sanitize_device_name",
    "upsert_commit_intent_journal",
    "upsert_file_index_entry",
    "upsert_sync_apply_journal",
    "upsert_vault_state",
    "write_filemap_atomic",
]
