"""Signing is always on (owner, 2026-10-31: the rule change in the tasks-and-ledger
ground-up report, plan phase A2).

A writer signs unless it is told not to with DATACORE_LEDGER_SIGN=0, and in a hub
space it signs regardless (the existing HubSigningRequired refusal)."""
from ledger.log import EventLog


def test_a_writer_signs_by_default(tmp_path):
    log = EventLog(tmp_path, "writer")
    assert log.sign is True
    ev = log.append("item.create", {"item_id": "t1", "title": "x"})
    assert ev.sig


def test_only_an_explicit_zero_turns_signing_off_outside_a_hub(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    assert EventLog(tmp_path, "writer").sign is False


def test_the_identity_file_can_say_zero_too(tmp_path, monkeypatch):
    ident = tmp_path / "identity.env"
    ident.write_text("DATACORE_LEDGER_SIGN=0\n")
    monkeypatch.setenv("DATACORE_IDENTITY_FILE", str(ident))
    assert EventLog(tmp_path / "s", "writer").sign is False


def test_a_scratch_keys_dir_takes_the_registry_with_it(tmp_path):
    """A process given DATACORE_KEYS_DIR (what the test isolation hands every
    subprocess) must not register keys in the installation's real registry: with
    signing on by default, a test subprocess wrote 'testactor' there on 2026-10-31."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    env = {**os.environ, "DATACORE_KEYS_DIR": str(tmp_path / "keys")}
    out = subprocess.run([sys.executable, "-c", "import ledger.keys as k; print(k.DEFAULT_REGISTRY_PATH)"],
                         cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, check=True)
    assert Path(out.stdout.strip()).parent == tmp_path / "keys"
