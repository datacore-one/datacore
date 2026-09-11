#!/usr/bin/env python3
"""Pre-push policy scanner for protected public repos.

Called by .datacore/githooks/pre-push with a list of commit SHAs (the
outgoing range) on stdin. Applies three layers of policy from
.datacore/config/public-repo-denylist.yaml:

1. Path denylist   — forbidden_paths / warning_patterns.paths globs
                     against every added/modified path in the range.
2. Content scan    — forbidden_content regexes (case-insensitive)
                     against complete changed Git blobs in the range, plus
                     patterns from the optional PRIVATE customer
                     denylist (private_patterns_file). The private file
                     is read at runtime and must never be committed.
3. New-file gate   — newly tracked (status A) paths under .datacore/
                     must match an allowed category; sensitive
                     categories (state/, env/, cos/ non-example,
                     modules/*/data/, private/) are categorically
                     rejected.

Exit codes: 0 = clean, 1 = policy violation (details on stderr),
2 = scanner error (caller should FAIL CLOSED for protected repos).
"""

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

from git_privacy import changed_paths, git_bytes, tree_entries

DEFAULT_DENYLIST = os.path.join(
    os.environ.get("DATA_DIR", os.path.expanduser("~/Data")),
    ".datacore/config/public-repo-denylist.yaml",
)

# --- P1-3: .datacore new-file policy (overridable via denylist yaml) --------
# Checked only for paths with git status 'A' (newly tracked) that live under
# a .datacore/ directory. Reject globs win over allow globs.
DATACORE_NEW_FILE_REJECT = [
    ".datacore/modules/*/data/**",
    ".datacore/state/**",
    ".datacore/env/**",
    ".datacore/private/**",
    ".datacore/secrets/**",
    # cos/* non-example is handled specially below (allow *.example only)
    ".datacore/cos/**",
]
DATACORE_NEW_FILE_ALLOW = [
    # core categories
    ".datacore/agents/**",
    ".datacore/commands/**",
    ".datacore/lib/**",
    ".datacore/dips/**",
    ".datacore/specs/**",
    ".datacore/templates/**",
    ".datacore/registry/**",
    ".datacore/docs/**",
    ".datacore/datacore-docs/**",
    ".datacore/workflows/**",
    ".datacore/hooks/**",
    ".datacore/githooks/**",
    # terminal entry points: thin wrappers that exec a lib/ script (bin/creds
    # is `exec creds.py "$@"`). Same class as lib/; nothing lives here that
    # could not live in lib/. Added 2026-09-03 after the audit branch was
    # refused on this one file for two days.
    ".datacore/bin/**",
    ".datacore/config/**",
    ".datacore/tests/**",
    ".datacore/skills/**",
    ".datacore/cos/*.example",
    # Retired agent/command definitions. Same content class as
    # .datacore/agents/** above — they are moved here, not newly written, to
    # get them out of the harness-scanned tree (.claude symlinks to .datacore
    # and scans agents/** recursively, so _deprecated/ defs were loading into
    # every session).
    ".datacore/4-archive/**",
    # single-level files directly in .datacore/ (registries, manifests)
    ".datacore/*",
    # module-internal categories
    ".datacore/modules/*/agents/**",
    ".datacore/modules/*/commands/**",
    ".datacore/modules/*/lib/**",
    ".datacore/modules/*/skills/**",
    ".datacore/modules/*/templates/**",
    ".datacore/modules/*/docs/**",
    ".datacore/modules/*/tests/**",
    ".datacore/modules/*/tools/**",
    # app-tools/ is the datacore-app counterpart of tools/ — decisions/ and
    # goals/ each ship tools/index.js AND app-tools/index.mjs. extension/ is
    # browser-extension source (manifest, popup, background, icons) for
    # tab-capture. Both are code-only by construction, exactly as lib/ is; the
    # categorical rejects (state/, env/, secrets/, private/, modules/*/data/)
    # and content scanning still apply inside them. Added 2026-08-10 when these
    # modules moved into core.
    ".datacore/modules/*/app-tools/**",
    ".datacore/modules/*/extension/**",
    ".datacore/modules/*/specs/**",
    # module top-level manifest/doc files (single level only)
    ".datacore/modules/*/*",
]


