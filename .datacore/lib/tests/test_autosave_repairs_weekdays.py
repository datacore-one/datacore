"""A wrong weekday is repaired in the autosave, not a reason to stop the cycle (2026-09-23).

An agent wrote `[2026-09-23 Tue]` (a Wednesday) in 6-meridian's inbox; the
pre-commit hook refused the autosave and the whole phase-1 cycle failed. The
weekday is derived from the date, so the autosave corrects it with the hook's
own validator and commits.
"""
import pathlib, subprocess, sys
LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
import ledger_transport as lt  # noqa: E402


def _repo(tmp_path, text):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    org = tmp_path / "org"; org.mkdir()
    f = org / "inbox.org"; f.write_text(text)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return f


def _staged(tmp_path, rel):
    return subprocess.run(["git", "-C", str(tmp_path), "show", f":{rel}"],
                          capture_output=True, text=True, check=True).stdout


def test_a_wrong_weekday_is_repaired_and_restaged(tmp_path):
    f = _repo(tmp_path, "* TODO x\n  :PROPERTIES:\n  :CREATED: [2026-09-23 Tue]\n  :END:\n")
    assert lt._repair_weekdays(tmp_path) == ["org/inbox.org"]
    assert "[2026-09-23 Wed]" in f.read_text()
    assert "[2026-09-23 Wed]" in _staged(tmp_path, "org/inbox.org"), "the repair is what gets committed"


def test_correct_dates_and_other_files_are_untouched(tmp_path):
    f = _repo(tmp_path, "* TODO x\n  SCHEDULED: <2026-09-23 Wed>\n")
    (tmp_path / "notes.txt").write_text("2026-09-23 Tue is not an org date\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    assert lt._repair_weekdays(tmp_path) == []
    assert f.read_text() == "* TODO x\n  SCHEDULED: <2026-09-23 Wed>\n"
