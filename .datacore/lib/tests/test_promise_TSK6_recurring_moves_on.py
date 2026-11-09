"""TSK-6: A recurring task moves to its next date when I finish it, instead of
closing for good.

Kind: deterministic. Every way a task gets finished, against a tmp space:
the core adapter (`complete`, `update --state DONE`: the GTD tools and agents)
and the desktop app's write path (datacored.adapters.org.set_state).

Seeded failure: a task with `SCHEDULED: <2026-09-20 Sun +1w>` marked DONE.
It must stay open and move to 2026-09-27, keeping its :ID:. Verified red by
forcing the plain keyword swap (node.todo = "DONE"), the fallback the app uses
when transition() raises.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
ROOT = LIB.parent.parent
APP = ROOT / "2-datacore" / "2-projects" / "datacore-app" / "daemon"
sys.path.insert(0, str(LIB))

ADAPTER = LIB / "org_workspace_adapter.py"
HEADER = "#+SEQ_TODO: TODO(t) NEXT(n) WAITING(w@) REVIEW(r!) | DONE(d!) DEFERRED(f@) CANCELLED(c@)\n"
CASES = [  # (repeater cookie, next date expected from 2026-09-20)
    ("+1w", "2026-09-27"),
    ("+1m", "2026-10-20"),
]


def _space(tmp_path: Path, cookie: str) -> Path:
    org = tmp_path / "5-evals" / "org"
    org.mkdir(parents=True)
    f = org / "next_actions.org"
    f.write_text(HEADER
                 + f"* TODO Pay the rent\nSCHEDULED: <2026-09-20 Sun {cookie}>\n"
                   ":PROPERTIES:\n:ID: org-t6-rent\n:END:\n"
                 + "* TODO Unrelated\n:PROPERTIES:\n:ID: org-t6-other\n:END:\n",
                 encoding="utf-8")
    return f


def _rent(f: Path) -> tuple[str, str | None, str]:
    text = f.read_text(encoding="utf-8")
    block = text.split("Pay the rent", 1)
    head = text[: len(block[0])].rsplit("\n", 1)[-1]
    state = re.match(r"^\*+ ([A-Z]+) ", head + " ").group(1)
    m = re.search(r"SCHEDULED: <(\d{4}-\d{2}-\d{2})", block[1].split("\n* ", 1)[0])
    return state, (m.group(1) if m else None), block[1]


def _assert_moved_on(f: Path, expected: str, how: str):
    state, when, rest = _rent(f)
    assert state not in ("DONE", "CANCELLED"), f"{how}: the recurring task closed for good ({state})"
    assert when == expected, f"{how}: next date is {when}, expected {expected}"
    assert ":ID: org-t6-rent" in rest, f"{how}: the recurring task lost its identity"


def _adapter(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700, exist_ok=True)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    return lambda *a: subprocess.run([sys.executable, str(ADAPTER), *a],
                                     capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("cookie,expected", CASES)
def test_completing_through_the_task_tools_moves_it_on(tmp_path, monkeypatch, cookie, expected):
    run = _adapter(tmp_path, monkeypatch)
    f = _space(tmp_path, cookie)
    r = run("complete", "--file", str(f), "--id", "org-t6-rent")
    assert r.returncode == 0, r.stdout + r.stderr
    _assert_moved_on(f, expected, "adapter complete")


@pytest.mark.parametrize("cookie,expected", CASES)
def test_setting_done_through_the_task_tools_moves_it_on(tmp_path, monkeypatch, cookie, expected):
    run = _adapter(tmp_path, monkeypatch)
    f = _space(tmp_path, cookie)
    r = run("update", "--file", str(f), "--id", "org-t6-rent", "--state", "DONE")
    assert r.returncode == 0, r.stdout + r.stderr
    _assert_moved_on(f, expected, "adapter update --state DONE")


@pytest.mark.parametrize("cookie,expected", CASES)
def test_finishing_in_the_app_moves_it_on(tmp_path, cookie, expected):
    sys.path.insert(0, str(APP))
    from datacored.adapters import org as app_org
    app_org.invalidate_cache()
    f = _space(tmp_path, cookie)
    assert app_org.set_state(tmp_path / "5-evals", "org-t6-rent", "DONE")
    _assert_moved_on(f, expected, "app set_state DONE")
