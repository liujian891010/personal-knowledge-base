from __future__ import annotations

from pathlib import PurePosixPath


_OFFICE_LOCK_FILE_SUFFIXES = {
    ".doc",
    ".docm",
    ".docx",
    ".dot",
    ".dotm",
    ".dotx",
    ".pot",
    ".potm",
    ".potx",
    ".pps",
    ".ppsm",
    ".ppsx",
    ".ppt",
    ".pptm",
    ".pptx",
    ".xls",
    ".xlsb",
    ".xlsm",
    ".xlsx",
    ".xlt",
    ".xltm",
    ".xltx",
}


def is_volatile_workspace_file_path(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    name = path.name
    lower_name = name.lower()
    suffix = path.suffix.lower()
    if name.startswith("~$") and suffix in _OFFICE_LOCK_FILE_SUFFIXES:
        return True
    if lower_name.startswith(".~lock.") and lower_name.endswith("#"):
        return True
    return False
