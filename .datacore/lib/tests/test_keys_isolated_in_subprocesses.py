"""A process a test starts must not sign with, or write into, this machine's real keys.

The suite's key isolation (conftest `_isolated_ledger_keys`) patched the module in
the test process only. A test that runs ledger_cli.py or any script as a
subprocess got the real ~/.datacore/keys: on 2026-10-27 dayshift's real key
folder held a fresh `miles.key` (and `drill.key`) written by test runs. Every
later write as miles on that machine signed with a key the registry does not
know, and LED-4's verify failed on it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
REAL = (Path.home() / ".datacore" / "keys").resolve()


def test_a_subprocess_resolves_a_key_folder_outside_the_real_one():
    code = "import sys; sys.path.insert(0, %r); from ledger import keys; print(keys.DEFAULT_KEYS_DIR.resolve())" % str(LIB)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert Path(out.stdout.strip()) != REAL, (
        "a process started by a test signs with this machine's real keys "
        f"({REAL}); test keys land there and real writers' keys are used by tests")
