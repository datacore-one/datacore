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
