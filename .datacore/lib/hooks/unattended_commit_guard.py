#!/usr/bin/env python3
"""Pre-commit: an overnight task never commits onto a default branch of code.

WHY. On 2026-09-27 20:31Z the overnight task "Fix sprint schema validation" ran
with its working directory at ~/Data, on `main` of the root Datacore repository
(public), and the executing agent committed 93f3879 straight onto the host's
local `main`. Every git guard looked at pushes (pre-push, the unattended tool
policy's push.shared); none looked at commits, so nothing refused it. It was not
published only because the fleet sync happened to find the tree clean.

RULE (owner, standing): agents never merge; overnight code work is delivered as
a task branch plus a pull request the owner merges. So under NIGHTSHIFT_RUN=1 --
which the executor sets for the agent session and so for every process it
starts -- a commit on main/master/develop/development/production (or the
repository's origin/HEAD) is refused unless the repository is registered as
knowledge or agent-personal (DIP-0046: those integrate on the default branch by
design, and the run publishes them). An unregistered repository gets the code
rule: unknown is not knowledge.

The refusal says what to do instead: open the task workspace
(`agent_workspace.py ensure`), commit there on `agent/<task-id>`, and the run
pushes the branch and opens the pull request.

Exit 0 = allowed, 1 = refused. Nothing is read or imported unless
NIGHTSHIFT_RUN=1, so a human commit pays nothing for this hook.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_NAMES = {'main', 'master', 'develop', 'development', 'production'}
DIRECT_CATEGORIES = {'knowledge', 'agent-personal'}


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True,
                       errors='replace', timeout=30)
    return r.stdout.strip() if r.returncode == 0 else ''


def default_branches(repo: Path) -> set:
    names = set(DEFAULT_NAMES)
    head = _git(repo, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD')
    if head.startswith('origin/'):
        names.add(head.split('/', 1)[1])
    return names


def category(repo: Path, root: Path) -> str:
    """'knowledge' | 'agent-personal' | 'code' | '' (unregistered/unreadable)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from ledger_transport import classify
        result = classify(repo, root)
    except Exception:  # noqa: BLE001 -- unreadable is not knowledge
        return ''
    return result.reason if result.ok else ''


def verdict(repo: Path, root: Path, env=None) -> str:
    """'' when the commit may proceed, else the refusal text."""
    env = os.environ if env is None else env
    if env.get('NIGHTSHIFT_RUN') != '1':
        return ''
    branch = _git(repo, 'symbolic-ref', '--short', '-q', 'HEAD')
    if not branch or branch not in default_branches(repo):
        return ''
    kind = category(repo, root)
    if kind in DIRECT_CATEGORIES:
        return ''
    task = env.get('DATACORE_POLICY_TASK') or '<task-id>'
    return (
        f"unattended-commit-guard: BLOCKED -- an overnight task may not commit onto "
        f"'{branch}' of {repo.name} ({kind or 'unregistered'} repository).\n"
        f"  Overnight code work is delivered as a task branch and a pull request the owner merges;\n"
        f"  agents never commit to or merge into a default branch.\n"
        f"  Do this instead (your staged changes stay staged here; move them with git stash):\n"
        f"    python3 .datacore/lib/agent_workspace.py ensure --source {repo} --task-id {task}\n"
        f"  then edit, test and commit in the printed workspace, on its agent/<task-id> branch.\n"
        f"  The run pushes that branch and opens the pull request.\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--repo', type=Path, required=True)
    ap.add_argument('--root', type=Path, default=None)
    a = ap.parse_args()
    root = a.root or Path(os.environ.get('DATACORE_ROOT', str(Path.home() / 'Data')))
    said = verdict(a.repo, root)
    if said:
        sys.stderr.write(said)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
