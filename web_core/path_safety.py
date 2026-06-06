from __future__ import annotations

import os
from typing import Iterable


BLOCKED_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".exe",
    ".js",
    ".msi",
    ".ps1",
    ".py",
    ".scr",
    ".sh",
    ".vbs",
}


def safe_user_path(path: str, allowed_roots: Iterable[str], must_exist: bool = True, allowed_extensions: Iterable[str] | None = None) -> str:
    """Resolve a user-supplied path only if it stays inside an allowed root."""
    if not path:
        raise ValueError("File path is required.")
    resolved = os.path.abspath(os.path.expanduser(str(path)))
    roots = [os.path.abspath(os.path.expanduser(str(root))) for root in allowed_roots if str(root or "").strip()]
    if not roots:
        raise ValueError("Allowed upload roots are not configured.")
    if not any(os.path.commonpath([resolved, root]) == root for root in roots):
        raise ValueError("FII/DII CSV path is outside allowed upload folder.")
    ext = os.path.splitext(resolved)[1].lower()
    if ext in BLOCKED_EXTENSIONS:
        raise ValueError("Executable/script uploads are not allowed.")
    allowed = {str(item).lower() for item in (allowed_extensions or [])}
    if allowed and ext not in allowed:
        raise ValueError("Only CSV uploads are allowed.")
    if must_exist and not os.path.isfile(resolved):
        raise ValueError("Uploaded file does not exist.")
    return resolved
