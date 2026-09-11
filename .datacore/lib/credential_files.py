"""Shared private-file publication and account filename validation."""
import re
from pathlib import Path

from file_utils import atomic_write_text


def calendar_token_path(directory: Path, default: Path, account=None) -> Path:
    if account is None or account == "" or account == "default":
        return default
    if not isinstance(account, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}", account):
        raise ValueError("calendar account must be a single safe filename component")
    return directory / f"google_calendar_token_{account}.json"


def write_private_text(path: Path, content: str) -> None:
    """Publish only a completely written mode-0600 file."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_write_text(path, content)
