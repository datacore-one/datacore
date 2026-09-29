"""Promise INB-8 (owner, 2026-09-29):

    Every night the GTD inbox processor processes my inbox: each entry is
    clarified and moved to where it belongs, finished ones leave, and anything
    it cannot decide stays in the inbox marked for my review.

Kind: contract + production + agent behaviour, one part each.

(a) Contract (the job list and the job's real script, in a sandbox):
    * the install declares exactly ONE scheduled job that processes the inbox
      (its command, or the script it runs, runs /process-inbox), and it fires
      once every night;
    * run in the chief-of-staff sandbox (_inbox_job_harness: the real script,
      the model call replaced by a recording stub), that job hands the
      processor the inbox of EVERY space that has one and holds anything --
      open or finished -- found the way Datacore finds spaces (a space marker,
      not a folder number), never a held space (install.yaml roles.held) and
      never a folder that is not a space; and it tells the processor that
      nobody is present (nightshift mode), so the command's unattended rules
      apply.

(b) Production (read-only, this install): the inbox job recorded a completed
    run in the system space's ledger (metric.attest, cos.job, job=inbox) in the
    last 26 hours; and in every writable space's inbox each remaining open
    top-level entry was captured after that run or carries [NEEDS_REVIEW],
    and no DONE/CANCELLED entry finished before that run is still there.
    "Could not tell" (no ledger, no timestamp, no git history) is red.

(c) Agent behaviour, pass^3, with the real standing context (CLAUDE.md +
    pinned memory) and the real /process-inbox command and processor spec: a
    scaffold inbox with a clear action, a bare link, a link with the owner's
    comment, a finished item and one fragment nobody could decide, processed in
    nightshift mode. Graded on the files it leaves: the action is a task in
    next_actions.org, the bare link is in research_learning.org, the commented
    link is an action in next_actions.org, the finished item left the inbox
    and was kept (not deleted, not reopened), the fragment is still in the
    inbox with a [NEEDS_REVIEW] prefix, and nothing is lost.

Seeded failures this must catch: the job globbing numbered folders (a marked
space without a number is never processed); skipping a space whose inbox holds
only finished entries; processing a held space; a prompt that never says no
one is present; a night with no run; finished entries piling up; the processor
filing an undecidable fragment in someday.org (the processor spec's own rule)
instead of leaving it marked in the inbox.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))
sys.path.insert(0, str(Path(__file__).resolve().parent))

#: The install whose live state (b) reads: DATACORE_ROOT when set, else this checkout.
INSTALL = Path(os.environ.get("DATACORE_ROOT") or ROOT)
COS_TESTS = ROOT / ".datacore" / "modules" / "chief-of-staff" / "tests"
UNATTENDED = re.compile(r"nightshift mode|no(body| one| user)\b[^.\n]{0,20}\bpresent|unattended", re.I)


# ── (a) contract ─────────────────────────────────────────────────────────────

def _jobs() -> list[dict]:
    from jobs.manifest import effective_doc
    doc = effective_doc(LIB / "jobs" / "manifest.yaml")
    return [j for j in (doc.get("jobs") or []) if isinstance(j, dict)]


def _script_of(cmd: str) -> Path | None:
    """The source of the script a job command runs.

    A module's server scripts are deployed into .datacore/lib/ (untracked there);
    their source is the module's server/lib/, so that copy is the one judged."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    for t in tokens:
        if re.match(r"^[A-Z_][A-Z0-9_]*=", t) or Path(t).name in ("python3", "python", "bash", "sh", "env"):
            continue
        if ".datacore/" in t:
            name = Path(t).name
            source = sorted((ROOT / ".datacore" / "modules").glob(f"*/server/lib/{name}"))
            return source[0] if source else ROOT / t[t.index(".datacore/"):]
        if t.startswith(("/", "~")):
            return Path(t).expanduser()
    return None


def _processes_inbox(job: dict) -> bool:
    cmd = str(job.get("cmd") or "")
    if "process-inbox" in cmd:
        return True
    script = _script_of(cmd)
    return bool(script and script.is_file() and "process-inbox" in script.read_text(errors="replace"))


