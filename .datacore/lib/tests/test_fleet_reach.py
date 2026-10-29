"""fleet_reach: one way to run a read-only command where a machine's work lives.

Since 2026-10-25 three agents run as pods on the box (k3s, namespace firm), not
on their own hosts. Every fleet eval kept its own host table and kept ssh-ing to
droplets that no longer run anything, so they could only ever say "could not
tell". The roster now says where each machine's work runs; this resolves it.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import fleet_reach as fr

ROSTER = """
servers:
  mac:
    access: {actor: mac}
  nightshift:
    ssh_alias: nightshift
    access: {actor: miles}
  hermes:
    ssh_alias: '-'
    access:
      actor: tris
      k8s: {kubeconfig: KCFG, namespace: firm, deployment: tris, container: cron}
  box:
    ssh_alias: winston
    access:
      actor: winston
      k8s: {kubeconfig: KCFG, namespace: firm, deployment: winston, container: cron}
  hermes-host:
    ssh_alias: hermes
    retired: true
    access: {}
"""


@pytest.fixture
def roster(tmp_path, monkeypatch):
    p = tmp_path / "infrastructure.yaml"
    p.write_text(ROSTER.replace("KCFG", str(tmp_path / "kube.yaml")))
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for tool in ("ssh", "kubectl"):
        s = bin_ / tool
        s.write_text(f'#!/bin/sh\necho "{tool} $*"\n')
        s.chmod(s.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    return p


def test_a_pod_agent_is_reached_through_kubectl_exec(roster, tmp_path):
    pl = fr.place("hermes", roster)
    assert pl.kind == "pod"
    r = fr.run("hermes", "echo hi", roster)
    assert r.stdout.startswith("kubectl --kubeconfig " + str(tmp_path / "kube.yaml"))
    assert "-n firm exec deploy/tris -c cron -- sh -c echo hi" in r.stdout


def test_a_host_is_reached_through_ssh_batch_mode(roster):
    r = fr.run("nightshift", "uptime", roster)
    assert r.stdout.startswith("ssh -o BatchMode=yes")
    assert r.stdout.rstrip().endswith("nightshift uptime")


def test_a_host_with_pods_keeps_its_own_host_duties(roster):
    # The box runs Winston's pod AND host duties (backups, the fleet probe).
    assert fr.place("box", roster).kind == "pod"
    assert fr.place("box", roster, host=True).kind == "host"
    assert "winston uptime" in fr.run("box", "uptime", roster, host=True).stdout


def test_the_local_machine_runs_locally(roster):
    assert fr.place("mac", roster, local="mac").kind == "local"
    assert fr.run("mac", "echo here", roster, local="mac").stdout == "here\n"


def test_retired_and_unreachable_machines_are_not_agent_places(roster):
    assert "hermes-host" not in fr.agent_machines(roster)
    assert set(fr.agent_machines(roster)) == {"mac", "nightshift", "hermes", "box"}
    with pytest.raises(fr.Unreachable):
        fr.place("hermes-host", roster)


def test_a_pod_expands_to_each_of_its_containers(roster, tmp_path, monkeypatch):
    # A process scan in one container cannot see its siblings' processes.
    k = tmp_path / "bin" / "kubectl"
    k.write_text('#!/bin/sh\ncase "$*" in *jsonpath*) echo "gateway cron daemon" ;; *) echo "kubectl $*" ;; esac\n')
    pods = fr.containers(fr.place("box", roster))
    assert [p.k8s["container"] for p in pods] == ["gateway", "cron", "daemon"]
    assert "-c daemon" in " ".join(fr.argv(pods[2], "true"))
    host = fr.place("box", roster, host=True)
    assert fr.containers(host) == [host]


def test_a_pods_container_readiness_is_asked_of_the_cluster(roster, tmp_path):
    k = tmp_path / "bin" / "kubectl"
    k.write_text('#!/bin/sh\necho "gateway=true cron=true daemon=false"\n')
    pl = fr.place("box", roster)
    assert fr.container_ready(pl, "gateway") is True
    assert fr.container_ready(pl, "daemon") is False
    assert fr.container_ready(pl, "nope") is None


def test_an_agent_living_as_a_user_on_another_machine_is_reached_as_that_user(roster, tmp_path):
    # Miles runs on dayshift as the Unix user `miles` (2026-10-28), not on the
    # old nightshift host: from dayshift itself that is sudo -u, elsewhere ssh.
    p = tmp_path / "infrastructure.yaml"
    p.write_text(p.read_text() + "  miles-host:\n    ssh_alias: dayshift\n"
                 "    access: {actor: miles, host_machine: here, run_as: miles}\n"
                 "  here:\n    ssh_alias: here\n    access: {}\n")
    local = fr.place("miles-host", p, local="here")
    assert local.kind == "local-user"
    assert fr.argv(local, "id")[:6] == ["sudo", "-n", "-u", "miles", "-H", "sh"]
    # It starts in the user's home, as an ssh login would: the caller's folder is
    # usually not readable by them, and git refuses to run there.
    assert fr.argv(local, "id")[-1].startswith("cd ~ && ")
    remote = fr.place("miles-host", p, local="elsewhere")
    assert remote.kind == "host" and remote.target == "dayshift"
    assert fr.argv(remote, "id")[-1].startswith("sudo -n -u miles -H sh -lc")


def test_every_place_a_machine_does_work(roster):
    assert [p.kind for p in fr.places("box", roster)] == ["pod", "host"]
    assert [p.kind for p in fr.places("hermes", roster)] == ["pod"]
    assert [p.kind for p in fr.places("nightshift", roster)] == ["host"]


def test_the_schedule_of_a_pod_is_its_supercronic_file(roster):
    assert "/etc/datacore/crontab" in fr.schedule_listing(fr.place("hermes", roster))
    host = fr.schedule_listing(fr.place("nightshift", roster))
    assert "crontab -l" in host and "systemctl" in host


def test_a_pod_is_reached_through_its_host_when_no_kubeconfig_is_kept_here(tmp_path, monkeypatch):
    # 2026-10-29: the owner's machines reach the pods through the box's own login
    # (ssh, then the box's k3s), so no copy of the cluster credential is kept on
    # them. On the box itself the same roster runs k3s locally.
    p = tmp_path / "infrastructure.yaml"
    p.write_text("servers:\n  box:\n    ssh_alias: winston\n    access:\n"
                 "      k8s: {via: box, namespace: firm, deployment: winston, container: cron}\n"
                 "  hermes:\n    ssh_alias: '-'\n    access:\n"
                 "      k8s: {via: box, namespace: firm, deployment: tris, container: cron}\n")
    monkeypatch.delenv("FLEET_KUBECONFIG", raising=False)
    remote = fr.argv(fr.place("hermes", p, local="mac"), "echo 'a b'")
    assert remote[:3] == ["ssh", "-o", "BatchMode=yes"] and remote[-2] == "winston"
    import shlex
    inner = shlex.split(remote[-1])
    assert inner[:4] == ["sudo", "-n", "k3s", "kubectl"]
    assert inner[4:] == ["-n", "firm", "exec", "deploy/tris", "-c", "cron", "--", "sh", "-c", "echo 'a b'"]
    here = fr.argv(fr.place("hermes", p, local="box"), "true")
    assert here[:4] == ["sudo", "-n", "k3s", "kubectl"]
    # The host's own duties are still plain ssh.
    assert fr.argv(fr.place("box", p, host=True, local="mac"), "uptime")[-2:] == ["winston", "uptime"]
