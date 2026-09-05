#!/usr/bin/env python3
"""
gh_reconcile.py — org-vs-GitHub reconciliation.

Scans open/WAITING tasks in inbox.org and next_actions.org across all spaces
for GitHub PR/issue references. Checks live state via `gh api`. Marks tasks
DONE if the referent is unambiguously closed (issue) or merged (PR).

False-positive policy
---------------------
Only transitions to DONE when state is provably terminal:
  - pull request : merged_at is NOT None  (merged — not merely closed without merge)
  - issue        : state == "closed"
Open, draft, CHANGES_REQUESTED → no change. Never a false DONE.

Reference extraction
--------------------
Searched text for each task (conservative to minimise false positives):
  1. Heading title        — bare refs ("PR #NNN"), owner/repo#NNN, full URLs
  2. Tracking properties  — PR, LINK, ISSUE, GITHUB_ISSUE, GITHUB_PR, GITHUB_URL,
                            EPIC, BLOCKER, MERGE, PR_URL, ISSUE_URL
  3. Full GitHub URLs     — also extracted from any property value or body line
     (URLs are unambiguous enough to search everywhere)

Informational properties like CONTEXT, ACCEPTANCE_CRITERIA, BOOTSTRAP, RESULT
are NOT searched for bare refs to prevent accidental closures of tasks that
merely mention a PR for context.

Config
------
~/.datacore/config/gh-reconcile.yaml  (optional)
Allows overriding the per-space "default GitHub repo" used to resolve bare refs
like "PR #123".  Useful when a space's git remote differs from the repo its
tasks track (e.g. 5-plur space remote is plur-ai/plur-space, but the tasks
track plur-ai/plur).

Usage
-----
    python3 gh_reconcile.py [--data-dir DIR] [--dry-run] [--verbose]
    python3 gh_reconcile.py --verify-patterns   # show what refs would be found
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

log = logging.getLogger("gh_reconcile")

# ── States that are candidates for reconciliation ────────────────────────────
OPEN_STATES = frozenset({"TODO", "NEXT", "WAITING", "QUEUED"})

# ── Tracking properties: refs here are treated as "this task IS about the ref"
TRACKING_PROPS = frozenset({
    "PR", "LINK", "ISSUE", "GITHUB_ISSUE", "GITHUB_PR", "GITHUB_URL",
    "EPIC", "BLOCKER", "MERGE", "PR_URL", "ISSUE_URL",
})

# ── File-path properties: value is a path to read for GitHub refs ─────────────
FILE_PATH_PROPS = frozenset({
    "NIGHTSHIFT_OUTPUT",
})

# ── Informational properties: bare refs here are skipped ─────────────────────
SKIP_BARE_PROPS = frozenset({
    "CONTEXT", "ACCEPTANCE_CRITERIA", "BOOTSTRAP", "RESULT", "CANCEL_REASON",
    "DRAFT_NOTES", "STATUS_NOTE", "ACTION", "COMMUNITY_CHECK", "KEY_FILES",
    "TOOLS", "ROLE", "VENTURE", "EFFORT", "CREATED", "CLOSED", "ASSIGNEE",
    "ID", "SPACE", "ASSIGNEE", "NIGHTSHIFT_STATUS", "NIGHTSHIFT_RECONCILED",
    "NIGHTSHIFT_EXECUTOR", "NIGHTSHIFT_STARTED",
})

# ── GitHub URL patterns (unambiguous — always extract regardless of source) ──
RE_GH_PR_URL = re.compile(
    r"https://github\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)/pull/(\d+)"
)
RE_GH_ISSUE_URL = re.compile(
    r"https://github\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)/issues/(\d+)"
)

# ── Contextual bare-ref patterns (need space-repo to resolve) ────────────────
RE_PR_BARE = re.compile(r"\bPR\s*#(\d+)\b", re.IGNORECASE)
RE_ISSUE_BARE = re.compile(r"\bissue\s*#(\d+)\b", re.IGNORECASE)

# ── owner/repo#NNN  or  reponame#NNN  (e.g. plur-ai/plur#504, plur#842) ─────
RE_REPO_REF = re.compile(
    r"(?<![./])\b"                         # not preceded by . or /
    r"([a-zA-Z][a-zA-Z0-9_-]*"            # owner-or-repo  (no dots — avoids version strings)
    r"(?:/[a-zA-Z][a-zA-Z0-9_-]*)?"       # optional /repo
    r")#(\d+)\b"
)

# ── "REPONAME #NNN" — space before # (e.g. "PLUR #240", "enterprise #389") ───
# The existing RE_REPO_REF requires no space before #; this pattern catches the
# space-separated form used in task titles throughout Datacore.
RE_REPO_SPACE_REF = re.compile(
    r"(?<![./])\b([a-zA-Z][a-zA-Z0-9_-]+)\s+#(\d{2,})\b"
)

# ── Org heading: * or ** + state keyword ─────────────────────────────────────
RE_HEADING = re.compile(
    r"^(\*+)\s+"
    r"(TODO|NEXT|WAITING|QUEUED|WORKING|DONE|REVIEW|FAILED|CANCELLED|DEFERRED|EXECUTING)"
    r"(\s)"
)


# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GithubRef:
    owner: str
    repo: str
    num: int
    kind: str  # "pr" | "issue" | "unknown"

    @property
    def full_repo(self) -> str:
        return f"{self.owner}/{self.repo}"

    def __hash__(self):
        return hash((self.owner.lower(), self.repo.lower(), self.num, self.kind))

    def __eq__(self, other):
        return (
            self.owner.lower() == other.owner.lower()
            and self.repo.lower() == other.repo.lower()
            and self.num == other.num
            and self.kind == other.kind
        )


@dataclass
class OrgTask:
    heading_line: int          # 0-indexed line index in the file
    state: str
    title: str
    tags: List[str]
    props: Dict[str, str]      # prop name → raw value (stripped)
    prop_block_start: int      # index of :PROPERTIES: line, -1 if absent
    prop_block_end: int        # index of :END: line, -1 if absent
    body_lines: List[str]      # lines after the properties block


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

def load_config(data_dir: Path) -> Tuple[Dict[str, Tuple[str, str]], Dict[str, Dict[str, Tuple[str, str]]]]:
    """
    Load space→repo mapping from .datacore/config/gh-reconcile.yaml.

    Returns:
        primary  : {space_dirname: (owner, repo)}  — primary repo per space
        secondary: {space_dirname: {shortname: (owner, repo)}}  — extra repos
                   for resolving "REPONAME #NNN" bare refs (e.g. "enterprise #389")
    Falls back to git remotes for unconfigured spaces.
    """
    config_path = data_dir / ".datacore" / "config" / "gh-reconcile.yaml"
    primary: Dict[str, Tuple[str, str]] = {}
    secondary: Dict[str, Dict[str, Tuple[str, str]]] = {}

    if config_path.exists():
        try:
            text = config_path.read_text()
            section = None
            cur_space = None
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or not stripped:
                    continue
                if stripped == "space_repos:":
                    section = "space_repos"
                    continue
                if stripped == "extra_repos:":
                    section = "extra_repos"
                    cur_space = None
                    continue
                if section == "space_repos":
                    m = re.match(r"^(\S+?):\s+([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)", stripped)
                    if m:
                        space_name = m.group(1).strip("'\"")
                        repo_full = m.group(2).strip("'\"")
                        if "/" in repo_full:
                            owner, repo = repo_full.split("/", 1)
                            primary[space_name] = (owner, repo)
                    elif re.match(r"^\S+:", stripped):
                        section = None
                elif section == "extra_repos":
                    # Indented space name
                    space_m = re.match(r"^(\S+?):\s*$", stripped)
                    if space_m:
                        cur_space = space_m.group(1).strip("'\"")
                        secondary.setdefault(cur_space, {})
                        continue
                    # Indented repo entry under a space
                    repo_m = re.match(r"^\s+(\S+?):\s+([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)", line)
                    if repo_m and cur_space:
                        short = repo_m.group(1).strip("'\"").lower()
                        repo_full = repo_m.group(2).strip("'\"")
                        if "/" in repo_full:
                            o, r = repo_full.split("/", 1)
                            secondary.setdefault(cur_space, {})[short] = (o, r)
                    elif re.match(r"^\S+:", stripped):
                        section = None
                        cur_space = None
        except Exception as e:
            log.warning(f"Could not read gh-reconcile.yaml: {e}")

    return primary, secondary


# ─────────────────────────────────────────────────────────────────────────────
# Git helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_space_repo_from_remote(space_dir: Path) -> Optional[Tuple[str, str]]:
    """Get (owner, repo) from the git remote of a space directory."""
    r = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=space_dir, capture_output=True, text=True, timeout=10
    )
    if r.returncode != 0:
        return None
    url = r.stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+)/([^/.\s]+?)(?:\.git)?$", url)
    if m:
        return m.group(1), m.group(2)
    return None


def git_pull_ff(space_dir: Path) -> bool:
    """Fetch and fast-forward merge. Returns True on success."""
    r = subprocess.run(
        ["git", "fetch", "origin"],
        cwd=space_dir, capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        log.warning(f"  git fetch failed for {space_dir.name}: {r.stderr.strip()}")
        return False

    r = subprocess.run(
        ["git", "merge", "--ff-only", "origin/main"],
        cwd=space_dir, capture_output=True, text=True, timeout=30
    )
    if r.returncode != 0:
        # Try origin/master as fallback
        r2 = subprocess.run(
            ["git", "merge", "--ff-only", "origin/master"],
            cwd=space_dir, capture_output=True, text=True, timeout=30
        )
        if r2.returncode != 0:
            log.warning(f"  git merge --ff-only failed for {space_dir.name}: {r.stderr.strip()}")
            return False
    return True


def git_commit_push(space_dir: Path, changed_files: List[Path], message: str) -> bool:
    """Stage specific files, commit, and push. Returns True on success."""
    # Stage only the modified org files
    for f in changed_files:
        r = subprocess.run(
            ["git", "add", str(f)],
            cwd=space_dir, capture_output=True, text=True
        )
        if r.returncode != 0:
            log.error(f"  git add failed for {f}: {r.stderr.strip()}")
            return False

    r = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=space_dir, capture_output=True, text=True
    )
    if r.returncode != 0:
        log.error(f"  git commit failed for {space_dir.name}: {r.stderr.strip()}")
        return False

    r = subprocess.run(
        ["git", "push"],
        cwd=space_dir, capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        log.error(f"  git push failed for {space_dir.name}: {r.stderr.strip()}")
        return False

    return True


# ─────────────────────────────────────────────────────────────────────────────
# Org parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_org_tasks(content: str) -> List[OrgTask]:
    """Parse org content; return all tasks at any level in file order."""
    lines = content.split("\n")
    tasks: List[OrgTask] = []
    i = 0

    while i < len(lines):
        hm = RE_HEADING.match(lines[i])
        if not hm:
            i += 1
            continue

        level = len(hm.group(1))
        state = hm.group(2)

        # Extract title + tags from the rest of the heading line
        rest = lines[i][hm.end():]
        tag_match = re.search(r"\s+(:[:\w]+:)\s*$", rest)
        tags: List[str] = []
        if tag_match:
            raw_tags = tag_match.group(1).strip(":")
            tags = [t for t in raw_tags.split(":") if t]
            title = rest[: tag_match.start()].strip()
        else:
            title = rest.strip()
        # Strip priority cookies like [#A]
        title = re.sub(r"^\s*\[#[A-Z]\]\s*", "", title).strip()

        # Skip SCHEDULED / DEADLINE / CLOSED timestamp lines immediately after heading
        j = i + 1
        while j < len(lines) and re.match(r"^\s*(SCHEDULED|DEADLINE|CLOSED):", lines[j]):
            j += 1

        # Parse :PROPERTIES: drawer
        props: Dict[str, str] = {}
        prop_start = -1
        prop_end = -1
        cur_prop: Optional[str] = None

        if j < len(lines) and lines[j].strip() == ":PROPERTIES:":
            prop_start = j
            j += 1
            while j < len(lines) and lines[j].strip() != ":END:":
                pm = re.match(r"^\s*:(\w+):\s*(.*)$", lines[j])
                if pm:
                    cur_prop = pm.group(1)
                    val = pm.group(2).strip()
                    if val == "|":
                        props[cur_prop] = ""
                    else:
                        props[cur_prop] = val
                        cur_prop = None
                elif cur_prop is not None:
                    # Continuation line of a multiline | property
                    props[cur_prop] = props[cur_prop] + lines[j].rstrip() + "\n"
                j += 1
            if j < len(lines) and lines[j].strip() == ":END:":
                prop_end = j
                j += 1

        # Collect body lines (stop at next heading of same or higher level)
        body: List[str] = []
        while j < len(lines):
            nl = lines[j]
            if re.match(r"^\*{1," + str(level) + r"}\s", nl):
                break
            body.append(nl)
            j += 1

        tasks.append(OrgTask(
            heading_line=i,
            state=state,
            title=title,
            tags=tags,
            props=props,
            prop_block_start=prop_start,
            prop_block_end=prop_end,
            body_lines=body,
        ))
        i = j  # advance past the task body

    return tasks


# ─────────────────────────────────────────────────────────────────────────────
# Reference extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_urls_from(text: str) -> List[GithubRef]:
    """Extract full GitHub PR/issue URLs from any text (unambiguous)."""
    refs: List[GithubRef] = []
    for m in RE_GH_PR_URL.finditer(text):
        refs.append(GithubRef(m.group(1), m.group(2), int(m.group(3)), "pr"))
    for m in RE_GH_ISSUE_URL.finditer(text):
        refs.append(GithubRef(m.group(1), m.group(2), int(m.group(3)), "issue"))
    return refs


def _extract_bare_refs_from(
    text: str,
    space_owner: Optional[str],
    space_repo: Optional[str],
    extra_repos: Optional[Dict[str, Tuple[str, str]]] = None,
) -> List[GithubRef]:
    """Extract bare refs (PR #N, issue #N, owner/repo#N) from title or tracking props.

    extra_repos maps lowercase short names to (owner, repo) for resolving
    "REPONAME #NNN" style references beyond the primary space repo.
    E.g. {"enterprise": ("plur-ai", "enterprise")} resolves "enterprise #389".
    """
    refs: List[GithubRef] = []
    _extra = extra_repos or {}

    # owner/repo#NNN  or  reponame#NNN  (no space before #)
    for m in RE_REPO_REF.finditer(text):
        ref_repo_str = m.group(1)
        num = int(m.group(2))
        if "/" in ref_repo_str:
            owner_part, repo_part = ref_repo_str.split("/", 1)
            if re.search(r"\d+\.\d+", ref_repo_str):
                continue
            refs.append(GithubRef(owner_part, repo_part, num, "unknown"))
        else:
            lc = ref_repo_str.lower()
            if space_repo and lc == space_repo.lower() and space_owner:
                refs.append(GithubRef(space_owner, space_repo, num, "unknown"))
            elif lc in _extra:
                o, r = _extra[lc]
                refs.append(GithubRef(o, r, num, "unknown"))

    # "REPONAME #NNN" — space before # (e.g. "PLUR #240", "enterprise #389")
    for m in RE_REPO_SPACE_REF.finditer(text):
        word, num_str = m.group(1), m.group(2)
        num = int(num_str)
        lc = word.lower()
        if space_repo and lc == space_repo.lower() and space_owner:
            refs.append(GithubRef(space_owner, space_repo, num, "unknown"))
        elif lc in _extra:
            o, r = _extra[lc]
            refs.append(GithubRef(o, r, num, "unknown"))

    # PR #NNN
    if space_owner and space_repo:
        for m in RE_PR_BARE.finditer(text):
            refs.append(GithubRef(space_owner, space_repo, int(m.group(1)), "pr"))

    # issue #NNN
    if space_owner and space_repo:
        for m in RE_ISSUE_BARE.finditer(text):
            refs.append(GithubRef(space_owner, space_repo, int(m.group(1)), "issue"))

    return refs


def _read_nightshift_output_refs(
    file_path: str,
    space_owner: Optional[str],
    space_repo: Optional[str],
) -> List[GithubRef]:
    """Read a NIGHTSHIFT_OUTPUT file and extract GitHub PR/issue URLs from it.

    This allows Review tasks to be auto-closed even if the output file was
    never explicitly linked with a PR_URL property.  Returns [] if the file
    is inaccessible or contains no GitHub refs.
    """
    try:
        content = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return []
    return _extract_urls_from(content)


# Archive subdirectories (relative to a space root) where nightshift outputs land
# after processing.  Order matters: check most specific first.
_NIGHTSHIFT_ARCHIVE_DIRS = [
    "4-archive/nightshift",           # outbox move (yearly/monthly subdirs)
    "0-inbox/_archive/nightshift-applied",  # route_dev in-place archive
]


def check_nightshift_output_archived(
    file_path: str,
    data_dir: Path,
    output_index: Optional[Dict[str, str]] = None,
) -> Optional[Dict]:
    """
    Check whether a NIGHTSHIFT_OUTPUT file was archived after processing.

    Returns a terminal result dict when the artifact is confirmed archived,
    or None when the file is still in-inbox (not yet processed) or not found
    in any known archive location.

    output_index — optional pre-built {filename: full_path} map for efficiency.
    If absent a live filesystem scan is performed (slow for many tasks).
    """
    orig = Path(file_path)
    if orig.exists():
        return None  # Still in 0-inbox — human review is still pending

    stem = orig.name
    found_at: Optional[str] = None

    if output_index is not None:
        found_at = output_index.get(stem)
    else:
        # Fallback: scan all spaces for the file in archive locations
        for space in sorted(data_dir.iterdir()):
            if not (space.is_dir() and space.name and space.name[0].isdigit()):
                continue
            for rel in _NIGHTSHIFT_ARCHIVE_DIRS:
                archive_dir = space / rel
                if not archive_dir.is_dir():
                    continue
                # Files may be nested one level deep (YYYY-MM/ subdirs)
                for candidate in archive_dir.rglob(stem):
                    found_at = str(candidate)
                    break
            if found_at:
                break

    if found_at:
        return {
            "terminal": True,
            "kind": "nightshift_output",
            "closed_at": None,
            "reason": f"Nightshift output archived: {found_at}",
        }
    return None  # Not in any archive — genuinely missing, skip


def build_output_index(data_dir: Path) -> Dict[str, str]:
    """
    Build a {filename: full_path} index of all nightshift output files found
    in archive locations across every space.  Call once per reconcile run.
    """
    index: Dict[str, str] = {}
    for space in sorted(data_dir.iterdir()):
        if not (space.is_dir() and space.name and space.name[0].isdigit()):
            continue
        for rel in _NIGHTSHIFT_ARCHIVE_DIRS:
            archive_dir = space / rel
            if not archive_dir.is_dir():
                continue
            for f in archive_dir.rglob("nightshift-exec-*.md"):
                index.setdefault(f.name, str(f))
    return index


def extract_github_refs(
    task: OrgTask,
    space_owner: Optional[str],
    space_repo: Optional[str],
    extra_repos: Optional[Dict[str, Tuple[str, str]]] = None,
) -> List[GithubRef]:
    """
    Extract all resolvable GitHub refs from a task using conservative rules:
    - Full URLs : everywhere (title + all props + body)
    - Bare refs : title + tracking properties only
    - NIGHTSHIFT_OUTPUT : full file contents searched for GitHub URLs
    """
    seen: Set[GithubRef] = set()
    refs: List[GithubRef] = []

    def add(new_refs: List[GithubRef]) -> None:
        for r in new_refs:
            if r not in seen:
                seen.add(r)
                refs.append(r)

    # ── 1. Full GitHub URLs from EVERYWHERE ───────────────────────────────────
    all_text = task.title + "\n"
    for v in task.props.values():
        all_text += v + "\n"
    all_text += "\n".join(task.body_lines) + "\n"
    add(_extract_urls_from(all_text))

    # ── 2. Bare refs from title ───────────────────────────────────────────────
    add(_extract_bare_refs_from(task.title, space_owner, space_repo, extra_repos))

    # ── 3. Bare refs from tracking properties ─────────────────────────────────
    for prop_name, prop_val in task.props.items():
        if prop_name.upper() in TRACKING_PROPS:
            add(_extract_bare_refs_from(prop_val, space_owner, space_repo, extra_repos))

    # ── 4. NIGHTSHIFT_OUTPUT file — read and search for GitHub URLs ────────────
    for prop_name, prop_val in task.props.items():
        if prop_name.upper() in FILE_PATH_PROPS and prop_val.strip():
            add(_read_nightshift_output_refs(prop_val.strip(), space_owner, space_repo))

    return refs


# ─────────────────────────────────────────────────────────────────────────────
# GitHub API calls
# ─────────────────────────────────────────────────────────────────────────────

_api_cache: Dict[str, Optional[Dict]] = {}


def check_github_ref(ref: GithubRef) -> Optional[Dict]:
    """
    Check state of a GitHub PR or issue via `gh api`.

    Returns dict with keys:
      terminal  : bool  — True if definitively merged/closed
      kind      : str   — "pr" or "issue"
      closed_at : str|None
      reason    : str   — human-readable description

    Returns None on auth error or API failure.
    """
    cache_key = f"{ref.owner}/{ref.repo}/{ref.num}/{ref.kind}"
    if cache_key in _api_cache:
        return _api_cache[cache_key]

    result = _check_github_ref_uncached(ref)
    _api_cache[cache_key] = result
    return result


def _check_github_ref_uncached(ref: GithubRef) -> Optional[Dict]:
    try:
        if ref.kind == "pr":
            r = subprocess.run(
                ["gh", "api",
                 f"repos/{ref.owner}/{ref.repo}/pulls/{ref.num}",
                 "--jq", "{state: .state, merged_at: .merged_at, merged: .merged}"],
                capture_output=True, text=True, timeout=30
            )
            if r.returncode != 0:
                log.debug(f"gh api pull/{ref.num} failed: {r.stderr.strip()[:120]}")
                return None
            data = json.loads(r.stdout)
            merged = data.get("merged_at") is not None or data.get("merged") is True
            return {
                "terminal": merged,
                "kind": "pr",
                "closed_at": data.get("merged_at"),
                "reason": (
                    f"PR {ref.full_repo}#{ref.num} merged"
                    if merged
                    else f"PR {ref.full_repo}#{ref.num} not yet merged "
                         f"(state={data.get('state')})"
                ),
            }

        elif ref.kind == "issue":
            r = subprocess.run(
                ["gh", "api",
                 f"repos/{ref.owner}/{ref.repo}/issues/{ref.num}",
                 "--jq", "{state: .state, closed_at: .closed_at, pull_request: .pull_request}"],
                capture_output=True, text=True, timeout=30
            )
            if r.returncode != 0:
                log.debug(f"gh api issue/{ref.num} failed: {r.stderr.strip()[:120]}")
                return None
            data = json.loads(r.stdout)
            if data.get("pull_request"):
                # GitHub issue endpoint covers PRs; re-check as PR for merged_at
                return _check_github_ref_uncached(
                    GithubRef(ref.owner, ref.repo, ref.num, "pr")
                )
            closed = data.get("state") == "closed"
            return {
                "terminal": closed,
                "kind": "issue",
                "closed_at": data.get("closed_at"),
                "reason": (
                    f"Issue {ref.full_repo}#{ref.num} closed"
                    if closed
                    else f"Issue {ref.full_repo}#{ref.num} still open"
                ),
            }

        else:  # "unknown" — try issues endpoint first (works for both PRs and issues)
            r = subprocess.run(
                ["gh", "api",
                 f"repos/{ref.owner}/{ref.repo}/issues/{ref.num}",
                 "--jq", "{state: .state, closed_at: .closed_at, pull_request: .pull_request}"],
                capture_output=True, text=True, timeout=30
            )
            if r.returncode != 0:
                log.debug(f"gh api issues/{ref.num} failed: {r.stderr.strip()[:120]}")
                return None
            data = json.loads(r.stdout)
            if data.get("pull_request"):
                return _check_github_ref_uncached(
                    GithubRef(ref.owner, ref.repo, ref.num, "pr")
                )
            closed = data.get("state") == "closed"
            return {
                "terminal": closed,
                "kind": "issue",
                "closed_at": data.get("closed_at"),
                "reason": (
                    f"Issue {ref.full_repo}#{ref.num} closed"
                    if closed
                    else f"Issue {ref.full_repo}#{ref.num} still open"
                ),
            }

    except subprocess.TimeoutExpired:
        log.warning(f"Timeout checking {ref.full_repo}#{ref.num}")
        return None
    except json.JSONDecodeError as e:
        log.warning(f"JSON parse error for {ref.full_repo}#{ref.num}: {e}")
        return None
    except Exception as e:
        log.warning(f"Unexpected error checking {ref.full_repo}#{ref.num}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Org file modification
# ─────────────────────────────────────────────────────────────────────────────

def today_org_ts() -> str:
    today = date.today()
    return f"[{today.strftime('%Y-%m-%d')} {today.strftime('%a')}]"


def mark_task_done(
    lines: List[str],
    task: OrgTask,
    reason: str,
    _closed_at: Optional[str],
) -> List[str]:
    """
    Return a new lines list with the task marked DONE and reconcile metadata added.
    Safe to call in descending heading_line order (bottom-up) — modifications
    to lower tasks don't affect line indexes of higher ones.
    """
    lines = list(lines)
    ts = today_org_ts()

    # 1. Change state keyword on heading line
    lines[task.heading_line] = RE_HEADING.sub(
        lambda m: m.group(1) + " DONE " + m.group(3)[0],
        lines[task.heading_line],
        count=1,
    )

    # 2. Build new property lines to inject
    new_props: List[str] = []
    if "CLOSED" not in task.props:
        new_props.append(f"  :CLOSED: {ts}")

    # Truncate reason to one clean line for RESULT
    reason_line = reason.replace("\n", " ").strip()
    if len(reason_line) > 160:
        reason_line = reason_line[:157] + "..."
    new_props.append(f"  :RESULT: Auto-closed by gh-reconcile: {reason_line}.")
    new_props.append(f"  :NIGHTSHIFT_RECONCILED: {ts}")

    # 3. Insert into existing drawer or create one
    if task.prop_block_end >= 0:
        insert_at = task.prop_block_end  # insert before :END:
        for offset, prop_line in enumerate(new_props):
            lines.insert(insert_at + offset, prop_line)
    else:
        # No properties drawer — create one right after the heading line
        # (after any SCHEDULED/DEADLINE/CLOSED lines)
        insert_at = task.heading_line + 1
        while insert_at < len(lines) and re.match(
            r"^\s*(SCHEDULED|DEADLINE|CLOSED):", lines[insert_at]
        ):
            insert_at += 1
        block = ["  :PROPERTIES:"] + new_props + ["  :END:"]
        for offset, bl in enumerate(block):
            lines.insert(insert_at + offset, bl)

    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Core reconciliation
# ─────────────────────────────────────────────────────────────────────────────

def reconcile_file(
    org_file: Path,
    space_owner: Optional[str],
    space_repo: Optional[str],
    dry_run: bool,
    extra_repos: Optional[Dict[str, Tuple[str, str]]] = None,
    data_dir: Optional[Path] = None,
    output_index: Optional[Dict[str, str]] = None,
) -> int:
    """
    Reconcile one org file. Returns number of tasks closed.
    Modifies the file in-place if dry_run is False.

    data_dir + output_index enable the NIGHTSHIFT_OUTPUT archive check:
    if the task's output file was moved to the nightshift archive the task
    is marked DONE even with no GitHub ref.
    """
    content = org_file.read_text(encoding="utf-8")
    lines = content.split("\n")
    tasks = parse_org_tasks(content)
    open_tasks = [t for t in tasks if t.state in OPEN_STATES]

    if not open_tasks:
        return 0

    log.debug(f"    {org_file.name}: {len(open_tasks)} open task(s) to inspect")

    to_close: List[Tuple[OrgTask, str, Optional[str]]] = []

    for task in open_tasks:
        # ── Path A: GitHub ref check ──────────────────────────────────────────
        refs = extract_github_refs(task, space_owner, space_repo, extra_repos)
        if refs:
            log.debug(f"      Checking task '{task.title[:70]}' — {len(refs)} ref(s)")

            terminal_refs: List[Tuple[GithubRef, Dict]] = []
            open_refs: List[Tuple[GithubRef, Dict]] = []

            for ref in refs:
                result = check_github_ref(ref)
                if result is None:
                    continue
                if result["terminal"]:
                    terminal_refs.append((ref, result))
                else:
                    open_refs.append((ref, result))

            if terminal_refs and not open_refs:
                reason = "; ".join(r["reason"] for _, r in terminal_refs)
                closed_at = terminal_refs[0][1].get("closed_at")
                log.info(f"      CLOSE: '{task.title[:70]}' — {reason}")
                to_close.append((task, reason, closed_at))
                continue  # No need to check archive path

            if terminal_refs and open_refs:
                log.info(
                    f"      Skipping '{task.title[:60]}' — terminal refs found but "
                    f"also {len(open_refs)} open ref(s)"
                )
                continue

        # ── Path B: NIGHTSHIFT_OUTPUT archive check ───────────────────────────
        # Close Review: tasks whose output file has been processed and archived.
        # Only runs when data_dir is provided (not available in verify-patterns mode).
        if data_dir is None:
            continue
        ns_path = task.props.get("NIGHTSHIFT_OUTPUT", "").strip()
        if not ns_path:
            continue
        archive_result = check_nightshift_output_archived(ns_path, data_dir, output_index)
        if archive_result:
            log.info(
                f"      CLOSE (archived output): '{task.title[:70]}' — "
                f"{archive_result['reason']}"
            )
            to_close.append((task, archive_result["reason"], None))

    if not to_close:
        return 0

    if dry_run:
        log.info(f"    [dry-run] Would close {len(to_close)} task(s) in {org_file.name}")
        return len(to_close)

    # Apply modifications bottom-up to preserve line numbers
    for task, reason, closed_at in sorted(
        to_close, key=lambda x: x[0].heading_line, reverse=True
    ):
        lines = mark_task_done(lines, task, reason, closed_at)

    try:
        org_file.write_text("\n".join(lines), encoding="utf-8")
    except PermissionError:
        log.warning(f"    Permission denied writing {org_file} — skipping (file may be root-owned)")
        return 0
    log.info(f"    Wrote {org_file.name} ({len(to_close)} task(s) closed)")
    return len(to_close)


def reconcile_all(data_dir: Path, dry_run: bool) -> int:
    """Reconcile all spaces. Returns total tasks closed."""
    # Verify gh auth first — fail loudly, never silently return 0
    r = subprocess.run(
        ["gh", "auth", "status"], capture_output=True, text=True, timeout=15
    )
    if r.returncode != 0:
        log.error("GitHub CLI not authenticated — gh-reconcile cannot run")
        log.error(r.stderr.strip())
        return -1  # Distinct from 0 (no tasks closed)

    primary_config, secondary_config = load_config(data_dir)

    # Pre-build nightshift output index for archive-aware NIGHTSHIFT_OUTPUT checks.
    log.debug("Building nightshift output index...")
    output_index = build_output_index(data_dir)
    log.debug(f"  {len(output_index)} archived nightshift outputs indexed")

    total_closed = 0

    # Discover spaces (e.g. 0-personal, 1-datafund …) — skip archive dirs
    space_dirs = sorted(
        d for d in data_dir.iterdir()
        if d.is_dir()
        and re.match(r"^\d+-", d.name)
        and not d.name.endswith("-archive")
        and not d.name.endswith(".git")
        and (d / "org").is_dir()
    )

    for space_dir in space_dirs:
        org_dir = space_dir / "org"
        org_files = [
            f for f in [org_dir / "inbox.org", org_dir / "next_actions.org"]
            if f.exists()
        ]
        if not org_files:
            continue

        log.info(f"\nSpace: {space_dir.name}")

        # Resolve the space's primary GitHub repo
        if space_dir.name in primary_config:
            space_owner, space_repo = primary_config[space_dir.name]
            log.debug(f"  repo (config): {space_owner}/{space_repo}")
        else:
            remote = get_space_repo_from_remote(space_dir)
            if remote:
                space_owner, space_repo = remote
                log.debug(f"  repo (remote): {space_owner}/{space_repo}")
            else:
                space_owner = space_repo = None
                log.debug("  repo: unknown — bare refs won't resolve")

        extra_repos = secondary_config.get(space_dir.name)

        # Pull before modifying
        if not dry_run and (space_dir / ".git").exists():
            git_pull_ff(space_dir)

        space_closed = 0
        changed_files: List[Path] = []
        for org_file in org_files:
            n = reconcile_file(
                org_file, space_owner, space_repo, dry_run, extra_repos,
                data_dir=data_dir, output_index=output_index,
            )
            if n > 0:
                space_closed += n
                changed_files.append(org_file)

        total_closed += space_closed

        if space_closed > 0 and not dry_run and changed_files:
            today_str = date.today().isoformat()
            msg = (
                f"nightshift: gh-reconcile {today_str} — "
                f"{space_closed} task(s) auto-closed"
            )
            if git_commit_push(space_dir, changed_files, msg):
                log.info(f"  Committed and pushed {space_dir.name}")
            else:
                log.error(f"  Failed to commit/push {space_dir.name}")

    return total_closed


# ─────────────────────────────────────────────────────────────────────────────
# Verify-patterns mode (dry-run showing ref extraction only, no API calls)
# ─────────────────────────────────────────────────────────────────────────────

def verify_patterns(data_dir: Path) -> None:
    """Print what refs would be extracted per task (no API calls)."""
    primary_config, secondary_config = load_config(data_dir)
    space_dirs = sorted(
        d for d in data_dir.iterdir()
        if d.is_dir()
        and re.match(r"^\d+-", d.name)
        and not d.name.endswith("-archive")
        and (d / "org").is_dir()
    )

    for space_dir in space_dirs:
        space_owner = space_repo = None
        if space_dir.name in primary_config:
            space_owner, space_repo = primary_config[space_dir.name]
        else:
            remote = get_space_repo_from_remote(space_dir)
            if remote:
                space_owner, space_repo = remote
        extra_repos = secondary_config.get(space_dir.name)

        for fname in ["inbox.org", "next_actions.org"]:
            org_file = space_dir / "org" / fname
            if not org_file.exists():
                continue
            tasks = parse_org_tasks(org_file.read_text(encoding="utf-8"))
            open_tasks = [t for t in tasks if t.state in OPEN_STATES]
            for task in open_tasks:
                refs = extract_github_refs(task, space_owner, space_repo, extra_repos)
                if refs:
                    print(
                        f"[{space_dir.name}/{fname}:{task.heading_line+1}] "
                        f"{task.state} {task.title[:60]}"
                    )
                    for ref in refs:
                        print(f"  → {ref.kind:7s} {ref.full_repo}#{ref.num}")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--data-dir", type=Path,
        default=Path.home() / "Data",
        help="Root data directory (default: ~/Data)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without writing files or committing",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--verify-patterns", action="store_true",
        help="Print GitHub refs found per task (no API calls, no file changes)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if not args.data_dir.exists():
        log.error(f"Data directory not found: {args.data_dir}")
        return 1

    if args.verify_patterns:
        verify_patterns(args.data_dir)
        return 0

    total = reconcile_all(args.data_dir, args.dry_run)
    if total < 0:
        print("\ngh-reconcile: FAILED — GitHub auth unavailable")
        return 1
    suffix = " (dry-run)" if args.dry_run else ""
    print(f"\ngh-reconcile complete: {total} task(s) closed{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