def _nightly(schedule: str) -> bool:
    f = str(schedule or "").split()
    return len(f) == 5 and f[0].isdigit() and f[1].isdigit() and f[2:] == ["*", "*", "*"]


def _the_inbox_job() -> dict:
    jobs = [j for j in _jobs() if _processes_inbox(j)]
    assert len(jobs) == 1, (
        f"expected exactly one scheduled job that runs /process-inbox; found {len(jobs)}: "
        f"{[j.get('name') for j in jobs]}")
    return jobs[0]


def test_exactly_one_nightly_job_processes_the_inbox():
    job = _the_inbox_job()
    assert _nightly(job.get("schedule")), (
        f"the inbox job {job.get('name')} should fire once every night; its schedule is "
        f"{job.get('schedule')!r}")


def _marked(data: Path, name: str, kind: str = "team") -> None:
    (data / name / ".datacore").mkdir(parents=True, exist_ok=True)
    (data / name / ".datacore" / "config.yaml").write_text(
        f"space:\n  name: {name}\n  type: {kind}\n", encoding="utf-8")


def test_the_nightly_job_hands_every_spaces_inbox_to_the_processor_unattended(tmp_path, monkeypatch):
    if not (COS_TESTS / "_inbox_job_harness.py").is_file():
        pytest.fail("cannot run the inbox job in its sandbox: the chief-of-staff module "
                    "(its tests/_inbox_job_harness.py) is not installed", pytrace=False)
    if str(COS_TESTS) not in sys.path:
        sys.path.insert(0, str(COS_TESTS))
    import _inbox_job_harness as H  # noqa: E402
    script = _script_of(str(_the_inbox_job().get("cmd")))
    assert script and script.is_file(), f"the inbox job's script is not in this checkout: {script}"
    monkeypatch.setattr(H, "JOB", script)

    data = tmp_path / "Data"
    data.mkdir()
    (data / "install.yaml").write_text("roles:\n  personal: personal\n  held: [vault]\n", encoding="utf-8")
    for name, kind in (("personal", "personal"), ("research-lab", "team"),
                       ("archive-only", "team"), ("vault", "team")):
        _marked(data, name, kind)
    monkeypatch.setenv("DATACORE_ROOT", str(data))
    vault_inbox = H.inbox("A capture in a held space")
    run = H.run_inbox_job(tmp_path, {
        "personal": {"inbox.org": H.inbox("Book the dentist appointment", done=("Pay the electricity bill",)),
                     "next_actions.org": H.next_actions()},
        "research-lab": {"inbox.org": H.inbox("Draft the grant outline"),
                         "next_actions.org": H.next_actions()},
        "archive-only": {"inbox.org": H.inbox(done=("Submit the expense report",)),
                         "next_actions.org": H.next_actions()},
        "vault": {"inbox.org": vault_inbox, "next_actions.org": H.next_actions()},
    }, timeout=120)

    # A space whose inbox holds only finished entries needs no model run: the
    # promise is that finished entries LEAVE, which the job's deterministic tidy
    # does (owner-approved revision 2026-09-29: judging hand-over here was a
    # proxy for the promise, and contradicted INB-1's "nothing new, no run").
    expected = {"personal", "research-lab"}
    prompted = set(run.spaces_prompted())
    problems = []
    for space in ("personal", "archive-only"):
        left = [t for t in ("Pay the electricity bill", "Submit the expense report")
                if t in run.read(space, "inbox.org")]
        if left:
            problems.append(f"{space}: finished entries are still in the inbox after the run: {left}")
    if expected - prompted:
        problems.append(f"these spaces' inboxes were never handed to the processor: "
                        f"{sorted(expected - prompted)}")
    if prompted - expected:
        problems.append(f"the processor was handed inboxes that are not a writable space's "
                        f"(held, or not a space at all): {sorted(prompted - expected)}")
    if run.read("vault", "inbox.org") != vault_inbox:
        problems.append("the held space's inbox was changed")
    for p in run.prompts:
        if "process-inbox" not in p["prompt"]:
            problems.append(f"the prompt for {p['space']} does not run /process-inbox")
        if not UNATTENDED.search(p["prompt"]):
            problems.append(f"the prompt for {p['space']} never says nobody is present "
                            f"(nightshift mode), so the command's unattended rules do not apply")
    assert not problems, ("the nightly inbox job does not process every inbox as promised:\n  - "
                          + "\n  - ".join(problems) + f"\n(job exit {run.rc}; stdout tail: "
                          f"{run.stdout[-300:]!r})")


