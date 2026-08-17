#!/usr/bin/env python3
"""
wrap_up_mechanics.py — the deterministic half of /wrap-up, as one script.

A measured wrap-up ran 338 tool calls, 218 of them ad-hoc Bash: ps pipelines,
git status across eight repos, ls of journal paths, grep for artifacts. None of
that needs a language model. It needs a program, run three times, returning JSON.

WHAT MOVED HERE (spec sections it replaces):
  preflight : §12 orphan cleanup, §12.5 nightshift archival, §14 context-sync
              check, §10 artifact scan, plus the session archive
  meta      : §9 session meta-analysis counters, read from the archived meta.json
  finalize  : §13 push, across root, spaces and subproject repos
  audit     : §16 completion verification and §18 self-audit

WHAT DELIBERATELY DID NOT MOVE: anything requiring judgement — the session
summary, the continuation decision, task-completion matching, the consolidated
report. A script that guessed at those would be confidently wrong, which is
worse than slow.

Every subcommand prints JSON on stdout and is safe to re-run. `preflight`
kills processes, so it honours --dry-run.

Usage:
  python3 .datacore/lib/wrap_up_mechanics.py preflight [--dry-run]
  python3 .datacore/lib/wrap_up_mechanics.py meta
  python3 .datacore/lib/wrap_up_mechanics.py finalize [--dry-run]
  python3 .datacore/lib/wrap_up_mechanics.py audit
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
LIB = DATACORE_ROOT / ".datacore" / "lib"
ARCHIVE_DIR = DATACORE_ROOT / ".datacore" / "state" / "sessions" / "archive"

# Dev servers Claude starts for preview and never cleans up.
DEV_SERVER_RE = re.compile(
    r"\bvite\b|\bbun run (dev|server)\b|\bnext dev\b|\bnpm run dev\b"
    r"|\bnpm exec vite\b|webpack-dev-server|node_modules/\.bin/",
)
# One-shot hook/CLI workers. These should live seconds. 24 immortal
# `plur hook-inject` orphans on a degraded network once drove a 16GB machine to
# 17.4GB swap (2026-07-06, plur-ai/plur#504) — the dev-server-only pattern in
# the old §12 looked straight past them.
HOOK_WORKER_RE = re.compile(r"plur hook-|hook-inject|hook-observe|@plur-ai/cli")
# Live MCP servers belong to active sessions and must survive.
MCP_RE = re.compile(r"datacore-mcp|exa-mcp|plur-mcp|mcp-server")


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 120,
         strip: bool = True) -> tuple[int, str, str]:
    """Run a command. `strip=False` for output whose leading whitespace matters.

    git's `status --porcelain` encodes the index state in column 1 and the
    worktree state in column 2, so an unstaged modification begins with a
    SPACE. Stripping stdout eats it on the first line only, which shifted a
    fixed-width parse by one character and silently misfiled exactly one file
    per repo as another session's work — leaving it uncommitted.
    """
    try:
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                           text=True, timeout=timeout)
        out, err = (r.stdout or ""), (r.stderr or "")
        return r.returncode, (out.strip() if strip else out), err.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except FileNotFoundError:
        return 127, "", f"not found: {cmd[0]}"
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {e}"


def git_status_entries(repo: Path, *paths: str) -> list[tuple[str, str]]:
    """(status, repo-relative path) for everything dirty, from NUL-separated porcelain.

    `-z` rather than line-splitting: it does not quote paths containing spaces
    or non-ASCII, and cannot be confused by a filename with a newline in it. A
    rename entry is followed by its origin path, which must be consumed rather
    than read as another file.

    THE ONLY CORRECT PARSE IN THIS FILE. Three separate call sites previously
    did `line[3:]` over stripped output and each dropped a character from the
    first entry.
    """
    cmd = ["git", "status", "--porcelain", "-z"] + (["--", *paths] if paths else [])
    rc, out, _ = _run(cmd, cwd=repo, timeout=60, strip=False)
    if rc != 0:
        return []
    parts = out.split("\0")
    entries, i = [], 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        entries.append((status, path))
        if "R" in status or "C" in status:
            i += 1                      # skip the origin path of a rename/copy
    return entries


def git_dirty(repo: Path) -> set[str]:
    return {p for _, p in git_status_entries(repo)}


def _etime_seconds(etime: str) -> int:
    """ps etime is [[dd-]hh:]mm:ss — parse it rather than string-matching."""
    days = 0
    if "-" in etime:
        d, etime = etime.split("-", 1)
        days = int(d)
    parts = [int(x) for x in etime.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts[-3:]
    return days * 86400 + h * 3600 + m * 60 + s


# --------------------------------------------------------------------------
# preflight
# --------------------------------------------------------------------------

def scan_processes() -> dict:
    """Classify stray processes into kill / flag / preserve. Never guesses."""
    rc, out, _ = _run(["ps", "-eo", "pid,ppid,etime,command"])
    kill, flag = [], []
    if rc != 0:
        return {"error": "ps failed", "kill": [], "flag": []}

    for line in out.splitlines()[1:]:
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid, ppid, etime, cmd = parts
        try:
            pid_i, ppid_i, age = int(pid), int(ppid), _etime_seconds(etime)
        except ValueError:
            continue
        if pid_i == os.getpid() or MCP_RE.search(cmd):
            continue

        rec = {"pid": pid_i, "ppid": ppid_i, "age_s": age, "command": cmd[:160]}
        if DEV_SERVER_RE.search(cmd):
            # Orphans are always safe; live-parent ones belong to this session,
            # which is ending anyway.
            rec["reason"] = "orphaned dev server" if ppid_i == 1 else "dev server"
            kill.append(rec)
        elif ppid_i == 1 and HOOK_WORKER_RE.search(cmd):
            rec["reason"] = "orphaned hook/CLI worker"
            kill.append(rec)
        elif ppid_i == 1 and " node" in f" {cmd}" and age > 3600:
            # Flag, never kill: cannot prove from here that it is a leak.
            rec["reason"] = "unexplained ppid=1 node process >1h — review"
            flag.append(rec)
    return {"kill": kill, "flag": flag}


def kill_processes(procs: list[dict], dry_run: bool) -> list[dict]:
    out = []
    for p in procs:
        if dry_run:
            out.append({**p, "killed": False, "dry_run": True})
            continue
        try:
            os.kill(p["pid"], signal.SIGTERM)
            out.append({**p, "killed": True})
        except ProcessLookupError:
            out.append({**p, "killed": False, "error": "already gone"})
        except PermissionError:
            out.append({**p, "killed": False, "error": "permission denied"})
    return out


def spaces() -> list[Path]:
    return sorted(p for p in DATACORE_ROOT.glob("[0-9]-*") if p.is_dir())


def repo_status() -> list[dict]:
    """Every git repo in the installation, and whether it is clean and pushed."""
    repos = [DATACORE_ROOT] + [s for s in spaces() if (s / ".git").exists()]
    repos += [p.parent for p in DATACORE_ROOT.glob("[0-9]-*/2-projects/*/.git")]
    repos += [p.parent for p in DATACORE_ROOT.glob(".datacore/dips/.git")]
    seen, out = set(), []
    for r in repos:
        key = str(r.resolve())
        if key in seen or not (r / ".git").exists():
            continue
        seen.add(key)
        _, dirty, _ = _run(["git", "status", "--porcelain"], cwd=r, timeout=30)
        _, branch, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=r, timeout=30)
        rc, ahead, _ = _run(["git", "rev-list", "--count", "@{u}..HEAD"], cwd=r, timeout=30)
        out.append({
            "repo": str(r.relative_to(DATACORE_ROOT)) if r != DATACORE_ROOT else ".",
            "branch": branch,
            "dirty_files": len([l for l in dirty.splitlines() if l.strip()]),
            "unpushed_commits": int(ahead) if rc == 0 and ahead.isdigit() else None,
            "has_upstream": rc == 0,
        })
    return out


def artifact_scan() -> dict:
    """§10 — files created/changed today across the installation, from git.

    Reads git rather than mtime: mtime catches every generated file and cache
    write, and the point is knowledge artifacts, not churn.
    """
    created, modified = [], []
    for code, path in git_status_entries(DATACORE_ROOT):
        (created if "?" in code or "A" in code else modified).append(path)
    docs = [p for p in created + modified if p.endswith((".md", ".org", ".yaml"))]
    return {
        "created": sorted(created)[:80],
        "modified": sorted(modified)[:80],
        "knowledge_docs": sorted(docs)[:80],
        "created_count": len(created),
        "modified_count": len(modified),
    }


def context_sync_check() -> dict:
    """§14 — did agents/commands/modules change, so CLAUDE.md tables are stale?"""
    changed = [p for _, p in git_status_entries(
        DATACORE_ROOT, ".datacore/agents", ".datacore/commands",
        ".datacore/modules", ".datacore/registry")]
    return {
        "registry_changed": bool(changed),
        "changed_paths": changed[:40],
        "action": ("rebuild context: python3 .datacore/lib/context_merge.py rebuild --path ."
                   if changed else "no agent/command registry changes — context in sync"),
    }


def cmd_preflight(dry_run: bool) -> dict:
    procs = scan_processes()
    killed = kill_processes(procs.get("kill", []), dry_run)

    rc, arch_out, arch_err = _run(
        [sys.executable, str(LIB / "nightshift_archival.py"), "--all-spaces"], timeout=180)
    archival = {"ok": rc == 0, "output": (arch_out or arch_err)[-600:]}
    if rc == 127:
        archival["note"] = "nightshift_archival.py missing — reported, not skipped"

    rc, sess_out, sess_err = _run(
        [sys.executable, str(LIB / "session_archive.py"), "--json"], timeout=300)
    try:
        archive = json.loads(sess_out) if rc == 0 else {"status": "error", "error": sess_err}
    except json.JSONDecodeError:
        archive = {"status": "error", "error": (sess_out or sess_err)[:300]}

    return {
        "step": "preflight",
        "processes": {"killed": killed, "flagged": procs.get("flag", [])},
        "nightshift_archival": archival,
        "session_archive": archive,
        "context_sync": context_sync_check(),
        "artifacts": artifact_scan(),
        "repos": repo_status(),
    }


# --------------------------------------------------------------------------
# meta  (§9)
# --------------------------------------------------------------------------

def cmd_meta() -> dict:
    """Session meta-analysis counters, read from the archive rather than recounted.

    §9 used to ask the model to tally corrections, artifacts and token cost from
    memory of a conversation it had partly compacted away. These come from the
    transcript, so they are right even when the session was long.
    """
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    meta_path = None
    if sid:
        hits = list(ARCHIVE_DIR.glob(f"*/{sid}/meta.json"))
        meta_path = hits[0] if hits else None
    if not meta_path:
        return {"step": "meta", "error": "session not archived yet — run preflight first"}

    m = json.loads(meta_path.read_text())
    t, st = m.get("tokens", {}), m.get("subagent_tokens", {})
    billable = (t.get("input_tokens", 0) + t.get("cache_creation_input_tokens", 0)
                + t.get("output_tokens", 0))
    return {
        "step": "meta",
        "session_id": m["session_id"],
        "date": m["date"],
        "turns": m.get("turns"),
        "user_turns": m.get("user_turns"),
        "tool_calls": m.get("tool_call_total"),
        "tool_breakdown": dict(list((m.get("tool_calls") or {}).items())[:10]),
        "agents_spawned": m.get("agents_spawned"),
        "output_tokens": t.get("output_tokens", 0),
        "subagent_output_tokens": st.get("output_tokens", 0),
        "billable_tokens": billable,
        "files_modified": m.get("files_modified", []),
        "spaces_touched": m.get("spaces_touched", []),
        "archive_path": str(meta_path.parent),
    }


# --------------------------------------------------------------------------
# finalize  (§13)
# --------------------------------------------------------------------------

def session_files() -> tuple[list[str], str | None]:
    """Absolute paths this session wrote, from its archived meta (main + subagents)."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if not sid:
        return [], "no CLAUDE_CODE_SESSION_ID"
    hits = list(ARCHIVE_DIR.glob(f"*/{sid}/meta.json"))
    if not hits:
        return [], "session not archived — run preflight first"
    try:
        return json.loads(hits[0].read_text()).get("files_modified", []), None
    except (OSError, ValueError) as e:
        return [], f"unreadable meta: {e}"


