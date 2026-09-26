"""MEM-63: A module never stores my data inside itself. My data lives in my spaces, and
module code stays separate.

Kind: production contract (read-only: this machine's .datacore/modules, and each agent
host's over read-only ssh) + a fixture self-test of the rule.
The rule (ENG-2026-09-07-023, pinned): a module ships code, manifest, hooks, docs and at
most a generic sample; user content lives outside it. module_data_migrate.py names the
in-module places user state accumulates: `data/` and `state/`. Checked: no module holds
user files there (samples/examples/fixtures, README and .gitkeep excepted), and no module
holds a database or event log (*.db, *.sqlite*, *.jsonl) outside its tests.

Seeded failure: a fixture module with data/scan_cache.json and state/app.db -> both
flagged (test_the_rule_flags_user_files_in_a_module); verified.
"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MODULES = ROOT / ".datacore" / "modules"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
PRUNE = {"node_modules", ".git", ".venv", "venv", "tests", "__pycache__"}
SAMPLE_WORDS = ("sample", "example", "fixture")


def _is_sample(rel: Path) -> bool:
    s = str(rel).lower()
    return rel.name in (".gitkeep",) or rel.name.upper().startswith("README") or any(w in s for w in SAMPLE_WORDS)


def module_data(modules: Path) -> list[str]:
    found = []
    for mod in sorted(p for p in modules.iterdir() if p.is_dir() and p.name not in PRUNE):
        for p in mod.rglob("*"):
            rel = p.relative_to(mod)
            if any(part in PRUNE for part in rel.parts) or not p.is_file() or p.suffix == ".pyc":
                continue
            in_store = rel.parts[0] in ("data", "state")
            is_db = p.suffix in (".db", ".jsonl") or ".sqlite" in p.name
            if (in_store or is_db) and not _is_sample(rel):
                found.append(f"{mod.name}/{rel}")
    return sorted(found)


def test_the_rule_flags_user_files_in_a_module(tmp_path):
    m = tmp_path / "mail"
    (m / "data").mkdir(parents=True)
    (m / "state").mkdir()
    (m / "lib").mkdir()
    (m / "data" / "scan_cache.json").write_text("{}")
    (m / "data" / "feeds.example.yaml").write_text("x: 1")
    (m / "state" / "app.db").write_text("")
    (m / "lib" / "mail.py").write_text("")
    assert module_data(tmp_path) == ["mail/data/scan_cache.json", "mail/state/app.db"]


@pytest.mark.production
def test_no_module_on_this_machine_holds_user_data():
    found = module_data(MODULES)
    assert not found, f"{len(found)} user-data files live inside modules, e.g. {found[:8]}"


@pytest.mark.production
@pytest.mark.parametrize("host", ["winston", "nightshift", "hermes", "plur-claw"])
def test_no_module_on_an_agent_host_holds_user_data(host):
    cmd = ("for d in ~/Data/.datacore/modules /root/Data/.datacore/modules; do [ -d $d ] || continue; "
           "cd $d && find . \\( -name node_modules -o -name .git -o -name .venv -o -name venv -o -name tests "
           "-o -name __pycache__ \\) -prune -o -type f \\( -regextype posix-extended -regex '\\./[^/]+/(data|state)/.*' "
           "-o -name '*.db' -o -name '*.jsonl' -o -name '*.sqlite*' \\) -print "
           "| grep -v -i -E 'sample|example|fixture|/README|\\.gitkeep$|\\.pyc$' | head -50; done; true")
    r = subprocess.run([*SSH, host, cmd], capture_output=True, text=True, timeout=50)
    assert r.returncode == 0, f"{host}: could not read ({r.stderr.strip()[-160:]})"
    found = [l for l in r.stdout.splitlines() if l.strip()]
    assert not found, f"{host}: user-data files inside modules, e.g. {found[:8]}"
