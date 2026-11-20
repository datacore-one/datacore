"""The scoreboard says so when its Python has no test runner.

2026-11-19: started with the system python3, which has no pytest, the board
reported all 254 promises red and gave no reason.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import promise_evals  # noqa: E402


def test_a_python_without_pytest_is_named_once(tmp_path, monkeypatch, capsys):
    fake = tmp_path / "python-without-pytest"
    fake.write_text("#!/bin/sh\necho \"$0: No module named pytest\" >&2\nexit 1\n")
    fake.chmod(0o755)
    monkeypatch.setattr(promise_evals, "PY", str(fake))
    monkeypatch.setattr(promise_evals, "_NO_RUNNER_SAID", [])
    files = [tmp_path / "test_a.py", tmp_path / "test_b.py"]
    assert promise_evals.run_suite(tmp_path, files, {}) == {"test_a.py": False, "test_b.py": False}
    promise_evals.run_suite(tmp_path, files, {})
    said = capsys.readouterr().err
    assert said.count("NOT RUN") == 1 and "has no pytest" in said
