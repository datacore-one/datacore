"""INS-3 batch 1: host lists and ssh aliases come from the install's machine
roster (.datacore/registry/infrastructure.yaml), never from names of ours
written into shipped code.

Every test points DATACORE_ROOT at a scratch tree, so the real roster is never
read: with no roster a tool names no host; with one, it names exactly those.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

ROSTER = """\
roles:
  always_on: srv
  sequencer: sealer
  assets: exec
  executor: exec
  hermes_hosts: [srv, gw]
servers:
  laptop:
    kind: workstation
    ssh_alias: '-'
    role_label: workstation
  srv:
    kind: server
    ssh_alias: srv-ssh
    manifest_machine: srvbox
    role_label: always-on host
  exec:
    kind: server
    ssh_alias: exec
  gw:
    kind: server
    ssh_alias: gw
    access: {run_as: svc}
"""


@pytest.fixture(autouse=True)
def _restore_modules():
    """Tests reload modules that read the roster at import; reload them again
    afterwards (env restored) so later tests see the real install's values."""
    yield
    for name in ("git_relay", "reliability_dashboard", "config_drift"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def roster(tmp_path, monkeypatch):
    reg = tmp_path / ".datacore" / "registry"
    reg.mkdir(parents=True)
    (reg / "infrastructure.yaml").write_text(ROSTER)
    monkeypatch.setenv("DATACORE_ROOT", str(tmp_path))
    for var in ("DATACORE_SEQUENCER", "DATACORE_SCOREBOARD_HOST", "CREDS_INSTANCE"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


@pytest.fixture
def no_roster(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_ROOT", str(tmp_path))
    for var in ("DATACORE_SEQUENCER", "DATACORE_SCOREBOARD_HOST"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_the_helpers_read_the_roster(roster):
    from jobs import manifest as m
    assert m.ssh_alias("srv") == "srv-ssh"
    assert m.ssh_alias("srvbox") == "srv-ssh", "a manifest name resolves too"
    assert m.ssh_alias("laptop") is None, "'-' means ssh does not reach it"
    assert m.ssh_alias("unknown") == "unknown", "an unknown name may already be an alias"
    assert m.ssh_hosts() == ["srv-ssh", "exec", "gw"]
    assert set(m.fleet_names()) == {"laptop", "srv", "srvbox", "srv-ssh", "exec", "gw"}
    assert m.role("always_on") == "srv" and m.role_all("hermes_hosts") == ["srv", "gw"]
    assert m.role("nothing") is None


def test_no_roster_names_no_host(no_roster):
    from jobs import manifest as m
    assert m.ssh_hosts() == [] and m.fleet_names() == [] and m.role("always_on") is None


def test_the_cli_serves_shell_callers(roster):
    run = lambda *a: subprocess.run([sys.executable, str(LIB / "jobs" / "manifest.py"), *a],
                                    capture_output=True, text=True).stdout.split()
    assert run("ssh-hosts") == ["srv-ssh", "exec", "gw"]
    assert run("role-ssh", "always_on") == ["srv-ssh"]
    assert run("role-ssh", "hermes_hosts") == ["srv-ssh", "gw"]
    assert run("role", "sequencer") == ["sealer"]


def test_git_relay_and_key_collection_use_the_roster(roster):
    import git_relay
    assert importlib.reload(git_relay).HOSTS == ("srv-ssh", "exec", "gw")


def test_git_relay_names_no_host_without_a_roster(no_roster):
    import git_relay
    assert importlib.reload(git_relay).HOSTS == ()


def test_job_checkers_map_machines_through_the_roster(roster):
    from jobs import fixtures, grounded
    assert grounded._ssh("srvbox") == "srv-ssh" and fixtures._ssh("srv") == "srv-ssh"
    assert grounded._ssh("elsewhere") == "elsewhere"


def test_the_sequencer_is_the_rosters(roster):
    from ledger.seal import sequencer
    assert sequencer() == "sealer"


def test_no_sequencer_without_a_roster(no_roster):
    from ledger.seal import sequencer
    assert sequencer() == ""


def test_dashboard_owner_and_labels_come_from_the_roster(roster):
    import reliability_dashboard as rd
    rd = importlib.reload(rd)
    assert rd.OWNER_HOST == "srv-ssh"
    assert rd.ROLES["srv-ssh"] == rd.ROLES["srvbox"] == "always-on host"
    assert rd.ROLES["exec"] == "server", "no role_label: the kind"


def test_dashboard_has_no_owner_without_a_roster(no_roster):
    import reliability_dashboard as rd
    assert importlib.reload(rd).OWNER_HOST == ""


def test_config_drift_polls_the_rosters_machines(roster):
    sys.path.insert(0, str(LIB / "detectors"))
    import config_drift
    assert importlib.reload(config_drift).MACHINES == [
        ("laptop", None, None), ("srv", "srv-ssh", None), ("exec", "exec", None), ("gw", "gw", "svc")]


def test_config_drift_without_a_roster_checks_this_machine(no_roster):
    sys.path.insert(0, str(LIB / "detectors"))
    import config_drift
    assert importlib.reload(config_drift).MACHINES == [("local", None, None)]


def test_egress_approves_exactly_the_rosters_hosts(roster):
    import tool_policy as tp
    fx = tp.load_effects()
    egress = lambda cmd: "data.egress" in tp.classify("Bash", {"command": cmd}, fx)
    assert not egress("rsync -a x srv-ssh:~/y") and not egress("scp f gw:/tmp")
    assert not egress("rsync x localhost:/a")
    assert egress("rsync x winston:/a"), "a host of ours is a stranger here"
    assert egress("scp f backup@203.0.113.9:/srv/")


def test_egress_without_a_roster_approves_only_localhost(no_roster):
    import tool_policy as tp
    fx = tp.load_effects()
    assert "data.egress" in tp.classify("Bash", {"command": "rsync x srv-ssh:/a"}, fx)
    assert "data.egress" not in tp.classify("Bash", {"command": "rsync x localhost:/a"}, fx)
