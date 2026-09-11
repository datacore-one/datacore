"""Create generated notes without overwriting existing knowledge."""
import hashlib
import os
from pathlib import Path
import re
import tempfile

from file_utils import atomic_write_text, file_lock, fsync_directory


def create_note(directory, filename, content):
    if not isinstance(filename, str) or not isinstance(content, str):
        raise ValueError("note filename and content must be strings")
    name = re.sub(r"[^\w\s.-]", "", filename).strip()[:160]
    if not name or name in {".", ".."}:
        name = "untitled"
    if not name.endswith(".md"):
        name += ".md"
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:20]
    with file_lock(directory / ".generated-notes"):
        for candidate in [target, target.with_name(target.stem + "--" + digest + ".md")]:
            if candidate.is_symlink():
                raise ValueError("generated note destination is a symbolic link")
            try:
                existing = candidate.read_bytes().decode("utf-8")
            except FileNotFoundError:
                existing = None
            if existing == content:
                return candidate
            if existing is not None:
                continue
            # link() publishes without replacement even if an uncooperative
            # writer creates the destination after our existence check.
            with tempfile.TemporaryDirectory(prefix=".generated-", dir=directory) as temporary:
                staged = Path(temporary) / "note"
                atomic_write_text(staged, content)
                try:
                    os.link(staged, candidate)
                except FileExistsError:
                    continue
                fsync_directory(directory)
            return candidate
    raise FileExistsError("generated note conflicts with existing knowledge; existing files preserved")
