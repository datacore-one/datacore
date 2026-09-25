#!/usr/bin/env python3
"""The one way to find and read sprint files.

THE PROBLEM THIS SOLVES. On 2026-09-25 the morning briefing named enterprise
W23 B1/B2 as open go-live blockers "in review". Their PRs had merged on
2026-06-09. Three separate defects lined up to produce that:

1. Readers read the WORKING TREE. `2-projects/enterprise` was checked out on a
   feature branch, so any sprint fix merged to `development` was invisible to
   every reader on this machine until someone switched branches.
2. Each reader found sprint files its own way. `sprint_sync.discover()` saw
   only `sprints/<id>/sprint.yaml`, so the flat `sprints/2026-W27-sprint.yaml`
   was skipped by the 2026-09-04 retroactive close. `sprint_standup_inputs`
   saw ONLY flat files, so it took W27, a July sprint, to be "the latest".
3. Nothing compared an item's `review` state with its PR. A merged PR left the
   item in review for good.

So: every reader discovers through `discover()`, which reads each repo's
INTEGRATION BRANCH (origin/development, else the remote's default branch) and
both file layouts; and `health()` turns the drift into named problems for the
briefing instead of a stdout line nobody reads.

    python3 .datacore/lib/sprint_files.py health --space 5-plur
    python3 .datacore/lib/sprint_files.py list   --space 5-plur
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

import yaml

REPO = Path(__file__).resolve().parents[2]

# The branch sprint work is merged into, in order of preference. `main` is the
# released record in repos that have a `development` line (enterprise), so
# `development` wins when it exists; otherwise the remote's default branch.
INTEGRATION_BRANCHES = ("development",)

# Someone is on it. Matches ACTIVE_STATES in enterprise scripts/claim/checks.py.
IN_FLIGHT = {"claimed", "in-progress", "review"}

GIT_TIMEOUT = 30


@dataclass
class SprintFile:
    sprint_id: str
    data: dict
    where: str          # human-readable origin: "<repo>@origin/development:<path>" or a path
    path: Path          # on-disk location (working tree), for writers and messages
    source: str = "branch"   # "branch" | "worktree"


@dataclass
class Discovery:
    sprints: list[SprintFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _git(repo: Path, *args: str, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=timeout)


def _is_main_checkout(repo: Path) -> bool:
    """A repo's main checkout has a `.git` DIRECTORY; linked worktrees have a file."""
    return (repo / ".git").is_dir()


