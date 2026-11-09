"""INS-8: "Upgrading an install keeps its local settings, private context layers
and history intact."

Kind: deterministic, end to end. An "upstream" repository is built from
`git archive HEAD`; an install is cloned from it and set up by INSTALL.md (see
_fresh_install.py); the install then acquires local state: settings
(install.yaml, .datacore/settings.local.yaml), private context layers
(CLAUDE.local.md), credentials (.datacore/env/.env, fixture value), and history
(a task inbox, a journal page and an event log in the personal space).
Upstream then ships a release touching the public layer and an example file,
and the upgrade is run exactly as INSTALL.md's "Upgrade Process" writes it.

Afterwards every local file is byte-identical, the event log still verifies,
the composed CLAUDE.md carries both the upstream change and the private layer,
and every upgrade step succeeded.

Seeded failure: an upgrade step that resets or cleans the tree (`git reset
--hard`, `git clean -fdx`), or a rebuild that drops the .local layer.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fresh_install as F  # noqa: E402

UPGRADE = [
    "git pull origin main",
    "python .datacore/lib/context_merge.py rebuild --path .",
    "python .datacore/lib/zettel_db.py init-all",
]
LOCAL = {
    "install.yaml": "meta:\n  name: fixture team\n  root: ~/Data\n",
    ".datacore/settings.local.yaml": "fixture_local_setting: kept\n",
    "CLAUDE.local.md": "## Fixture private layer\n\nFIXTURE-PRIVATE-MARKER\n",
    ".datacore/env/.env": "FIXTURE_API_KEY=fixture-value\n",
    "0-personal/org/inbox.org": "* TODO fixture task\n",
    "0-personal/journal/2026-09-26.md": "# fixture journal\n",
}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(F.GIT + ["-C", str(repo), *args], capture_output=True, text=True, timeout=60)


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def upgraded(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ins8")
    upstream = F.unpack(tmp / "up")
    _git(upstream.data, "branch", "-M", "main")
    data, home = tmp / "Data", tmp / "home"
    subprocess.run(F.GIT + ["clone", "-q", str(upstream.data), str(data)], check=True, timeout=120)
    home.mkdir()
    inst = F.Install(data, home, F.scrubbed_env(data, home))
    for step, cmd in F.GUIDE_STEPS:
        inst.run(cmd)
    for rel, body in LOCAL.items():
        (data / rel).parent.mkdir(parents=True, exist_ok=True)
        (data / rel).write_text(body)
    events = inst.run("python3 -c 'import sys; sys.path.insert(0, \".datacore/lib\"); "
                      "from ledger.log import EventLog; "
                      "EventLog(\"0-personal\", \"fixture\", sign=False).append(\"item.create\", "
                      "{\"id\": \"f1\", \"title\": \"fixture task\"})'")
    assert events.returncode == 0, events.stderr[-300:]
    before = {str(p.relative_to(data)): _digest(p) for p in
              [data / r for r in LOCAL] + sorted((data / "0-personal/.datacore/events").glob("*.jsonl"))}
    # upstream ships a release
    base = upstream.data / "CLAUDE.base.md"
    base.write_text(base.read_text() + "\nUPSTREAM-RELEASE-MARKER\n")
    ex = upstream.data / "install.yaml.example"
    ex.write_text(ex.read_text() + "# new option in this release\n")
    _git(upstream.data, "commit", "-qam", "release")
    results = []
    for cmd in UPGRADE:
        p = inst.run(cmd, timeout=90)
        results.append((cmd, p.returncode, (p.stdout + p.stderr)[-300:]))
    return inst, before, results


def test_the_harness_follows_the_upgrade_section():
    text = F.guide_text()
    section = text[text.index("## Upgrade Process"):]
    assert all(c in section for c in UPGRADE), "INSTALL.md's upgrade steps changed -- update UPGRADE"


def test_every_upgrade_step_succeeds(upgraded):
    _, _, results = upgraded
    failed = [f"`{c}` -> {rc}: {o}" for c, rc, o in results if rc != 0]
    assert failed == [], "\n".join(failed)


def test_upstream_release_arrived(upgraded):
    inst, _, _ = upgraded
    assert "UPSTREAM-RELEASE-MARKER" in (inst.data / "CLAUDE.base.md").read_text()


def test_local_settings_layers_and_history_are_intact(upgraded):
    inst, before, _ = upgraded
    changed = [rel for rel, d in before.items()
               if not (inst.data / rel).exists() or _digest(inst.data / rel) != d]
    assert changed == [], f"the upgrade changed or removed local state: {changed}"


def test_composed_context_keeps_the_private_layer(upgraded):
    inst, _, _ = upgraded
    composed = (inst.data / "CLAUDE.md").read_text()
    assert "UPSTREAM-RELEASE-MARKER" in composed, "composed context missed the release"
    assert "FIXTURE-PRIVATE-MARKER" in composed, "composed context dropped the private layer"


def test_history_still_verifies(upgraded):
    inst, _, _ = upgraded
    v = inst.run("python3 .datacore/lib/ledger_cli.py verify --space 0-personal")
    assert v.returncode == 0, v.stdout + v.stderr
