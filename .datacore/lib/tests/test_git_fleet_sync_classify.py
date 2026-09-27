"""Pull-failure classification in git_fleet_sync.

The distinction this guards: a host that CANNOT REACH a remote is not a host
with a MERGE CONFLICT. Calling the first the second sends someone hunting a
merge that does not exist, and — because a credential gap never clears on its
own — makes the check permanently red, which is how a check stops being read.

Regression under test: the pattern list matched the literal '403 Forbidden',
but git's HTTP transport emits "The requested URL returned error: 403". Six
module repos on winston were reported as PULL CONFLICT on 2026-08-31 for what
was purely a missing credential.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import git_fleet_sync as fs  # noqa: E402

# The exact strings git emits, as observed in the wild.
NO_ACCESS = [
    "fatal: unable to access 'https://github.com/datacore-one/module-grants.git/': "
    "The requested URL returned error: 403",
    "fatal: unable to access 'https://example.com/x.git/': "
    "The requested URL returned error: 401",
    "fatal: could not read Username for 'https://github.com': terminal prompts disabled",
    "git@github.com: Permission denied (publickey).",
    "remote: Repository not found.",
    "fatal: Authentication failed for 'https://github.com/x/y.git/'",
]

REAL_CONFLICT = [
    "Automatic merge failed; fix conflicts and then commit the result.",
    "error: Your local changes to the following files would be overwritten by merge:",
]


OFFLINE = [
    "ssh: connect to host example.invalid port 22: Operation timed out\n"
    "fatal: Could not read from remote repository.\n\n"
    "Please make sure you have the correct access rights\nand the repository exists.",
    "ssh: Could not resolve hostname h: nodename nor servname provided, or not known",
    "fatal: unable to access 'https://github.com/x/y.git/': Failed to connect to github.com port 443",
]


def _classify(out: str) -> str:
    """The branch under test, through the classifier it uses."""
    kind = fs.failure_kind(out)
    if kind == 'denied':
        return "NO ACCESS"
    if kind == 'offline':
        return "OFFLINE"
    if 'refusing to merge unrelated histories' in out:
        return "UNRELATED HISTORY"
    return "PULL CONFLICT"


@pytest.mark.parametrize("out", OFFLINE)
def test_an_unreachable_host_is_offline_not_denied(out):
    """git appends "check your access rights" to EVERY ssh failure (SYN-6)."""
    assert _classify(out) == "OFFLINE", f"misclassified: {out[:60]}"


@pytest.mark.parametrize("out", NO_ACCESS)
def test_credential_failures_are_no_access(out):
    assert _classify(out) == "NO ACCESS", f"misclassified: {out[:60]}"


@pytest.mark.parametrize("out", REAL_CONFLICT)
def test_real_merge_failures_stay_conflicts(out):
    """The narrowing must not swallow genuine conflicts."""
    assert _classify(out) == "PULL CONFLICT"


def test_unrelated_history_keeps_its_own_class():
    assert _classify("fatal: refusing to merge unrelated histories") == "UNRELATED HISTORY"


def test_no_access_never_claims_work_is_at_risk_from_a_stale_ref():
    """A host that cannot fetch cannot answer "am I holding unpushed work".

    Its remote-tracking ref froze when access last worked, so the count only
    ever over-reports — on 2026-08-31 winston named five module repos as
    holding unpushed commits and all five HEADs were already ancestors of
    origin. Deciding it here made the run permanently red; the question
    belongs to git_relay.py, which verifies against origin from a machine
    that can reach it.
    """
    src = (LIB / "git_fleet_sync.py").read_text()
    branch = src[src.index("elif kind == 'denied':"):]
    branch = branch[:branch.index("elif 'refusing to merge unrelated histories'")]

    assert "result['access_at_risk'] = False" in branch, (
        "the NO ACCESS branch must not derive at-risk from @{u}"
    )
    assert "UNVERIFIABLE" in branch, "it must say the count cannot be trusted"


def test_source_carries_every_pattern_this_asserts():
    """Pin the mirror to the implementation, so they cannot drift apart."""
    src = (LIB / "git_fleet_sync.py").read_text()
    for pattern in ('error: 403', 'error: 401', '403 forbidden',
                    'could not read username', 'repository not found'):
        assert f"'{pattern}'" in src, f"{pattern} missing from git_fleet_sync.py"
