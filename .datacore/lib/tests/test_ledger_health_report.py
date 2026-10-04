"""The ledger-only health check (audit C7): integrity of every space's ledger,
nothing about the fleet, in a form an alert can read.

`ledger_health.py --root R` stays the content-free JSON the MCP status reads.
`--report` names each space with its verdict and exits 0 sound, 1 broken (a
fault in the data), 3 could not check on this machine -- never a pass.
"""
import os
import subprocess
import sys
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parents[1]
PRINCIPALS = "principals:\n  alice: {kind: human, writes_as: [alice]}\n  bot: {kind: agent, writes_as: [bot]}\n"


def _root(tmp_path, monkeypatch):
    import actor_identity
    from ledger import keys
    root = tmp_path / "root"
    (root / ".datacore" / "registry").mkdir(parents=True)
    (root / ".datacore" / "keys").mkdir(parents=True)
    (root / ".datacore" / "registry" / "principals.yaml").write_text(PRINCIPALS)
    monkeypatch.setenv("DATACORE_ROOT", str(root))
    monkeypatch.setattr(actor_identity, "PRINCIPALS", root / ".datacore" / "registry" / "principals.yaml")
    monkeypatch.setattr(keys, "DATACORE_ROOT", root)
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", root / ".datacore" / "keys" / "registry.yaml")
    monkeypatch.setattr(keys, "DEFAULT_KEYS_DIR", tmp_path / "private-keys")
    return root


def _space(root, name, *, sign=False):
    from ledger.log import EventLog
    space = root / name
    (space / ".datacore").mkdir(parents=True)
    (space / ".datacore" / "config.yaml").write_text(f"space:\n  name: {name}\n  type: team\n")
    log = EventLog(space, "alice", sign=sign)
    for i in range(3):
        log.append("item.create", {"id": f"{name}-{i}", "title": "x", "state": "NEXT"})
    return space


def _report(root):
    return subprocess.run([sys.executable, str(LIB / "ledger_health.py"), "--root", str(root), "--report"],
                          capture_output=True, text=True, timeout=120, env=dict(os.environ))


def test_all_sound_exits_zero_and_says_ok(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    _space(root, "0-alpha")
    _space(root, "1-beta")
    r = _report(root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0-alpha: ok" in r.stdout and "1-beta: ok" in r.stdout
    assert r.stdout.strip().splitlines()[-1].startswith("OK 2 space(s)"), r.stdout


def test_a_broken_space_is_named_and_exits_one(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    bad = _space(root, "0-alpha")
    _space(root, "1-beta")
    log = bad / ".datacore" / "events" / "alice.jsonl"
    log.write_text(log.read_text().replace('"title":"x"', '"title":"edited"', 1))
    r = _report(root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "0-alpha: broken" in r.stdout and "1-beta: ok" in r.stdout, r.stdout
    assert not any(line.startswith("OK ") for line in r.stdout.splitlines()), r.stdout


def test_a_missing_key_here_is_could_not_check_and_exits_three(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    _space(root, "0-alpha", sign=True)
    reg = root / ".datacore" / "keys" / "registry.yaml"
    data = yaml.safe_load(reg.read_text())
    del data["actors"]["alice"]
    reg.write_text(yaml.safe_dump(data))
    r = _report(root)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "0-alpha: could not check" in r.stdout, r.stdout
    assert not any(line.startswith("OK ") for line in r.stdout.splitlines()), r.stdout