def eprint(*args):
    print(*args, file=sys.stderr)


def glob_match(path, pat):
    """fnmatch with '**/'-prefix and '/**'-suffix semantics like the hook
    historically used, plus '**' segments handled by fnmatch itself."""
    if fnmatch.fnmatch(path, pat):
        return True
    if pat.startswith("**/") and fnmatch.fnmatch(path, pat[3:]):
        return True
    if pat.endswith("/**"):
        base = pat[:-3]
        # base itself may contain wildcards (e.g. .datacore/modules/*/data)
        parts = path.split("/")
        for i in range(1, len(parts)):
            if fnmatch.fnmatch("/".join(parts[:i]), base):
                return True
        if fnmatch.fnmatch(path, base):
            return True
    return False


def match_any(path, patterns):
    for pat in patterns:
        if glob_match(path, pat):
            return pat
    return None


def single_level_match(path, pat):
    """True if fnmatch matches AND the '*' wildcards did not swallow '/'.
    Used for allow patterns like '.datacore/*' that must not allow deep paths."""
    if "**" in pat:
        return glob_match(path, pat)
    return fnmatch.fnmatch(path, pat) and path.count("/") == pat.count("/")


def git(args):
    return subprocess.run(
        ["git"] + args, capture_output=True, text=True, check=False
    )


def load_yaml(path):
    import yaml  # noqa: deferred so the caller can catch ImportError cleanly

    with open(path) as f:
        return yaml.safe_load(f) or {}


def collect_changes(commits):
    """Return exact added/modified paths, including root and merge commits."""
    added, modified = set(), set()
    for sha in commits:
        new, changed = changed_paths(Path.cwd(), sha)
        added.update(new)
        modified.update(changed)
    return added, modified


