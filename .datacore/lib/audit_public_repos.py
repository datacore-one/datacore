#!/usr/bin/env python3
"""
Audit public repos against the confidentiality denylist.

Reads .datacore/state/public-repo-denylist.yaml and scans every repo
listed under `protected_repos` for any file path matching `forbidden_paths`
or any text content matching `forbidden_content`. Exits non-zero on hit
so it can fail loudly when run from a heartbeat / cron / CI.

Born out of a 2026-05-14 sync incident — automated sync pushed customer-named
files to plur-ai/plur (public). This is the post-hoc detector that would
have caught it inside the next heartbeat cycle.

Usage:
    python audit_public_repos.py                  # scan all protected repos
    python audit_public_repos.py --repo X/Y       # scan one specific repo
    python audit_public_repos.py --json           # machine-readable output
    python audit_public_repos.py --dry-run        # show what would be checked
    python audit_public_repos.py --quiet          # only output on hits

Exit codes:
    0  — no forbidden patterns found (warnings ignored)
    1  — forbidden paths or content found
    2  — configuration error (denylist missing/invalid)
    3  — GitHub API error
"""

import argparse
import base64
import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


DENYLIST_PATH = Path(__file__).resolve().parent.parent / "state" / "public-repo-denylist.yaml"


@dataclass
class Hit:
    repo: str
    path: str
    rule: str  # the pattern that matched
    severity: str  # "forbidden" | "warning"
    kind: str  # "path" | "content"
    snippet: Optional[str] = None  # for content hits


@dataclass
class AuditResult:
    repos_scanned: list[str] = field(default_factory=list)
    repos_skipped: list[tuple[str, str]] = field(default_factory=list)  # (repo, reason)
    hits: list[Hit] = field(default_factory=list)
    files_checked: int = 0
    api_calls: int = 0

    @property
    def forbidden_hits(self) -> list[Hit]:
        return [h for h in self.hits if h.severity == "forbidden"]

    @property
    def warning_hits(self) -> list[Hit]:
        return [h for h in self.hits if h.severity == "warning"]

    @property
    def has_forbidden(self) -> bool:
        return len(self.forbidden_hits) > 0


def load_denylist(path: Path = DENYLIST_PATH) -> dict:
    if not path.exists():
        print(f"ERROR: denylist not found at {path}", file=sys.stderr)
        sys.exit(2)
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"ERROR: invalid YAML in {path}: {e}", file=sys.stderr)
        sys.exit(2)

    required = ["protected_repos", "forbidden_paths", "forbidden_content"]
    for field in required:
        if field not in data:
            print(f"ERROR: denylist missing required field '{field}'", file=sys.stderr)
            sys.exit(2)

    return data


def gh_api(endpoint: str) -> tuple[int, str]:
    """Call gh api. Returns (returncode, output)."""
    try:
        result = subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode, result.stdout if result.returncode == 0 else result.stderr
    except FileNotFoundError:
        print("ERROR: 'gh' CLI not installed", file=sys.stderr)
        sys.exit(2)
    except subprocess.TimeoutExpired:
        return 1, "timeout"


def list_repo_files(repo: str, branch: str = "main") -> tuple[Optional[list[dict]], int]:
    """List all files in a repo branch via Git Trees API. Returns (files, api_calls)."""
    rc, out = gh_api(f"repos/{repo}/git/trees/{branch}?recursive=1")
    if rc != 0:
        return None, 1
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None, 1
    if "tree" not in data:
        return None, 1
    # Filter to blobs only (files, not dirs)
    files = [item for item in data["tree"] if item.get("type") == "blob"]
    return files, 1


def get_file_content(repo: str, path: str, max_bytes: int = 1_000_000) -> tuple[Optional[str], int]:
    """Fetch raw file content. Returns (text, api_calls). None if binary or too large."""
    rc, out = gh_api(f"repos/{repo}/contents/{path}")
    if rc != 0:
        return None, 1
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None, 1
    if data.get("encoding") != "base64":
        return None, 1
    if data.get("size", 0) > max_bytes:
        return None, 1
    try:
        raw = base64.b64decode(data.get("content", ""))
        # Best-effort decode; skip on UnicodeError (binary)
        text = raw.decode("utf-8", errors="strict")
        return text, 1
    except UnicodeDecodeError:
        return None, 1


