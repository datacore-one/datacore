"""OPS-6: "Jobs get credentials only from the broker, and every machine holds the
same current value for each credential it needs."

Kind: deterministic.
  - Parity: the real distribute.sh (the one step that delivers credentials to
    every machine) runs against a disposable two-host fleet (fake ssh/scp, fixture
    values only -- see _creds_fleet_harness.py). After it, every host holds the
    central value, and any copy on any host that disagrees -- inside one host OR
    between hosts -- is reported (non-zero exit naming the variable).
  - Broker only: every script a declared scheduled job runs (jobs/manifest.yaml)
    obtains credentials through the broker (creds.py get / credential_access),
    never by sourcing an env file or reading an indexed credential variable
    straight from the environment. (OI-12: no such lint existed.)

Seeded failure: a host keeps a stale copy of a rotated key in a second store, or
two hosts hold different values of a key the central store does not carry
(the 2026-07-08 X-key rotation that reached one host only); a job script that
does `source ~/.config/cos.env`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _creds_fleet_harness import Fleet, fp  # noqa: E402

LIB = Path(__file__).resolve().parents[1]
DATA = LIB.parents[1]


def test_every_host_gets_the_central_value(tmp_path):
    f = Fleet(tmp_path)
    f.central({"FIXTURE_API_KEY": "fixture-v2"})
    for h in f.hosts:
        f.write_store(h, "Data/.datacore/env/.env", {"FIXTURE_API_KEY": "fixture-v1"})
    out = f.distribute()
    for h in f.hosts:
        got = f.stores(h).get("Data/.datacore/env/.env", {}).get("FIXTURE_API_KEY")
        assert got == fp("fixture-v2"), f"{h} holds {got}\n{out.stdout}\n{out.stderr}"


def test_a_stale_second_copy_on_one_host_is_reported(tmp_path):
    f = Fleet(tmp_path)
    f.central({"FIXTURE_API_KEY": "fixture-v2"})
    f.write_store("hostb", ".config/cos.env", {"FIXTURE_API_KEY": "fixture-v1"})
    out = f.distribute()
    stale = [(h, rel) for h in f.hosts for rel, vals in f.stores(h).items()
             if vals.get("FIXTURE_API_KEY") not in (None, fp("fixture-v2"))]
    if stale:
        assert out.returncode != 0 and "FIXTURE_API_KEY" in out.stdout, (
            f"stale copies {stale} left silently:\n{out.stdout}")


def test_hosts_disagreeing_with_each_other_is_reported(tmp_path):
    f = Fleet(tmp_path)
    f.central({"OTHER_API_KEY": "fixture-other"})
    f.write_store("hosta", ".config/cos.env", {"FIXTURE_BOT_TOKEN": "fixture-a"})
    f.write_store("hostb", ".config/cos.env", {"FIXTURE_BOT_TOKEN": "fixture-b"})
    out = f.distribute()
    assert out.returncode != 0 and "FIXTURE_BOT_TOKEN" in out.stdout, (
        "two machines hold different values of one credential and the delivery "
        f"step reported nothing:\n{out.stdout[-1500:]}")


# ── broker only ─────────────────────────────────────────────────────────────

_SOURCE_ENV = re.compile(
    r"(?:^|[;&|{(]\s*)\s*(?:source|\.)\s+[\"']?\S*(?:\.env|ENV_FILE|_ENV)\b", re.M)
_DOTENV = re.compile(r"load_dotenv|EnvironmentFile|open\([^)]*\.env['\"]")


def _credential_vars() -> set[str]:
    idx = yaml.safe_load((DATA / ".datacore" / "specs" / "credential-index.yaml").read_text()) or {}
    out = set()
    for c in idx.get("credentials") or []:
        for loc in c.get("locations") or []:
            if loc.get("var_name"):
                out.add(loc["var_name"])
    return out


def _job_scripts() -> dict[Path, list[str]]:
    jobs = yaml.safe_load((LIB / "jobs" / "manifest.yaml").read_text())["jobs"]
    out: dict[Path, list[str]] = {}
    for j in jobs:
        for m in re.finditer(r"~/Data/(\S+?\.(?:sh|py))\b", j["cmd"]):
            p = DATA / m.group(1)
            if p.is_file():
                out.setdefault(p, []).append(j["name"])
    return out


def test_job_scripts_ask_the_broker_not_the_env():
    creds = _credential_vars()
    assert creds, "no credential variables in the index: the lint would be vacuous"
    scripts = _job_scripts()
    assert len(scripts) >= 10, "manifest scripts not resolved: the lint would be vacuous"
    direct = []
    for p, jobs in sorted(scripts.items()):
        text = p.read_text(errors="replace")
        hits = [m.group(0).strip() for m in _SOURCE_ENV.finditer(text)]
        hits += [m.group(0) for m in _DOTENV.finditer(text)]
        for var in creds:
            if p.suffix == ".py":
                if re.search(rf"(?:environ(?:\.get)?\(|environ\[|getenv\()\s*['\"]{var}['\"]", text):
                    hits.append(f"env[{var}]")
            elif re.search(rf"\$\{{?{var}\b", text):
                hits.append(f"${var}")
        if hits:
            direct.append(f"{p.relative_to(DATA)} ({', '.join(jobs)}): {sorted(set(hits))[:4]}")
    assert direct == [], "job scripts reading credentials around the broker:\n" + "\n".join(direct)
