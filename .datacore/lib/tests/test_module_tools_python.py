"""Module tools must run Python through the runtime's interpreter selection.

A bare 'python3' resolves to whatever interpreter is first on PATH. On a Mac
with Homebrew that interpreter cannot import org_workspace, pyyaml or the
other packages installed into .datacore/venv, so a module's tools register
but every call fails ("No module named 'org_workspace'" from inbox_count on a
fresh install, 2026-09-24).
"""
import re
from pathlib import Path

MODULES = Path(__file__).resolve().parents[2] / "modules"
BARE = re.compile(r"""execFile(?:Async|Sync)?\(\s*['"]python3['"]""")


def test_no_module_tool_hardcodes_python3():
    offenders = [str(p.relative_to(MODULES)) for p in sorted(MODULES.glob("*/tools/index.js"))
                 if BARE.search(p.read_text())]
    assert offenders == []