def _default_branch_ok(repo: Path) -> tuple[bool, str]:
    """Refuse to push from a non-default branch.

    `./sync` learned this the expensive way: the warning fired correctly for two
    months while 610 commits piled up on a feature branch in 5-plur — 52
    zettels, every weekly content calendar since mid-June — all pushed, none on
    main, none visible to anyone. A warning nobody reads is not a guard.
    """
    _, branch, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, timeout=30)
    if branch in ("main", "master"):
        return True, branch
    if os.environ.get("DATACORE_SYNC_ALLOW_BRANCH") == "1":
        return True, branch
    return False, branch


def finalize_session_scope(dry_run: bool) -> dict:
    """Commit and push ONLY what this session touched.

    `./sync push` stages everything with `git add --ignore-removal .` and
    commits it as "Sync: <date>". Correct for a single-session day, wrong the
    moment two sessions are open: one session's wrap-up sweeps up the other's
    half-finished work and pushes it. Scoping to the archived `files_modified`
    fixes that — and the files another session touched are REPORTED, never
    silently dropped, because "I left 4 files behind" and "there was nothing to
    do" must not look the same.
    """
    mine, err = session_files()
    if err:
        return {"scope": "session", "error": err, "pushes": [],
                "note": "refusing to guess which files are this session's"}

    # Bucket the session's files by the repo that owns them.
    repos = [DATACORE_ROOT] + [s for s in spaces() if (s / ".git").exists()]
    project_repos = [p.parent for p in DATACORE_ROOT.glob("[0-9]-*/2-projects/*/.git")]
    by_repo: dict[Path, list[str]] = {}
    in_project: list[str] = []
    unversioned: list[str] = []
    for f in mine:
        fp = Path(f)
        if any(str(fp).startswith(str(pr) + os.sep) for pr in project_repos):
            in_project.append(f)          # code repos are never auto-committed
            continue
        owner = max((r for r in repos if str(fp).startswith(str(r) + os.sep)),
                    key=lambda r: len(str(r)), default=None)
        if owner:
            by_repo.setdefault(owner, []).append(f)
        else:
            # Touched by this session but owned by no repo — outside ~/Data, or
            # inside it but unreachable to git (`.claude/` is behind a symlink,
            # so `git ls-files` reports "beyond a symbolic link"). No scope will
            # ever push these. Silence here is how a launchd plist or a hook
            # config change survives only on one machine.
            unversioned.append(f)

    results = []
    for repo, files in sorted(by_repo.items(), key=lambda kv: str(kv[0])):
        dirty = git_dirty(repo)
        rel_mine = {str(Path(f).relative_to(repo)) for f in files}
        staged = sorted(rel_mine & dirty)
        others = sorted(dirty - rel_mine)

        # A session file that is not dirty is EITHER already committed OR
        # invisible to git — ignored, or behind a symlink (`.claude/` reports
        # "beyond a symbolic link"). Those two look identical here and must not.
        # Without this check the invisible ones fell out of every bucket and
        # were reported nowhere, which is the precise failure this scoping
        # exists to prevent.
        quiet = sorted(rel_mine - dirty)
        if quiet:
            _, tracked_out, _ = _run(["git", "ls-files", "-z", "--"] + quiet,
                                     cwd=repo, timeout=60, strip=False)
            tracked = {p for p in tracked_out.split("\0") if p}
            invisible = [str(repo / q) for q in quiet if q not in tracked]
            if invisible:
                unversioned.extend(invisible)

        entry = {
            "repo": str(repo.relative_to(DATACORE_ROOT)) if repo != DATACORE_ROOT else ".",
            "session_files_staged": staged,
            "left_for_other_sessions": others,
        }
        if not staged:
            entry["ok"] = None
            entry["note"] = f"nothing of this session's is dirty here ({len(others)} other file(s) untouched)"
            results.append(entry)
            continue

        ok_branch, branch = _default_branch_ok(repo)
        entry["branch"] = branch
        if not ok_branch:
            entry["ok"] = False
            entry["note"] = (f"REFUSED — HEAD is on '{branch}', not the default branch. "
                             "Anything pushed from here lands where no one reads it. "
                             "Merge to main, open a PR, or set DATACORE_SYNC_ALLOW_BRANCH=1.")
            results.append(entry)
            continue

        # A push carries every earlier unpushed commit on the branch — that is
        # git, not a choice. Say so rather than implying the push was scoped.
        _, ahead, _ = _run(["git", "rev-list", "--count", "@{u}..HEAD"], cwd=repo, timeout=30)
        entry["preexisting_unpushed_commits"] = int(ahead) if ahead.isdigit() else 0

        if dry_run:
            entry["ok"] = None
            entry["note"] = "dry run — would stage, commit and push the listed files only"
            results.append(entry)
            continue

        rc, _, add_err = _run(["git", "add", "--"] + staged, cwd=repo, timeout=120)
        if rc != 0:
            entry.update(ok=False, note=f"git add failed: {add_err[:200]}")
            results.append(entry)
            continue

        msg = f"Session {date.today().isoformat()}: {len(staged)} file(s)"
        rc, _, c_err = _run(["git", "commit", "-m", msg], cwd=repo, timeout=120)
        if rc != 0:
            # A rejected commit followed by a "successful" push is the exact
            # failure ./sync's safety check 3 exists to stop.
            entry.update(ok=False,
                         note=f"COMMIT REJECTED (likely a pre-commit hook) — nothing "
                              f"staged was saved or pushed: {c_err[:200]}")
            results.append(entry)
            continue

        rc, p_out, p_err = _run(["git", "push"], cwd=repo, timeout=300)
        entry["ok"] = rc == 0
        entry["output"] = (p_out or p_err)[-300:]
        if rc != 0:
            entry["note"] = "commit saved locally; push failed — /tomorrow retries"
        results.append(entry)

    return {
        "scope": "session",
        "session_file_count": len(mine),
        "pushes": results,
        "skipped_project_repos": in_project,
        "project_repo_note": ("code repos are never auto-committed — commit and push "
                              "these yourself or open a PR" if in_project else None),
        "unversioned": unversioned,
        "unversioned_note": ("outside any git repo (or unreachable to git) — these "
                             "changes exist on this machine only and no push will "
                             "carry them" if unversioned else None),
    }


