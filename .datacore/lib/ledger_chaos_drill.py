#!/usr/bin/env python3
"""Inject real faults into a throwaway fleet, and assert on the state left behind.

A chaos harness existed on 2026-09-08. It measured ONE condition -- converge
against a blackhole address -- and was never committed, so all that survives of
it is a comment in `ledger_transport.py` and the five-second connect timeout it
argued for. This is its replacement, kept where scripts are kept.

Three of the seven scenarios are not hypothetical. They are failures this
installation actually had on 2026-09-16, written down as tests so the next one
is caught by a drill rather than by an operator reading a red dashboard:

  RESURRECTED WRITER REF   a `ledger/<actor>` branch was deleted as though it
                           were stale, a host still holding an older copy
                           re-pushed it, and converge stopped for a day.
  EDIT BETWEEN INGEST AND PROJECT  the projection refuses, correctly -- and the
                           refusal leaves the base un-advanced, so the same
                           conflict is re-derived every cycle for ever.
  TRUNCATED WRITER LOG     a chain of zero events is a VALID chain, so the seal
                           cannot see a truncation; only the presence detector
                           can, and only against a roster.

Everything runs under a temporary root with DATACORE_STATE and both git config
files redirected. That is not politeness: on 2026-09-16 a test suite that did
not do this wrote its Org transaction journal into the real state directory and
blocked every Org transaction on the machine.

    ledger_chaos_drill.py [--only NAME ...] [--keep] [--list]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

#: TEST-NET-1 (RFC 5737). Reserved for documentation and guaranteed never to be
#: routed, so this cannot accidentally reach a real host even on a strange network.
BLACKHOLE = "192.0.2.1"

#: An unreachable remote must fail inside this, or a sweep over ten spaces
#: becomes twelve minutes of waiting and the job is killed before it can write
#: its artifact. `SSH_FAIL_FAST` sets ConnectTimeout=5; this allows generous
#: slack over it and still fails loudly if the setting is ever lost.
UNREACHABLE_BUDGET_S = 30.0


class Drill:
    def __init__(self, root: Path, keep: bool = False):
        self.root = root
        self.keep = keep
        self.failures: list[str] = []
        self.ran: list[str] = []

    # -- reporting ------------------------------------------------------
    def check(self, name: str, cond: bool, detail: str = "") -> bool:
        print(f"    {'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if not cond else ""))
        if not cond:
            self.failures.append(name)
        return bool(cond)

    # -- fixtures -------------------------------------------------------
    def git(self, repo: Path, *args: str, check: bool = False) -> tuple[int, str]:
        r = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=120)
        if check and r.returncode:
            raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def space(self, name: str, *, remote: bool = False) -> tuple[Path, Path | None]:
        """A registered scratch space, optionally with a bare origin."""
        space = self.root / name
        (space / "org").mkdir(parents=True, exist_ok=True)
        (space / ".datacore" / "events").mkdir(parents=True, exist_ok=True)
        (space / ".datacore" / "space.yaml").write_text("name: drill\ntype: personal\n")
        self.register()
        self.git(space, "init", "-q", "-b", "main")
        self.git(space, "config", "user.email", "drill@datacore")
        self.git(space, "config", "user.name", "drill")
        self.git(space, "config", "core.hooksPath", str(space / ".git" / "hooks"))
        (space / "org" / "next_actions.org").write_text("* Drill\n")
        self.git(space, "add", "-A")
        self.git(space, "commit", "-qm", "seed")
        origin = None
        if remote:
            origin = self.root / f"{name}.git"
            self.git(self.root, "init", "-q", "--bare", "-b", "main", str(origin))
            self.git(space, "remote", "add", "origin", str(origin))
            self.git(space, "push", "-qu", "origin", "main")
        return space, origin

    def register(self) -> None:
        """Every scratch directory is a registered knowledge repo.

        Rewritten on demand rather than once at setup: an unregistered repo is
        REFUSED by `classify`, and a clone made after the first space would
        otherwise converge into "repository not in registry/repositories.yaml"
        — which reads exactly like a transport failure and is not one.
        """
        registry = self.root / ".datacore" / "registry"
        registry.mkdir(parents=True, exist_ok=True)
        (registry / "repositories.yaml").write_text(
            "repositories:\n" + "".join(
                f"  {d.name}: {{category: knowledge}}\n"
                for d in sorted(self.root.iterdir())
                if d.is_dir() and not d.name.startswith(".") and not d.name.endswith(".git")))

    def log(self, space: Path, actor: str):
        from ledger.log import EventLog
        return EventLog(space, actor, sign=False)

    def events(self, space: Path, actor: str) -> list[dict]:
        p = space / ".datacore" / "events" / f"{actor}.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    # -- scenarios ------------------------------------------------------
    def unreachable_remote(self) -> None:
        """A host that swallows packets must cost seconds, not minutes."""
        import ledger_transport as t
        space, _ = self.space("1-unreachable", remote=True)
        before = len(self.events(space, "drill"))
        self.log(space, "drill").append("item.create", {"id": "u1", "title": "before the outage"})
        self.git(space, "remote", "set-url", "origin", f"ssh://git@{BLACKHOLE}:22/drill.git")

        started = time.monotonic()
        result = t.converge(space, root=self.root)
        elapsed = time.monotonic() - started

        self.check("converge refuses an unreachable remote", not result.ok, result.reason)
        self.check(f"it gives up within {UNREACHABLE_BUDGET_S:.0f}s",
                   elapsed < UNREACHABLE_BUDGET_S, f"took {elapsed:.1f}s")
        after = self.events(space, "drill")
        self.check("the local log is untouched by the failure",
                   len(after) == before + 1 and after[-1]["payload"]["id"] == "u1",
                   f"{len(after)} events")

    def rejected_push(self) -> None:
        """A remote that accepts the connection and then refuses the write."""
        import ledger_transport as t
        space, origin = self.space("2-rejected", remote=True)
        self.log(space, "drill").append("item.create", {"id": "r1", "title": "work to publish"})
        hook = origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)

        blocked = t.converge(space, root=self.root)
        self.check("converge reports the rejection", not blocked.ok, blocked.reason)
        local = self.events(space, "drill")
        self.check("the rejected work is still here", len(local) == 1 and local[0]["payload"]["id"] == "r1")

        hook.unlink()
        healed = t.converge(space, root=self.root)
        self.check("the same work publishes once the remote accepts again", healed.ok, healed.reason)
        rc, out = self.git(origin, "show", "main:.datacore/events/drill.jsonl")
        self.check("the remote now holds the event", rc == 0 and '"r1"' in out)

    def resurrected_writer_ref(self) -> None:
        """A stale `ledger/<actor>` ref pushed back over a DIVERGED chain.

        This is 2026-09-16. The remote ref was deleted as though it were a stale
        branch; a host still holding an older copy re-pushed it, and every
        converge afterwards stopped on the fork. The fork is the point -- an
        ancestor tip would merge cleanly and prove nothing -- so the drill
        builds a real one: a second clone appends its OWN event at the same
        sequence number, giving two events with the same seq and different
        hashes, which is precisely what a resurrected writer log looks like.

        The demand is not that converge succeeds. It must not. The demand is
        that it refuses without truncating the local chain.
        """
        import ledger_transport as t
        space, origin = self.space("3-resurrected", remote=True)
        writer = self.log(space, "drill")
        writer.append("item.create", {"id": "w1", "title": "first"})
        self.git(space, "add", "-A")
        self.git(space, "commit", "-qm", "first event")
        self.git(space, "push", "-q", "origin", "main")
        fork_point = self.git(space, "rev-parse", "HEAD")[1].strip()

        # The other host: same history, its own second event.
        other = self.root / "3-resurrected-other"
        self.git(self.root, "clone", "-q", str(origin), str(other))
        self.git(other, "config", "user.email", "other@datacore")
        self.git(other, "config", "user.name", "other")
        self.register()
        self.git(other, "checkout", "-q", fork_point)
        self.log(other, "drill").append("item.create", {"id": "x2", "title": "the other copy"})
        self.git(other, "add", "-A")
        self.git(other, "commit", "-qm", "divergent second event")
        stale = self.git(other, "rev-parse", "HEAD")[1].strip()
        self.git(other, "push", "-q", "origin", f"{stale}:refs/heads/ledger/drill")

        # This host keeps going on its own line.
        for n in range(2, 6):
            writer.append("item.create", {"id": f"w{n}", "title": f"later {n}"})
        self.git(space, "add", "-A")
        self.git(space, "commit", "-qm", "four more events")
        self.git(space, "push", "-q", "origin", "main")

        mine = {e["hash"] for e in self.events(space, "drill")}
        result = t.converge(space, root=self.root)
        kept = self.events(space, "drill")

        self.check("the fork is genuine, not an ancestor",
                   self.git(space, "merge-base", "--is-ancestor",
                            "origin/ledger/drill", "HEAD")[0] != 0)
        self.check("converge refuses a forked writer ref rather than merging it",
                   not result.ok, result.reason or "it merged")
        if not result.ok:
            self.check("the refusal says which ref, so an operator can act",
                       "ledger" in json.dumps(result.context) or "ledger" in result.reason,
                       f"{result.reason} {result.context}")
        self.check("not one local event was dropped",
                   mine <= {e["hash"] for e in kept}, f"{len(kept)} events remain")
        seqs = [e["seq"] for e in kept]
        self.check("the chain stays strictly increasing",
                   all(b > a for a, b in zip(seqs, seqs[1:])), str(seqs))
        self.check("the working tree is not left mid-merge",
                   not (space / ".git" / "MERGE_HEAD").exists())

    def concurrent_appenders(self) -> None:
        """Eight processes appending to ONE writer log at the same moment."""
        space, _ = self.space("4-concurrent")
        writers, each = 8, 25
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "from pathlib import Path\n"
            "from ledger.log import EventLog\n"
            "log = EventLog(Path(%r), 'drill', sign=False)\n"
            "for i in range(%d):\n"
            "    log.append('item.create', {'id': f'{sys.argv[1]}-{i}', 'title': 'race'})\n"
        ) % (str(LIB), str(space), each)
        runner = self.root / "appender.py"
        runner.write_text(script)
        procs = [subprocess.Popen([sys.executable, str(runner), str(w)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                 for w in range(writers)]
        errors = [p.communicate()[1].decode()[-200:] for p in procs]
        failed = [e for e, p in zip(errors, procs) if p.returncode]

        rows = self.events(space, "drill")
        self.check("every appender exited cleanly", not failed, "; ".join(failed)[:200])
        self.check(f"all {writers * each} events landed",
                   len(rows) == writers * each, f"{len(rows)} present")
        seqs = [r["seq"] for r in rows]
        self.check("no sequence number is reused", len(set(seqs)) == len(seqs))
        self.check("sequence numbers are contiguous and ordered",
                   seqs == sorted(seqs) and seqs == list(range(seqs[0], seqs[0] + len(seqs))))
        from ledger.seal import _chain_issue
        from ledger.log import read_events
        self.check("the hash chain verifies after the race",
                   not _chain_issue(list(read_events(space))))

    def kill_mid_transaction(self) -> None:
        """SIGKILL inside an Org transaction: never a half-written file."""
        space, _ = self.space("5-killed")
        target = space / "org" / "next_actions.org"
        original = "* Drill\n** TODO Authored before the kill\n"
        target.write_text(original)
        state = Path(os.environ["DATACORE_STATE"]) / "kill"
        state.mkdir(mode=0o700, exist_ok=True)

        script = (
            "import os, sys, time\n"
            "sys.path.insert(0, %r)\n"
            "os.environ['DATACORE_STATE'] = %r\n"
            "from pathlib import Path\n"
            "from org_transaction import serialized, watch_file, write_org_text\n"
            "@serialized\n"
            "def run():\n"
            "    p = Path(%r)\n"
            "    watch_file(p)\n"
            "    write_org_text(p, '* Drill\\n** TODO Written inside the transaction\\n')\n"
            "    sys.stdout.write('written\\n'); sys.stdout.flush()\n"
            "    time.sleep(30)\n"
            "run()\n"
        ) % (str(LIB), str(state), str(target))
        runner = self.root / "killer.py"
        runner.write_text(script)
        proc = subprocess.Popen([sys.executable, str(runner)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        line = proc.stdout.readline().decode().strip()
        self.check("the transaction reached its write", line == "written", line)
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=30)

        text = target.read_text()
        self.check("the file is one whole version, never a fragment",
                   text in (original, "* Drill\n** TODO Written inside the transaction\n"),
                   repr(text[:80]))

        env = {**os.environ, "DATACORE_STATE": str(state)}
        probe = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r)\n"
             "from org_transaction import journal_path, recover\n"
             "recover(journal_path()); print('recovered')" % str(LIB)],
            capture_output=True, text=True, env=env, timeout=60)
        self.check("recovery after the kill is possible, not a permanent block",
                   probe.returncode == 0, (probe.stderr or "").strip()[-160:])

    def truncated_writer_log(self) -> None:
        """Half a log is still a valid chain. Only the roster can notice."""
        space, _ = self.space("6-truncated")
        writer = self.log(space, "drill")
        for n in range(10):
            writer.append("item.create", {"id": f"t{n}", "title": "before truncation"})
        path = space / ".datacore" / "events" / "drill.jsonl"
        full = path.read_text().splitlines()
        path.write_text("\n".join(full[:4]) + "\n")

        from ledger.seal import _chain_issue
        from ledger.log import read_events
        self.check("the seal CANNOT see the truncation (a short chain is valid)",
                   not _chain_issue(list(read_events(space))))

        roster = self.root / "presence.json"
        roster.write_text(json.dumps({"actors": {"drill": {"spaces": {space.name: 9}}}}))
        rows = self.events(space, "drill")
        head = rows[-1]["seq"] if rows else -1
        self.check("the head went backwards, which is what the detector keys on",
                   head < 9, f"head now {head}, baseline 9")

    def edit_between_ingest_and_project(self) -> None:
        """The deadlock: a refusal that preserves the condition causing it."""
        from ledger.fold import fold
        from ledger.log import read_events
        from ledger.projection_state import (guard_projection, ProjectionConflict,
                                              base_document, STATE)
        from ledger.projector import project
        space, _ = self.space("7-deadlock")
        (space / ".datacore" / "ledger-phase").write_text("1\n")
        (space / ".datacore" / "ledger-edit-protocol").write_text("1\n")
        writer = self.log(space, "drill")
        writer.append("item.create", {"id": "d1", "title": "A task", "tags": ["work"]})

        target = space / "org" / "next_actions.org"
        rendered = project(fold(read_events(space)), space=space.name, as_of=0).text
        target.write_text(rendered)
        # Establish the projection base. Without it the guard takes its earlier
        # "no base" path, and the scenario would quietly test something easier
        # than the three-way merge it is named after.
        (space / STATE).parent.mkdir(parents=True, exist_ok=True)
        (space / STATE).write_text(base_document(rendered), encoding="utf-8")
        try:
            guard_projection(space, target.read_text(), rendered)
            agreed = True
        except ProjectionConflict:
            agreed = False
        self.check("a file that matches the ledger projects cleanly", agreed)

        # The hand edit that lands after the ingest has already run.
        target.write_text(rendered.replace("A task", "A task, renamed by hand"))
        try:
            guard_projection(space, target.read_text(), rendered)
            refused = False
        except ProjectionConflict as exc:
            refused = True
            reason = str(exc)
        self.check("an unseen authored edit is REFUSED, not overwritten", refused,
                   "projection would have destroyed the edit")
        if refused:
            self.check("the refusal names what to do about it",
                       any(word in reason.lower()
                           for word in ("ingest", "conflict", "reconcil")), reason)

    def torn_final_line(self) -> None:
        """A crash mid-append leaves half a line. That is in-flight, not corrupt.

        The log's contract distinguishes the two by POSITION: an unparseable
        FINAL line can only be an interrupted write, so `append` truncates it
        and `read_events` skips it; the same damage anywhere else cannot be
        explained that way and must raise rather than silently drop events.
        The drill asserts both halves, because a reader that swallowed a
        mid-file corruption would lose data with no error at all.
        """
        from ledger.log import read_events, CorruptLogError
        space, _ = self.space("8-torn")
        writer = self.log(space, "drill")
        for n in range(5):
            writer.append("item.create", {"id": f"p{n}", "title": "complete"})
        path = space / ".datacore" / "events" / "drill.jsonl"
        whole = path.read_text()

        path.write_text(whole + '{"seq": 5, "type": "item.crea')
        self.check("a torn final line is skipped, not fatal",
                   len(list(read_events(space))) == 5)
        self.log(space, "drill").append("item.create", {"id": "p5", "title": "after the tear"})
        rows = self.events(space, "drill")
        self.check("the next append repairs the file",
                   len(rows) == 6 and rows[-1]["payload"]["id"] == "p5", f"{len(rows)} rows")
        self.check("no complete event was lost to the repair",
                   [r["payload"]["id"] for r in rows[:5]] == [f"p{n}" for n in range(5)])

        lines = whole.splitlines()
        lines[2] = '{"seq": 2, "type": "item.crea'
        path.write_text("\n".join(lines) + "\n")
        try:
            list(read_events(space))
            raised, detail = False, ""
        except CorruptLogError as exc:
            raised, detail = True, str(exc)
        self.check("damage in the MIDDLE of the log raises instead of dropping events",
                   raised, "read_events silently returned a short log")
        if raised:
            self.check("the error names the file and the line",
                       "drill.jsonl" in detail and "3" in detail, detail)

    def simultaneous_hosts(self) -> None:
        """Two hosts appending and publishing to one remote at the same moment.

        `_push_with_retry` exists because the competing writer is on another
        machine and no local lock can help. The drill puts two clones in a real
        race and asks the only question that matters: does either host's work
        disappear?
        """
        import ledger_transport as t
        space, origin = self.space("9-race", remote=True)
        other = self.root / "9-race-other"
        self.git(self.root, "clone", "-q", str(origin), str(other))
        self.git(other, "config", "user.email", "other@datacore")
        self.git(other, "config", "user.name", "other")
        self.git(other, "config", "core.hooksPath", str(other / ".git" / "hooks"))
        (other / ".datacore" / "events").mkdir(parents=True, exist_ok=True)
        self.register()

        # Distinct writers, as two hosts would be: per-writer files are disjoint.
        self.log(space, "mac").append("item.create", {"id": "m1", "title": "from mac"})
        self.log(other, "winston").append("item.create", {"id": "n1", "title": "from winston"})

        script = (
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "from pathlib import Path\n"
            "import ledger_transport as t\n"
            "r = t.converge(Path(sys.argv[1]), root=Path(%r))\n"
            "print(('ok' if r.ok else 'no') + ' ' + r.reason)\n"
        ) % (str(LIB), str(self.root))
        runner = self.root / "racer.py"
        runner.write_text(script)
        procs = [subprocess.Popen([sys.executable, str(runner), str(d)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for d in (space, other)]
        outs = [p.communicate()[0].decode().strip() for p in procs]
        print(f"      mac     -> {outs[0] or 'no output'}")
        print(f"      winston -> {outs[1] or 'no output'}")

        # Whatever each race outcome was, converging again must reconcile them.
        for d in (space, other):
            t.converge(d, root=self.root)
        rc_m, remote_mac = self.git(origin, "show", "main:.datacore/events/mac.jsonl")
        rc_w, remote_win = self.git(origin, "show", "main:.datacore/events/winston.jsonl")
        self.check("mac's event reached the remote", rc_m == 0 and '"m1"' in remote_mac)
        self.check("winston's event reached the remote", rc_w == 0 and '"n1"' in remote_win)
        self.check("neither host was left mid-merge",
                   not (space / ".git" / "MERGE_HEAD").exists()
                   and not (other / ".git" / "MERGE_HEAD").exists())

    def ingest_closes_the_drift_it_reports(self) -> None:
        """An ingest that reports drift it cannot close is an infinite loop.

        Not hypothetical. 2026-09-17: five items in 5-plur carried an empty
        NIGHTSHIFT_OUTPUT -- which nightshift writes when a run produced no
        artifact, and which requeue_rate_limited reads to tell a real
        execution from a rate-limited no-op. The importer read the bare
        `:KEY:` back as ""; the projector dropped empty values. So the ingest
        reported `updated= 5` every hour and closed nothing, the projection
        REFUSED, and the space's Phase-1 cycle -- which is fail-closed -- had
        been down for days. The refusal was right; the loop was the bug.
        """
        import re
        from ledger.fold import fold
        from ledger.log import read_events
        from ledger.projection_state import (guard_projection, ProjectionConflict,
                                              base_document, STATE, sync_generated)
        from ledger.projector import project
        space, _ = self.space("10-nonconvergence")
        (space / ".datacore" / "ledger-phase").write_text("1\n")
        (space / ".datacore" / "ledger-edit-protocol").write_text("1\n")
        self.log(space, "drill").append("item.create", {"id": "n1", "title": "A task"})

        target = space / "org" / "next_actions.org"
        rendered = project(fold(read_events(space)), space=space.name, as_of=0).text
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered)
        (space / STATE).parent.mkdir(parents=True, exist_ok=True)
        (space / STATE).write_text(base_document(rendered), encoding="utf-8")

        # Author an empty property value -- legitimate org, and legitimate
        # content here. Inserted before the drawer's own :END:, at its indent.
        edited, n = re.subn(r"^([ \t]*):END:", r"\1:NOTE:\n\1:END:", rendered,
                            count=1, flags=re.M)
        self.check("the drill authored an empty property value", n == 1)
        target.write_text(edited)

        proposed = project(fold(read_events(space)), space=space.name, as_of=0).text
        try:
            guard_projection(space, edited, proposed)
            drifted = False
        except ProjectionConflict:
            drifted = True
        self.check("an un-ingested authored field is REFUSED", drifted)

        sync_generated(space, fold(read_events(space)), "drill")

        after = project(fold(read_events(space)), space=space.name, as_of=0).text
        try:
            guard_projection(space, target.read_text(), after)
            closed, reason = True, ""
        except ProjectionConflict as exc:
            closed, reason = False, str(exc)
        self.check("ONE ingest closes the drift it reported", closed, reason)
        self.check("the ingested value survived the round trip",
                   ":NOTE:" in after, "the projector dropped what the ingest stored")

    def a_closer_must_not_rename_what_it_closes(self) -> None:
        """Closing a task through the real gh_reconcile, then ingesting twice.

        Not hypothetical. 2026-09-17 06:50Z: gh_reconcile's substitution added
        a separator on top of the one it captured, so every task it closed
        became `* DONE  Title`. Its own sync_state dismissed the item; the NEXT
        ingest then saw a leading space in the title -- an authored rename of a
        terminal item -- and the ledger refused it. 0-personal and 5-plur
        failed ingest on every cycle after. The second ingest is the one that
        broke, so this runs it.
        """
        import gh_reconcile
        from ledger.fold import fold
        from ledger.log import read_events
        from ledger.projection_state import base_document, STATE
        from ledger.projector import project
        from ledger_ingest_org import sync_state

        space, _ = self.space("11-closer")
        (space / ".datacore" / "ledger-phase").write_text("1\n")
        (space / ".datacore" / "ledger-edit-protocol").write_text("1\n")
        self.log(space, "drill").append("item.create", {
            "id": "c1", "title": "Ship the release notes", "state": "NEXT"})

        target = space / "org" / "next_actions.org"
        rendered = project(fold(read_events(space)), space=space.name, as_of=0).text
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered)
        (space / STATE).parent.mkdir(parents=True, exist_ok=True)
        (space / STATE).write_text(base_document(rendered), encoding="utf-8")

        text = target.read_text()
        tasks = [t for t in gh_reconcile.parse_org_tasks(text) if t.state == "NEXT"]
        self.check("the drill found the open task to close", len(tasks) == 1)
        if not tasks:
            return
        closed = gh_reconcile.mark_task_done(text.splitlines(), tasks[0], "merged", None)
        target.write_text("\n".join(closed) + "\n")

        first = second = None
        try:
            first = sync_state(space)
            second = sync_state(space)
            ok, why = True, ""
        except Exception as exc:  # noqa: BLE001 -- the failure IS the finding
            ok, why = False, f"{type(exc).__name__}: {exc}"
        self.check("the closing ingest and the one after it both succeed", ok, why)

        item = fold(read_events(space)).items.get("c1")
        self.check("the item is closed in the ledger", item is not None and item.status == "dismissed",
                   getattr(item, "status", "missing"))
        title = (getattr(item, "payload", None) or {}).get("title")
        self.check("closing did not change the title", title == "Ship the release notes", repr(title))

    # -- driver ---------------------------------------------------------
    SCENARIOS = ("unreachable_remote", "rejected_push", "resurrected_writer_ref",
                 "concurrent_appenders", "kill_mid_transaction",
                 "truncated_writer_log", "edit_between_ingest_and_project",
                 "torn_final_line", "simultaneous_hosts",
                 "ingest_closes_the_drift_it_reports",
                 "a_closer_must_not_rename_what_it_closes")

    def run(self, only: list[str] | None = None) -> int:
        names = only or list(self.SCENARIOS)
        for name in names:
            print(f"\n{name.replace('_', ' ').upper()}")
            self.ran.append(name)
            try:
                getattr(self, name)()
            except Exception as exc:  # noqa: BLE001 -- a crashed scenario is a failure, not a stop
                import traceback
                self.check(f"{name} completed", False, f"{type(exc).__name__}: {exc}")
                traceback.print_exc()
        print(f"\nchaos drill: {len(self.ran)} scenario(s), "
              + (f"FAILED — {', '.join(self.failures)}" if self.failures
                 else "every injected fault was survived or correctly refused"))
        return 1 if self.failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", metavar="NAME", help="run just these scenarios")
    ap.add_argument("--keep", action="store_true", help="leave the scratch fleet in place")
    ap.add_argument("--list", action="store_true", help="print scenario names and exit")
    a = ap.parse_args(argv)
    if a.list:
        for name in Drill.SCENARIOS:
            print(name)
        return 0
    unknown = set(a.only or ()) - set(Drill.SCENARIOS)
    if unknown:
        raise SystemExit(f"unknown scenario(s): {', '.join(sorted(unknown))}")

    # RESOLVED. `private_state_directory` refuses an aliased path, and on macOS
    # tempfile hands back /var/folders/... which is a symlink to /private/var.
    # Unresolved, every scenario that takes the transport's repo lock dies on
    # "runtime state must have an absolute unaliased path".
    base = Path(tempfile.mkdtemp(prefix="chaos-drill-")).resolve()
    root = base / "fleet"
    root.mkdir()
    # Nothing here may reach the real installation: its own state directory, and
    # neither git config file, so a global core.hooksPath cannot neutralise the
    # hooks these scenarios install.
    os.environ["DATACORE_ROOT"] = str(root)
    # OUTSIDE the data root, not under it: `private_state_directory` refuses
    # runtime state stored inside the data it is meant to protect.
    state_root = base / "state"
    # 0700, explicitly: `private_state_directory` refuses any component of the
    # state path that is readable or writable by anyone else, and mkdir's
    # default mode would hand it 0755.
    state_root.mkdir(mode=0o700)
    os.environ["DATACORE_STATE"] = str(state_root)
    os.environ["GIT_CONFIG_GLOBAL"] = "/dev/null"
    os.environ["GIT_CONFIG_SYSTEM"] = "/dev/null"
    os.environ.pop("DATACORE_LEDGER_SIGN", None)
    os.environ["DATACORE_ACTOR"] = "drill"
    print(f"scratch fleet at {root}")
    drill = Drill(root, keep=a.keep)
    try:
        return drill.run(a.only)
    finally:
        if a.keep:
            print(f"  scratch fleet kept at {base}")
        else:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