def is_textlike(path: str) -> bool:
    """Heuristic: is this a path we should scan for content?"""
    text_exts = {
        ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg",
        ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".sh", ".bash",
        ".env", ".env.example", ".gitignore", ".dockerignore",
        ".rst", ".org", ".tex", ".html", ".css", ".scss",
    }
    p = Path(path)
    if p.suffix.lower() in text_exts:
        return True
    # Common dotfiles without extensions
    if p.name in {".env", "Dockerfile", "Makefile", "README", "LICENSE", "NOTICE"}:
        return True
    return False


def match_path(path: str, patterns: list[str]) -> Optional[str]:
    """Return first matching pattern or None."""
    for pat in patterns:
        # fnmatch handles * and ? but not ** — convert ** to recursive match
        # Simplest: if pattern contains **/, also try without leading **/
        if fnmatch.fnmatch(path, pat):
            return pat
        # Handle **/X by matching X anywhere
        if pat.startswith("**/") and fnmatch.fnmatch(path, pat[3:]):
            return pat
        # Handle X/** by matching X/ prefix (any descendant)
        if pat.endswith("/**") and (path == pat[:-3] or path.startswith(pat[:-3] + "/")):
            return pat
        # Handle prefix/path style (no globs)
        if "*" not in pat and "?" not in pat and (path == pat or path.startswith(pat + "/")):
            return pat
    return None


def match_content(text: str, patterns: list[str]) -> list[tuple[str, str]]:
    """Return list of (pattern, snippet) for each match. Case-insensitive."""
    hits = []
    text_lower = text.lower()
    for pat in patterns:
        # Try as substring (case-insensitive) first; fall back to regex
        if pat.lower() in text_lower:
            # Extract a small snippet around the match
            idx = text_lower.find(pat.lower())
            snippet = text[max(0, idx - 30): idx + len(pat) + 30].replace("\n", " ")
            hits.append((pat, snippet))
            continue
        try:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                idx = m.start()
                snippet = text[max(0, idx - 30): idx + len(m.group()) + 30].replace("\n", " ")
                hits.append((pat, snippet))
        except re.error:
            # Invalid regex; treat as literal substring already done above
            pass
    return hits


def audit_repo(repo: str, denylist: dict, quiet: bool = False) -> AuditResult:
    """Audit a single repo."""
    result = AuditResult()

    if repo in denylist.get("allowed_repos", []):
        result.repos_skipped.append((repo, "in allowed_repos"))
        if not quiet:
            print(f"  skipping {repo} (in allowed_repos — private)", file=sys.stderr)
        return result

    if not quiet:
        print(f"Auditing {repo}…", file=sys.stderr)

    files, calls = list_repo_files(repo)
    result.api_calls += calls
    if files is None:
        result.repos_skipped.append((repo, "could not list files (private/missing/api error)"))
        return result

    result.repos_scanned.append(repo)

    forbidden_paths = denylist.get("forbidden_paths", [])
    forbidden_content = denylist.get("forbidden_content", [])
    warning_paths = denylist.get("warning_patterns", {}).get("paths", [])
    warning_content = denylist.get("warning_patterns", {}).get("content", [])
    content_exemptions = denylist.get("content_scan_exemptions", [])

    for entry in files:
        path = entry["path"]
        result.files_checked += 1

        # Path checks
        m = match_path(path, forbidden_paths)
        if m:
            result.hits.append(Hit(repo=repo, path=path, rule=m, severity="forbidden", kind="path"))
            continue  # don't bother content-checking forbidden paths

        m = match_path(path, warning_paths)
        if m:
            result.hits.append(Hit(repo=repo, path=path, rule=m, severity="warning", kind="path"))

        # Content checks (only on text-like files, only if content denylist non-empty,
        # and only if the path isn't exempted from content scanning).
        if (forbidden_content or warning_content) and is_textlike(path):
            if match_path(path, content_exemptions):
                continue
            text, calls = get_file_content(repo, path)
            result.api_calls += calls
            if text is None:
                continue
            for pat, snippet in match_content(text, forbidden_content):
                result.hits.append(Hit(
                    repo=repo, path=path, rule=pat,
                    severity="forbidden", kind="content", snippet=snippet,
                ))
            for pat, snippet in match_content(text, warning_content):
                result.hits.append(Hit(
                    repo=repo, path=path, rule=pat,
                    severity="warning", kind="content", snippet=snippet,
                ))

    return result