def cmd_finalize(dry_run: bool, scope: str = "session") -> dict:
    if scope == "session":
        out = finalize_session_scope(dry_run)
    else:
        results = []
        sync = DATACORE_ROOT / "sync"
        if sync.exists() and not dry_run:
            rc, o, e = _run([str(sync), "push"], cwd=DATACORE_ROOT, timeout=600)
            results.append({"target": "./sync push", "ok": rc == 0, "output": (o or e)[-800:]})
        else:
            results.append({"target": "./sync push", "ok": None, "dry_run": True})
        out = {"scope": "all", "pushes": results}

    # Index the journal to the knowledge DB (old §11). Path was wrong in the
    # spec for an unknown length of time — `~/.datacore/lib/` does not exist.
    rc, jo, je = _run([sys.executable, str(LIB / "journal_parser.py"),
                       "--sync", "--space", "personal"], timeout=180)
    out["journal_index"] = {"ok": rc == 0, "output": (jo or je)[-400:]}

    out["step"] = "finalize"
    out["repos_after"] = repo_status()
    return out


# --------------------------------------------------------------------------
# audit  (§16 + §18)
# --------------------------------------------------------------------------

def cmd_audit() -> dict:
    today = date.today().isoformat()
    checks = []

    def check(name, ok, detail=""):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    personal = DATACORE_ROOT / "0-personal" / "journal" / f"{today}.md"
    alt = DATACORE_ROOT / "0-personal" / "notes" / "journals" / f"{today}.md"
    check("personal journal written", personal.exists() or alt.exists(),
          str(personal if personal.exists() else alt))

    space_journals = [str(p.relative_to(DATACORE_ROOT))
                      for s in spaces()
                      for p in [s / "journal" / f"{today}.md"] if p.exists()]
    check("space journals", True, f"{len(space_journals)} written: {space_journals}")

    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    archived = bool(sid and list(ARCHIVE_DIR.glob(f"*/{sid}/meta.json")))
    check("session archived", archived,
          "learning sweep will pick it up" if archived else "run preflight")

    repos = repo_status()
    unpushed = [r for r in repos if r["unpushed_commits"]]
    dirty = [r for r in repos if r["dirty_files"]]
    check("all repos pushed", not unpushed,
          f"{len(unpushed)} unpushed: {[r['repo'] for r in unpushed]}")
    check("no uncommitted work", not dirty,
          f"{len(dirty)} dirty: {[r['repo'] for r in dirty]}")

    cs = context_sync_check()
    check("context in sync", not cs["registry_changed"], cs["action"])

    return {
        "step": "audit",
        "date": today,
        "checks": checks,
        "passed": sum(1 for c in checks if c["pass"]),
        "total": len(checks),
        "failed": [c for c in checks if not c["pass"]],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("step", choices=["preflight", "meta", "finalize", "audit"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--scope", choices=["session", "all"], default="session",
                    help="finalize only: 'session' commits just this session's files "
                         "(default); 'all' delegates to ./sync push, which stages "
                         "everything dirty including other sessions' work")
    args = ap.parse_args()

    if args.step == "preflight":
        result = cmd_preflight(args.dry_run)
    elif args.step == "meta":
        result = cmd_meta()
    elif args.step == "finalize":
        result = cmd_finalize(args.dry_run, args.scope)
    else:
        result = cmd_audit()

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
