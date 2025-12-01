"""
Media Index Generator for Datacore.

Creates folder-level _media-index.md files that catalog non-AI-readable files,
replacing the 1:1 companion stub approach with a cleaner index pattern.

Usage:
    from media_index import generate_media_index

    # Generate index for a folder
    content = generate_media_index(Path('/path/to/folder'))
    (folder / '_media-index.md').write_text(content)

    # Generate recursive index (for inbox folders)
    content = generate_media_index(Path('/path/to/inbox'), recursive=True)
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set
import re

# =============================================================================
# CONSTANTS
# =============================================================================

# Non-AI-readable formats requiring indexing (from DIP-0015)
MEDIA_EXTENSIONS: Dict[str, Set[str]] = {
    'Audio': {'.wav', '.mp3', '.m4a', '.aac', '.flac', '.ogg'},
    'Video': {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'},
    'Presentations': {'.key', '.pptx', '.ppt'},
    'Design': {'.psd', '.ai', '.sketch', '.graffle', '.fig', '.xd'},
    'Documents': {'.pages', '.numbers'},
    'Archives': {'.zip', '.tar', '.tar.gz', '.dmg', '.rar', '.7z'},
}

# Flatten for quick lookup
ALL_MEDIA_EXTENSIONS: Set[str] = set()
for exts in MEDIA_EXTENSIONS.values():
    ALL_MEDIA_EXTENSIONS.update(exts)


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class MediaFile:
    """A single non-AI-readable file."""
    path: Path
    name: str
    extension: str
    size: int
    mtime: datetime
    category: str
    description: Optional[str] = None

    @property
    def size_human(self) -> str:
        """Human-readable file size."""
        if self.size < 1024:
            return f"{self.size}B"
        elif self.size < 1024 * 1024:
            return f"{self.size / 1024:.1f}KB"
        elif self.size < 1024 * 1024 * 1024:
            return f"{self.size / (1024 * 1024):.1f}MB"
        else:
            return f"{self.size / (1024 * 1024 * 1024):.1f}GB"

    @property
    def date_str(self) -> str:
        """Formatted date string."""
        return self.mtime.strftime('%Y-%m-%d')


@dataclass
class MediaIndex:
    """Index of media files in a folder."""
    folder: Path
    files: List[MediaFile] = field(default_factory=list)
    generated: datetime = field(default_factory=datetime.now)
    recursive: bool = False

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def total_size_human(self) -> str:
        size = self.total_size
        if size < 1024 * 1024:
            return f"{size / 1024:.1f}KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.0f}MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.1f}GB"

    @property
    def by_category(self) -> Dict[str, List[MediaFile]]:
        """Group files by category."""
        result: Dict[str, List[MediaFile]] = {}
        for f in self.files:
            result.setdefault(f.category, []).append(f)
        # Sort files within each category by name
        for cat in result:
            result[cat].sort(key=lambda x: x.name.lower())
        return result


# =============================================================================
# CORE FUNCTIONS
# =============================================================================

def get_category(extension: str) -> Optional[str]:
    """Get category for a file extension."""
    ext = extension.lower()
    for category, extensions in MEDIA_EXTENSIONS.items():
        if ext in extensions:
            return category
    return None


def scan_folder(folder: Path, recursive: bool = False) -> List[MediaFile]:
    """Scan folder for non-AI-readable files."""
    files: List[MediaFile] = []

    if recursive:
        iterator = folder.rglob('*')
    else:
        iterator = folder.glob('*')

    for item in iterator:
        if not item.is_file():
            continue

        # Skip hidden files and _media-index.md itself
        if item.name.startswith('.') or item.name == '_media-index.md':
            continue

        ext = item.suffix.lower()
        category = get_category(ext)

        if category:
            try:
                stat = item.stat()
                files.append(MediaFile(
                    path=item,
                    name=item.name,
                    extension=ext,
                    size=stat.st_size,
                    mtime=datetime.fromtimestamp(stat.st_mtime),
                    category=category,
                ))
            except (OSError, PermissionError):
                continue

    return files


def generate_media_index(folder: Path, recursive: bool = False) -> str:
    """
    Generate _media-index.md content for a folder.

    Args:
        folder: Path to scan
        recursive: If True, include all subfolders

    Returns:
        Markdown content for _media-index.md
    """
    folder = Path(folder).resolve()
    files = scan_folder(folder, recursive)

    if not files:
        return ""

    index = MediaIndex(folder=folder, files=files, recursive=recursive)
    return format_index(index)


def format_index(index: MediaIndex) -> str:
    """Format MediaIndex as markdown."""
    lines = []

    # Frontmatter
    folder_name = index.folder.name
    lines.append("---")
    lines.append("type: media-index")
    lines.append(f"folder: {folder_name}")
    lines.append(f"generated: {index.generated.strftime('%Y-%m-%d')}")
    lines.append(f"file_count: {len(index.files)}")
    lines.append(f"total_size: {index.total_size_human}")
    if index.recursive:
        lines.append("recursive: true")
    lines.append("---")
    lines.append("")

    # Title
    lines.append(f"# Media Index: {folder_name}")
    lines.append("")
    lines.append("Non-AI-readable files in this folder.")
    lines.append("")

    # Group by category
    by_cat = index.by_category

    # Sort categories by file count (most first)
    sorted_cats = sorted(by_cat.keys(), key=lambda c: len(by_cat[c]), reverse=True)

    for category in sorted_cats:
        files = by_cat[category]
        cat_size = sum(f.size for f in files)
        cat_size_human = f"{cat_size / (1024 * 1024):.0f}MB" if cat_size > 1024 * 1024 else f"{cat_size / 1024:.0f}KB"

        lines.append(f"## {category} ({len(files)} files, {cat_size_human})")
        lines.append("")
        lines.append("| File | Size | Date | Description |")
        lines.append("|------|------|------|-------------|")

        for f in files:
            desc = f.description or "[needs description]"
            # Escape pipe characters in filename
            name = f.name.replace("|", "\\|")
            lines.append(f"| {name} | {f.size_human} | {f.date_str} | {desc} |")

        lines.append("")

    # Footer
    lines.append("---")
    lines.append("")
    lines.append("*Generated by structural-integrity. Edit descriptions manually or run `/ingest` for AI summaries.*")

    return '\n'.join(lines)


def parse_existing_index(index_path: Path) -> Dict[str, str]:
    """
    Parse existing _media-index.md to extract descriptions.

    Returns:
        Dict mapping filename to description
    """
    descriptions: Dict[str, str] = {}

    if not index_path.exists():
        return descriptions

    content = index_path.read_text()

    # Parse table rows: | filename | size | date | description |
    table_row_pattern = re.compile(r'^\|\s*([^|]+)\s*\|\s*[^|]+\s*\|\s*[^|]+\s*\|\s*([^|]+)\s*\|$', re.MULTILINE)

    for match in table_row_pattern.finditer(content):
        filename = match.group(1).strip().replace("\\|", "|")
        description = match.group(2).strip()

        # Skip header row and placeholder descriptions
        if filename == 'File' or description == '[needs description]':
            continue

        descriptions[filename] = description

    return descriptions


def update_media_index(index_path: Path, preserve_descriptions: bool = True) -> str:
    """
    Update existing index, optionally preserving manual descriptions.

    Args:
        index_path: Path to existing _media-index.md
        preserve_descriptions: If True, keep existing descriptions

    Returns:
        Updated markdown content
    """
    folder = index_path.parent
    existing_descriptions = {}

    if preserve_descriptions and index_path.exists():
        existing_descriptions = parse_existing_index(index_path)

    # Scan for current files
    files = scan_folder(folder, recursive=False)

    # Apply existing descriptions
    for f in files:
        if f.name in existing_descriptions:
            f.description = existing_descriptions[f.name]

    index = MediaIndex(folder=folder, files=files)
    return format_index(index)


# =============================================================================
# CLI
# =============================================================================

if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python media_index.py <folder> [--recursive]")
        sys.exit(1)

    folder = Path(sys.argv[1])
    recursive = '--recursive' in sys.argv or '-r' in sys.argv

    if not folder.is_dir():
        print(f"Error: {folder} is not a directory")
        sys.exit(1)

    content = generate_media_index(folder, recursive=recursive)

    if content:
        print(content)
    else:
        print(f"No media files found in {folder}")
