FILEMAP_FILENAME = "filemap.json"
FILEMAP_TMP_FILENAME = "filemap.json.tmp"
TOMBSTONE_LEDGER_FILENAME = "tombstone-ledger.jsonl"
VAULTINFO_FILENAME = ".vaultinfo"

NOTEAPP_DIRNAME = ".noteapp"
AI_DIRNAME = ".ai"
NOTES_DIRNAME = "Notes"
ATTACHMENTS_DIRNAME = "Attachments"
STAGING_DIRNAME = ".noteapp/staging"
STAGING_ORPHANS_DIRNAME = ".noteapp/staging-orphans"
CONFLICT_ORPHANS_DIRNAME = ".noteapp/conflict-orphans"

LOCAL_ONLY_DIRS = (
    ".noteapp/drafts",
    STAGING_DIRNAME,
    STAGING_ORPHANS_DIRNAME,
    CONFLICT_ORPHANS_DIRNAME,
    ".ai/raw",
)

SYNCED_DIRS = (
    ".ai/wiki",
    "Notes",
    "Attachments",
)
