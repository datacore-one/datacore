"""INS-2: "Right after install, a first health check passes and a task can be
created, completed and seen in the history."

Kind: deterministic, end to end on a fresh install built from `git archive HEAD`
by following INSTALL.md (see _fresh_install.py) -- nothing hand-configured after.
  - the health check (v2_verify.py --quick) reports no FAIL;
  - a task is created in the personal inbox with the org-workspace adapter (the
    documented way to add a task), completed with it, and the ledger fold
    (`ledger_cli.py items`) of the personal space shows that task as done.

Seeded failure: a fresh install whose health check FAILs on this fleet's jobs /
remotes, or whose first item.create is refused (2026-09-19: "unregistered
writer" on an install with no principals.yaml).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fresh_install as F  # noqa: E402

TITLE = "first task on a fresh install"


@pytest.fixture(scope="module")
def install(tmp_path_factory):
    return F.follow_guide(tmp_path_factory.mktemp("ins2"))


def test_first_health_check_passes(install):
    p = install.run("python3 .datacore/lib/v2_verify.py --quick --json", timeout=55)
    text = p.stdout
    assert "{" in text, f"health check produced no report (rc {p.returncode}): {p.stderr[-400:]}"
    report = json.loads(text[text.index("{"):])
    failed = [f"{c['dip']} {c['name']}: {c['detail'][:100]}" for c in report["checks"] if c["ok"] is False]
    assert failed == [], "a fresh install fails its first health check:\n" + "\n".join(failed)


def test_a_task_is_created_completed_and_in_the_history(install):
    _task_round_trip(install)


def test_task_round_trip_once_the_inbox_exists(tmp_path):
    """Isolates the ledger path from INS-1's template step: the inbox the guide
    meant to create is created, nothing else is configured."""
    inst = F.follow_guide(tmp_path)
    inst.run("mkdir -p 0-personal/org && cp .datacore/templates/org/inbox.org.example 0-personal/org/inbox.org")
    _task_round_trip(inst)


def _task_round_trip(install):
    inbox = "0-personal/org/inbox.org"
    add = install.run(f'python3 .datacore/lib/org_workspace_adapter.py add --file {inbox} '
                      f'--state TODO --heading "{TITLE}"')
    assert add.returncode == 0, f"create failed: {(add.stdout + add.stderr)[-400:]}"
    m = re.search(r'"id":\s*"([^"]+)"', add.stdout)
    assert m, f"create returned no id: {add.stdout[-300:]}"
    done = install.run(f"python3 .datacore/lib/org_workspace_adapter.py complete --file {inbox} --id {m.group(1)}")
    assert done.returncode == 0, f"complete failed: {(done.stdout + done.stderr)[-400:]}"
    items = install.run("python3 .datacore/lib/ledger_cli.py items --space 0-personal")
    line = [l for l in items.stdout.splitlines() if TITLE in l or m.group(1) in l]
    assert line, f"the task is not in the history:\n{(items.stdout + items.stderr)[-600:]}"
    assert any(re.search(r"\b(done|DONE|completed)\b", l) for l in line), f"not shown as done: {line}"