# ── (b) production ───────────────────────────────────────────────────────────

_JOB_RE = re.compile(r'"job"\s*:\s*"inbox"')


def _last_inbox_run(system: Path) -> datetime | None:
    """The newest completed inbox-job run the ledger recorded (UTC), or None."""
    newest = None
    # Job records are routine measurements: since the ledger split them out
    # (e6c0928, 2026-09-27) they live in .datacore/telemetry/, not events/.
    logs = [*sorted((system / ".datacore" / "events").glob("*.jsonl")),
            *sorted((system / ".datacore" / "telemetry").glob("*.jsonl"))]
    for log in logs:
        with log.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not _JOB_RE.search(line) or "cos.job" not in line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                pl = ev.get("payload") or {}
                if ev.get("type") != "metric.attest" or pl.get("job") != "inbox" or pl.get("status") != "ok":
                    continue
                ms = int(str(ev.get("hlc", "0")).split(".")[0] or 0)
                at = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
                newest = at if newest is None or at > newest else newest
    return newest


_HEAD = re.compile(r"^(\*+)\s+(.*)$")
_STATES = {"TODO", "NEXT", "WAITING", "REVIEW", "DONE", "DEFERRED", "CANCELLED", "WORKING"}
_STAMP = r"\[(\d{4}-\d{2}-\d{2})(?:\s+[A-Za-z]{2,3})?(?:\s+(\d{1,2}:\d{2}))?[^\]]*\]"


def _local(day: str, hm: str | None) -> datetime:
    """An org timestamp, read as this machine's local time."""
    return datetime.fromisoformat(f"{day} {hm or '00:00'}").astimezone()


def _blame_times(space: Path, rel: str) -> dict[int, datetime]:
    """line number (1-based) -> when that line last changed; uncommitted lines are now."""
    r = subprocess.run(["git", "-C", str(space), "blame", "--line-porcelain", "--", rel],
                       capture_output=True, text=True, timeout=120)
    out: dict[int, datetime] = {}
    if r.returncode != 0:
        return out
    cur, when, sha = None, None, ""
    for line in r.stdout.splitlines():
        m = re.match(r"^([0-9a-f]{40}) \d+ (\d+)", line)
        if m:
            sha, cur = m.group(1), int(m.group(2))
            continue
        if line.startswith("committer-time "):
            when = datetime.fromtimestamp(int(line.split()[1]), tz=timezone.utc)
        elif line.startswith("\t") and cur is not None:
            out[cur] = datetime.now(timezone.utc) if set(sha) == {"0"} else when
    return out


def _entries(text: str):
    """(line_no, parked, state, title, block) for each entry of an inbox.

    An entry is a direct child of the stateless "* Inbox" section, a level-1
    heading with a state, or a stateless level-1 heading with nothing under it
    (a bare capture). A stateless level-1 heading WITH children is a section:
    the install's convention parks undecided work for the owner under such a
    heading, so its open children are waiting for review by design (parked=True)
    and only a finished one among them counts against the night."""
    lines = text.splitlines()
    heads = [(i, len(m.group(1)), m.group(2)) for i, l in enumerate(lines) for m in [_HEAD.match(l)] if m]
    parent = None      # (is_inbox_section) of the current stateless level-1 section
    for k, (i, lvl, rest) in enumerate(heads):
        word = rest.split(" ", 1)[0]
        state = word if word in _STATES else ""
        has_children = k + 1 < len(heads) and heads[k + 1][1] > lvl
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        if lvl == 1:
            is_inbox = re.sub(r"\s+:[\w:@-]+:\s*$", "", rest).strip().lower() == "inbox"
            if not state and (has_children or is_inbox):
                parent = is_inbox
                continue
            parent = None
            yield i + 1, False, state, rest, "\n".join(lines[i:end])
        elif lvl == 2 and parent is not None:
            yield i + 1, not parent, state, rest, "\n".join(lines[i:end])