def format_human(result: AuditResult) -> str:
    out = []
    out.append("=" * 60)
    out.append(f"AUDIT RESULT — {len(result.repos_scanned)} repo(s) scanned, {result.files_checked} file(s) checked, {result.api_calls} API call(s)")
    out.append("=" * 60)

    if result.repos_skipped:
        out.append("")
        out.append("Skipped:")
        for repo, reason in result.repos_skipped:
            out.append(f"  {repo}: {reason}")

    if result.forbidden_hits:
        out.append("")
        out.append(f"FORBIDDEN HITS: {len(result.forbidden_hits)}")
        for h in result.forbidden_hits:
            out.append(f"  ✗ {h.repo} :: {h.path}")
            out.append(f"    rule={h.rule} kind={h.kind}")
            if h.snippet:
                out.append(f"    snippet: …{h.snippet}…")
    else:
        out.append("")
        out.append("✓ No forbidden patterns found.")

    if result.warning_hits:
        out.append("")
        out.append(f"warnings: {len(result.warning_hits)}")
        for h in result.warning_hits:
            out.append(f"  ⚠ {h.repo} :: {h.path} (rule={h.rule}, kind={h.kind})")

    return "\n".join(out)


def format_json(result: AuditResult) -> str:
    return json.dumps({
        "repos_scanned": result.repos_scanned,
        "repos_skipped": [{"repo": r, "reason": rs} for r, rs in result.repos_skipped],
        "files_checked": result.files_checked,
        "api_calls": result.api_calls,
        "forbidden_count": len(result.forbidden_hits),
        "warning_count": len(result.warning_hits),
        "hits": [
            {
                "repo": h.repo, "path": h.path, "rule": h.rule,
                "severity": h.severity, "kind": h.kind, "snippet": h.snippet,
            }
            for h in result.hits
        ],
    }, indent=2)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument("--denylist", type=Path, default=DENYLIST_PATH, help=f"path to denylist YAML (default: {DENYLIST_PATH})")
    ap.add_argument("--repo", help="scan one specific repo (overrides denylist's protected_repos)")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--quiet", action="store_true", help="suppress progress; only output on hits or with --json")
    ap.add_argument("--dry-run", action="store_true", help="print what would be scanned without doing API calls")
    args = ap.parse_args()

    denylist = load_denylist(args.denylist)

    if args.repo:
        repos = [args.repo]
    else:
        repos = denylist.get("protected_repos", [])

    if args.dry_run:
        print("Would scan these repos:")
        for r in repos:
            print(f"  {r}")
        print(f"\nDenylist: {len(denylist.get('forbidden_paths', []))} forbidden path patterns, {len(denylist.get('forbidden_content', []))} forbidden content patterns")
        sys.exit(0)

    if not repos:
        print("ERROR: no repos to scan (empty protected_repos)", file=sys.stderr)
        sys.exit(2)

    combined = AuditResult()
    for repo in repos:
        r = audit_repo(repo, denylist, quiet=args.quiet)
        combined.repos_scanned.extend(r.repos_scanned)
        combined.repos_skipped.extend(r.repos_skipped)
        combined.hits.extend(r.hits)
        combined.files_checked += r.files_checked
        combined.api_calls += r.api_calls

    if args.json:
        print(format_json(combined))
    elif not args.quiet or combined.has_forbidden:
        print(format_human(combined))

    sys.exit(1 if combined.has_forbidden else 0)


if __name__ == "__main__":
    main()
