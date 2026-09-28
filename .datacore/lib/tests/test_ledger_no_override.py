"""Nobody goes past the ledger (owner decision 2026-09-28).

On 2026-09-27 05:03 the box's inbox job hit the correct StaleLogError, then
overwrote and deleted the sequence witness and appended with
DATACORE_HWM_OVERRIDE=1 -- the two exits the error message itself named. That
forked Winston's 0-personal log. These tests pin the repair:

  * no environment flag lets an append past the refusal, attended or not;
  * the message names the situation and says stop / tell the owner / the
    runbook -- never `rm <witness>` or an override;
  * a refusal is RECORDED as a stop record beside the witness, and that record
    keeps refusing after the witness is deleted, so deleting it is no bypass;
  * the owner's procedure (move the witness and the stop record aside to
    seq-hwm-retired/, never delete) reopens the log, and a genuine converge
    (the log catches up) clears the stop by itself.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
DATA = LIB.parents[1]
sys.path.insert(0, str(LIB))

from ledger.log import CorruptLogError, EventLog, StaleLogError, stop_path, witness_path  # noqa: E402


def _rewound(tmp_path):
    space = tmp_path / "9-x"
    log = EventLog(space, "mac", sign=False)
    for i in range(3):
        log.append("item.create", {"id": f"a{i}", "title": f"t{i}"})
    f = space / ".datacore" / "events" / "mac.jsonl"
    full = f.read_text()
    f.write_text("".join(full.splitlines(keepends=True)[:1]))   # a bad checkout
    return space, f, full


def _append(space, ident="z"):
    return EventLog(space, "mac", sign=False).append("item.create", {"id": ident, "title": "x"})


def test_the_override_flag_no_longer_lets_an_append_past_a_rewound_log(tmp_path, monkeypatch):
    space, f, _ = _rewound(tmp_path)
    before = f.read_bytes()
    monkeypatch.setenv("DATACORE_HWM_OVERRIDE", "1")
    with pytest.raises(StaleLogError):
        _append(space)
    assert f.read_bytes() == before


def test_the_override_flag_does_not_accept_an_unreadable_witness(tmp_path, monkeypatch):
    space, f, _ = _rewound(tmp_path)
    witness_path(f).write_text("not-a-number")
    monkeypatch.setenv("DATACORE_HWM_OVERRIDE", "1")
    with pytest.raises(CorruptLogError):
        _append(space)


def test_the_message_says_stop_and_owner_never_how_to_bypass(tmp_path):
    space, f, _ = _rewound(tmp_path)
    with pytest.raises(StaleLogError) as exc:
        _append(space)
    msg = str(exc.value)
    assert "DATACORE_HWM_OVERRIDE" not in msg
    assert not re.search(r"\brm\s", msg) and "unlink" not in msg
    assert "retry" not in msg.lower()          # nightshift files "retry" as transient
    low = msg.lower()
    assert "stop" in low and "owner" in low and "the firm" in low
    assert "recovery.md" in msg
    assert "mac.jsonl" in msg and "seq 0" in msg and "seq 2" in msg


def test_a_refusal_is_recorded_beside_the_witness(tmp_path):
    space, f, _ = _rewound(tmp_path)
    with pytest.raises(StaleLogError):
        _append(space)
    rec = stop_path(f)
    assert rec.parent == witness_path(f).parent
    data = json.loads(rec.read_text())
    assert data["log"] == "mac.jsonl" and data["tail"] == 0 and data["hwm"] == 2
    assert data["at"]


def test_deleting_the_witness_after_a_refusal_does_not_reopen_the_log(tmp_path):
    """Exactly the 2026-09-27 move: echo/rm the witness, then append."""
    space, f, _ = _rewound(tmp_path)
    with pytest.raises(StaleLogError):
        _append(space)
    witness_path(f).unlink()
    witness_path(f).with_suffix(".hash").unlink()
    before = f.read_bytes()
    with pytest.raises(StaleLogError):
        _append(space)
    assert f.read_bytes() == before
    # and overwriting it with a lower number is no bypass either
    witness_path(f).write_text("0")
    with pytest.raises(StaleLogError):
        _append(space)


def test_verify_reports_a_stop_whose_witness_was_deleted(tmp_path):
    space, f, _ = _rewound(tmp_path)
    with pytest.raises(StaleLogError):
        _append(space)
    witness_path(f).unlink()
    witness_path(f).with_suffix(".hash").unlink()
    r = subprocess.run([sys.executable, str(LIB / "ledger_cli.py"), "verify", "--space", str(space)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "TRUNCATED" in r.stderr


def test_the_owner_procedure_moving_both_aside_reopens_the_log(tmp_path):
    space, f, _ = _rewound(tmp_path)
    with pytest.raises(StaleLogError):
        _append(space)
    hwm_dir = witness_path(f).parent
    retired = hwm_dir.parent / "seq-hwm-retired"
    retired.mkdir()
    for p in (witness_path(f), witness_path(f).with_suffix(".hash"), stop_path(f)):
        p.rename(retired / (p.name + ".retired"))
    ev = _append(space, "after")
    assert ev.seq == 1
    r = subprocess.run([sys.executable, str(LIB / "ledger_cli.py"), "verify", "--space", str(space)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_a_genuine_converge_clears_the_stop_by_itself(tmp_path):
    space, f, full = _rewound(tmp_path)
    with pytest.raises(StaleLogError):
        _append(space)
    f.write_text(full)                         # the missing events arrive
    ev = _append(space, "caught-up")
    assert ev.seq == 3
    assert not stop_path(f).exists()


def test_ledger_cli_stopped_lists_binding_stops(tmp_path):
    space, f, full = _rewound(tmp_path)
    cli = [sys.executable, str(LIB / "ledger_cli.py"), "stopped", "--space", str(space)]
    assert subprocess.run(cli, capture_output=True, text=True, timeout=60).returncode == 0
    with pytest.raises(StaleLogError):
        _append(space)
    r = subprocess.run(cli, capture_output=True, text=True, timeout=60)
    assert r.returncode == 3 and "mac.jsonl" in r.stdout
    f.write_text(full)                         # caught up: the stop no longer binds
    assert subprocess.run(cli, capture_output=True, text=True, timeout=60).returncode == 0


def _code_files():
    out = subprocess.run(["git", "-C", str(DATA), "ls-files", ".datacore/lib"],
                         capture_output=True, text=True, timeout=30).stdout.splitlines()
    for rel in out:
        p = DATA / rel
        if "/tests/" in rel or not p.suffix in (".py", ".sh"):
            continue
        yield p
    mods = DATA / ".datacore" / "modules"
    for pattern in ("*/lib/**/*.py", "*/lib/**/*.sh", "*/server/**/*.sh", "*/server/**/*.py"):
        for p in mods.glob(pattern):
            if "/tests/" not in str(p) and "node_modules" not in str(p):
                yield p


#: Reading the flag, or setting it: what a bypass looks like in code. The guards
#: that REFUSE it (config_protection.py, tool_effects.yaml) name it as a
#: pattern, which this does not match.
_USES_OVERRIDE = re.compile(r"(environ|getenv)[^\n]{0,24}HWM_OVERRIDE|HWM_OVERRIDE\s*=\s*['\"]?1|\$\{?DATACORE_HWM_OVERRIDE")


def test_no_code_path_honours_an_override_flag():
    offenders = [str(p) for p in _code_files()
                 if p.is_file() and _USES_OVERRIDE.search(p.read_text(errors="replace"))]
    assert not offenders, f"code still reads or sets the ledger override: {offenders}"


def _stale_log_section() -> str:
    text = (DATA / ".datacore" / "docs" / "recovery.md").read_text()
    start = text.index("## Stale log")
    end = text.index("\n## ", start + 1)
    return text[start:end]


def test_the_runbook_moves_the_witness_aside_and_never_deletes_it():
    body = _stale_log_section()
    assert "seq-hwm-retired" in body
    assert ".unlink(" not in body and not any(
        l.strip().startswith(("rm ", "rm -")) for l in body.splitlines())
    assert "HWM_OVERRIDE" not in body


def test_the_runbook_is_the_owners_and_ends_in_verify_and_invariants():
    body = _stale_log_section()
    low = body.lower()
    assert "owner" in low and "the firm" in low
    code = "".join(re.findall(r"```(?:bash|sh)?\n(.*?)```", body, re.S))
    assert "merge --no-edit" in code and "rebase" not in code and " pull" not in code
    assert "ledger_cli.py verify" in body and "ledger_invariants.py --quick" in body
