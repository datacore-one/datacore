"""Tests for ledger_cli.py -- the operator CLI over the event-ledger substrate.

Exercised as a real subprocess (not imported) so the argparse wiring,
exit codes, and stdout/stderr discipline are verified exactly as an
operator or script would see them. `sys.executable` (not "python3") keeps
this hermetic under whatever interpreter is running the test suite.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

CLI = Path(__file__).parent.parent / "ledger_cli.py"


def _env(tmp_path, actor="test-actor"):
    """A minimal, hermetic env: DATACORE_ACTOR set, DATACORE_LEDGER_SIGN
    unset regardless of the ambient environment, so appended events are
    unsigned by default (Task 1.4b) and no key material is touched."""
    env = dict(os.environ)
    env.pop("DATACORE_LEDGER_SIGN", None)
    env["DATACORE_ACTOR"] = actor
    env["HOME"] = str(tmp_path)  # keep ~/.datacore/keys out of the picture entirely
    return env


def run_cli(*args, tmp_path, actor="test-actor"):
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        env=_env(tmp_path, actor=actor),
    )


def test_append_then_verify_ok(tmp_path):
    space = tmp_path / "space"
    space.mkdir()

    r1 = run_cli("append", "--space", str(space), "--type", "item.create",
                  "--payload", json.dumps({"id": "t1", "title": "Ship it"}), tmp_path=tmp_path)
    assert r1.returncode == 0, r1.stderr
    out1 = json.loads(r1.stdout)
    assert "hash" in out1 and "hlc" in out1

    r2 = run_cli("append", "--space", str(space), "--type", "item.claim",
                  "--payload", json.dumps({"id": "t1"}), tmp_path=tmp_path)
    assert r2.returncode == 0, r2.stderr
    out2 = json.loads(r2.stdout)
    assert out2["hash"] != out1["hash"]

    r3 = run_cli("verify", "--space", str(space), tmp_path=tmp_path)
    assert r3.returncode == 0, r3.stderr
    assert r3.stdout.strip() == "OK 1 files 2 events"
    assert r3.stderr == ""


def test_items_and_balances_after_create_claim_spend(tmp_path):
    space = tmp_path / "space"
    space.mkdir()

    run_cli("append", "--space", str(space), "--type", "item.create",
             "--payload", json.dumps({"id": "t1", "title": "Ship it"}), tmp_path=tmp_path)
    run_cli("append", "--space", str(space), "--type", "item.claim",
             "--payload", json.dumps({"id": "t1"}), tmp_path=tmp_path)
    r_spend = run_cli("append", "--space", str(space), "--type", "spend.record",
                       "--payload", json.dumps({"cents": 500}), tmp_path=tmp_path)
    assert r_spend.returncode == 0, r_spend.stderr

    r_items = run_cli("items", "--space", str(space), tmp_path=tmp_path)
    assert r_items.returncode == 0, r_items.stderr
    lines = [json.loads(line) for line in r_items.stdout.splitlines()]
    assert lines == [{"id": "t1", "title": "Ship it", "owner": "test-actor", "status": "claimed"}]

    # index.db was actually created under .datacore/state/ledger/
    assert (space / ".datacore" / "state" / "ledger" / "index.db").exists()

    r_balances = run_cli("balances", "--space", str(space), tmp_path=tmp_path)
    assert r_balances.returncode == 0, r_balances.stderr
    assert json.loads(r_balances.stdout) == {"test-actor": 500}


def test_items_and_balances_on_poisoned_space_exit_cleanly(tmp_path):
    """A space with malformed events -- an item-type event missing 'id' and
    spend.record entries with invalid 'cents' -- must not brick items/
    balances. fold()'s poison-event defense (final-review wave) routes
    each to orphans; both commands exit 0 with no traceback and the
    legitimate events still fold correctly."""
    space = tmp_path / "space"
    space.mkdir()

    run_cli("append", "--space", str(space), "--type", "item.create",
             "--payload", json.dumps({"id": "t1", "title": "Real task"}), tmp_path=tmp_path)
    # poison: item.claim with no "id" at all
    run_cli("append", "--space", str(space), "--type", "item.claim",
             "--payload", json.dumps({}), tmp_path=tmp_path)
    # poison: spend.record with a negative cents value
    run_cli("append", "--space", str(space), "--type", "spend.record",
             "--payload", json.dumps({"cents": -100}), tmp_path=tmp_path)
    # poison: spend.record with non-int cents
    run_cli("append", "--space", str(space), "--type", "spend.record",
             "--payload", json.dumps({"cents": "not-a-number"}), tmp_path=tmp_path)
    # legitimate spend, must still be counted
    run_cli("append", "--space", str(space), "--type", "spend.record",
             "--payload", json.dumps({"cents": 250}), tmp_path=tmp_path)

    r_items = run_cli("items", "--space", str(space), tmp_path=tmp_path)
    assert r_items.returncode == 0, r_items.stderr
    assert "Traceback" not in r_items.stderr
    lines = [json.loads(line) for line in r_items.stdout.splitlines()]
    assert lines == [{"id": "t1", "title": "Real task", "owner": None, "status": "created"}]

    r_balances = run_cli("balances", "--space", str(space), tmp_path=tmp_path)
    assert r_balances.returncode == 0, r_balances.stderr
    assert "Traceback" not in r_balances.stderr
    assert json.loads(r_balances.stdout) == {"test-actor": 250}


def test_items_filters_by_status_and_owner(tmp_path):
    space = tmp_path / "space"
    space.mkdir()

    run_cli("append", "--space", str(space), "--type", "item.create",
             "--payload", json.dumps({"id": "t1", "title": "One"}), tmp_path=tmp_path, actor="mac")
    run_cli("append", "--space", str(space), "--type", "item.claim",
             "--payload", json.dumps({"id": "t1"}), tmp_path=tmp_path, actor="mac")
    run_cli("append", "--space", str(space), "--type", "item.create",
             "--payload", json.dumps({"id": "t2", "title": "Two"}), tmp_path=tmp_path, actor="pi")
    run_cli("append", "--space", str(space), "--type", "item.claim",
             "--payload", json.dumps({"id": "t2"}), tmp_path=tmp_path, actor="pi")

    r = run_cli("items", "--space", str(space), "--status", "claimed", "--owner", "pi", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    lines = [json.loads(line) for line in r.stdout.splitlines()]
    assert lines == [{"id": "t2", "title": "Two", "owner": "pi", "status": "claimed"}]

    r = run_cli("items", "--space", str(space), "--owner", "mac", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    lines = [json.loads(line) for line in r.stdout.splitlines()]
    assert lines == [{"id": "t1", "title": "One", "owner": "mac", "status": "claimed"}]


def test_tampered_file_verify_fails(tmp_path):
    space = tmp_path / "space"
    space.mkdir()

    run_cli("append", "--space", str(space), "--type", "item.create",
             "--payload", json.dumps({"id": "t1", "title": "Ship it"}), tmp_path=tmp_path)
    run_cli("append", "--space", str(space), "--type", "item.claim",
             "--payload", json.dumps({"id": "t1"}), tmp_path=tmp_path)

    log_file = space / ".datacore" / "events" / "test-actor.jsonl"
    lines = log_file.read_text().splitlines()
    tampered = json.loads(lines[0])
    tampered["payload"] = {"id": "TAMPERED"}
    lines[0] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
    log_file.write_text("\n".join(lines) + "\n")

    r = run_cli("verify", "--space", str(space), tmp_path=tmp_path)
    assert r.returncode == 1
    assert r.stdout == ""
    assert "line 1" in r.stderr
    assert "hash mismatch" in r.stderr
    assert "Traceback" not in r.stderr


def test_bad_payload_json_exits_2(tmp_path):
    space = tmp_path / "space"
    space.mkdir()

    r = run_cli("append", "--space", str(space), "--type", "item.create",
                "--payload", "{not json", tmp_path=tmp_path)
    assert r.returncode == 2
    assert r.stdout == ""
    assert "Traceback" not in r.stderr


def test_payload_not_a_dict_exits_2(tmp_path):
    space = tmp_path / "space"
    space.mkdir()

    r = run_cli("append", "--space", str(space), "--type", "item.create",
                "--payload", "[1, 2, 3]", tmp_path=tmp_path)
    assert r.returncode == 2
    assert "Traceback" not in r.stderr


def test_unknown_event_type_exits_1_no_traceback(tmp_path):
    space = tmp_path / "space"
    space.mkdir()

    r = run_cli("append", "--space", str(space), "--type", "not.a.real.type",
                "--payload", "{}", tmp_path=tmp_path)
    assert r.returncode == 1
    assert r.stdout == ""
    assert "Traceback" not in r.stderr
    assert "unknown event type" in r.stderr


def test_strict_verify_flags_unsigned_events(tmp_path):
    space = tmp_path / "space"
    space.mkdir()

    run_cli("append", "--space", str(space), "--type", "item.create",
             "--payload", json.dumps({"id": "t1", "title": "Ship it"}), tmp_path=tmp_path)

    r_plain = run_cli("verify", "--space", str(space), tmp_path=tmp_path)
    assert r_plain.returncode == 0, r_plain.stderr

    r_strict = run_cli("verify", "--space", str(space), "--strict", tmp_path=tmp_path)
    assert r_strict.returncode == 1
    assert r_strict.stdout == ""
    assert "unsigned" in r_strict.stderr
    assert "Traceback" not in r_strict.stderr


def test_missing_space_dir_exits_1_no_traceback(tmp_path):
    missing = tmp_path / "does-not-exist"

    for args in (
        ("verify", "--space", str(missing)),
        ("items", "--space", str(missing)),
        ("balances", "--space", str(missing)),
    ):
        r = run_cli(*args, tmp_path=tmp_path)
        assert r.returncode == 1, args
        assert r.stdout == "", args
        assert "Traceback" not in r.stderr, args
        assert "not found" in r.stderr, args


def test_default_actor_from_hostname_when_env_unset(tmp_path):
    import socket

    space = tmp_path / "space"
    space.mkdir()
    env = _env(tmp_path)
    del env["DATACORE_ACTOR"]

    r = subprocess.run(
        [sys.executable, str(CLI), "append", "--space", str(space),
         "--type", "item.create", "--payload", json.dumps({"id": "t1", "title": "X"})],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    hostname = socket.gethostname()
    log_file = space / ".datacore" / "events" / f"{hostname}.jsonl"
    assert log_file.exists()
