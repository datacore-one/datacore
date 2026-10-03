"""O4 (formal): the projector's Phase 4 guarantees, proved in Lean.

Ledger upgrade Phase 4, eval O4 (PLAN.md). The model of the org round-trip is
`.datacore/specs/datacore-lean/DatacoreSpec/Projector.lean` (built with the rest of
DatacoreSpec, sorry-free). The guarantees are stated against it in the eval-owned
`O4Targets.lean` beside this file:

  * rendering after fold is idempotent, for every space;
  * a recorded conflict never blocks rendering, or ingesting, another item.

Green when `O4Targets.lean` compiles with no error. Red today: the isolation
checks are refuted by `decide` over every two-item space (the model's section 3
names the witnesses), and the three general theorems are not proved.

A machine without Lean fails this eval ("could not run"); it never skips.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2] / "specs" / "datacore-lean"
TARGETS = HERE / "O4Targets.lean"


def _lake() -> str:
    found = shutil.which("lake") or str(Path.home() / ".elan" / "bin" / "lake")
    assert Path(found).exists(), "could not run: Lean's `lake` is not installed (elan)"
    return found


def test_the_projector_guarantees_are_proved():
    lake = _lake()
    env = {**os.environ, "PATH": f"{Path(lake).parent}:{os.environ.get('PATH', '')}"}
    built = subprocess.run([lake, "build", "DatacoreSpec.Projector"], cwd=PROJECT, env=env,
                           capture_output=True, text=True, timeout=1800)
    assert built.returncode == 0, f"SETUP: the model does not build:\n{built.stdout[-2000:]}{built.stderr[-1000:]}"

    run = subprocess.run([lake, "env", "lean", str(TARGETS)], cwd=PROJECT, env=env,
                         capture_output=True, text=True, timeout=1800)
    out = run.stdout + run.stderr
    errors: list[str] = []
    current = None                          # an error and its indented continuation, on one line
    for line in out.splitlines():
        if re.search(r":\d+:\d+: ", line):
            current = None
            if ": error" in line:
                errors.append(line.replace(str(HERE) + "/", ""))
                current = len(errors) - 1
        elif current is not None:
            errors[current] += " " + line.strip()
    assert run.returncode == 0 and not errors, \
        "the Phase 4 projector guarantees do not hold in the model:\n  " + "\n  ".join(errors)
