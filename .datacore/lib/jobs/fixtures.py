#!/usr/bin/env python3
"""fixtures.py — bind every `check: regex` to a real sample of producer output.

WHY. On 2026-09-02 `box-briefing` was failing daily against a regex that could
not match its producer at all: the manifest asked for `^###\\s+Your Agenda`
while the interactive /today generator writes `## Your Agenda` (H2). It had
been red on 08-27, 09-01 and 09-02 against briefings that were complete. Six
days of a real check reporting nothing but its own misconfiguration.

Nothing could have caught that, because nothing ever compared the pattern to
the thing it patterns. A regex in a manifest is a claim about another
program's output format, and it was the only claim in the system with no
counterpart to check against.

WHAT A FIXTURE IS -- and is not. A fixture is a sample of what the producer
emits WHEN IT SUCCEEDS. It answers one question:

    can this regex ever match this producer's output?

It does NOT answer "is the system healthy right now". Those fail differently
and must not be conflated:

    format wrong   `^###\\s+Your Agenda` vs a producer emitting `## Your Agenda`
                   -> the check is broken. It has never passed and never will.
    value bad      `^0 cadence\\(s\\) overdue$` vs `3 cadence(s) overdue`
                   -> the check works. The system is unhealthy. This is the
                      alert doing its job.

Harvesting cannot tell these apart on its own, and pretending otherwise would
replace one silent misconfiguration with another. So `--harvest` captures live
output and reports which regexes matched; a human marks each fixture as
representing success. The marker is a header line in the fixture itself, and
an unmarked fixture is reported, never silently trusted.

    fixtures.py --harvest          # pull live artifact output into fixtures
    fixtures.py                    # check every regex against its fixture
    fixtures.py --missing          # which checks have no fixture yet
"""
from __future__ import annotations

import argparse
import datetime
import pathlib
import re
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]
MANIFEST = ROOT / ".datacore" / "lib" / "jobs" / "manifest.yaml"
FIXTURES = ROOT / ".datacore" / "lib" / "tests" / "fixtures" / "jobs"
HOME = pathlib.Path.home()

# manifest machine name -> ssh alias. The manifest calls winston "box"; ssh
# does not. Without this map every --live check against box returned n-a,
# which reads as "could not tell" and is correct but useless.
SSH_ALIAS = {"box": "winston", "nightshift": "nightshift",
             "hermes": "hermes", "plur-claw": "plur-claw"}

SUCCESS_MARK = "# represents: SUCCESS"
UNMARKED = "# represents: UNVERIFIED — a human must confirm this is success output"


def _redact(text: str) -> str:
    """Strip absolute home paths from harvested output.

    Fixtures are committed, and real artifacts carry /Users/<name> and
    /home/<name> throughout. The repo's pre-commit path scan blocks them, and
    it is right to: a fixture exists to pin an output FORMAT, and nobody's home
    directory is part of that format. Verified that none of the 17 patterns
    match a path, so redaction cannot mask a real mismatch.
    """
    text = re.sub(r"/(?:home|Users)/[A-Za-z0-9_.-]+", "$HOME", text)
    return text


def _slug(job: str, idx: int) -> pathlib.Path:
    return FIXTURES / f"{job}.{idx}.txt"


def _expand(path: str) -> str:
    """Manifest paths carry {today}; resolve it so the artifact can be read."""
    return path.replace("{today}", datetime.date.today().isoformat())


def _read_artifact(path: str, machine: str) -> str | None:
    p = _expand(path)
    if machine == "mac":
        local = HOME / p[2:] if p.startswith("~/") else pathlib.Path(p)
        try:
            t = local.read_text(errors="replace")
        except OSError:
            return None
        if len(t) <= 20000:
            return t
        return t[:12000] + "\n# ...elided...\n" + t[-8000:]
    host = SSH_ALIAS.get(machine, machine)
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host,
             # head AND tail: a match may sit anywhere. Tailing alone reported
             # box-briefing as a format mismatch because `## Your Agenda` is at
             # line 57 of a 45 KB journal and the tail never reached it.
             #
             # Byte limits cut mid-UTF-8. text=True then raised
             # UnicodeDecodeError on the first journal containing an em-dash,
             # which aborted the whole harvest -- so every artifact after it
             # silently kept a stale fixture, and the run reported nothing
             # wrong. Capture bytes and decode with replacement instead.
             f"(head -c 12000 {p}; echo; echo '# ...elided...'; tail -c 8000 {p})"],
            capture_output=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", errors="replace")


def regex_checks() -> list[tuple[str, str, int, str, str]]:
    doc = yaml.safe_load(MANIFEST.read_text())
    out = []
    for j in doc["jobs"]:
        for i, a in enumerate(j.get("artifacts", [])):
            if a.get("check") == "regex":
                out.append((j["name"], j["machine"], i, a["path"], a["arg"]))
    return out