def _inbox_problems(space: Path, run: datetime) -> list[str]:
    rel = "org/inbox.org"
    text = (space / rel).read_text(encoding="utf-8", errors="replace")
    blame = _blame_times(space, rel)
    out = []
    for n, parked, state, title, block in _entries(text):
        if state in ("DONE", "CANCELLED"):
            c = re.search(r"CLOSED:\s*" + _STAMP, block)
            done_at = _local(c.group(1), c.group(2)) if c else blame.get(n)
            if done_at is None or done_at < run:
                out.append(f"finished before the last run, still here: {title[:60]!r}")
            continue
        if parked or "[NEEDS_REVIEW]" in title:
            continue
        c = re.search(r":CREATED:\s*" + _STAMP, block)
        times = [t for t in (_local(c.group(1), c.group(2)) if c else None, blame.get(n)) if t]
        if not times:
            out.append(f"cannot tell when it was captured: {title[:60]!r}")
        elif max(times) < run:
            out.append(f"not processed and not marked [NEEDS_REVIEW]: {title[:60]!r}")
    return out


@pytest.mark.production
def test_last_night_every_inbox_was_processed():
    import spaces
    system_name = spaces.space_for("system", INSTALL)
    assert system_name, "could not tell: install.yaml declares no system space, so no ledger to read"
    run = _last_inbox_run(INSTALL / system_name)
    problems = []
    if run is None:
        problems.append("the ledger holds no completed run of the inbox job at all")
    elif datetime.now(timezone.utc) - run > timedelta(hours=26):
        problems.append(f"the last completed inbox run the ledger recorded was {run:%Y-%m-%d %H:%M} UTC, "
                        f"more than a night ago (a later run may have started and not finished)")
    held = set(spaces.space_for_all("held", INSTALL))
    judged = 0
    for s in spaces.discover_spaces(INSTALL):
        # A held space is named by its path in the install; a nested one is not
        # a top-level folder (owner-approved 2026-09-29: nested spaces can be held).
        if (s.path.relative_to(INSTALL).as_posix() in held or s.path.name in held
                or not (s.path / "org" / "inbox.org").is_file()):
            continue
        judged += 1
        if run is None:
            continue
        found = _inbox_problems(s.path, run)
        if found:
            problems.append(f"{s.name}: {len(found)} entr{'y' if len(found) == 1 else 'ies'} left, e.g. "
                            + "; ".join(found[:3]))
    assert judged, "could not tell: no space with an inbox was found"
    assert not problems, "the inbox was not processed last night:\n  - " + "\n  - ".join(problems)


# ── (c) agent behaviour ──────────────────────────────────────────────────────

from agent_eval import AgentCase, plant_stub, require_enabled, run_case  # noqa: E402
from agent_context import write_context  # noqa: E402

INBOX = """#+TITLE: Inbox

* Inbox
** TODO Email Ana the signed lease agreement by Friday
:PROPERTIES:
:ID: inb8-action
:CREATED: [2026-09-28 Mon 09:12]
:END:
** https://example.org/articles/crdt-sync-explained
:PROPERTIES:
:ID: inb8-bare-link
:CREATED: [2026-09-28 Mon 09:20]
:END:
** https://example.org/blog/three-pricing-tiers
:PROPERTIES:
:ID: inb8-link-comment
:CREATED: [2026-09-28 Mon 09:31]
:END:
We should move our own pricing page to three tiers like this.
** DONE Renew the car insurance
CLOSED: [2026-09-27 Sun 18:00]
:PROPERTIES:
:ID: inb8-done
:END:
** TODO blue thing w/ Marko??
:PROPERTIES:
:ID: inb8-ambiguous
:CREATED: [2026-09-28 Mon 10:02]
:END:
"""

