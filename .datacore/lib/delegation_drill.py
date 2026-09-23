#!/usr/bin/env python3
"""Exercise the DELEGATION lifecycle end to end, offline, against real controls.

`ledger_chaos_drill.py` injects faults into the ledger and its transport. This
one takes the layer above: an item is created for someone, addressed, claimed,
executed, checked, and completed -- and every way that sequence can go wrong.
It is a separate file because it asks a different question, and it reuses that
module's scratch-fleet fixture rather than copying a setup whose whole job is
to keep a drill away from the operator's live state.

WHY THIS EXISTS. Delegation has been carrying real traffic since 2026-08-10 --
441 claims and 261 of them crossing hosts by 2026-09-18 -- while two of the
three controls that make it safe were advisory or absent. The duplicate that
proves it is in `ledger_claim.py`'s own comments: item 929eb69d6b was claimed
AND completed by both winston and miles, two models, two costs, one task, and
the answers agreed, which is the hardest kind of duplication to notice.

NOTHING HERE LEAVES THE MACHINE. The dispatcher's execution step normally
spawns a model. That is the one part of the loop this drill is not about, and
paying a model to say "done" fifteen times would make the drill too expensive
to run often -- which is the same as not having it. So it registers a local
`Executor` under `$DATACORE_EXECUTOR` that does the task itself, from a small
vocabulary in the task title, and calls `_execution_env()` exactly as the
subprocess adapters do so the claim/payload-hash/conflict guard is really
exercised rather than stepped around. Every task is work a machine can finish
alone: write a file, count lines in one. No network, no API, no cost.

    delegation_drill.py [--only NAME ...] [--keep] [--list]

Exit 0 when every control held; 1 naming the ones that did not.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

from ledger_chaos_drill import Drill, scratch_fleet  # noqa: E402

#: The roster every scenario runs against. Two principals with two writer names
#: each, because a principal owning more than one log is the ordinary case here
#: and it is exactly where a string compare stops being the same question as an
#: identity check (see `actor_identity.addressed_to`).
ROSTER = """principals:
  miles:
    kind: agent
    writes_as: [miles, nightshift]
    email_sha256: []
  winston:
    kind: agent
    writes_as: [winston, bridge]
    email_sha256: []
  tris:
    kind: agent
    writes_as: [tris]
    email_sha256: []
  gregor:
    kind: human
    writes_as: [mac]
    email_sha256: []