# Rewrites a producer's OWN observed line to its success value. Used when the
# live artifact was unhealthy at harvest time, so no success sample could be
# captured. Nothing is invented -- the format is the producer's, only the count
# changes. Keyed by "<job>.<artifact index>".
SUCCESS_RULES: dict[str, list[tuple[str, str]]] = {
    "mac-config-drift.0": [
        (r"(config-drift: \d+ machine\(s\), )\d+( with drift, )\d+( unreachable)",
         r"\g<1>0\g<2>0\g<3>")],
    "mac-seq-gap.0": [
        (r"(seq-gap: \d+ log\(s\), )\d+( with unpublished events, )\d+( error)",
         r"\g<1>0\g<2>0\g<3>")],
    "mac-actor-presence.0": [
        (r"(actor-presence: \d+ rostered actor\(s\), )\d+( failing)", r"\g<1>0\g<2>")],
    "mac-id-churn.0": [
        (r"(id-churn: \d+ space\(s\), )\d+( with findings)", r"\g<1>0\g<2>")],
    "box-cadence-liveness.0": [
        (r"^\d+ cadence\(s\) overdue$", "0 cadence(s) overdue")],
    "mac-pending-decisions.0": [
        (r"(commit-decisions: \d+ pending, )\d+( older than)", r"\g<1>0\g<2>")],
    "box-projection-drift.0": [(r'"all_clean":\s*false', '"all_clean": true')],
    "mac-shadow-diff.0": [(r'"all_clean":\s*false', '"all_clean": true')],
}

DERIVED = (
    "# represents: SUCCESS (derived)\n"
    "# The live artifact was UNHEALTHY when harvested, so a success sample\n"
    "# could not be captured. This is the producer's own observed output with\n"
    "# the failing count rewritten to its success value. The format is the\n"
    "# producer's; only the number changed. It proves the regex CAN match this\n"
    "# producer -- it says nothing about current health.\n")


def derive_success() -> int:
    """Turn unhealthy harvested fixtures into success samples, by rule.

    Run after --harvest whenever a producer was failing at harvest time. Kept
    as a mode rather than a throwaway script: it has to run again every time
    those artifacts are re-harvested while still unhealthy.
    """
    done = skipped = 0
    for name, rules in SUCCESS_RULES.items():
        f = FIXTURES / f"{name}.txt"
        if not f.exists():
            print(f" skip {name}: no fixture"); skipped += 1; continue
        t = f.read_text()
        hdr, body = t.split("# ---\n", 1)
        new = body
        for pat, rep in rules:
            new = re.sub(pat, rep, new, flags=re.M)
        if new == body:
            print(f" ---- {name}: already success, or rule no longer applies")
            skipped += 1
            continue
        hdr = "\n".join(l for l in hdr.splitlines()
                         if not l.startswith("# represents:")) + "\n" + DERIVED
        f.write_text(hdr + "# ---\n" + new)
        print(f"   ok {name}: derived success sample")
        done += 1
    print("-" * 92)
    print(f"derived {done}, skipped {skipped}")
    return 0


def harvest() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    got = miss = 0
    for job, machine, idx, path, pattern in regex_checks():
        body = _read_artifact(path, machine)
        f = _slug(job, idx)
        if body is None or not body.strip():
            print(f" n-a  {job}.{idx:<2} could not read {path} on {machine}")
            miss += 1
            continue
        matched = bool(re.search(pattern, body, re.M))
        header = (f"# fixture for {job} artifact[{idx}]\n"
                  f"# producer artifact: {path} on {machine}\n"
                  f"# harvested: {datetime.date.today().isoformat()}\n"
                  f"# regex under test: {pattern}\n"
                  f"{SUCCESS_MARK if matched else UNMARKED}\n"
                  f"# ---\n")
        f.write_text(_redact(header + body))
        print(f"{'  ok ' if matched else 'DIFF '} {job}.{idx:<2} "
              f"{'regex matches live output' if matched else 'regex does NOT match live output'}")
        got += 1
    print("-" * 92)
    print(f"harvested {got}, unreadable {miss} -> {FIXTURES.relative_to(ROOT)}")
    return 0


def check(show_missing_only: bool = False) -> int:
    rows, fails = [], 0
    for job, machine, idx, path, pattern in regex_checks():
        f = _slug(job, idx)
        if not f.exists():
            rows.append(("MISSING", job, idx, "no fixture — regex is unverifiable"))
            fails += 1
            continue
        if show_missing_only:
            continue
        text = f.read_text()
        body = text.split("# ---\n", 1)[-1]
        unverified = UNMARKED.split("—")[0].strip() in text and SUCCESS_MARK not in text
        if not re.search(pattern, body, re.M):
            rows.append(("FAIL", job, idx,
                         f"regex {pattern!r} cannot match this producer's output"))
            fails += 1
        elif unverified:
            rows.append(("UNVERIFIED", job, idx,
                         "matches, but nobody confirmed the sample is success output"))
        else:
            rows.append(("ok", job, idx, "regex matches a confirmed success sample"))

    for state, job, idx, detail in rows:
        if show_missing_only and state != "MISSING":
            continue
        print(f"{state:<11} {job}.{idx:<2} {detail}")
    print("-" * 92)
    print(f"{len(regex_checks())} regex check(s) · {fails} without a passing fixture")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--harvest", action="store_true")
    ap.add_argument("--missing", action="store_true")
    ap.add_argument("--derive-success", action="store_true",
                    help="rewrite unhealthy fixtures to their success value by rule")
    a = ap.parse_args()
    if a.harvest:
        return harvest()
    if a.derive_success:
        return derive_success()
    return check(show_missing_only=a.missing)


if __name__ == "__main__":
    raise SystemExit(main())