def integration_ref(repo: Path, fetch: bool = True) -> tuple[str | None, str | None]:
    """(ref, warning). The remote branch sprint work lands on, freshly fetched.

    Returns (None, reason) when the repo has no usable remote branch, so the
    caller can say it is falling back to the working tree rather than doing it
    silently.
    """
    branch = None
    for b in INTEGRATION_BRANCHES:
        if _git(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{b}").returncode == 0:
            branch = b
            break
    if branch is None:
        head = _git(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
        if head.returncode == 0:
            branch = head.stdout.strip().rsplit("/", 1)[-1]
    if branch is None:
        return None, f"{repo.name}: no origin integration branch — reading the working tree"

    warning = None
    if fetch:
        try:
            r = _git(repo, "fetch", "--quiet", "origin", branch)
            if r.returncode != 0:
                warning = (f"{repo.name}: could not fetch origin/{branch} "
                           f"({(r.stderr or '').strip()[:80]}) — reading the last fetched copy")
        except subprocess.TimeoutExpired:
            warning = f"{repo.name}: fetching origin/{branch} timed out — reading the last fetched copy"
    return f"origin/{branch}", warning


def _is_sprint_path(p: str) -> bool:
    """`sprints/<id>/sprint.yaml` or flat `sprints/<id>.yaml`; never `_archive/`."""
    parts = p.split("/")
    if len(parts) < 2 or parts[0] != "sprints" or "_archive" in parts:
        return False
    if len(parts) == 3:
        return parts[2] == "sprint.yaml"
    return len(parts) == 2 and parts[1].endswith(".yaml")


def _sprint_id(data: dict, rel: str) -> str:
    sid = data.get("sprint_id")
    if sid:
        return str(sid)
    parts = rel.split("/")
    return parts[-2] if parts[-1] == "sprint.yaml" else Path(parts[-1]).stem


def _read_repo(repo: Path, fetch: bool, out: Discovery) -> list[SprintFile]:
    ref, warning = integration_ref(repo, fetch)
    if warning:
        out.warnings.append(warning)
    found: list[SprintFile] = []
    if ref is None:
        for p in sorted((repo / "sprints").glob("**/*.yaml")):
            rel = str(p.relative_to(repo))
            if _is_sprint_path(rel):
                data = _load(p.read_text(), rel, out)
                if data is not None:
                    found.append(SprintFile(_sprint_id(data, rel), data, str(p), p, "worktree"))
        return found
    ls = _git(repo, "ls-tree", "-r", "--name-only", ref, "--", "sprints")
    if ls.returncode != 0:
        out.warnings.append(f"{repo.name}: cannot list sprints on {ref} — skipped")
        return found
    for rel in ls.stdout.splitlines():
        if not _is_sprint_path(rel):
            continue
        show = _git(repo, "show", f"{ref}:{rel}")
        if show.returncode != 0:
            out.warnings.append(f"{repo.name}: cannot read {rel} on {ref} — skipped")
            continue
        data = _load(show.stdout, rel, out)
        if data is not None:
            found.append(SprintFile(_sprint_id(data, rel), data,
                                    f"{repo.name}@{ref}:{rel}", repo / rel))
    return found


def _load(text: str, where: str, out: Discovery) -> dict | None:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        out.warnings.append(f"{where}: YAML parse error — skipped ({str(exc).splitlines()[0]})")
        return None
    if not isinstance(data, dict):
        out.warnings.append(f"{where}: not a mapping — skipped")
        return None
    return data


def discover(space: str, *, fetch: bool = True, root: Path | None = None) -> Discovery:
    """Every sprint in a space, ONE per sprint_id, read from integration branches.

    Code-repo sprints come from `2-projects/<repo>/sprints/` on the repo's
    integration branch. Linked worktrees (`.git` is a file) are the same repo
    and are skipped; so are checkouts that share a main checkout's git dir.
    Track-level sprints (`1-tracks/<track>/sprints/`) live in the space repo,
    which the ledger keeps current, so they are read from disk.
    """
    base = (root or REPO) / space
    out = Discovery()
    seen_git_dirs: set[str] = set()
    by_id: dict[str, SprintFile] = {}

    for repo in sorted((base / "2-projects").glob("*")):
        if not (repo / "sprints").is_dir() or not _is_main_checkout(repo):
            continue
        common = _git(repo, "rev-parse", "--git-common-dir")
        key = str((repo / common.stdout.strip()).resolve()) if common.returncode == 0 else str(repo)
        if key in seen_git_dirs:
            continue
        seen_git_dirs.add(key)
        for sf in _read_repo(repo, fetch, out):
            by_id.setdefault(sf.sprint_id, sf)

    for p in sorted(base.glob("1-tracks/*/sprints/*/sprint.yaml")):
        data = _load(p.read_text(), str(p), out)
        if data is not None:
            sf = SprintFile(_sprint_id(data, f"sprints/{p.parent.name}/sprint.yaml"),
                            data, str(p.relative_to(base.parent)), p, "worktree")
            by_id.setdefault(sf.sprint_id, sf)

    out.sprints = sorted(by_id.values(), key=lambda s: s.sprint_id)
    return out


def _end_date(sf: SprintFile) -> date | None:
    end = (sf.data.get("dates") or {}).get("end")
    if isinstance(end, date):
        return end
    try:
        return date.fromisoformat(str(end)[:10])
    except (TypeError, ValueError):
        return None


def active(disc: Discovery, today: date | None = None) -> tuple[list[SprintFile], list[SprintFile]]:
    """(running, expired): status active, split on whether the end date has passed.

    The status field is a claim and the end date is a fact; a sprint whose end
    has passed is over whatever its status says.
    """
    today = today or date.today()
    running, expired = [], []
    for sf in disc.sprints:
        if sf.data.get("status") != "active":
            continue
        end = _end_date(sf)
        (expired if end and end < today else running).append(sf)
    return running, expired


# ── PR state ────────────────────────────────────────────────────────────────

_PR_PATTERNS = (
    re.compile(r"github\.com/(?P<repo>[\w.-]+/[\w.-]+)/pull/(?P<n>\d+)"),
    re.compile(r"github:(?P<repo>[\w.-]+/[\w.-]+)/pull/(?P<n>\d+)"),
    re.compile(r"^(?P<repo>[\w.-]+/[\w.-]+)#(?P<n>\d+)$"),
)


def parse_pr(value) -> tuple[str, int] | None:
    s = str(value or "").strip().strip('"')
    for pat in _PR_PATTERNS:
        m = pat.search(s)
        if m:
            return m.group("repo"), int(m.group("n"))
    return None


def gh_pr_state(repo: str, number: int) -> dict:
    """{'state': 'MERGED'|'OPEN'|'CLOSED', 'mergedAt': ...}; raises on failure."""
    r = subprocess.run(["gh", "pr", "view", str(number), "-R", repo,
                        "--json", "state,mergedAt"],
                       capture_output=True, text=True, timeout=GIT_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "gh failed").strip()[:120])
    return json.loads(r.stdout)


PrLookup = Callable[[str, int], dict]


def health(disc: Discovery, *, today: date | None = None,
           pr_lookup: PrLookup | None = gh_pr_state) -> list[str]:
    """Drift a reader must not repeat as fact, as one line per problem.

    Empty list means checked and clean. A PR that could not be checked is
    reported as unverified — never counted as fine.
    """
    today = today or date.today()
    problems = list(disc.warnings)

    _, expired = active(disc, today)
    for sf in expired:
        end = _end_date(sf)
        problems.append(f"{sf.sprint_id}: still marked active, ended {end} "
                        f"({(today - end).days} days ago) — close it")

    unverified = 0
    cache: dict[tuple[str, int], dict | None] = {}
    for sf in disc.sprints:
        carried = _carried_ids(sf.data.get("carryover") or [], sf.sprint_id)
        closed = sf.data.get("status") == "closed"
        for section in ("backlog", "stretch"):
            for it in sf.data.get(section) or []:
                if not isinstance(it, dict) or it.get("state") not in IN_FLIGHT:
                    continue
                iid, state = it.get("id", "?"), it.get("state")
                if closed and iid not in carried:
                    problems.append(f"{sf.sprint_id}#{iid}: {state!r} in a closed sprint "
                                    f"and not carried — nobody is on it")
                pr = parse_pr(it.get("pr"))
                if pr is None or pr_lookup is None:
                    continue
                if pr not in cache:
                    try:
                        cache[pr] = pr_lookup(*pr)
                    except Exception:   # noqa: BLE001 — any failure is "could not check"
                        cache[pr] = None
                st = cache[pr]
                if st is None:
                    unverified += 1
                elif st.get("state") == "MERGED":
                    problems.append(
                        f"{sf.sprint_id}#{iid}: says {state!r} but {pr[0]}#{pr[1]} merged "
                        f"{str(st.get('mergedAt') or '')[:10]} — the sprint file is stale, "
                        f"not the work")
                elif st.get("state") == "CLOSED":
                    problems.append(f"{sf.sprint_id}#{iid}: says {state!r} but "
                                    f"{pr[0]}#{pr[1]} was closed unmerged")
    if unverified:
        problems.append(f"PR state UNVERIFIED for {unverified} in-flight item(s) "
                        f"(gh lookup failed) — treat their state as unconfirmed")
    return problems


def pr_is_merged(item: dict, pr_lookup: PrLookup | None = gh_pr_state) -> bool | None:
    """True/False for an item's linked PR; None when there is none or it cannot be checked."""
    pr = parse_pr(item.get("pr"))
    if pr is None or pr_lookup is None:
        return None
    try:
        return pr_lookup(*pr).get("state") == "MERGED"
    except Exception:   # noqa: BLE001
        return None


def _carried_ids(carryover, sprint_id: str) -> set[str]:
    """Item ids named by carryover: `B1`, `<sprint_id>#B1`, or `{id: B1}`."""
    ids: set[str] = set()
    for entry in carryover if isinstance(carryover, list) else []:
        if isinstance(entry, dict):
            entry = entry.get("id")
        if not isinstance(entry, str):
            continue
        sprint, sep, item = entry.partition("#")
        if not sep:
            ids.add(entry)
        elif sprint == sprint_id:
            ids.add(item)
    return ids


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("list", "health"))
    ap.add_argument("--space", default="5-plur")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--no-pr-check", action="store_true")
    args = ap.parse_args(argv)

    disc = discover(args.space, fetch=not args.no_fetch)
    if args.command == "list":
        running, expired = active(disc)
        for sf in disc.sprints:
            tag = "RUNNING" if sf in running else "EXPIRED" if sf in expired else sf.data.get("status")
            print(f"{sf.sprint_id:40} {tag:10} {sf.where}")
        for w in disc.warnings:
            print(f"warning: {w}")
        return 0

    problems = health(disc, pr_lookup=None if args.no_pr_check else gh_pr_state)
    if not problems:
        print(f"sprints ({args.space}): {len(disc.sprints)} checked, no drift")
    for p in problems:
        print(f"⚠ {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
