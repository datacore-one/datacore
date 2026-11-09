"""MOD-3: Removing a module removes everything it added, and never deletes my data.

Owner decision 2026-09-27: "remove it, but don't delete user data". Disabling a
module means removing it; the module's code, commands and schedules go, while
what it holds of mine (its `data/`, `state/` and `settings.local.yaml`, the three
components module_data_migrate.py already treats as user state) and anything it
wrote into my spaces are kept.

Kind: deterministic. A tmp installation holds a module with user data, a declared
schedule and a journal line it wrote in a space; `module_remove.py <name>` is run
with DATACORE_ROOT pointing at it.
  * the module folder is gone and its schedule is no longer in the job list;
  * every byte of its data/, state/ and settings.local.yaml still exists somewhere
    under the installation, and the command's output names where;
  * the space file the module wrote is untouched.

Seeded failure: module_remove.py deletes the module folder with shutil.rmtree and
nothing else -> the data is gone.
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parents[1]
REMOVE = LIB / "module_remove.py"

USER_FILES = {
    "data/holdings.json": '{"kept": true}\n',
    "state/cursor.txt": "2026-09-27T03:30:00Z\n",
    "settings.local.yaml": "api_budget: 3\n",
}
JOURNAL = "0-personal/notes/journals/2026-09-27.md"


def _install(tmp_path):
    root = tmp_path / "Data"
    mod = root / ".datacore/modules/leaving"
    (mod / "lib").mkdir(parents=True)
    (mod / "module.yaml").write_text(yaml.safe_dump({
        "name": "leaving", "version": "0.1.0",
        "schedules": [{"name": "leaving-nightly", "cmd": "python3 .datacore/modules/leaving/lib/run.py"}]}))
    (mod / "lib/run.py").write_text("print('hi')\n")
    for rel, body in USER_FILES.items():
        (mod / rel).parent.mkdir(parents=True, exist_ok=True)
        (mod / rel).write_text(body)
    jobs = root / ".datacore/lib/jobs"
    jobs.mkdir(parents=True)
    (jobs / "manifest.yaml").write_text(yaml.safe_dump({"jobs": [
        {"name": "leaving-nightly", "cmd": "python3 .datacore/modules/leaving/lib/run.py"},
        {"name": "stays", "cmd": "python3 .datacore/lib/stays.py"}]}))
    j = root / JOURNAL
    j.parent.mkdir(parents=True)
    j.write_text("- 09:00 leaving module logged a note\n")
    return root


def _digests(root):
    out = {}
    for p in root.rglob("*"):
        if p.is_file():
            out.setdefault(hashlib.sha256(p.read_bytes()).hexdigest(), []).append(p)
    return out


def test_removing_a_module_keeps_my_data_and_takes_its_schedules(tmp_path):
    assert REMOVE.is_file(), f"no module removal command: {REMOVE}"
    root = _install(tmp_path)
    journal_before = (root / JOURNAL).read_bytes()
    env = {**os.environ, "DATACORE_ROOT": str(root), "HOME": str(tmp_path / "home"),
           "DATACORE_STATE": str(tmp_path / "home/.datacore/state")}
    p = subprocess.run([sys.executable, str(REMOVE), "leaving"], env=env, cwd=root,
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr

    assert not (root / ".datacore/modules/leaving").exists(), "the module is still installed"
    names = [j.get("name") for j in (yaml.safe_load(
        (root / ".datacore/lib/jobs/manifest.yaml").read_text()) or {}).get("jobs") or []]
    assert "leaving-nightly" not in names, "the removed module's schedule is still declared"
    assert "stays" in names, "removal took a schedule that was not the module's"

    after = _digests(root)
    for rel, body in USER_FILES.items():
        h = hashlib.sha256(body.encode()).hexdigest()
        assert h in after, f"user data deleted with the module: {rel}"
        kept = after[h][0]
        assert kept.parent.name in (p.stdout + p.stderr) or str(kept) in (p.stdout + p.stderr), (
            f"the output does not say where {rel} was kept ({kept})")
    assert (root / JOURNAL).read_bytes() == journal_before, "a space file the module wrote was changed"
