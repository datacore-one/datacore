"""MEM-51: Every agent commits, pushes and posts under its own account, never under mine.

Kind: deterministic (registry) + production contract (read-only ssh).
  * registry (.datacore/registry/principals.yaml, what the ledger and git gates bind
    writers to): every agent principal names its own GitHub account, not a human's, and
    its commit email hashes are disjoint from every human's;
  * production, per agent host (box/winston, nightshift/miles, hermes/tris,
    plur-claw/data): the git author email the host commits with hashes to that agent's
    declared email and not to the owner's; the account `gh` is logged in as, and the
    account the host's GitHub SSH key authenticates as, are not the owner's (plur9).
    Emails are compared by actor_identity.email_hash and never printed.

Seeded failure: a registry where Winston's github is plur9 / an agent host whose git
email is the owner's -> red; verified with an in-memory registry mutation.
"""
import subprocess
from pathlib import Path

import pytest
import yaml

from actor_identity import email_hash

ROOT = Path(__file__).resolve().parents[3]
REGISTRY = ROOT / ".datacore" / "registry" / "principals.yaml"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
HOST_OF = {"winston": "winston", "miles": "nightshift", "tris": "hermes", "data": "plur-claw"}


def _principals(doc=None):
    return (doc or yaml.safe_load(REGISTRY.read_text()))["principals"]


def _humans(ps):
    return {n: p for n, p in ps.items() if p.get("kind") == "human"}


def _problems(ps) -> list[str]:
    humans = _humans(ps)
    human_gh = {str(p.get("github")).lower() for p in humans.values() if p.get("github")}
    human_mail = {str(h).lower() for p in humans.values() for h in (p.get("email_sha256") or [])}
    out = []
    for name in HOST_OF:
        p = ps.get(name) or {}
        gh = str(p.get("github") or "").lower()
        if not gh:
            out.append(f"{name}: no GitHub account of its own (github: {p.get('github')!r})")
        elif gh in human_gh:
            out.append(f"{name}: GitHub account {gh} is a human's")
        shared = {str(h).lower() for h in (p.get("email_sha256") or [])} & human_mail
        if shared:
            out.append(f"{name}: commit email hash shared with a human ({sorted(shared)})")
    return out


def test_every_agent_has_its_own_account_in_the_registry():
    bad = _problems(_principals())
    assert not bad, "agents without an identity of their own: " + "; ".join(bad)


def test_the_check_catches_an_agent_on_the_owners_account():
    doc = yaml.safe_load(REGISTRY.read_text())
    doc["principals"]["miles"]["github"] = "plur9"
    assert _problems(_principals(doc))


def _remote(host: str, cmd: str) -> str:
    r = subprocess.run([*SSH, host, cmd], capture_output=True, text=True, timeout=45)
    assert r.returncode in (0, 1), f"{host}: could not read ({r.stderr.strip()[-160:]})"
    return (r.stdout + r.stderr).strip()


@pytest.mark.production
@pytest.mark.parametrize("agent", list(HOST_OF))
def test_the_agent_host_commits_and_pushes_as_the_agent(agent):
    host = HOST_OF[agent]
    ps = _principals()
    mine = {str(h).lower() for h in (ps[agent].get("email_sha256") or [])}
    owner = {str(h).lower() for p in _humans(ps).values() for h in (p.get("email_sha256") or [])}
    owner_gh = {str(p.get("github")).lower() for p in _humans(ps).values() if p.get("github")}

    email = _remote(host, "git config --global user.email || git config user.email || true").splitlines()
    h = email_hash(email[-1]) if email and "@" in email[-1] else ""
    gh = _remote(host, "gh api user -q .login 2>/dev/null || true").splitlines()
    gh_login = gh[-1].strip().lower() if gh and " " not in gh[-1].strip() else ""
    greet = _remote(host, "ssh -o BatchMode=yes -o ConnectTimeout=10 -T git@github.com 2>&1 || true")
    key_login = greet.split("Hi ", 1)[1].split("!", 1)[0].lower() if "Hi " in greet else ""

    bad = []
    if not h:
        bad.append("no git author email configured")
    elif h in owner:
        bad.append("commits under the owner's email")
    elif h not in mine:
        bad.append(f"commits under an email (hash {h}) not declared for {agent}")
    if gh_login in owner_gh:
        bad.append(f"gh is logged in as the owner ({gh_login})")
    if key_login in owner_gh:
        bad.append(f"the GitHub SSH key authenticates as the owner ({key_login})")
    assert not bad, f"{agent} on {host}: " + "; ".join(bad)
