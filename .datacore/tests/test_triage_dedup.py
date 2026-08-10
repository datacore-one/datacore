"""Triage must recognise the same GitHub item across days.

On 2026-08-10, the first successful github-triage run after a 23-day outage
reported "created: 1". What it actually did was append a second copy of the
body to an already-DONE task from 2026-07-08, because:

  1. the dedup key was date-suffixed (gh-<repo>-<n>-<YYYY-MM-DD>), so the same
     issue produced a different identity every day and never matched itself;
  2. _find_task_by_id was a substring test over the whole file, which could not
     express "this id, allowing the legacy date suffix";
  3. _append_task_body matched the FIRST heading containing the text — an old
     entry whenever the heading repeats — and appended unconditionally.

Triage runs daily, so an un-fixed version grows one duplicate block per day on
every long-lived open issue.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "lib" / "triage_utils.py"
CREATOR = (Path(__file__).resolve().parent.parent
           / "modules" / "github" / "lib" / "task_creator.py")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def tu():
    return _load(LIB, "triage_utils_t")


@pytest.fixture()
def creator():
    sys.path.insert(0, str(LIB.parent))
    return _load(CREATOR, "task_creator_t")


DONE_TASK = """* Focus
** DONE Respond to fairDataSociety/fairdrive-theapp#593 — ci: fdp-play boot :AI:github:
SCHEDULED: <2026-07-08 Wed>
  :PROPERTIES:
  :CREATED: [2026-07-08 Wed 04:31]
  :ID: org-20260708-043112-b341d149
   :TRIAGE_ID:    gh-fairdrive-theapp-593-2026-07-08
   :GITHUB_URL:   https://github.com/fairDataSociety/fairdrive-theapp/issues/593
  :END:
   Mentioned in fairDataSociety/fairdrive-theapp#593: ci: fdp-play boot
   URL: https://github.com/fairDataSociety/fairdrive-theapp/issues/593
"""

OPEN_TASK = DONE_TASK.replace("** DONE ", "** TODO ")


# --- identity ---------------------------------------------------------------

def test_task_id_has_no_date(creator):
    tid = creator._make_task_id("fairDataSociety/fairdrive-theapp", 593)
    assert tid == "gh-fairdrive-theapp-593"


def test_task_id_is_stable_across_days(creator):
    a = creator._make_task_id("fairDataSociety/fairdrive-theapp", 593)
    b = creator._make_task_id("fairDataSociety/fairdrive-theapp", 593)
    assert a == b and not any(ch.isdigit() for ch in a.split("-")[-1][4:])


# --- dedup ------------------------------------------------------------------

def test_legacy_date_suffixed_id_still_matches(tu, tmp_path):
    """The exact 2026-08-10 failure: new id vs a task filed under the old scheme."""
    f = tmp_path / "next_actions.org"
    f.write_text(DONE_TASK)
    assert tu._find_task_by_id(f, "gh-fairdrive-theapp-593") is True


def test_exact_id_matches(tu, tmp_path):
    f = tmp_path / "next_actions.org"
    f.write_text(DONE_TASK.replace("gh-fairdrive-theapp-593-2026-07-08",
                                   "gh-fairdrive-theapp-593"))
    assert tu._find_task_by_id(f, "gh-fairdrive-theapp-593") is True


def test_different_issue_does_not_match(tu, tmp_path):
    f = tmp_path / "next_actions.org"
    f.write_text(DONE_TASK)
    assert tu._find_task_by_id(f, "gh-fairdrive-theapp-594") is False


def test_id_that_is_a_prefix_of_another_does_not_match(tu, tmp_path):
    """The old substring test matched #59 against #593."""
    f = tmp_path / "next_actions.org"
    f.write_text(DONE_TASK)
    assert tu._find_task_by_id(f, "gh-fairdrive-theapp-59") is False


def test_missing_file_is_not_a_match(tu, tmp_path):
    assert tu._find_task_by_id(tmp_path / "nope.org", "gh-x-1") is False


# --- append guards ----------------------------------------------------------

def test_never_appends_to_a_done_task(tu, tmp_path):
    f = tmp_path / "next_actions.org"
    f.write_text(DONE_TASK)
    before = f.read_text()
    tu._append_task_body(f, "Respond to fairDataSociety/fairdrive-theapp#593",
                         "Mentioned again\nURL: http://x")
    assert f.read_text() == before


def test_does_not_write_the_same_body_twice(tu, tmp_path):
    f = tmp_path / "next_actions.org"
    f.write_text(OPEN_TASK)
    body = ("Mentioned in fairDataSociety/fairdrive-theapp#593: ci: fdp-play boot\n"
            "URL: https://github.com/fairDataSociety/fairdrive-theapp/issues/593")
    tu._append_task_body(f, "Respond to fairDataSociety/fairdrive-theapp#593", body)
    assert f.read_text().count("Mentioned in fairDataSociety") == 1


def test_appends_new_body_to_an_open_task(tu, tmp_path):
    f = tmp_path / "next_actions.org"
    f.write_text(OPEN_TASK)
    tu._append_task_body(f, "Respond to fairDataSociety/fairdrive-theapp#593",
                         "Brand new context line")
    assert "Brand new context line" in f.read_text()


def test_repeated_runs_are_idempotent(tu, tmp_path):
    """Triage runs daily — N runs must not produce N copies."""
    f = tmp_path / "next_actions.org"
    f.write_text(OPEN_TASK)
    for _ in range(5):
        tu._append_task_body(f, "Respond to fairDataSociety/fairdrive-theapp#593",
                             "Repeated context")
    assert f.read_text().count("Repeated context") == 1
