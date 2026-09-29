"""Tests for retiring a credential from a host's stores (promise OPS-7).

A credential is retired by removing it from the central store. The delivered
.env then lacks it, but the per-host stores (cos.env, datacore.env, local.env)
kept their copies and a daemon started earlier kept the value in its
environment. `retire_absent` removes a variable that was delivered last time and
is not delivered now from every Datacore-owned store on the host, and names the
systemd units that read a changed file so they can be restarted.
"""

import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import credential_access as ca


@pytest.fixture
def host(tmp_path, monkeypatch):
    home = tmp_path / "home"
    data = home / "Data"
    (data / ".datacore" / "env").mkdir(parents=True)
    (home / ".config").mkdir(parents=True)
    (home / ".datacore").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(ca, "DATA", data)
    monkeypatch.setattr(ca, "ENV", data / ".datacore" / "env")
    monkeypatch.setattr(ca, "_store_config", lambda: {
        "stores": ["{DATA}/.datacore/env/.env", "{DATA}/.datacore/env/local.env",
                   "{HOME}/.config/cos.env", "{HOME}/.datacore/datacore.env"],
        "external": ["{HOME}/.hermes/.env"],
    })
    (data / ".datacore" / "env" / ".env").write_text("KEPT_API_KEY=kept\n")
    return home


def _w(p: Path, text: str, mode: int = 0o600) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    p.chmod(mode)
    return p


def test_retired_variable_leaves_every_owned_store(host):
    cos = _w(host / ".config/cos.env", "# cos\nexport OLD_API_KEY=old\nKEPT_API_KEY=kept\n")
    dce = _w(host / ".datacore/datacore.env", "OLD_API_KEY=old\nOTHER=x\n")
    loc = _w(host / "Data/.datacore/env/local.env", "OLD_API_KEY=old\n")
    out = ca.retire_absent({"OLD_API_KEY", "KEPT_API_KEY"})
    assert out["retired"] == ["OLD_API_KEY"]
    assert cos.read_text() == "# cos\nKEPT_API_KEY=kept\n"
    assert dce.read_text() == "OTHER=x\n"
    assert loc.read_text() == ""
    assert sorted(p for p, _ in out["pruned"]) == sorted([cos, dce, loc])
    assert stat.S_IMODE(cos.stat().st_mode) == 0o600


def test_host_local_variable_never_delivered_is_untouched(host):
    loc = _w(host / "Data/.datacore/env/local.env", "HOST_ONLY_TOKEN=mine\n")
    out = ca.retire_absent({"KEPT_API_KEY"})
    assert out["retired"] == [] and out["pruned"] == []
    assert loc.read_text() == "HOST_ONLY_TOKEN=mine\n"


def test_still_delivered_variable_is_not_pruned(host):
    cos = _w(host / ".config/cos.env", "KEPT_API_KEY=kept\n")
    ca.retire_absent({"KEPT_API_KEY"})
    assert cos.read_text() == "KEPT_API_KEY=kept\n"


def test_app_owned_store_is_not_ours_to_edit(host):
    ext = _w(host / ".hermes/.env", "OLD_API_KEY=theirs\n")
    ca.retire_absent({"OLD_API_KEY"})
    assert ext.read_text() == "OLD_API_KEY=theirs\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write read-only files")
def test_unwritable_store_is_reported_not_raised(host):
    ro = _w(host / ".datacore/datacore.env", "OLD_API_KEY=old\n", mode=0o400)
    ro.parent.chmod(0o500)
    try:
        out = ca.retire_absent({"OLD_API_KEY"})
    finally:
        ro.parent.chmod(0o700)
    assert out["unwritable"] == [(ro, ["OLD_API_KEY"])]
    assert "OLD_API_KEY" in ro.read_text()


def test_units_reading_a_changed_file_are_named(host, tmp_path):
    user_dir = _w(host / ".config/systemd/user/cos.service",
                  "[Service]\nEnvironmentFile=%h/.config/cos.env\n").parent
    _w(user_dir / "idle.service", "[Service]\nEnvironmentFile=%h/.other.env\n")
    sys_dir = tmp_path / "etc-systemd"
    _w(sys_dir / "datacored.service",
       f"[Service]\nEnvironmentFile=-{host}/Data/.datacore/env/.env\n")
    got = ca.env_consumers([host / ".config/cos.env", host / "Data/.datacore/env/.env"],
                           unit_dirs={"user": [user_dir], "system": [sys_dir]})
    assert got == [("system", "datacored.service"), ("user", "cos.service")]


def test_retire_names_the_consumers_of_what_it_changed(host):
    _w(host / ".config/cos.env", "OLD_API_KEY=old\n")
    _w(host / ".config/systemd/user/cos.service",
       "[Service]\nEnvironmentFile=%h/.config/cos.env\n")
    out = ca.retire_absent({"OLD_API_KEY"}, env_replaced=False,
                           unit_dirs={"user": [host / ".config/systemd/user"], "system": []})
    assert out["consumers"] == [("user", "cos.service")]
