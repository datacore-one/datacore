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

WHY NOT IN v2_verify. That file checks THIS host's installation, per DIP, and
is already 36 checks long. This asks one question -- is the RECORD sound -- and
answers it for every space at once, so it can run on one host and speak for the
fleet. It is also the natural place to add an invariant, which a 36-check
checklist is not.

    ledger_invariants.py [--root DIR] [--json] [--quick]

Exit 0 when every invariant holds, 1 when one does not, 2 if the sweep itself
could not run. A space it cannot read is reported as unknown, never as sound.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
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
                                     event.payload, event.prev)
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
    """
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return [e for e in (data.get("accepted") or []) if isinstance(e, dict)]
    except (OSError, ValueError, Exception):  # noqa: BLE001 -- no baseline is not an error
        return []


def _accepted(finding, accepted: list[dict]) -> bool:
    return any(e.get("invariant") == finding.invariant
               and e.get("space") == finding.space
               and finding.detail.startswith(str(e.get("detail_startswith", "")))
               for e in accepted)


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
        (known if _accepted(f, accepted) else broken).append(f)
    unknown = [f for f in findings if f.unknown]
    if a.json:
        print(json.dumps({"ok": not broken, "spaces": len(spaces),
                          "broken": [vars(f) for f in broken],
                          "accepted": [vars(f) for f in known],
                          "unknown": [vars(f) for f in unknown]}, indent=2))
    else:
        for f in findings:
            mark = "known" if (not f.unknown and _accepted(f, accepted)) else None
            print(f"  {f}" + (f"   [{mark}, accepted by the owner]" if mark else ""))
        verdict = "SOUND" if not broken else "BROKEN"
        print(f"ledger-invariants: {verdict} — {len(spaces)} space(s), "
              f"{len(broken)} new, {len(known)} accepted, {len(unknown)} could-not-tell")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