def added_lines_by_file(commits):
    """Scan complete changed blobs, never working-tree files or quoted diffs.

    Full blobs also catch content assembled during a merge or made sensitive
    by a neighboring edit. Deduplicate repeated versions, not filenames.
    """
    seen = set()
    for sha in commits:
        added, modified = changed_paths(Path.cwd(), sha)
        touched = added | modified
        for mode, kind, oid, path in tree_entries(Path.cwd(), sha):
            if path not in touched or kind != "blob" or (path, oid) in seen:
                continue
            seen.add((path, oid))
            content = git_bytes(Path.cwd(), "cat-file", "blob", oid)
            for line in content.decode("utf-8", "replace").splitlines():
                yield path, line


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="org/name being pushed to")
    ap.add_argument("--denylist", default=DEFAULT_DENYLIST)
    ap.add_argument("--index", action="store_true", help="scan staged Git objects instead of stdin commits")
    args = ap.parse_args()

    try:
        policy = load_yaml(args.denylist)
    except ImportError:
        eprint("pre-push-scan: FATAL: pyyaml not available — cannot load "
               "policy. Install with: pip3 install pyyaml")
        return 2
    except OSError as e:
        eprint(f"pre-push-scan: FATAL: cannot read denylist {args.denylist}: {e}")
        return 2

    commits = [":index"] if args.index else [c.strip() for c in sys.stdin.read().split() if c.strip()]
    if not commits:
        eprint("pre-push-scan: no commits to scan — nothing to do")
        return 0

    forbidden_paths = policy.get("forbidden_paths", []) or []
    warning_paths = (policy.get("warning_patterns", {}) or {}).get("paths", []) or []
    exemptions = policy.get("content_scan_exemptions", []) or []
    reject_globs = policy.get("datacore_new_file_reject", DATACORE_NEW_FILE_REJECT)
    allow_globs = policy.get("datacore_new_file_allow", DATACORE_NEW_FILE_ALLOW)

    # Content patterns: public + optional private customer denylist.
    content_patterns = [(p, "public denylist")
                        for p in (policy.get("forbidden_content", []) or [])]
    private_path = policy.get("private_patterns_file")
    if private_path:
        private_path = os.path.expanduser(private_path)
        if os.path.exists(private_path):
            try:
                private = load_yaml(private_path)
                forbidden_paths += private.get("forbidden_paths", []) or []
                content_patterns += [
                    (p, "private denylist")
                    for p in (private.get("forbidden_content", []) or [])
                ]
            except Exception as e:
                eprint(f"pre-push-scan: FATAL: private denylist exists but "
                       f"failed to load ({e}) — failing CLOSED")
                return 2
        else:
            eprint("=" * 63)
            eprint("pre-push-scan: NOTICE: private customer denylist NOT found:")
            eprint(f"  {private_path}")
            eprint("  Customer-name content scanning is SKIPPED on this host")
            eprint("  until that file is provisioned. Path + public content")
            eprint("  policies still apply. (fail-open for this list only)")
            eprint("=" * 63)

    compiled = []
    for pat, src in content_patterns:
        try:
            compiled.append((re.compile(pat, re.IGNORECASE), pat, src))
        except re.error:
            compiled.append((re.compile(re.escape(pat), re.IGNORECASE), pat, src))

    added, modified = collect_changes(commits)
    touched = sorted(added | modified)

    blocks = []
    warns = []

    # --- 1. Path denylist -------------------------------------------------
    for p in touched:
        pat = match_any(p, forbidden_paths)
        if pat:
            blocks.append(f"path: {p}  (forbidden path pattern: {pat})")
            continue
        wpat = match_any(p, warning_paths)
        if wpat:
            warns.append(f"path: {p}  (warning pattern: {wpat})")

    # --- 2. New-file gate for .datacore/ ----------------------------------
    for p in sorted(added):
        if ".datacore/" not in p and not p.startswith(".datacore"):
            continue
        # Normalize: policy globs are rooted at '.datacore/...'; strip any
        # leading dirs (spaces embed .datacore/ one level down).
        idx = p.find(".datacore/")
        rel = p[idx:]
        # cos/*.example is the one allowed exception inside cos/
        if rel.startswith(".datacore/cos/") and rel.endswith(".example") \
                and rel.count("/") == 2:
            continue
        rpat = match_any(rel, reject_globs)
        if rpat:
            blocks.append(f"new file: {p}  (REJECTED category: {rpat} — "
                          "this category never belongs on a public repo)")
            continue
        if not any(single_level_match(rel, a) for a in allow_globs):
            blocks.append(
                f"new file: {p}  (not in .datacore new-file allowlist — "
                "allowed: agents/ commands/ lib/ bin/ dips/ specs/ templates/ "
                "registry/ docs/ workflows/ hooks/ githooks/ config/ tests/ "
                "skills/ cos/*.example modules/*/{agents,commands,lib,...})")

    # --- 3. Content scan of complete changed blobs -----------------------------------
    seen = set()
    for path, line in added_lines_by_file(commits):
        if match_any(path, exemptions):
            continue
        for rx, pat, src in compiled:
            if rx.search(line):
                key = (path, pat)
                if key in seen:
                    continue
                seen.add(key)
                label = pat if src == "public denylist" else "<private pattern>"
                blocks.append(f"content: {path}  matches {src} pattern: {label}")

    for w in warns:
        eprint(f"  ⚠ pre-push-scan: {w} (push allowed)")

    if blocks:
        eprint("")
        eprint("✗ pre-push-scan: %d violation(s) for %s:" % (len(blocks), args.repo))
        for b in blocks:
            eprint(f"  ✗ {b}")
        return 1

    eprint(f"pre-push-scan: ✓ {len(commits)} commit(s), "
           f"{len(touched)} path(s) clean for {args.repo}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        eprint(f"pre-push-scan: FATAL: validation could not complete: {exc}")
        sys.exit(2)
