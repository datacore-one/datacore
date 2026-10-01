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


# ── cross-host parity (promise OPS-6) ─────────────────────────────────────────

def test_host_fingerprints_cover_owned_shared_stores(host):
    _w(host / ".config/cos.env", "BOT_TOKEN=a\nPLAIN_SETTING=x\n")
    _w(host / "Data/.datacore/env/local.env", "HOST_ONLY_TOKEN=mine\n")
    _w(host / ".hermes/.env", "BOT_TOKEN=theirs\n")
    got = ca.host_fingerprints()
    assert got == {"BOT_TOKEN": {ca.fingerprint("a")}, "KEPT_API_KEY": {ca.fingerprint("kept")}}


def test_cross_host_divergence_names_the_variable():
    per_host = {"hosta": {"BOT_TOKEN": {"aaa"}, "SAME_KEY": {"s"}},
                "hostb": {"BOT_TOKEN": {"bbb"}, "SAME_KEY": {"s"}},
                "hostc": {"ONLY_HERE_KEY": {"x"}}}
    assert ca.cross_host_divergence(per_host) == [
        ("BOT_TOKEN", [("hosta", "aaa"), ("hostb", "bbb")])]


# ── a template unit is not a restartable unit (2026-10-01) ────────────────────
#
# The box's alert@.service reads the fleet .env. env_consumers named the
# TEMPLATE, and `systemctl try-restart alert@.service` fails on every host
# ("missing the instance name") with or without root, so every distribution
# ended "RESTART FAILED alert@.service (needs root)" and exit 1.

def test_template_unit_is_named_by_its_instances(host, tmp_path):
    sys_dir = tmp_path / "etc-systemd"
    _w(sys_dir / "worker@.service",
       f"[Service]\nType=simple\nEnvironmentFile=-{host}/Data/.datacore/env/.env\n")
    got = ca.env_consumers([host / "Data/.datacore/env/.env"],
                           unit_dirs={"user": [], "system": [sys_dir]})
    assert got == [("system", "worker@*.service")]


# ── cross-host parity skips host-scoped credentials ───────────────────────────
#
# A credential whose index entry names its hosts (hosts: [...]) is one machine's
# own -- Winston's bot on the box, Data's on hers. Comparing it across machines
# reports two different credentials as one divergent value. A variable that any
# fleet-wide entry also declares is still compared, and so is an unindexed one.

INDEX_FIXTURE = [
    {"id": "winston-telegram-bot", "var_name": "WINSTON_BOT_TOKEN", "hosts": ["winston"]},
    {"id": "tris-telegram-bot", "var_name": "TELEGRAM_BOT_TOKEN", "hosts": ["tris"]},
    {"id": "mrdata-telegram-bot", "vars": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]},
    {"id": "redalert", "var_name": "REDALERT_BOT_TOKEN", "hosts": ["nightshift"],
     "vars": ["REDALERT_TELEGRAM_BOT_TOKEN"]},
]


def test_host_scoped_vars_are_those_only_host_scoped_entries_declare():
    assert ca.host_scoped_vars(INDEX_FIXTURE) == {
        "WINSTON_BOT_TOKEN", "REDALERT_BOT_TOKEN", "REDALERT_TELEGRAM_BOT_TOKEN"}


def test_cross_host_divergence_skips_host_scoped_variables():
    per_host = {"winston": {"WINSTON_BOT_TOKEN": {"aaa"}, "BOT_TOKEN": {"x"}},
                "mac": {"WINSTON_BOT_TOKEN": {"bbb"}, "BOT_TOKEN": {"y"}}}
    assert ca.cross_host_divergence(per_host, skip={"WINSTON_BOT_TOKEN"}) == [
        ("BOT_TOKEN", [("mac", "y"), ("winston", "x")])]


def test_cross_host_command_reads_the_index(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ca, "_index", lambda: INDEX_FIXTURE)
    fps = tmp_path / "fps"
    fps.write_text("winston WINSTON_BOT_TOKEN aaa\nmac WINSTON_BOT_TOKEN bbb\n"
                   "winston TELEGRAM_BOT_TOKEN t1\nmac TELEGRAM_BOT_TOKEN t2\n"
                   "winston FIXTURE_BOT_TOKEN f1\nmac FIXTURE_BOT_TOKEN f2\n")
    assert ca._cmd_cross_host(str(fps)) == 1
    out = capsys.readouterr().out
    assert "WINSTON_BOT_TOKEN differs" not in out
    assert "not compared (host-scoped in the index): WINSTON_BOT_TOKEN" in out
    assert "TELEGRAM_BOT_TOKEN differs" in out and "FIXTURE_BOT_TOKEN differs" in out


def test_cross_host_command_without_an_index_compares_everything(tmp_path, monkeypatch, capsys):
    def boom():
        raise ca.CredentialUnresolvable("no index")
    monkeypatch.setattr(ca, "_index", boom)
    fps = tmp_path / "fps"
    fps.write_text("winston WINSTON_BOT_TOKEN aaa\nmac WINSTON_BOT_TOKEN bbb\n")
    assert ca._cmd_cross_host(str(fps)) == 1
    assert "WINSTON_BOT_TOKEN differs" in capsys.readouterr().out


# ── a oneshot unit is not restarted (2026-10-01) ──────────────────────────────
#
# try-restart on a RUNNING oneshot kills its in-flight work and starts it again.
# On 2026-10-01 the Mac's distribution restarted nightshift-overnight.service 24
# minutes into a task; the second start was refused by the git preflight over
# the first one's unfinished changes. A oneshot reads its EnvironmentFile at its
# next start, so a restart buys nothing and costs the run.

def test_oneshot_unit_is_not_named_for_restart(host, tmp_path):
    sys_dir = tmp_path / "etc-systemd"
    env = f"EnvironmentFile=-{host}/Data/.datacore/env/.env\n"
    _w(sys_dir / "overnight.service", "[Service]\nType=oneshot\n" + env)
    _w(sys_dir / "daemon.service", "[Service]\nType=simple\n" + env)
    _w(sys_dir / "plain.service", "[Service]\n" + env)
    got = ca.env_consumers([host / "Data/.datacore/env/.env"],
                           unit_dirs={"user": [], "system": [sys_dir]})
    assert got == [("system", "daemon.service"), ("system", "plain.service")]
