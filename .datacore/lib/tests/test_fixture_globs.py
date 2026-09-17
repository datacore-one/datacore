"""The fixture harvester reads a glob artifact's newest match, like the verifier.

Before 2026-09-17 a `*` was quoted for the remote shell (never expanded) and
joined literally onto the local path, so every glob artifact was "could not
read" and its fixture had to be written by hand.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

from jobs import fixtures as F  # noqa: E402


def test_a_quoted_glob_still_expands_in_a_real_shell(tmp_path):
    home = tmp_path / "home"
    d = home / "Data" / "reports dir"
    d.mkdir(parents=True)
    (d / "report-2026-09-16.md").write_text("old\n")
    time.sleep(0.01)
    (d / "report-2026-09-17.md").write_text("newest\n")
    quoted = F._quote_remote("~/Data/reports dir/report-*.md")
    out = subprocess.run(["bash", "-c", f"ls -t -- {quoted} | head -n 1"], capture_output=True,
                         text=True, env={**os.environ, "HOME": str(home)})
    assert out.stdout.strip().endswith("report-2026-09-17.md"), (quoted, out.stdout, out.stderr)


def test_a_local_glob_reads_the_newest_match(tmp_path, monkeypatch):
    d = tmp_path / "state"
    d.mkdir()
    (d / "a-1.txt").write_text("older\n")
    time.sleep(0.01)
    (d / "a-2.txt").write_text("newer\n")
    monkeypatch.setattr(F, "HOME", tmp_path)
    assert F._read_artifact("~/state/a-*.txt", "mac") == "newer\n"
    assert F._read_artifact("~/state/none-*.txt", "mac") is None