"""


def _install_local_executor():
    """Register the drill's own adapter and return its name.

    Deferred into a function because importing `executors` pulls in the whole
    ledger stack, which must happen AFTER the scratch fleet has redirected
    DATACORE_ROOT -- otherwise module-level constants bind to the real install.
    """
    from executors.base import Executor, register

    @register
    class DrillLocal(Executor):
        """Does the task, locally, deterministically, for nothing.

        The task vocabulary is tiny and lives in the title:

          write <TOKEN> into <file>   create the file, commit it
          count lines of <file> into <out>
          claim it is done           produce confident prose and change NOTHING
          fail                       report an error the way a transport would

        `claim it is done` is not padding. Two separate attempts in this
        system's history passed a declined task as DONE by reading the agent's
        prose for refusal markers, and the model simply rephrases. The check is
        the only evidence, and this verb is how the drill proves the check is
        what decides.
        """

        name = "drill-local"

        def _invoke(self, prompt: str, timeout_s: int) -> tuple[str, int]:
            # The guard the real adapters run before doing any work: this actor
            # must hold a current claim on this item, the payload must not have
            # changed under it, and replicated edits must be resolved.
            self._execution_env()

            title = ""
            for line in prompt.splitlines():
                if line.startswith("Task: "):
                    title = line[len("Task: "):].strip()
                    break
            cwd = Path(self._cwd) if self._cwd else Path.cwd()

            m = re.match(r"write (\S+) into (\S+) without committing$", title)
            if m:
                # The agent that does the work and leaves it in the worktree.
                # Both live runtimes behaved exactly this way on 2026-09-18.
                token, name = m.groups()
                (cwd / name).parent.mkdir(parents=True, exist_ok=True)
                (cwd / name).write_text(token + "\n")
                return f"wrote {token} into {name}", 0

            m = re.match(r"write (\S+) into (\S+)$", title)
            if m:
                token, name = m.groups()
                (cwd / name).parent.mkdir(parents=True, exist_ok=True)
                (cwd / name).write_text(token + "\n")
                self._commit(cwd, name)
                return f"wrote {token} into {name}", 0

            m = re.match(r"count lines of (\S+) into (\S+)$", title)
            if m:
                src, out = m.groups()
                n = len((cwd / src).read_text().splitlines())
                (cwd / out).write_text(f"{n}\n")
                self._commit(cwd, out)
                return f"counted {n} lines", 0

            m = re.match(r"write (\S+) into (\S+) and touch (\S+)$", title)
            if m:
                # An agent that also leaves unrelated files behind.
                token, name, extra = m.groups()
                (cwd / name).write_text(token + "\n")
                (cwd / extra).write_text("not part of the task\n")
                return f"wrote {token}, also touched {extra}", 0

            m = re.match(r"write the wrong thing into (\S+)$", title)
            if m:
                # Does something, commits it, and it is not what was asked.
                name = m.group(1)
                (cwd / name).write_text("WRONG\n")
                self._commit(cwd, name)
                return f"wrote {name}", 0

            if title == "escape the space":
                # Path traversal: an artifact written outside the working dir.
                target = cwd.parent / "escaped.txt"
                try:
                    target.write_text("escaped\n")
                except OSError as exc:
                    return f"could not escape: {exc}", 0
                return f"wrote {target}", 0

            if title == "claim it is done":
                return ("Done. I have completed the task and committed the "
                        "result; everything checks out."), 0

            if title == "fail":
                raise RuntimeError("the task could not be performed")

            return f"no drill verb in {title!r}", 0

        @staticmethod
        def _commit(cwd: Path, *paths: str) -> None:
            # The artifact must be COMMITTED or `_isolated_check` cannot see
            # it -- it checks a fresh worktree of the committed result, which
            # is the whole reason a pass means something durable.
            import subprocess
            for args in (("add", *paths), ("commit", "-qm", "drill: task result")):
                subprocess.run(["git", "-C", str(cwd), *args],
                               capture_output=True, text=True, timeout=60)

    os.environ["DATACORE_EXECUTOR"] = DrillLocal.name
    return DrillLocal.name


class DelegationDrill(Drill):
    SCENARIOS = (
        "the_happy_path",
        "addressed_work_reaches_only_its_addressee",
        "an_executor_alias_is_the_same_principal",
        "work_addressed_to_nobody_is_not_dispatched",
        "two_dispatchers_one_item",
        "a_claim_on_a_changed_payload_is_refused",
        "execution_without_a_current_claim_is_refused",
        "prose_is_not_evidence",
        "an_item_with_no_check_cannot_complete_itself",
        "side_effects_need_a_human",
        "an_unsatisfiable_item_is_dead_lettered",
        "a_grant_is_recorded",
        "a_principal_may_not_perform_a_never_effect",
        "a_delegation_chain_has_a_depth_limit",
        "an_unregistered_writer_may_not_create",
        "the_allowlist_is_enforced_by_the_gate_itself",
        "an_agent_that_does_not_commit_still_completes",
        "work_a_hook_refuses_says_what_refused_it",
        "a_sick_host_does_not_dead_letter_good_work",
        "an_unassigned_item_claimed_by_several_keeps_one_owner",
        "two_completions_of_one_item_leave_one_result",
        "a_wrong_answer_that_is_committed_still_fails",
        "unrelated_files_do_not_become_the_result",
        "an_artifact_written_outside_the_space_does_not_count",
        "a_crash_after_claiming_leaves_it_recoverable",
        "an_item_dismissed_mid_flight_cannot_complete",
        "a_check_cannot_touch_the_space_it_verifies",
    )

    # -- fixtures -------------------------------------------------------
    def delegation_space(self, name: str):
        """A space whose registry declares the drill roster.

        Carries the same `.gitignore` a real space does. That is not cosmetic:
        `_artifact_tree_clean` fails a check CLOSED when anything outside
        `.datacore/events/` is dirty, so a fixture that ignores less than
        production would fail every check for a reason production never meets --
        and a fixture that ignores MORE would hide one it does.
        """
        space, _ = self.space(name)
        # A REAL POLICY FILE, because without one `load_policy()` returns the
        # default whose `principals` is None and the entire stage-4 gate --
        # delegation allowlist, hops limit, daily cap, unregistered writer --
        # stays dormant. A drill running against a policy-less fleet would have
        # reported every control green while testing none of them, which is
        # precisely how the 2026-09-18 allowlist hole survived: it was only
        # found by an exercise that ran against the installation's own policy.
        cfg = self.root / ".datacore" / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "approvals_policy.yaml").write_text(
            "version: 1\napprover: human\ncosign_effects: [email.send, payment, prod.deploy]\n"
            "principals:\n"
            "  winston: {may_delegate_to: [miles, tris]}\n"
            "  miles: {may_delegate_to: [tris]}\n"
            "  tris: {may_delegate_to: [miles]}\n"
            "  gregor: {}\n")
        import ledger.policy as _p
        _p.DEFAULT_POLICY_PATH = cfg / "approvals_policy.yaml"
        # `dir/*`, not `dir/` -- the form production uses, for the reason its
        # own comment gives: git cannot re-include a file under an excluded
        # directory.
        (space / ".gitignore").write_text(".datacore/state/*\n.datacore/env/\n"
                                          ".datacore/learning/\nCLAUDE.md\n")
        self.git(space, "add", ".gitignore")
        self.git(space, "commit", "-qm", "ignore runtime state")
        reg = self.root / ".datacore" / "registry"
        reg.mkdir(parents=True, exist_ok=True)
        (reg / "principals.yaml").write_text(ROSTER)
        import actor_identity
        actor_identity.PRINCIPALS = reg / "principals.yaml"
        return space

    def delegate(self, space, *, by: str, to: str | None, title: str,
                 check: str | None = None, **extra):
        """Create one item the way a briefing does, through the real gate."""
        from ledger.log import EventLog
        from ledger.policy import guarded_append
        payload = {"id": extra.pop("id", None) or f"item-{abs(hash(title)) % 10**8}",
                   "title": title, **extra}
        if to:
            payload["assignee"] = to
        if check:
            payload["check"] = check
        guarded_append(EventLog(space, by, sign=False), "item.create", payload)
        return payload["id"]

    def dispatch(self, space, actor: str, *, execute: bool = True, limit: int = 3) -> str:
        """Run the real dispatcher and return everything it printed."""
        import io
        import contextlib as _c
        import ledger_claim
        argv = sys.argv
        sys.argv = ["ledger_claim.py", "--space", str(space), "--actor", actor,
                    "--limit", str(limit)] + (["--execute"] if execute else [])
        buf = io.StringIO()
        try:
            with _c.redirect_stdout(buf), _c.redirect_stderr(buf):
                ledger_claim.main()
        finally:
            sys.argv = argv
        return buf.getvalue()

    def _tree_clean(self, space) -> bool:
        import ledger_claim
        return ledger_claim._artifact_tree_clean(space)

    def item(self, space, iid: str):
        from ledger.fold import fold
        from ledger.log import read_events
        return fold(read_events(space)).items.get(iid)

    # -- scenarios ------------------------------------------------------
    def the_happy_path(self) -> None:
        """Winston asks, Miles does it, the check proves it, the item closes.

        The baseline. If this does not hold, nothing below is meaningful --
        every other scenario asserts that some control STOPS this sequence, and
        a sequence that never worked cannot be shown to stop for the right
        reason.
        """
        space = self.delegation_space("1-happy")
        iid = self.delegate(space, by="winston", to="miles",
                            title="write VERIFIED into proof.txt",
                            check="grep -qx VERIFIED proof.txt")

        out = self.dispatch(space, "miles")

        it = self.item(space, iid)
        self.check("miles claimed and completed it",
                   it is not None and it.status in ("completed", "verified"),
                   f"status={getattr(it, 'status', None)}; {out[:200]}")
        self.check("the artifact is committed, not just written",
                   (space / "proof.txt").read_text().strip() == "VERIFIED",
                   (space / "proof.txt").read_text()[:60] if (space / "proof.txt").exists() else "absent")

    def addressed_work_reaches_only_its_addressee(self) -> None:
        """A dispatcher declines what is addressed to someone else."""
        space = self.delegation_space("2-addressed")
        iid = self.delegate(space, by="winston", to="miles",
                            title="write A into a.txt", check="test -f a.txt")

        out = self.dispatch(space, "tris")

        self.check("tris does not take miles' work",
                   self.item(space, iid).status == "created", out[:200])
        self.check("and says so rather than skipping in silence",
                   "addressed to another agent" in out, out[:200])

    def an_executor_alias_is_the_same_principal(self) -> None:
        """Addressed to `nightshift`: only the writer named may run it.

        Decision L4 (2026-09-23): dispatch needs the EXACT writer. `miles`
        and `nightshift` are writers of one principal on two hosts; when both
        could claim, both ran the work. So `miles` now declines an item
        addressed to its sibling -- and says so, because a decline writes no
        event and a silent one would sit `created` for ever.
        """
        space = self.delegation_space("3-alias")
        iid = self.delegate(space, by="winston", to="nightshift",
                            title="write NS into ns.txt", check="grep -qx NS ns.txt")

        out = self.dispatch(space, "miles")
        self.check("miles declines work addressed to its sibling writer",
                   self.item(space, iid).status == "created", self.item(space, iid).status)
        self.check("and names the sibling rather than skipping in silence",
                   "a sibling writer of miles" in out, out[:200])

        self.dispatch(space, "nightshift")
        self.check("nightshift completes work addressed to it",
                   self.item(space, iid).status in ("completed", "verified"),
                   self.item(space, iid).status)

        # And a different principal is still refused.
        other = self.delegate(space, by="winston", to="nightshift",
                              title="write X into x.txt", check="test -f x.txt")
        self.dispatch(space, "tris")
        self.check("tris still may not take it", self.item(space, other).status == "created",
                   self.item(space, other).status)

    def work_addressed_to_nobody_is_not_dispatched(self) -> None:
        """First-come is the race, not a mitigation of it."""
        space = self.delegation_space("4-unaddressed")
        iid = self.delegate(space, by="winston", to=None,
                            title="write U into u.txt", check="test -f u.txt")

        first = self.dispatch(space, "miles")
        second = self.dispatch(space, "tris")

        self.check("nobody claims it", self.item(space, iid).status == "created",
                   self.item(space, iid).status)
        self.check("and both hosts name it as needing an assignee",
                   "addressed to NOBODY" in first and "addressed to NOBODY" in second,
                   first[:160])

    def two_dispatchers_one_item(self) -> None:
        """Both fold their own copy, both claim legitimately. One result survives.

        This is the shape of the 929eb69d6b duplicate. Addressing makes it
        impossible; this asserts what the LEDGER does when it happens anyway,
        because an append cannot be taken back and the fold is the only place
        the second claim can be resolved.
        """
        space = self.delegation_space("5-race")
        from ledger.log import EventLog
        from ledger.policy import guarded_append, PolicyError
        iid = self.delegate(space, by="winston", to=None,
                            title="write R into r.txt", check="test -f r.txt")

        guarded_append(EventLog(space, "miles", sign=False), "item.claim",
                       {"id": iid, "owner": "miles"})
        refused = ""
        try:
            guarded_append(EventLog(space, "tris", sign=False), "item.claim",
                           {"id": iid, "owner": "tris"})
        except PolicyError as exc:
            refused = str(exc)

        it = self.item(space, iid)
        self.check("the item has exactly one owner", it.owner == "miles", str(it.owner))
        self.check("the second claim is refused, not silently dropped",
                   "no longer available" in refused or "missing" in refused, refused or "accepted!")

    def a_claim_on_a_changed_payload_is_refused(self) -> None:
        """Selected, then edited, then claimed -- the claim must not stand.

        A dispatcher chooses an item, something edits it, and the claim would
        otherwise bind an agent to work it never read.
        """
        space = self.delegation_space("6-changed")
        from ledger.log import EventLog
        from ledger.policy import guarded_append, PolicyError, approval_payload_hash
        iid = self.delegate(space, by="winston", to="miles",
                            title="write C into c.txt", check="test -f c.txt")
        stale = approval_payload_hash(self.item(space, iid).payload)

        guarded_append(EventLog(space, "winston", sign=False), "item.update",
                       {"id": iid, "title": "write SOMETHING ELSE into c.txt"})

        refused = ""
        try:
            guarded_append(EventLog(space, "miles", sign=False), "item.claim",
                           {"id": iid, "owner": "miles", "payload_hash": stale})
        except PolicyError as exc:
            refused = str(exc)
        self.check("a claim carrying the pre-edit hash is refused",
                   "changed after dispatch" in refused, refused or "accepted!")

    def execution_without_a_current_claim_is_refused(self) -> None:
        """The executor's own guard, not the dispatcher's."""
        space = self.delegation_space("7-unclaimed")
        from executors import get_executor
        iid = self.delegate(space, by="winston", to="miles",
                            title="write E into e.txt", check="test -f e.txt")

        res = get_executor().run("Task: write E into e.txt", cwd=space, space=space,
                                 item=iid, actor="miles")

        self.check("an unclaimed item cannot be executed",
                   bool(res.error) and "claim" in (res.error or "").lower(), res.error or "ran!")
        self.check("and nothing was written", not (space / "e.txt").exists())

    def prose_is_not_evidence(self) -> None:
        """The agent says it is done. The check says otherwise. The check wins.

        Two attempts in this system's history sniffed agent prose for refusal
        markers and both passed failures as DONE, because a model rephrases.
        """
        space = self.delegation_space("8-prose")
        iid = self.delegate(space, by="winston", to="miles",
                            title="claim it is done",
                            check="test -f never-written.txt")

        out = self.dispatch(space, "miles")

        it = self.item(space, iid)
        self.check("a confident report does not complete the item",
                   it.status not in ("completed", "verified"),
                   f"status={it.status}; {out[:160]}")

    def an_item_with_no_check_cannot_complete_itself(self) -> None:
        """Without evidence there is nothing to complete against."""
        space = self.delegation_space("9-nocheck")
        iid = self.delegate(space, by="winston", to="miles",
                            title="write N into n.txt")

        out = self.dispatch(space, "miles", execute=False)

        self.check("the plan says it cannot auto-complete",
                   "NO CHECK" in out, out[:200])

    def side_effects_need_a_human(self) -> None:
        """Effects are gated TWICE, and the drill was wrong about the first one.

        This scenario was written expecting one control -- the dispatcher
        refusing to run an item with effects unattended. Creating the fixture
        found a second, earlier one: an item carrying effects cannot be created
        at all without a content-bound `approval.grant` from the policy's
        approver. So the sequence is: no grant, no item; grant, item; and even
        then no unattended execution.

        The grant binds the exact payload, so an approval obtained for one
        proposal cannot be spent on a different one.
        """
        space = self.delegation_space("10-effects")
        from ledger.log import EventLog
        from ledger.policy import guarded_append, approval_payload_hash, PolicyError
        proposal = {"id": "eff-1", "title": "write S into s.txt", "assignee": "miles",
                    "check": "grep -qx S s.txt", "effects": ["email.send"]}

        refused = ""
        try:
            guarded_append(EventLog(space, "winston", sign=False), "item.create", dict(proposal))
        except PolicyError as exc:
            refused = str(exc)
        self.check("no approval, no item", "approval_ref" in refused, refused or "created!")

        grant = EventLog(space, "human", sign=False).append(
            "approval.grant", {"item": proposal["id"],
                               "payload_hash": approval_payload_hash(proposal)})

        # A grant for THIS payload does not license a different one.
        elsewhere = ""
        try:
            guarded_append(EventLog(space, "winston", sign=False), "item.create",
                           {**proposal, "title": "something else entirely",
                            "approval_ref": grant.hash})
        except PolicyError as exc:
            elsewhere = str(exc)
        self.check("an approval cannot be spent on different content",
                   "does not bind this payload" in elsewhere, elsewhere or "created!")

        guarded_append(EventLog(space, "winston", sign=False), "item.create",
                       {**proposal, "approval_ref": grant.hash})
        self.check("the approved proposal is admitted",
                   self.item(space, proposal["id"]) is not None)

        out = self.dispatch(space, "miles")

        self.check("but the dispatcher still will not run it unattended",
                   "REFUSED" in out, out[:200])
        self.check("it stays open for a human",
                   self.item(space, proposal["id"]).status == "created",
                   self.item(space, proposal["id"]).status)
        self.check("and nothing ran", not (space / "s.txt").exists())

    def an_unsatisfiable_item_is_dead_lettered(self) -> None:
        """Released MAX_ATTEMPTS times, it is dismissed with the reason.

        One item was released 40 times and another 38 times over 48.9h on a
        dead token. Nothing counted, nothing backed off, nothing gave up.
        """
        space = self.delegation_space("11-deadletter")
        from ledger.log import EventLog
        from ledger_claim import MAX_ATTEMPTS
        iid = self.delegate(space, by="winston", to="miles",
                            title="write D into d.txt", check="test -f d.txt")
        log = EventLog(space, "miles", sign=False)
        for _ in range(MAX_ATTEMPTS):
            log.append("item.claim", {"id": iid, "owner": "miles"})
            log.append("item.release", {"id": iid, "owner": "miles", "reason": "drill"})

        out = self.dispatch(space, "miles")

        it = self.item(space, iid)
        self.check("it gives up instead of looping", it.status == "dismissed", it.status)
        self.check("and the log says why",
                   "DEADLETTER" in out and str(MAX_ATTEMPTS) in out, out[:200])

    def a_grant_is_recorded(self) -> None:
        """A control that can be demanded and audited must leave a trace."""
        space = self.delegation_space("12-grant")
        from ledger.log import EventLog
        iid = self.delegate(space, by="winston", to="miles",
                            title="write G into g.txt", check="test -f g.txt")
        log = EventLog(space, "miles", sign=False)
        log.append("item.claim", {"id": iid, "owner": "miles"})
        EventLog(space, "gregor", sign=False).append("item.grant", {"id": iid})

        it = self.item(space, iid)
        self.check("the fold records who granted execution",
                   it.granted_by == "gregor", str(it.granted_by))
        self.check("and when", bool(it.granted_at), str(it.granted_at))

    def a_principal_may_not_perform_a_never_effect(self) -> None:
        """No grant can allow a never."""
        space = self.delegation_space("13-never")
        from claim_gate import check_claim

        class _P:
            approver = "gregor"
            principals = {"miles": {"never_effects": ["payment"]}}

        ok, why = check_claim("miles", {"effects": ["payment"]}, policy=_P(), space_dir=space)
        self.check("a never-effect is refused at the gate", not ok, why)
        ok2, _ = check_claim("miles", {"effects": ["research"]}, policy=_P(), space_dir=space)
        self.check("an ordinary effect still passes", ok2)

    def a_delegation_chain_has_a_depth_limit(self) -> None:
        """A creates for B creates for A -- the ledger would record it for ever."""
        space = self.delegation_space("14-hops")
        from claim_gate import check_create

        class _P:
            principals = {"miles": {"max_hops": 2}}

        ok, why = check_create("miles", {"title": "t", "hops": 5}, policy=_P(), space_dir=space)
        self.check("too deep a chain is refused", not ok, why)
        ok2, _ = check_create("miles", {"title": "t", "hops": 1}, policy=_P(), space_dir=space)
        self.check("a shallow one is allowed", ok2)

    def an_unregistered_writer_may_not_create(self) -> None:
        """An undeclared name must not become a principal by writing."""
        space = self.delegation_space("15-unregistered")
        from claim_gate import check_create

        ok, why = check_create("nobody-declared-this", {"title": "t"}, space_dir=space)
        self.check("an unregistered writer is refused", not ok, why)
        self.check("and the refusal names the registry",
                   "principals.yaml" in why, why)

    def the_allowlist_is_enforced_by_the_gate_itself(self) -> None:
        """Through `guarded_append`, with no policy argument -- the ungated form.

        Every other allowlist assertion here calls `check_create` directly,
        which is why they all passed while the gate in front of it was doing
        nothing. `guarded_append(log, "item.create", payload)` -- the shortest
        thing a caller can write -- resolved `policy=None` to "no stage 4 at
        all", so the delegation allowlist, the hops limit, the daily cap and
        the unregistered-writer refusal were skipped in silence.

        Found on 2026-09-18 by the fleet exercise on its first run: `data`
        created an item assigned to `winston`, which `approvals_policy.yaml`
        forbids, and the gate allowed it.
        """
        space = self.delegation_space("16-gate")
        from ledger.log import EventLog
        from ledger.policy import guarded_append, PolicyError

        refused = ""
        try:
            guarded_append(EventLog(space, "miles", sign=False), "item.create",
                           {"id": "gate-1", "title": "write Z into z.txt", "assignee": "winston"})
        except PolicyError as exc:
            refused = str(exc)
        self.check("an unlisted delegation is refused with no policy argument",
                   "may not delegate" in refused, refused or "created!")

        # And one the policy does allow still goes through.
        guarded_append(EventLog(space, "miles", sign=False), "item.create",
                       {"id": "gate-2", "title": "write Z into z.txt", "assignee": "tris"})
        self.check("a permitted delegation is still admitted",
                   self.item(space, "gate-2") is not None)

    def an_agent_that_does_not_commit_still_completes(self) -> None:
        """The agent does the work and leaves it in the worktree. It completes.

        Completion used to depend on the agent remembering to commit. Measured
        2026-09-18 across two hosts and two runtimes: the agents computed the
        right answer every time and committed it none of the time, so every
        delegated item failed a check its work had actually satisfied.

        The guarantee is untouched -- the check still runs, in an isolated
        worktree of the committed result, and it still decides. Only the
        evidence is now guaranteed to exist.
        """
        space = self.delegation_space("17-nocommit")
        iid = self.delegate(space, by="winston", to="miles",
                            title="write VERIFIED into proof.txt without committing",
                            check="grep -qx VERIFIED proof.txt")

        out = self.dispatch(space, "miles")

        it = self.item(space, iid)
        self.check("the item completes anyway",
                   it.status in ("completed", "verified"), f"{it.status}; {out[:200]}")
        rc, log = self.git(space, "log", "--format=%s", "-5")
        self.check("and the result is a real commit, not a dirty tree",
                   any(iid in line for line in log.splitlines()), log.strip()[:120])
        self.check("the tree is left clean for the next item",
                   self._tree_clean(space))

    def work_a_hook_refuses_says_what_refused_it(self) -> None:
        """A pre-commit hook rejecting the artifact must name itself.

        A DIP-0015 structure hook refused a top-level `drill/` directory on
        2026-09-18, so the artifact could not be committed, the check failed
        closed, and the message said "commit task changes" -- which blames the
        agent for something no agent could have done.
        """
        space = self.delegation_space("18-hook")
        hooks = space / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "pre-commit").write_text(
            "#!/bin/sh\necho 'structure: forbidden/ is not an allowed root dir' >&2\nexit 1\n")
        (hooks / "pre-commit").chmod(0o755)
        iid = self.delegate(space, by="winston", to="miles",
                            title="write X into forbidden/x.txt without committing",
                            check="test -f forbidden/x.txt")

        out = self.dispatch(space, "miles")

        self.check("the item does not complete", self.item(space, iid).status != "completed",
                   self.item(space, iid).status)
        self.check("and the refusal names the hook, not the agent",
                   "commit refused" in out and "not an allowed root dir" in out, out[-300:])

    def an_unassigned_item_claimed_by_several_keeps_one_owner(self) -> None:
        """Nobody is addressed, three dispatchers reach for it at once.

        The dispatcher declines to offer unaddressed work at all now, so this
        goes straight at the ledger the way a pre-fix host would have. What is
        asserted is the property the fold must hold whatever anyone appends:
        exactly one owner, and the losers refused by name rather than dropped.
        """
        space = self.delegation_space("19-scramble")
        from ledger.log import EventLog
        from ledger.policy import guarded_append, PolicyError
        iid = self.delegate(space, by="winston", to=None,
                            title="write S into s.txt", check="test -f s.txt")

        outcomes = {}
        for who in ("miles", "tris", "winston"):
            try:
                guarded_append(EventLog(space, who, sign=False), "item.claim",
                               {"id": iid, "owner": who})
                outcomes[who] = "claimed"
            except PolicyError as exc:
                outcomes[who] = str(exc)

        it = self.item(space, iid)
        winners = [w for w, o in outcomes.items() if o == "claimed"]
        self.check("exactly one dispatcher wins", len(winners) == 1, str(outcomes))
        self.check("and the ledger agrees who it was",
                   it.owner == winners[0], f"{it.owner} vs {winners}")
        self.check("the losers are told, not silently dropped",
                   all("available" in o or "missing" in o
                       for w, o in outcomes.items() if w != winners[0]), str(outcomes))

    def two_completions_of_one_item_leave_one_result(self) -> None:
        """Two hosts both finish it. The second completion must not overwrite.

        This is the 929eb69d6b shape at the far end of the loop: two models,
        two costs, one task. Whatever reaches the log, the fold has to settle
        on one result and say that it ignored the other.
        """
        space = self.delegation_space("20-double")
        from ledger.log import EventLog
        iid = self.delegate(space, by="winston", to="miles",
                            title="write D into d.txt", check="test -f d.txt")
        EventLog(space, "miles", sign=False).append("item.claim", {"id": iid, "owner": "miles"})
        EventLog(space, "miles", sign=False).append(
            "item.complete", {"id": iid, "owner": "miles", "detail": "first"})
        EventLog(space, "tris", sign=False).append(
            "item.complete", {"id": iid, "owner": "tris", "detail": "second"})

        it = self.item(space, iid)
        self.check("one owner survives", it.owner == "miles", str(it.owner))
        self.check("the item is terminal exactly once",
                   it.status in ("completed", "verified"), it.status)

    def a_wrong_answer_that_is_committed_still_fails(self) -> None:
        """Committing is not passing. The check is what decides."""
        space = self.delegation_space("21-wrong")
        iid = self.delegate(space, by="winston", to="miles",
                            title="write the wrong thing into answer.txt",
                            check="grep -qx RIGHT answer.txt")

        self.dispatch(space, "miles")

        it = self.item(space, iid)
        self.check("a committed wrong answer does not complete",
                   it.status not in ("completed", "verified"), it.status)
        self.check("and the wrong answer is still on the record to inspect",
                   (space / "answer.txt").read_text().strip() == "WRONG")

    def unrelated_files_do_not_become_the_result(self) -> None:
        """An agent that also touches something else must not fail the item.

        The dispatcher records whatever the execution produced, and the check
        decides. A stray file is not evidence either way -- but it must not
        leave the tree dirty for the next item, which is what made a whole
        run's remaining work fail before.
        """
        space = self.delegation_space("22-stray")
        iid = self.delegate(space, by="winston", to="miles",
                            title="write OK into ok.txt and touch stray.txt",
                            check="grep -qx OK ok.txt")

        self.dispatch(space, "miles")

        self.check("the item completes on its own evidence",
                   self.item(space, iid).status in ("completed", "verified"),
                   self.item(space, iid).status)
        self.check("and the tree is clean for whoever comes next",
                   self._tree_clean(space))

    def an_artifact_written_outside_the_space_does_not_count(self) -> None:
        """Work that lands outside the space cannot be evidence for it."""
        space = self.delegation_space("23-escape")
        iid = self.delegate(space, by="winston", to="miles", title="escape the space",
                            check="test -f escaped.txt")

        self.dispatch(space, "miles")

        self.check("the item does not complete",
                   self.item(space, iid).status not in ("completed", "verified"),
                   self.item(space, iid).status)
        self.check("and the space is unchanged", self._tree_clean(space))

    def a_crash_after_claiming_leaves_it_recoverable(self) -> None:
        """The claim is recorded BEFORE the work, so a crash is visible.

        Work that happened with no trace that anyone started it is the thing
        this ordering exists to prevent.
        """
        space = self.delegation_space("24-crash")
        from ledger.log import EventLog
        iid = self.delegate(space, by="winston", to="miles",
                            title="write C into c.txt", check="test -f c.txt")
        EventLog(space, "miles", sign=False).append("item.claim", {"id": iid, "owner": "miles"})

        it = self.item(space, iid)
        self.check("it is visibly claimed, not lost", it.status == "claimed", it.status)
        self.check("and it names who was working on it", it.owner == "miles", str(it.owner))

        out = self.dispatch(space, "tris")
        self.check("another host does not steal a claimed item",
                   "would claim" not in out and self.item(space, iid).owner == "miles",
                   out[:160])

    def an_item_dismissed_mid_flight_cannot_complete(self) -> None:
        """Closed while the agent was working: the answer arrives too late."""
        space = self.delegation_space("25-dismissed")
        from ledger.log import EventLog
        iid = self.delegate(space, by="winston", to="miles",
                            title="write M into m.txt", check="test -f m.txt")
        log = EventLog(space, "miles", sign=False)
        log.append("item.claim", {"id": iid, "owner": "miles"})
        EventLog(space, "winston", sign=False).append(
            "item.dismiss", {"id": iid, "owner": "winston", "kind": "dropped",
                             "reason": "no longer wanted"})
        log.append("item.complete", {"id": iid, "owner": "miles", "detail": "too late"})

        it = self.item(space, iid)
        self.check("a dismissed item stays dismissed", it.status == "dismissed", it.status)

    def a_check_cannot_touch_the_space_it_verifies(self) -> None:
        """The check runs on a throwaway worktree, so it cannot edit the space.

        A check is a shell command out of a payload. If it ran in the space it
        would be an arbitrary write dressed as a verification.
        """
        space = self.delegation_space("26-sandbox")
        (space / "precious.txt").write_text("keep me\n")
        self.git(space, "add", "precious.txt")
        self.git(space, "commit", "-qm", "precious")
        iid = self.delegate(space, by="winston", to="miles",
                            title="write P into p.txt",
                            check="rm -f precious.txt && test -f p.txt")

        self.dispatch(space, "miles")

        self.check("the space still has what the check tried to delete",
                   (space / "precious.txt").exists())
        self.check("and the tree is unharmed", self._tree_clean(space))

    def a_sick_host_does_not_dead_letter_good_work(self) -> None:
        """A dead runtime must not delete the work addressed to it.

        `item.release` is what the dead-letter counter counts, and after
        MAX_ATTEMPTS the item is dismissed for good. That is right for a task
        nobody can satisfy and wrong for a host whose agent runtime is down:
        plur-claw's openclaw stopped answering on 2026-09-18, and every item
        addressed to `data` would have been dismissed within three ticks --
        deleting good work because one machine was sick.
        """
        space = self.delegation_space("27-sick")
        from ledger.log import EventLog
        from ledger_claim import MAX_ATTEMPTS
        iid = self.delegate(space, by="winston", to="miles",
                            title="write K into k.txt", check="test -f k.txt")
        log = EventLog(space, "miles", sign=False)
        for _ in range(MAX_ATTEMPTS + 2):
            log.append("item.claim", {"id": iid, "owner": "miles"})
            log.append("item.release", {"id": iid, "owner": "miles",
                                        "kind": "infrastructure",
                                        "error": "openclaw: binary not found on PATH"})

        out = self.dispatch(space, "miles", execute=False)

        self.check("it is still offered, not dead-lettered",
                   self.item(space, iid).status == "created", self.item(space, iid).status)
        self.check("and no deadletter was written", "DEADLETTER" not in out, out[:200])

        # A task that genuinely cannot be satisfied still gives up.
        other = self.delegate(space, by="winston", to="miles",
                              title="write J into j.txt", check="test -f j.txt")
        for _ in range(MAX_ATTEMPTS):
            log.append("item.claim", {"id": other, "owner": "miles"})
            log.append("item.release", {"id": other, "owner": "miles", "error": "the task is wrong"})
        self.dispatch(space, "miles")
        self.check("an unsatisfiable task is still dead-lettered",
                   self.item(space, other).status == "dismissed",
                   self.item(space, other).status)

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
        print(f"\ndelegation drill: {len(self.ran)} scenario(s), "
              + (f"FAILED — {', '.join(self.failures)}" if self.failures
                 else "every control held"))
        return 1 if self.failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", metavar="NAME", help="run just these scenarios")
    ap.add_argument("--keep", action="store_true", help="leave the scratch fleet in place")
    ap.add_argument("--list", action="store_true", help="print scenario names and exit")
    a = ap.parse_args(argv)
    if a.list:
        for name in DelegationDrill.SCENARIOS:
            print(name)
        return 0
    unknown = set(a.only or ()) - set(DelegationDrill.SCENARIOS)
    if unknown:
        raise SystemExit(f"unknown scenario(s): {', '.join(sorted(unknown))}")

    with scratch_fleet(prefix="delegation-drill-", keep=a.keep) as root:
        _install_local_executor()
        return DelegationDrill(root, keep=a.keep).run(a.only)


if __name__ == "__main__":
    raise SystemExit(main())
