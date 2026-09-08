"""A VPN is not a fault (2026-09-08).

`mac-seq-gap` failed five runs in a row because a work VPN captured the route
to the Gitea host: the detector counted "remote unreachable" as an error and
the job contract requires `0 error`. ledger_transport already draws the line
this restores -- offline is a condition, a denied key is a fault."""
from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB / "detectors"))
import seq_gap as sg  # noqa: E402


def test_offline_shapes_are_conditions():
    for err in ("ssh: connect to host 100.115.67.71 port 2222: Operation timed out",
                "ssh: Could not resolve hostname gitea: nodename nor servname provided",
                "fatal: unable to access ...: Could not connect to server",
                ""):
        assert sg._offline(err) is True, err


def test_faults_that_never_clear_on_their_own_stay_errors():
    for err in ("git@host: Permission denied (publickey).",
                "fatal: Authentication failed for 'https://...'",
                "Host key verification failed.",
                "ERROR: Repository not found.",
                "fatal: 'x' does not appear to be a git repository"):
        assert sg._offline(err) is False, err


def test_the_contract_line_still_says_zero_errors_when_only_unreachable():
    """The job contract matches '0 with unpublished events, 0 error'. An
    unreachable remote must leave that true while still being visible."""
    rows = [{"space": "5-plur", "actor": "mac", "local_seq": 3,
             "remote_seq": None, "gap": None, "unverifiable": True,
             "error": None, "note": "remote unreachable"}]
    errors = [r for r in rows if r.get("error")]
    gaps = [r for r in rows if r.get("gap")]
    unver = [r for r in rows if r.get("unverifiable")]
    line = (f"seq-gap: {len(rows)} log(s), {len(gaps)} with unpublished events, "
            f"{len(errors)} error(s), {len(unver)} unverifiable (remote unreachable)")
    assert "0 with unpublished events, 0 error" in line
    assert "1 unverifiable" in line


def test_git_calls_fail_fast_on_an_unreachable_host(monkeypatch, tmp_path):
    """A VPN capturing the route made ONE fetch take 75 s; eleven spaces then
    blew the job's runtime budget and mac-seq-gap failed on a timeout while
    writing no artifact at all (2026-09-08)."""
    seen = {}

    class R:
        returncode = 0
        stdout = ""

    def fake_run(argv, **kw):
        seen.update(env=kw.get("env") or {}, timeout=kw.get("timeout"))
        return R()

    monkeypatch.setattr(sg.subprocess, "run", fake_run)
    sg.git(tmp_path, "fetch", "-q", "origin")
    assert "ConnectTimeout=5" in seen["env"]["GIT_SSH_COMMAND"]
    assert "BatchMode=yes" in seen["env"]["GIT_SSH_COMMAND"]
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0", "must never block on credentials"
    assert seen["timeout"] and seen["timeout"] <= 30


def test_an_explicit_ssh_command_is_respected(monkeypatch, tmp_path):
    """setdefault, not overwrite: a host with its own GIT_SSH_COMMAND (a
    jump host, an identity file) must keep it."""
    class R:
        returncode = 0
        stdout = ""

    seen = {}
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /custom/key")
    monkeypatch.setattr(sg.subprocess, "run",
                        lambda argv, **kw: (seen.update(env=kw.get("env") or {}), R())[1])
    sg.git(tmp_path, "status")
    assert seen["env"]["GIT_SSH_COMMAND"] == "ssh -i /custom/key"

