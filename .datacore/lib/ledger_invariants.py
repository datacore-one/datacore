#!/usr/bin/env python3
"""Step 3 of 5: the whole-ledger truths, asserted in one sweep, on a schedule.

On 2026-09-18 exactly ONE event in 67,610 had a payload that no longer matched
its recorded hash. That single event made `latest_jobs` raise, which made
`claim_gate.absent` report EVERY principal absent, which stamped
`assignee_absent` on every delegated item -- an alarm that was always on, for a
reason unrelated to whether anyone had been heard from. It was found by hand, in
a transcript, by someone who happened to be looking. Nothing asserted it.

These are the invariants that hold across a healthy installation regardless of
what anyone did today. They are cheap, they are global, and none of them was
checked anywhere:

  hashes        every event's payload still hashes to its recorded hash
  chains        every writer log links prev -> hash with dense seq from 0
  parseable     every line of every log is JSON (a merge marker is not)
  declared      every writer belongs to a declared principal
  unforked      no (actor, seq) disagrees with origin
  published     nothing sits unpublished past the grace window
  delivered     the hub's admitted log and the git copy agree: no event the
                hub admitted is missing from git, and none in git is missing
                from the hub, past the grace window (hub spaces only; a hub
                that does not answer, or is not named, is could-not-tell)

WHY NOT IN v2_verify. That file checks THIS host's installation, per DIP, and
is already 36 checks long. This asks one question -- is the RECORD sound -- and
answers it for every space at once, so it can run on one host and speak for the
fleet. It is also the natural place to add an invariant, which a 36-check
checklist is not.

    ledger_invariants.py [--root DIR] [--json] [--quick]

Exit 0 when every invariant holds, 1 when one does not, 2 if the sweep itself
could not run OR could not tell. A space it cannot read is reported as unknown,
never as sound: when the only findings left after the baseline are
could-not-tell, the verdict line reads `ledger-invariants: UNKNOWN` and the exit
code is 2 (owner decision L2, 2026-09-23). Before that such a run printed SOUND
and exited 0, which the nightly contract's `last_line_regex` accepted.
A new (unaccepted) finding still wins: BROKEN, exit 1.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

ROOT = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))


class Finding:
    def __init__(self, invariant: str, space: str, detail: str, unknown: bool = False):
        self.invariant, self.space, self.detail, self.unknown = invariant, space, detail, unknown

    def __str__(self) -> str:
        return f"{'?' if self.unknown else 'x'} {self.invariant:<11} {self.space:<16} {self.detail}"


def _spaces(root: Path) -> list[Path]:
    return [p for p in sorted(root.glob("[0-9]-*"))
            if (p / ".datacore" / "events").is_dir()]


#: How long an event may sit on one side of the hub/git boundary before it is
#: a finding: the same 90-minute grace as `published` (ledger_transport.gaps).
DELIVERY_GRACE_MS = 90 * 60 * 1000


def delivered(space: Path, transport, *, now_ms: int | None = None, grace_ms: int = DELIVERY_GRACE_MS,
              registry_path=None) -> list[Finding]:
    """The hub's admitted log against this git copy (plan 2026-10-31, phase H).

    Once the hub is the delivery path the git copy must be checked against it,
    both ways, for events older than `grace_ms`:
      * an event the hub admitted that never reached git -- what the hub sends
        beyond this copy's heads, through the readers' own path
        (`ledger.hub.task_events_hub_first`), so the same chain rule applies;
      * an event this copy admits that the hub never admitted -- above the
        hub's admitted watermark for its chain (`status()["watermarks"]`).
    Task logs only (telemetry is not delivered through the hub, phase C).
    A space without a hub: nothing to compare. A hub space with no transport
    (no hub named, the broker served no token) or a hub that does not answer:
    one could-not-tell finding -- never a pass."""
    from ledger import hub as hubmod
    from ledger.log import TELEMETRY_SUFFIX, read_task_events

    name = Path(space).name
    if hubmod.load_config(space) is None:
        return []
    if transport is None:
        return [Finding("delivered", name, "could not tell: no hub named for this host (DATACORE_HUB_URL / "
                        "DATACORE_HUB_TOKEN_ID) or the broker served no token -- not compared, not a pass", True)]
    now = int(time.time() * 1000) if now_ms is None else now_ms

    def old(e) -> bool:
        ms = hubmod._physical_ms(e.hlc)
        return ms is not None and now - ms > grace_ms

    def chain(e) -> str:
        return getattr(e, "log", None) or e.actor

    def summary(events, what: str) -> str:
        by: dict[str, list[int]] = {}
        for e in events:
            by.setdefault(chain(e), []).append(e.seq)
        parts = [f"{c} seq {min(v)}" + (f"-{max(v)}" if len(v) > 1 else "") for c, v in sorted(by.items())]
        return f"{len(events)} event(s) {what}: {', '.join(parts)}"

    local = read_task_events(space)
    held = {e.hash for e in local}
    merged, source = hubmod.task_events_hub_first(space, transport)
    if source != "hub":
        return [Finding("delivered", name, "could not tell: the hub did not answer -- not compared, not a pass",
                        True)]
    try:
        marks = transport.status().get("watermarks")
    except (hubmod.HubError, ValueError, TypeError, OSError, AttributeError):
        marks = None
    if not isinstance(marks, dict):
        return [Finding("delivered", name, "could not tell: the hub did not report its admitted watermarks "
                        "-- not compared, not a pass", True)]

    findings = []
    missing = [e for e in merged if e.hash not in held and old(e)]
    if missing:
        findings.append(Finding("delivered", name, summary(missing, "the hub admitted never reached git")))
    admitted, _ = hubmod.authoritative_events(space, local, registry_path=registry_path, now_ms=now)
    unseen = [e for e in admitted if not chain(e).endswith(TELEMETRY_SUFFIX) and type(e.seq) is int
              and e.seq > (marks.get(chain(e)) if type(marks.get(chain(e))) is int else -1) and old(e)]
    if unseen:
        findings.append(Finding("delivered", name, summary(unseen, "in git the hub never admitted")))
    return findings


def _hub_transport(space: Path):
    """The hub transport for this space, from the environment (phase D2)."""
    from ledger import hub as hubmod
    return hubmod.transport_from_env(space, "ledger-invariants")


def sweep(root: Path, quick: bool = False) -> list[Finding]:
    from ledger.events import from_line, body_dict, compute_hash
    from ledger.log import read_events
    from actor_identity import principal_of

    findings: list[Finding] = []
    for space in _spaces(root):
        events_dir = space / ".datacore" / "events"

        # parseable + hashes + chains, read straight off disk rather than
        # through read_events, because read_events sorts and merges and this
        # must see each FILE exactly as it sits.
        for log in sorted(events_dir.glob("*.jsonl")):
            writer = log.stem
            prev, expected_seq, broke = "GENESIS", 0, False
            try:
                lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                findings.append(Finding("parseable", space.name, f"{writer}: unreadable ({exc})", True))
                continue
            for n, raw in enumerate(lines, 1):
                if not raw.strip():
                    continue
                try:
                    event = from_line(raw)
                except Exception as exc:  # noqa: BLE001 -- a bad line is the finding
                    findings.append(Finding("parseable", space.name,
                                            f"{writer}: line {n} is not an event ({str(exc)[:60]})"))
                    broke = True
                    break
                if not quick:
                    body = body_dict(event.seq, event.hlc, event.actor, event.type,
                                     event.payload, event.prev, hub=event.hub or "",
                                     domain=event.domain or "")
                    if compute_hash(body) != event.hash:
                        findings.append(Finding("hashes", space.name,
                                                f"{writer} seq {event.seq}: payload no longer "
                                                f"hashes to its recorded hash"))
                if event.seq != expected_seq or event.prev != prev:
                    findings.append(Finding("chains", space.name,
                                            f"{writer}: expected seq {expected_seq} prev "
                                            f"{prev[:8]}, found seq {event.seq} prev {str(event.prev)[:8]}"))
                    broke = True
                    break
                expected_seq, prev = event.seq + 1, event.hash
            if broke:
                continue

            principal, _ = principal_of(writer)
            if principal is None:
                findings.append(Finding("declared", space.name,
                                        f"{writer}: belongs to no declared principal"))

        # unforked -- compared against origin, and only where that comparison
        # can actually be made (see ledger.fork: a space that is not its own
        # repository is not comparable, and saying so beats guessing).
        try:
            from ledger.fork import detect
            report = detect(space)
            if report.reason:
                findings.append(Finding("unforked", space.name, report.reason, True))
            elif not report.clean:
                first = report.collisions[0]
                findings.append(Finding("unforked", space.name,
                                        f"{len(report.collisions)} colliding (actor, seq), "
                                        f"e.g. {first[0]} seq {first[1]}"))
        except Exception as exc:  # noqa: BLE001
            findings.append(Finding("unforked", space.name, f"detector unavailable: {exc}", True))

        if not quick:
            try:
                from ledger_transport import gaps
                result = gaps(space)
                # ONLY PAST THE GRACE WINDOW. `gaps()` is not ok while events
                # are merely pending, which is the normal steady state between
                # a local write and the next hourly converge -- so alerting on
                # it would page after every single append. seq-gap already owns
                # the moment the grace expires; this asserts the harder fact,
                # that nothing has been stranded beyond it.
                stranded = [r for r in (result.context or {}).get("rows", []) if r.get("gap")]
                if stranded:
                    findings.append(Finding("published", space.name,
                                            f"{len(stranded)} log(s) unpublished past the grace window"))
            except Exception as exc:  # noqa: BLE001
                findings.append(Finding("published", space.name, f"could not tell: {exc}", True))

            try:
                findings.extend(delivered(space, _hub_transport(space)))
            except Exception as exc:  # noqa: BLE001 -- the comparison breaking is could-not-tell
                findings.append(Finding("delivered", space.name, f"could not tell: {type(exc).__name__}: {exc}",
                                        True))

    return findings


def _baseline(path: Path) -> list[dict]:
    """Findings the owner has seen and accepted.

    A NEW audit that is red on its first day teaches everyone to ignore it, and
    the two findings live here on 2026-09-19 are both owner decisions -- one
    needs a principal declared, the other would mean rewriting published ledger
    history. Neither is mine to make, and neither should page anyone nightly.

    This is an allowlist, not a mute button: entries match on invariant, space
    and the START of the detail, so a SECOND bad event in the same log is still
    a new finding. Each entry carries the date it was accepted and why, so the
    list can be read later and argued with.

    The shipped file is a template; this install's accepted findings live in
    the gitignored `<name>.local.yaml` beside it, read too (INS-3).
    """
    out: list[dict] = []
    for p in (path, path.with_name(path.name.replace(".yaml", ".local.yaml"))):
        try:
            import yaml
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            out += [e for e in (data.get("accepted") or []) if isinstance(e, dict)]
        except (OSError, ValueError, Exception):  # noqa: BLE001 -- no baseline is not an error
            continue
    return out


def _prefix_matches(detail: str, prefix: object) -> bool:
    """`detail` starts with `prefix` AT A TOKEN BOUNDARY, and `prefix` is not empty.

    Two ways the plain `startswith` turned the allowlist into a mute button
    (both replayed 2026-09-23, DatacoreSpec/LedgerSeal.lean):
      * an entry with no `detail_startswith` compared against "" and accepted
        EVERY finding of that invariant in that space;
      * "tris seq 5" also accepted "tris seq 50", "tris seq 51", ... -- a second
        bad event in the same log, the exact case the docstring says stays new.
    """
    if not isinstance(prefix, str) or not prefix.strip():
        return False
    if not detail.startswith(prefix):
        return False
    rest = detail[len(prefix):]
    return not rest or not (prefix[-1:].isalnum() and (rest[0].isalnum() or rest[0] == "_"))


def _accepted(finding, accepted: list[dict], root: Path | None = None) -> bool:
    """An entry names its space by NAME (`plur`), never by this host's folder
    number; a legacy `5-plur` still matches `3-plur` (ENG-2026-08-03-047)."""
    from spaces import same_space
    folder = (root / finding.space) if root else Path(finding.space)
    return any(e.get("invariant") == finding.invariant
               and same_space(e.get("space"), folder)
               and _prefix_matches(finding.detail, e.get("detail_startswith"))
               for e in accepted)


def verdict(broken: list, unknown: list) -> tuple[str, int]:
    """(verdict word, exit code). SOUND only when nothing is broken AND nothing
    is could-not-tell (Lean: DatacoreSpec/LedgerSeal.lean `exit_zero_iff_all_sound`)."""
    if broken:
        return "BROKEN", 1
    if unknown:
        return "UNKNOWN", 2
    return "SOUND", 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quick", action="store_true",
                    help="skip the per-event hash recomputation and the publish check")
    ap.add_argument("--baseline", type=Path, default=LIB.parent / "config" / "ledger-invariants-baseline.yaml",
                    help="findings already accepted by the owner; anything NEW still fails")
    a = ap.parse_args(argv)

    spaces = _spaces(a.root)
    if not spaces:
        print(f"ledger-invariants: no space with an event log under {a.root} — "
              f"refusing to report a sound ledger")
        return 2

    try:
        findings = sweep(a.root, quick=a.quick)
    except Exception as exc:  # noqa: BLE001 -- the harness itself broke
        print(f"ledger-invariants: sweep failed: {type(exc).__name__}: {exc}")
        return 2

    accepted = _baseline(a.baseline)
    broken, known = [], []
    for f in findings:
        if f.unknown:
            continue
        (known if _accepted(f, accepted, a.root) else broken).append(f)
    unknown = [f for f in findings if f.unknown]
    word, code = verdict(broken, unknown)
    if a.json:
        print(json.dumps({"ok": code == 0, "verdict": word, "spaces": len(spaces),
                          "broken": [vars(f) for f in broken],
                          "accepted": [vars(f) for f in known],
                          "unknown": [vars(f) for f in unknown]}, indent=2))
    else:
        for f in findings:
            mark = "known" if (not f.unknown and _accepted(f, accepted, a.root)) else None
            print(f"  {f}" + (f"   [{mark}, accepted by the owner]" if mark else ""))
        print(f"ledger-invariants: {word} — {len(spaces)} space(s), "
              f"{len(broken)} new, {len(known)} accepted, {len(unknown)} could-not-tell")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
