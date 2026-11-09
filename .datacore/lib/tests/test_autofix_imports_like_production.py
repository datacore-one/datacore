"""jobs.autofix must import the way job_verify imports it.

The boundary tests put lib/jobs on sys.path and `import autofix` bare, so a bare
`from fix_check import ...` inside it passed every test and failed on every
host: job_verify does `from jobs.autofix import delegate` with only lib on the
path. Result, 2026-09-20 -> 09-22: "could NOT delegate <job> (autofix
unavailable: ModuleNotFoundError: No module named 'fix_check'); escalating to
the operator" on every first failure, on every host. The delegation path the
owner asked for was wired, tested, and had never once run.

This test spawns a fresh interpreter with the production path, and nothing
else, on sys.path.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]


def test_autofix_imports_with_only_lib_on_the_path():
    code = (
        "import sys\n"
        f"sys.path[:0] = [{str(LIB)!r}]\n"
        "from jobs.autofix import delegate, escalations, repair_body\n"
        "print('ok')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          cwd=str(Path.home()), timeout=60)
    assert proc.returncode == 0 and proc.stdout.strip() == "ok", proc.stderr[-800:]


def test_no_module_under_jobs_imports_a_sibling_bare():
    """The class, not the instance: every sibling import under lib/jobs names
    the package, so it resolves the same way from a test and from a host."""
    siblings = {p.stem for p in (LIB / "jobs").glob("*.py") if p.stem != "__init__"}
    bad = []
    for p in sorted((LIB / "jobs").glob("*.py")):
        for n, line in enumerate(p.read_text().splitlines(), 1):
            head = line.strip().split()
            if len(head) >= 2 and head[0] in ("from", "import") and head[1] in siblings:
                bad.append(f"{p.name}:{n}: {line.strip()}")
    assert not bad, "bare sibling imports under lib/jobs:\n  " + "\n  ".join(bad)
