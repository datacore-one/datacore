"""
Structural Integrity Checker for Datacore.

Implements the checks defined in DIP-0015 (Semantic Organization):
- Folder structure validation
- Companion file requirements
- Inbox freshness
- Naming conventions
- Binaries committed to git (no Git LFS)
- Wiki-link integrity

Usage:
    from structural_integrity import StructuralIntegrityChecker

    checker = StructuralIntegrityChecker(Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data"))) / '0-personal')
    result = checker.run_all_checks()
    print(format_summary(result))
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Literal, Optional, Set, Tuple
import hashlib
import os
import re
import subprocess
import time


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class Issue:
    """Single structural integrity issue."""
    severity: Literal['error', 'warning', 'info']
    check_type: str  # folder_structure, companion, inbox, naming, binary_in_git, wiki_link
    path: Path
    message: str
    fix_suggestion: Optional[str] = None
    auto_fixable: bool = False


@dataclass
class AuditResult:
    """Result of structural integrity audit."""
    space: str
    space_type: Literal['personal', 'team']
    issues: List[Issue] = field(default_factory=list)
    checks_run: int = 0
    duration_ms: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == 'error']

    @property
    def warnings(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == 'warning']

    @property
    def infos(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == 'info']

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    @property
    def passed(self) -> bool:
        return not self.has_errors


# =============================================================================
# CONSTANTS (from DIP-0015)
# =============================================================================

# Expected folder prefixes for numbered folders
PERSONAL_FOLDERS = {
    '0-inbox',
    '1-active',
    '2-code',
    '3-knowledge',
    '4-outbox',
    '4-archive',
}

TEAM_FOLDERS = {
    '0-inbox',
    '1-tracks',
    '2-projects',
    '3-knowledge',
    '4-outbox',
    '4-archive',
}

# Allowlists for top-level directories (DIP-0015 + agent definition)
# Anything not in these sets at the space root is flagged as unexpected.
PERSONAL_ALLOWED_DIRS = {
    'org', 'journal', 'notes', 'content',
    '0-inbox', '1-active', '2-code', '3-knowledge', '4-outbox', '4-archive',
    '.obsidian', '.datacore', '.claude', '.git', '.lfs-cache',
}

TEAM_ALLOWED_DIRS = {
    'org', 'journal', 'today', 'notes', 'content',
    '0-inbox', '1-tracks', '2-projects', '3-knowledge', '4-outbox', '4-archive',
    '.datacore', '.claude', '.git',
}

# The space pre-commit hook is what actually enforces the root allowlist, so
# its ALLOWED_FILES are read from the hook rather than kept as a second copy
# here (the two copies disagreed and this checker flagged files commits accept).
SPACE_HOOK = Path(__file__).resolve().parent.parent / 'hooks' / 'space-pre-commit'


def _hook_allowed_files() -> Set[str]:
    try:
        match = re.search(r'^ALLOWED_FILES="([^"]*)"', SPACE_HOOK.read_text(), re.M)
    except OSError:
        return set()
    return set(match.group(1).split()) if match else set()


def _grandfathered(space_path: Path) -> Set[str]:
    """Entries listed in <space>/.datacore/structure-allow: known drift the hook tolerates."""
    allow = space_path / '.datacore' / 'structure-allow'
    try:
        lines = allow.read_text().splitlines()
    except OSError:
        return set()
    return {l.strip() for l in lines if l.strip() and not l.lstrip().startswith('#')}

ALLOWED_ROOT_FILES = {
    '.gitignore', '.gitattributes', '_index.md',
    'CLAUDE.md', 'CLAUDE.base.md', 'CLAUDE.org.md',
    'CLAUDE.space.md', 'CLAUDE.template.md', 'CLAUDE.local.md',
    'SCAFFOLDING.md', 'SCAFFOLDING.base.md', 'SCAFFOLDING.space.md',
    'README.md', 'LICENSE', 'CODEOWNERS',
    'knowledge.db',
    # Written by the goals module — its module.yaml declares
    # `goals_path: 0-personal/goals.yaml`, i.e. the space root is the intended
    # location, not drift. Added 2026-07-26 after this guard aborted
    # `./sync push` for 0-personal on two consecutive wrap-ups, forcing a manual
    # commit each time. If the goals module ever relocates the file, drop this.
    'goals.yaml',
    # Written by the ventures module at space root by design, and already
    # permitted by .datacore/hooks/space-pre-commit's ALLOWED_FILES. Two
    # allowlists existed and disagreed, so this checker reported a DIP-0015
    # violation in all seven venture spaces for files the commit hook accepts.
    # Keep the two lists in step: the hook is what actually enforces.
    'venture.yaml',
    'hypotheses.yaml',
    'roadmap.yaml',
    '.DS_Store',
} | _hook_allowed_files()

# Hints for where unexpected folders likely belong
ROGUE_FOLDER_HINTS = {
    'contacts': '3-knowledge/reference/people/',
    'research': '1-tracks/research/',
    'health': '1-active/health-longevity/ (personal)',
    'coach': '1-active/ (personal)',
    'code': '2-code/ or 2-projects/',
    'inbox': '0-inbox/',
    'products': '1-tracks/product/ or 2-projects/',
    'sales': '1-tracks/comms/ or 1-tracks/ops/',
    'reports': '1-tracks/ops/ or 3-knowledge/',
    'opportunities': '1-tracks/research/',
    'docs': '3-knowledge/pages/ or 1-tracks/dev/',
    'today': 'journal/',
}

# Non-AI-readable formats that require companion markdown
COMPANION_REQUIRED_EXTENSIONS = {
    # Apple formats
    '.key', '.pages', '.numbers',
    # Design files
    '.psd', '.ai', '.sketch', '.graffle', '.fig',
    # Video/Audio
    '.mp4', '.mov', '.m4a', '.mp3', '.wav', '.avi', '.mkv',
    # Archives (if you want content described)
    '.zip', '.tar', '.tar.gz', '.dmg',
}

# Binary types that must never be committed (DIP-0015 as amended 2026-10-16:
# no Git LFS, binaries stay gitignored). PDFs and images are an open question
# in the DIP, so they are not listed here; nor are office documents (XLSX is
# AI-readable in DIP-0015, the same tier as PDFs).
BINARY_EXTENSIONS = COMPANION_REQUIRED_EXTENSIONS | {'.pptx'}

# Directories never walked: dependency trees and build output. Nested git
# repositories are also skipped (see _walk) — they are checked as their own repos.
PRUNE_DIRS = {'node_modules', '.git', '__pycache__', '.venv', 'venv', 'target', 'dist', 'build'}

# Inbox freshness threshold (days)
INBOX_FRESHNESS_DAYS = 7

# Naming convention pattern (kebab-case)
VALID_NAME_PATTERN = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
INVALID_NAME_PATTERNS = [
    re.compile(r'[A-Z]'),  # CamelCase
    re.compile(r'\s'),      # Spaces
    re.compile(r'_'),       # Underscores (use hyphens)
]


# =============================================================================
# CHECKER IMPLEMENTATION
# =============================================================================

class StructuralIntegrityChecker:
    """
    Check structural integrity of a Datacore space.

    Args:
        space_path: Path to the space (e.g., ~/Data/0-personal/)
        quick_mode: If True, only run fast checks (skip binaries-in-git, wiki-links)
        companion_mode: 'index' (default) uses _media-index.md, 'strict' uses 1:1 companions
    """

    def __init__(self, space_path: Path, quick_mode: bool = False,
                 companion_mode: str = 'index'):
        self.space_path = Path(space_path).resolve()
        self.quick_mode = quick_mode
        self.companion_mode = companion_mode  # 'index' | 'strict'
        self.issues: List[Issue] = []
        self.space_type = self._detect_space_type()
        self.checks_run = 0

    def _walk(self, pattern: str = '*', root: Optional[Path] = None):
        """rglob() that skips PRUNE_DIRS and nested git repositories.

        A space can hold project checkouts with hundreds of thousands of
        dependency files (5-plur: ~1M), which made a full scan time out.
        """
        import fnmatch
        base = root or self.space_path
        for dirpath, dirnames, filenames in os.walk(base):
            current = Path(dirpath)
            keep = []
            for d in dirnames:
                if d in PRUNE_DIRS or (current / d / '.git').exists():
                    continue
                keep.append(d)
            dirnames[:] = keep
            for name in keep + filenames:
                if fnmatch.fnmatch(name, pattern):
                    yield current / name

    def _detect_space_type(self) -> Literal['personal', 'team']:
        """Detect if this is a personal or team space."""
        space_name = self.space_path.name
        if space_name.startswith('0-'):
            return 'personal'
        return 'team'

    def run_all_checks(self) -> AuditResult:
        """Run all structural integrity checks."""
        start_time = time.time()
        self.issues = []
        self.checks_run = 0

        # Quick mode checks (always run)
        self._check_folder_structure()
        self._check_companions()
        self._check_inbox_freshness()

        # Full mode checks
        if not self.quick_mode:
            self._check_naming_conventions()
            self._check_tracked_derived_files()
            self._check_binaries_in_git()
            self._check_empty_folders()
            self._check_wiki_links()
            self._check_duplicates()

        duration_ms = int((time.time() - start_time) * 1000)

        return AuditResult(
            space=self.space_path.name,
            space_type=self.space_type,
            issues=self.issues,
            checks_run=self.checks_run,
            duration_ms=duration_ms,
        )

    # -------------------------------------------------------------------------
    # Check: Folder Structure
    # -------------------------------------------------------------------------

    def _check_folder_structure(self):
        """Verify expected numbered folders exist."""
        self.checks_run += 1

        all_valid = PERSONAL_FOLDERS if self.space_type == 'personal' else TEAM_FOLDERS
        # Core folders that should always exist (archive/outbox are optional)
        required = {f for f in all_valid if f not in ('4-archive', '4-outbox')}
        existing_numbered = set()

        for item in self.space_path.iterdir():
            if item.is_dir() and item.name[0].isdigit():
                existing_numbered.add(item.name)

        # Check for missing required folders (not optional ones)
        for folder in required:
            if folder not in existing_numbered:
                self.issues.append(Issue(
                    severity='warning',
                    check_type='folder_structure',
                    path=self.space_path / folder,
                    message=f"Missing expected folder: {folder}/",
                    fix_suggestion=f"mkdir -p {self.space_path / folder}",
                    auto_fixable=True,
                ))

        # Check for unexpected numbered folders
        for folder in existing_numbered:
            if folder not in all_valid:
                self.issues.append(Issue(
                    severity='info',
                    check_type='folder_structure',
                    path=self.space_path / folder,
                    message=f"Non-standard numbered folder: {folder}/ (expected: {all_valid})",
                ))

        # Check ALL top-level entries against allowlist (DIP-0015)
        allowed_dirs = PERSONAL_ALLOWED_DIRS if self.space_type == 'personal' else TEAM_ALLOWED_DIRS
        grandfathered = _grandfathered(self.space_path)
        for item in self.space_path.iterdir():
            name = item.name
            if name in grandfathered and name not in allowed_dirs and name not in ALLOWED_ROOT_FILES:
                self.issues.append(Issue(
                    severity='info',
                    check_type='folder_structure',
                    path=item,
                    message=f"Known drift, grandfathered in .datacore/structure-allow: {name}",
                    fix_suggestion="Move to its DIP-0015 location, then delete the line from .datacore/structure-allow",
                ))
                continue
            if item.is_dir():
                if name not in allowed_dirs and not name.startswith('.'):
                    hint = ROGUE_FOLDER_HINTS.get(name, 'an appropriate location under 1-tracks/ or 3-knowledge/')
                    self.issues.append(Issue(
                        severity='warning',
                        check_type='folder_structure',
                        path=item,
                        message=f"Unexpected top-level folder: {name}/ — not in DIP-0015 allowlist. Consider moving to {hint}",
                        fix_suggestion=f"Move contents of {name}/ to {hint}",
                    ))
            elif item.is_file():
                if name not in ALLOWED_ROOT_FILES and not name.startswith('.'):
                    self.issues.append(Issue(
                        severity='warning',
                        check_type='folder_structure',
                        path=item,
                        message=f"Unexpected file at space root: {name} — should be in a semantic location",
                        fix_suggestion=f"Move {name} to appropriate folder (0-inbox/ if unsure)",
                    ))
            elif item.is_symlink():
                # Symlinks are allowed (e.g., contacts → 3-knowledge/reference)
                pass

    # -------------------------------------------------------------------------
    # Check: Companion Files
    # -------------------------------------------------------------------------

    def _check_companions(self):
        """Route to appropriate companion check based on mode."""
        if self.companion_mode == 'index':
            self._check_media_indexes()
        else:
            self._check_individual_companions()

    def _check_individual_companions(self):
        """Verify non-AI-readable files have individual companion markdown (strict mode)."""
        self.checks_run += 1

        # Find all files needing companions
        for ext in COMPANION_REQUIRED_EXTENSIONS:
            for file_path in self._walk(f'*{ext}'):
                # Skip hidden folders, .datacore, and node_modules
                parts = file_path.relative_to(self.space_path).parts
                if any(p.startswith('.') or p == 'node_modules' for p in parts):
                    continue

                # Check for companion
                companion_path = file_path.with_suffix('.md')
                if not companion_path.exists():
                    self.issues.append(Issue(
                        severity='warning',
                        check_type='companion',
                        path=file_path,
                        message=f"Missing companion for non-AI-readable file: {file_path.name}",
                        fix_suggestion=f"Create {companion_path.name} with summary of {file_path.name}",
                        auto_fixable=True,  # Can create stub
                    ))

    def _check_media_indexes(self):
        """Check folders have _media-index.md if they contain non-readable files (index mode)."""
        self.checks_run += 1

        # Find all folders with media files
        folders_with_media: Dict[Path, int] = {}

        for ext in COMPANION_REQUIRED_EXTENSIONS:
            for file_path in self._walk(f'*{ext}'):
                # Skip hidden folders, .datacore, and node_modules
                rel_path = file_path.relative_to(self.space_path)
                if any(p.startswith('.') or p == 'node_modules' for p in rel_path.parts):
                    continue

                folder = file_path.parent
                folders_with_media[folder] = folders_with_media.get(folder, 0) + 1

        # Check each folder for _media-index.md
        for folder, count in folders_with_media.items():
            # Determine if this folder should use recursive index
            rel_folder = folder.relative_to(self.space_path)
            is_inbox = any(p.startswith('0-inbox') for p in rel_folder.parts)

            # For inbox folders, check if parent has recursive index
            if is_inbox:
                # Find the top-level ingest folder
                parts = rel_folder.parts
                if len(parts) > 1:
                    # Check if parent already has _media-index.md
                    parent_index = self.space_path / parts[0] / parts[1] / '_media-index.md'
                    if parent_index.exists():
                        continue  # Parent has recursive index, skip this subfolder

            index_path = folder / '_media-index.md'
            if not index_path.exists():
                self.issues.append(Issue(
                    severity='warning',
                    check_type='media_index',
                    path=folder,
                    message=f"Missing _media-index.md for {count} non-readable file{'s' if count > 1 else ''}",
                    fix_suggestion=f"Run: python ~/.datacore/lib/media_index.py {folder}",
                    auto_fixable=True,
                ))

    # -------------------------------------------------------------------------
    # Check: Inbox Freshness
    # -------------------------------------------------------------------------

    def _check_inbox_freshness(self):
        """Check for stale items in 0-inbox/."""
        self.checks_run += 1

        inbox_path = self.space_path / '0-inbox'
        if not inbox_path.exists():
            return

        threshold = datetime.now() - timedelta(days=INBOX_FRESHNESS_DAYS)
        stale_count = 0
        oldest_item = None
        oldest_age = 0

        for item in self._walk('*', root=inbox_path):
            if item.is_file() and not item.name.startswith('.'):
                mtime = datetime.fromtimestamp(item.stat().st_mtime)
                if mtime < threshold:
                    stale_count += 1
                    age_days = (datetime.now() - mtime).days
                    if age_days > oldest_age:
                        oldest_age = age_days
                        oldest_item = item

        if stale_count > 0:
            self.issues.append(Issue(
                severity='warning',
                check_type='inbox',
                path=inbox_path,
                message=f"{stale_count} items older than {INBOX_FRESHNESS_DAYS} days in 0-inbox/ (oldest: {oldest_age} days)",
                fix_suggestion="Run /ingest or manually process inbox items",
            ))

    # -------------------------------------------------------------------------
    # Check: Naming Conventions
    # -------------------------------------------------------------------------

    def _check_naming_conventions(self):
        """Check for naming violations (CamelCase, spaces, etc.)."""
        self.checks_run += 1

        violations = []

        for item in self._walk('*'):
            # Skip hidden folders and node_modules
            rel_path = item.relative_to(self.space_path)
            if any(p.startswith('.') or p == 'node_modules' for p in rel_path.parts):
                continue

            name = item.stem  # Name without extension

            # Skip special files
            if name in ('CLAUDE', '_index', 'README', 'CANVAS'):
                continue

            # Check for violations
            has_uppercase = bool(re.search(r'[A-Z]', name))
            has_spaces = ' ' in item.name
            has_underscores = '_' in name and not name.startswith('_')

            if has_spaces:
                violations.append((item, 'spaces'))
            elif has_uppercase:
                violations.append((item, 'uppercase'))
            elif has_underscores:
                violations.append((item, 'underscores'))

        # Group violations to avoid spam
        if len(violations) > 10:
            self.issues.append(Issue(
                severity='info',
                check_type='naming',
                path=self.space_path,
                message=f"{len(violations)} naming violations found (spaces, uppercase, or underscores)",
                fix_suggestion="Use kebab-case: lowercase with hyphens",
            ))
        else:
            for item, violation_type in violations:
                suggested_name = self._suggest_kebab_case(item.name)
                self.issues.append(Issue(
                    severity='info',
                    check_type='naming',
                    path=item,
                    message=f"Naming violation ({violation_type}): {item.name}",
                    fix_suggestion=f"Rename to: {suggested_name}",
                    auto_fixable=True,
                ))

    def _suggest_kebab_case(self, name: str) -> str:
        """Convert a filename to kebab-case."""
        stem, ext = Path(name).stem, Path(name).suffix
        # Replace spaces and underscores with hyphens
        result = stem.replace(' ', '-').replace('_', '-')
        # Handle CamelCase
        result = re.sub(r'([a-z])([A-Z])', r'\1-\2', result)
        # Lowercase and clean up multiple hyphens
        result = re.sub(r'-+', '-', result.lower()).strip('-')
        return f"{result}{ext.lower()}"

    # -------------------------------------------------------------------------
    # Check: Tracked Derived Files
    # -------------------------------------------------------------------------

    def _check_tracked_derived_files(self):
        """Flag derived/regenerable files that must never be git-tracked (issue #29)."""
        self.checks_run += 1

        MUST_NOT_TRACK = [
            '.datacore/knowledge.db',
            '.datacore/knowledge.db-journal',
            '.datacore/knowledge.db-wal',
            '.datacore/knowledge.db-shm',
        ]

        try:
            result = subprocess.run(
                ['git', '-C', str(self.space_path), 'ls-files', '--'] + MUST_NOT_TRACK,
                capture_output=True, text=True, timeout=10
            )
            if result.returncode:
                raise OSError('repository index unavailable')
            tracked = [f.strip() for f in result.stdout.splitlines() if f.strip()]
            for f in tracked:
                self.issues.append(Issue(
                    severity='error',
                    check_type='tracked_derived',
                    path=self.space_path / f,
                    message=f"Derived file is git-tracked and will bloat the repo: {f}",
                    fix_suggestion=(
                        f"git rm --cached '{f}' && echo '{f}' >> .gitignore && git commit -m 'chore: untrack derived file'"
                    ),
                    auto_fixable=False,
                ))
        except (subprocess.TimeoutExpired, OSError):
            self.issues.append(Issue(
                severity='error', check_type='tracked_derived_unverified', path=self.space_path,
                message='Derived-file tracking could not be verified; check repository and Git availability.',
                auto_fixable=False,
            ))

    # -------------------------------------------------------------------------
    # Check: Binaries in Git
    # -------------------------------------------------------------------------

    def _check_binaries_in_git(self):
        """Flag binaries git tracks (DIP-0015 as amended: no Git LFS, binaries gitignored).

        Asks git for its index, so gitignored and untracked files are never
        reported -- the old size scan flagged ignored transcripts and build output.
        """
        self.checks_run += 1
        try:
            result = subprocess.run(
                ['git', '-C', str(self.space_path), 'ls-files', '-z'],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode:
                raise OSError('repository index unavailable')
        except (subprocess.TimeoutExpired, OSError):
            self.issues.append(Issue(
                severity='error', check_type='binary_in_git_unverified', path=self.space_path,
                message='Could not read the git index, so binaries in git were not checked.',
                auto_fixable=False,
            ))
            return
        for rel in filter(None, result.stdout.split('\0')):
            lower = rel.lower()
            if not any(lower.endswith(ext) for ext in BINARY_EXTENSIONS):
                continue
            path = self.space_path / rel
            if not path.is_file():
                continue
            self.issues.append(Issue(
                severity='error',
                check_type='binary_in_git',
                path=path,
                message=f"Binary committed to git: {rel}",
                fix_suggestion=f"git rm --cached '{rel}' and add its pattern to .gitignore (no Git LFS)",
                auto_fixable=False,
            ))

    # -------------------------------------------------------------------------
    # Check: Empty Folders
    # -------------------------------------------------------------------------

    def _check_empty_folders(self):
        """Find folders with no content (excluding hidden files)."""
        self.checks_run += 1

        empty_folders = []

        for folder in self._walk('*'):
            if not folder.is_dir():
                continue

            # Skip hidden folders and node_modules
            rel_path = folder.relative_to(self.space_path)
            if any(p.startswith('.') or p == 'node_modules' for p in rel_path.parts):
                continue

            # Check if empty (ignoring hidden files and .DS_Store)
            contents = [f for f in folder.iterdir()
                       if not f.name.startswith('.') and f.name != '.DS_Store']
            if not contents:
                empty_folders.append(folder)

        if empty_folders:
            if len(empty_folders) > 5:
                self.issues.append(Issue(
                    severity='info',
                    check_type='empty_folder',
                    path=self.space_path,
                    message=f"{len(empty_folders)} empty folders found",
                    fix_suggestion="Consider removing empty folders or adding _index.md",
                ))
            else:
                for folder in empty_folders:
                    self.issues.append(Issue(
                        severity='info',
                        check_type='empty_folder',
                        path=folder,
                        message=f"Empty folder: {folder.relative_to(self.space_path)}",
                        fix_suggestion=f"Remove with: rmdir {folder}",
                        auto_fixable=True,
                    ))

    # -------------------------------------------------------------------------
    # Check: Wiki-Link Integrity
    # -------------------------------------------------------------------------

    def _check_wiki_links(self):
        """Find broken wiki-links [[Target]] in markdown files."""
        self.checks_run += 1

        # Collect all valid link targets (markdown files without extension)
        valid_targets: Set[str] = set()
        for md_file in self._walk('*.md'):
            # Skip hidden folders and node_modules
            rel_path = md_file.relative_to(self.space_path)
            if any(p.startswith('.') or p == 'node_modules' for p in rel_path.parts):
                continue
            # Add stem as valid target
            valid_targets.add(md_file.stem.lower())
            # Also add full relative path without extension
            valid_targets.add(str(rel_path.with_suffix('')).lower())

        # Find all wiki-links and check if targets exist
        wiki_link_pattern = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]')
        broken_links = []

        for md_file in self._walk('*.md'):
            rel_path = md_file.relative_to(self.space_path)
            if any(p.startswith('.') or p == 'node_modules' for p in rel_path.parts):
                continue

            try:
                content = md_file.read_text(errors='ignore')
                for match in wiki_link_pattern.finditer(content):
                    target = match.group(1).strip().lower()
                    # Skip external links and anchors
                    if target.startswith('http') or target.startswith('#'):
                        continue
                    # Check if target exists
                    if target not in valid_targets:
                        broken_links.append((md_file, target))
            except Exception:
                pass  # Skip unreadable files

        # Report broken links (grouped to avoid spam)
        if len(broken_links) > 10:
            self.issues.append(Issue(
                severity='warning',
                check_type='wiki_link',
                path=self.space_path,
                message=f"{len(broken_links)} broken wiki-links found across {len(set(f for f, _ in broken_links))} files",
                fix_suggestion="Run `/structural-integrity report` for full list",
            ))
        else:
            for md_file, target in broken_links:
                self.issues.append(Issue(
                    severity='warning',
                    check_type='wiki_link',
                    path=md_file,
                    message=f"Broken wiki-link: [[{target}]]",
                    fix_suggestion=f"Create {target}.md or update link",
                ))

    # -------------------------------------------------------------------------
    # Check: Duplicate Files
    # -------------------------------------------------------------------------

    def _check_duplicates(self):
        """Find files with identical content in multiple locations."""
        self.checks_run += 1

        # Build hash map of files (skip large files and hidden)
        hash_to_files: Dict[str, List[Path]] = {}
        MAX_SIZE = 10 * 1024 * 1024  # 10MB limit for hashing

        for file_path in self._walk('*'):
            if not file_path.is_file():
                continue

            # Skip hidden folders, .datacore, and node_modules
            rel_path = file_path.relative_to(self.space_path)
            if any(p.startswith('.') or p == 'node_modules' for p in rel_path.parts):
                continue

            # Skip large files
            try:
                if file_path.stat().st_size > MAX_SIZE:
                    continue
                file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
                hash_to_files.setdefault(file_hash, []).append(file_path)
            except Exception:
                pass

        # Find duplicates
        duplicates = [(h, files) for h, files in hash_to_files.items() if len(files) > 1]

        if duplicates:
            total_dupes = sum(len(files) - 1 for _, files in duplicates)
            if total_dupes > 5:
                self.issues.append(Issue(
                    severity='info',
                    check_type='duplicate',
                    path=self.space_path,
                    message=f"{total_dupes} duplicate files found in {len(duplicates)} groups",
                    fix_suggestion="Review and consolidate duplicate files",
                ))
            else:
                for _, files in duplicates:
                    files_str = ', '.join(str(f.relative_to(self.space_path)) for f in files)
                    self.issues.append(Issue(
                        severity='info',
                        check_type='duplicate',
                        path=files[0],
                        message=f"Duplicate files: {files_str}",
                        fix_suggestion="Keep one copy, remove duplicates",
                    ))


# =============================================================================
# AUTO-FIX CAPABILITIES
# =============================================================================

@dataclass
class FixResult:
    """Result of attempting to fix an issue."""
    issue: Issue
    success: bool
    action_taken: str
    error: Optional[str] = None


class StructuralIntegrityFixer:
    """
    Attempt to automatically fix structural integrity issues.

    Only fixes issues marked as auto_fixable=True.
    """

    def __init__(self, space_path: Path, dry_run: bool = False):
        self.space_path = Path(space_path).resolve()
        self.dry_run = dry_run
        self.results: List[FixResult] = []

    def fix_issues(self, issues: List[Issue], confirm: bool = True) -> List[FixResult]:
        """
        Attempt to fix all auto-fixable issues.

        Args:
            issues: List of issues to fix
            confirm: If True, print what will be done but don't execute (dry run)

        Returns:
            List of fix results
        """
        fixable = [i for i in issues if i.auto_fixable]

        if not fixable:
            return []

        for issue in fixable:
            result = self._fix_issue(issue)
            self.results.append(result)

        return self.results

    def _fix_issue(self, issue: Issue) -> FixResult:
        """Attempt to fix a single issue."""
        fix_methods = {
            'folder_structure': self._fix_missing_folder,
            'companion': self._fix_missing_companion,
            'media_index': self._fix_missing_media_index,
            'naming': self._fix_naming,
            'empty_folder': self._fix_empty_folder,
        }

        method = fix_methods.get(issue.check_type)
        if not method:
            return FixResult(
                issue=issue,
                success=False,
                action_taken="No fix method available",
                error=f"Unknown check type: {issue.check_type}"
            )

        try:
            return method(issue)
        except Exception as e:
            return FixResult(
                issue=issue,
                success=False,
                action_taken="Fix attempted",
                error=str(e)
            )

    def _fix_missing_folder(self, issue: Issue) -> FixResult:
        """Create missing folder."""
        if self.dry_run:
            return FixResult(
                issue=issue,
                success=True,
                action_taken=f"Would create: {issue.path}"
            )

        issue.path.mkdir(parents=True, exist_ok=True)
        return FixResult(
            issue=issue,
            success=True,
            action_taken=f"Created folder: {issue.path}"
        )

    def _fix_missing_companion(self, issue: Issue) -> FixResult:
        """Create stub companion markdown file."""
        companion_path = issue.path.with_suffix('.md')
        source_name = issue.path.name
        source_ext = issue.path.suffix.lstrip('.')

        if self.dry_run:
            return FixResult(
                issue=issue,
                success=True,
                action_taken=f"Would create: {companion_path.name}"
            )

        # Create stub companion
        content = f"""---
type: document-companion
source: {source_name}
format: {source_ext}
created: {datetime.now().strftime('%Y-%m-%d')}
status: stub
ai_readable: false
---

# {issue.path.stem}

## Summary

TODO: Add summary of {source_name}

## Notes

- Created automatically by structural-integrity fix
- Please add meaningful description

## Related

- (Add related links here)
"""
        companion_path.write_text(content)
        return FixResult(
            issue=issue,
            success=True,
            action_taken=f"Created companion: {companion_path.name}"
        )

    def _fix_missing_media_index(self, issue: Issue) -> FixResult:
        """Generate _media-index.md for folder with non-readable files."""
        folder = issue.path
        index_path = folder / '_media-index.md'

        # Determine if recursive (for inbox folders)
        try:
            rel_folder = folder.relative_to(self.space_path)
            is_inbox = any(p.startswith('0-inbox') for p in rel_folder.parts)
        except ValueError:
            is_inbox = False

        if self.dry_run:
            mode = "recursive " if is_inbox else ""
            return FixResult(
                issue=issue,
                success=True,
                action_taken=f"Would create {mode}_media-index.md in {folder.name}"
            )

        # Import and generate
        try:
            import sys
            lib_path = Path(__file__).parent
            if str(lib_path) not in sys.path:
                sys.path.insert(0, str(lib_path))

            from media_index import generate_media_index
            content = generate_media_index(folder, recursive=is_inbox)

            if content:
                index_path.write_text(content)
                return FixResult(
                    issue=issue,
                    success=True,
                    action_taken=f"Created _media-index.md in {folder.name}"
                )
            else:
                return FixResult(
                    issue=issue,
                    success=False,
                    action_taken="No media files found",
                    error="generate_media_index returned empty content"
                )
        except ImportError as e:
            return FixResult(
                issue=issue,
                success=False,
                action_taken="Import failed",
                error=str(e)
            )

    def _fix_naming(self, issue: Issue) -> FixResult:
        """Rename file to kebab-case."""
        old_path = issue.path
        new_name = self._suggest_kebab_case(old_path.name)
        new_path = old_path.parent / new_name

        if new_path.exists():
            return FixResult(
                issue=issue,
                success=False,
                action_taken="Rename skipped",
                error=f"Target already exists: {new_name}"
            )

        if self.dry_run:
            return FixResult(
                issue=issue,
                success=True,
                action_taken=f"Would rename: {old_path.name} → {new_name}"
            )

        old_path.rename(new_path)
        return FixResult(
            issue=issue,
            success=True,
            action_taken=f"Renamed: {old_path.name} → {new_name}"
        )

    def _suggest_kebab_case(self, name: str) -> str:
        """Convert a filename to kebab-case."""
        stem, ext = Path(name).stem, Path(name).suffix
        result = stem.replace(' ', '-').replace('_', '-')
        result = re.sub(r'([a-z])([A-Z])', r'\1-\2', result)
        result = re.sub(r'-+', '-', result.lower()).strip('-')
        return f"{result}{ext.lower()}"

    def _fix_empty_folder(self, issue: Issue) -> FixResult:
        """Remove empty folder."""
        if self.dry_run:
            return FixResult(
                issue=issue,
                success=True,
                action_taken=f"Would remove: {issue.path}"
            )

        try:
            issue.path.rmdir()
            return FixResult(
                issue=issue,
                success=True,
                action_taken=f"Removed empty folder: {issue.path}"
            )
        except OSError as e:
            return FixResult(
                issue=issue,
                success=False,
                action_taken="Remove failed",
                error=str(e)
            )


def format_fix_results(results: List[FixResult]) -> str:
    """Format fix results for display."""
    if not results:
        return "No fixes applied."

    lines = [
        "FIX RESULTS",
        "=" * 11,
        "",
    ]

    success = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    if success:
        lines.append(f"✅ Fixed: {len(success)}")
        for r in success:
            lines.append(f"   - {r.action_taken}")

    if failed:
        lines.append(f"❌ Failed: {len(failed)}")
        for r in failed:
            lines.append(f"   - {r.issue.path.name}: {r.error}")

    return '\n'.join(lines)


# =============================================================================
# OUTPUT FORMATTING
# =============================================================================

def format_summary(result: AuditResult) -> str:
    """Format audit result as brief summary."""
    lines = [
        "STRUCTURAL INTEGRITY CHECK",
        "=" * 26,
        f"Space: {result.space} ({result.space_type})",
    ]

    if not result.passed:
        lines.append(f"Status: ❌ ISSUES FOUND")
    elif result.warnings:
        lines.append(f"Status: ⚠️ WARNINGS FOUND ({result.checks_run} checks in {result.duration_ms}ms)")
    else:
        lines.append(f"Status: ✅ PASSED ({result.checks_run} checks in {result.duration_ms}ms)")

    lines.append(f"Errors: {len(result.errors)} | Warnings: {len(result.warnings)} | Info: {len(result.infos)}")

    # Show top issues
    if result.issues:
        lines.append("")
        lines.append("Issues:")
        for issue in result.issues[:5]:  # Show first 5
            icon = {'error': '❌', 'warning': '⚠️', 'info': 'ℹ️'}[issue.severity]
            try:
                data_dir = Path(os.environ.get('DATACORE_ROOT', str(Path.home() / 'Data')))
                rel_path = issue.path.relative_to(data_dir)
            except ValueError:
                rel_path = issue.path.name
            lines.append(f"  {icon} {rel_path}: {issue.message}")

        if len(result.issues) > 5:
            lines.append(f"  ... and {len(result.issues) - 5} more")

    return '\n'.join(lines)


def format_detailed_report(result: AuditResult) -> str:
    """Format audit result as detailed report."""
    lines = [
        "# Structural Integrity Report",
        "",
        f"**Space:** {result.space}",
        f"**Type:** {result.space_type}",
        f"**Timestamp:** {result.timestamp}",
        f"**Duration:** {result.duration_ms}ms",
        f"**Checks Run:** {result.checks_run}",
        "",
        "## Summary",
        "",
        f"| Severity | Count |",
        f"|----------|-------|",
        f"| Errors | {len(result.errors)} |",
        f"| Warnings | {len(result.warnings)} |",
        f"| Info | {len(result.infos)} |",
        "",
    ]

    # Group by check type
    by_type: Dict[str, List[Issue]] = {}
    for issue in result.issues:
        by_type.setdefault(issue.check_type, []).append(issue)

    for check_type, issues in by_type.items():
        lines.append(f"## {check_type.replace('_', ' ').title()}")
        lines.append("")

        for issue in issues:
            icon = {'error': '❌', 'warning': '⚠️', 'info': 'ℹ️'}[issue.severity]
            lines.append(f"### {icon} {issue.message}")
            lines.append(f"**Path:** `{issue.path}`")
            if issue.fix_suggestion:
                lines.append(f"**Fix:** {issue.fix_suggestion}")
            if issue.auto_fixable:
                lines.append("**Auto-fixable:** Yes")
            lines.append("")

    return '\n'.join(lines)


def format_briefing_section(results: List[AuditResult]) -> str:
    """Format results for inclusion in daily briefing."""
    total_errors = sum(len(r.errors) for r in results)
    total_warnings = sum(len(r.warnings) for r in results)

    if total_errors == 0 and total_warnings == 0:
        return "✅ Structural integrity verified"

    lines = []

    if total_errors > 0:
        lines.append(f"🔴 **System Errors** ({total_errors})")
        for result in results:
            for issue in result.errors[:3]:
                lines.append(f"- {result.space}: {issue.message}")
        lines.append("")

    if total_warnings > 0:
        lines.append(f"⚠️ **Structural Warnings** ({total_warnings})")
        for result in results:
            for issue in result.warnings[:3]:
                lines.append(f"- {result.space}: {issue.message}")

    if total_errors + total_warnings > 6:
        lines.append(f"\n→ Run `/structural-integrity report` for full details")

    return '\n'.join(lines)


# =============================================================================
# CLI INTERFACE
# =============================================================================

def check_root_directory(data_root: Path) -> AuditResult:
    """Check the Datacore root directory for unexpected entries."""
    start_time = time.time()
    issues = []
    # Root should only contain numbered space dirs and system files/dirs
    root_allowed_dirs = {'.datacore', '.git', '.claude', '.lfs-cache', '.obsidian', '.github', '.superpowers', '.worktrees', 'docs', 'dips'}
    root_allowed_files = {'.gitignore', '.gitattributes', '.DS_Store', 'CLAUDE.md', 'CLAUDE.base.md', 'CLAUDE.org.md', 'CLAUDE.local.md', 'install.yaml', 'install.yaml.example', 'install.yaml.pm.example', 'datacore.lock.yaml', 'sync', 'README.md', 'LICENSE', 'CODEOWNERS', 'CHANGELOG.md', 'CONTRIBUTING.md', 'CODE_OF_CONDUCT.md', 'GETTING_STARTED.md', 'INSTALL.md', 'ROADMAP.md', 'SECURITY.md'}
    for item in sorted(data_root.iterdir()):
        name = item.name
        if name.startswith('.') and name in root_allowed_dirs:
            continue
        if item.is_dir():
            if name[0:1].isdigit():
                continue  # numbered space — checked separately
            if name in root_allowed_dirs:
                continue
            issues.append(Issue(severity='warning', check_type='folder_structure', path=item, message=f"Unexpected directory at Datacore root: {name}/ — root should only contain numbered spaces and system dirs", fix_suggestion=f"Move {name}/ into the appropriate space or remove"))
        elif item.is_file():
            if name in root_allowed_files:
                continue
            if name.startswith('.'):
                continue
            issues.append(Issue(severity='warning', check_type='folder_structure', path=item, message=f"Unexpected file at Datacore root: {name}", fix_suggestion=f"Move {name} into appropriate space"))
    duration_ms = int((time.time() - start_time) * 1000)
    return AuditResult(space='root', space_type='team', issues=issues, checks_run=1, duration_ms=duration_ms)


def check_all_spaces(data_root: Path, quick_mode: bool = True,
                     companion_mode: str = 'index') -> List[AuditResult]:
    """Check all spaces in Datacore, including root directory."""
    results = []

    # Check root directory first
    results.append(check_root_directory(data_root))

    for space_dir in sorted(data_root.iterdir()):
        if not space_dir.is_dir():
            continue
        if not space_dir.name[0].isdigit():
            continue
        if space_dir.name.startswith('.'):
            continue

        checker = StructuralIntegrityChecker(space_dir, quick_mode=quick_mode,
                                             companion_mode=companion_mode)
        results.append(checker.run_all_checks())

    return results


if __name__ == '__main__':
    import sys

    # Default to DATACORE_ROOT or ~/Data
    data_root = Path(os.environ.get('DATACORE_ROOT', str(Path.home() / 'Data')))

    # Parse flags
    quick = '--quick' in sys.argv or '-q' in sys.argv
    fix_mode = '--fix' in sys.argv or '-f' in sys.argv
    dry_run = '--dry-run' in sys.argv or '-n' in sys.argv
    report_mode = '--report' in sys.argv or '-r' in sys.argv
    strict_mode = '--strict' in sys.argv  # Use 1:1 companions instead of media indexes

    companion_mode = 'strict' if strict_mode else 'index'

    # Get positional argument (space path)
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    space_path = Path(args[0]) if args else None

    if space_path:
        # Check specific space
        checker = StructuralIntegrityChecker(space_path, quick_mode=quick,
                                             companion_mode=companion_mode)
        result = checker.run_all_checks()

        if report_mode:
            print(format_detailed_report(result))
        else:
            print(format_summary(result))

        # Fix mode
        if fix_mode and result.issues:
            fixable = [i for i in result.issues if i.auto_fixable]
            if fixable:
                print(f"\n{'DRY RUN: ' if dry_run else ''}Fixing {len(fixable)} auto-fixable issues...")
                fixer = StructuralIntegrityFixer(space_path, dry_run=dry_run)
                fix_results = fixer.fix_issues(result.issues)
                print(format_fix_results(fix_results))
            else:
                print("\nNo auto-fixable issues found.")
    else:
        # Check all spaces
        results = check_all_spaces(data_root, quick_mode=quick, companion_mode=companion_mode)
        for result in results:
            if report_mode:
                print(format_detailed_report(result))
            else:
                print(format_summary(result))
            print()

        # Fix mode for all spaces
        if fix_mode:
            for result in results:
                fixable = [i for i in result.issues if i.auto_fixable]
                if fixable:
                    print(f"\n{'DRY RUN: ' if dry_run else ''}Fixing {len(fixable)} issues in {result.space}...")
                    fixer = StructuralIntegrityFixer(data_root / result.space, dry_run=dry_run)
                    fix_results = fixer.fix_issues(result.issues)
                    print(format_fix_results(fix_results))
