FILEMAP_FILENAME = "filemap.json"
FILEMAP_TMP_FILENAME = "filemap.json.tmp"
VAULTINFO_FILENAME = ".vaultinfo"

NOTEAPP_DIRNAME = ".noteapp"
AI_DIRNAME = ".ai"
NOTES_DIRNAME = "Notes"
ATTACHMENTS_DIRNAME = "Attachments"

LOCAL_ONLY_DIRS = (
    ".noteapp/drafts",
    ".noteapp/staging",
    ".noteapp/staging-orphans",
    ".noteapp/conflict-orphans",
    ".ai/raw",
)

SYNCED_DIRS = (
    ".ai/wiki",
    "Notes",
    "Attachments",
)