NEXT_ACTIONS = "#+TITLE: Next Actions\n\n* Operations\n* Personal Development\n* Strategy\n"
RESEARCH = "#+TITLE: Research & Learning\n\n* Research\n** Technology & Innovation\n** Business & Strategy\n"
SOMEDAY = "#+TITLE: Someday\n\n* Someday\n"
SPACE = "personal"


def _build(d: Path) -> None:
    write_context(d, "This is a scratch copy of a Datacore install. Its only space is `personal/`; "
                     "the Datacore MCP tools are not available here, the commands are in .claude/commands/.")
    for kind, name in (("commands", "process-inbox.md"), ("agents", "gtd-inbox-processor.md")):
        src = (ROOT / ".datacore" / kind / name).read_text(encoding="utf-8")
        for base in (d / ".claude" / kind, d / ".datacore" / kind):
            base.mkdir(parents=True, exist_ok=True)
            (base / name).write_text(src, encoding="utf-8")
    _marked(d, SPACE, "personal")
    org = d / SPACE / "org"
    org.mkdir(parents=True)
    for name, text in (("inbox.org", INBOX), ("next_actions.org", NEXT_ACTIONS),
                       ("research_learning.org", RESEARCH), ("someday.org", SOMEDAY)):
        (org / name).write_text(text, encoding="utf-8")
    plant_stub(d, "git")


def _everything_but_the_inbox(r) -> str:
    out = []
    for p in sorted(r.scaffold.rglob("*")):
        rel = p.relative_to(r.scaffold).as_posix()
        if not p.is_file() or rel == f"{SPACE}/org/inbox.org" or rel == "CLAUDE.md" \
                or rel.startswith((".claude/", ".datacore/")):
            continue
        out.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(out)


def _grade(r) -> tuple[bool, str]:
    org = f"{SPACE}/org/"
    inbox, na, rl = r.file(org + "inbox.org"), r.file(org + "next_actions.org"), r.file(org + "research_learning.org")
    problems = []
    amb = [l for l in inbox.splitlines() if _HEAD.match(l) and "blue thing" in l.lower()]
    if not amb:
        problems.append("the undecidable fragment ('blue thing w/ Marko??') left the inbox")
    elif "[NEEDS_REVIEW]" not in amb[0]:
        problems.append(f"the undecidable fragment stayed but is not marked [NEEDS_REVIEW]: {amb[0]!r}")
    for key, what in (("lease", "the clear action"), ("crdt-sync-explained", "the bare link"),
                      ("three-pricing-tiers", "the link with a comment"), ("car insurance", "the finished item")):
        if key in inbox.lower():
            problems.append(f"{what} is still in the inbox")
    if not re.search(r"^\*+\s+(TODO|NEXT)\b.*lease", na, re.M | re.I):
        problems.append("the clear action is not a task in next_actions.org")
    if "crdt-sync-explained" not in rl:
        problems.append("the bare link is not in research_learning.org")
    if "three-pricing-tiers" not in na:
        problems.append("the link with a comment is not an action in next_actions.org")
    rest = _everything_but_the_inbox(r)
    if "car insurance" not in rest.lower():
        problems.append("the finished item was deleted rather than kept outside the inbox")
    if re.search(r"^\*+\s+(TODO|NEXT|WAITING)\b.*car insurance", rest, re.M | re.I):
        problems.append("the finished item was reopened as an open task")
    return (not problems, "; ".join(problems) or "every entry went where it belongs")


CASE = AgentCase(
    name="inb8-nightly-inbox",
    prompt=(f"Run /process-inbox (the workflow in .claude/commands/process-inbox.md) against "
            f"{SPACE}/org/inbox.org. This is the scheduled nightly run: nobody is present, so work in "
            f"nightshift mode and do not ask questions."),
    build=_build,
    grade=_grade,
    runs=3,
    timeout_s=480,
    allowed_tools=("Read", "Write", "Edit", "Glob", "Grep", "Bash", "Agent"),
    max_budget_usd=2.0,
)


@pytest.mark.agent
def test_the_processor_routes_each_entry_and_leaves_only_the_undecidable_one_marked():
    require_enabled()
    verdict = run_case(CASE)
    assert verdict.passed, verdict.report()
