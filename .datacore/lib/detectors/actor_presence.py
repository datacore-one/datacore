#!/usr/bin/env python3
"""Every rostered actor still has a log, and it is still advancing (DIP-0046 A2).

`verify_chain` proves a log you hand it is intact. It has nothing to say about a
log that is not there, because **a chain of zero events is a valid chain**.
Delete an actor's `.jsonl` and every surviving copy still reports OK. That is not
hypothetical: a sweep wiped 110 files from datafund-space on 2026-07-21, and
`git_fleet_sync` refuses to propagate deletions precisely because of it.

So absence needs its own detector, and absence can only be detected against a
statement of what *should* exist. The roster in `registry/infrastructure.yaml` is
therefore load-bearing for INTEGRITY, not merely for naming — this is the thing
that reads it and holds it to account.

Two failures, deliberately distinguished:

  MISSING   a rostered actor has no log, or a log with no readable events.
  STALLED   the log exists but its `seq` went BACKWARDS since the last run.

Backwards is the interesting one. An append-only log whose head seq decreases has
been truncated or restored from an older copy — silent data loss that leaves a
perfectly valid chain behind. A log that merely stops growing is not an error:
an actor with nothing to say writes nothing, and alerting on that trains the
operator to ignore this detector.

State lives in ~/.datacore/state/actor-presence.json. A first run establishes the
baseline and cannot report STALLED, which is stated rather than hidden: a
detector that cannot fire on its first run should say so.

Exit 0 all present, 1 on any missing/stalled, 2 on error.

    actor_presence.py [--root DIR] [--json]
"""
from __future__ import annotations

import argparse
import os
import json
from datetime import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seq_gap import head_seq  # noqa: E402  — same parsing, one definition

STATE = Path.home() / ".datacore" / "state" / "actor-presence.json"


def roster(root: Path) -> dict[str, list[str]]:
    """machine -> [actor]. Deployment shape, per DIP-0046 §11: this file says
    which actors may RUN somewhere, which is a different fact from which spaces
    they may write to. Absence detection only needs the set of actor names."""
    import yaml
    p = root / ".datacore" / "registry" / "infrastructure.yaml"
    d = yaml.safe_load(p.read_text()) or {}
    servers = d.get("servers") or {}
    return {name: (cfg or {}).get("ledger_actors") or []
            for name, cfg in servers.items() if isinstance(cfg, dict)}


def observed(root: Path) -> dict[str, dict]:
    """actor -> {spaces: {space: head_seq}} across every space log on this box."""
    out: dict[str, dict] = {}
    for space in sorted(root.glob("[0-9]-*")):
        ev = space / ".datacore" / "events"
        if not ev.is_dir():
            continue
        for log in sorted(ev.glob("*.jsonl")):
            seq = head_seq(log.read_text(errors="replace"))
            out.setdefault(log.stem, {"spaces": {}})["spaces"][space.name] = seq
    return out


# ---- silence, learned per actor -------------------------------------------
#
# A liveness BEACON proves the wrong thing. Tris wrote HEARTBEAT_OK faithfully
# every 30 minutes while its transport was unusable, its runner unreadable by
# the user running it, and its sync on a pre-DIP path — a dead-man switch on
# that signal would have been green throughout. So silence is measured from
# WORK: an actor whose seq advances did something, and no beacon can fake that.
#
# The threshold is per-actor because a global one is empirically wrong. Measured
# inter-event gaps: `data` has a p90 of 6.95h and a max of 22.9h, while winston
# sits at 0.25h. One hour would fire on `data` constantly — and a detector that
# cries wolf gets muted, which is worse than not having it.
MIN_SAMPLES = 20        # below this, cadence cannot be learned honestly
FLOOR_HOURS = 6.0       # never alarm faster than this, whatever history says
MARGIN = 2.0            # x the LONGEST quiet spell already seen
                        # 1.5 flagged nightshift at 13.9h against a 13.7h
                        # threshold — a 0.2h overshoot on an actor whose
                        # batches are ~12h apart, i.e. the observed max simply
                        # under-sampled its true period. For periodic work the
                        # longest gap seen is a floor on the real one, so the
                        # margin has to absorb that. 2.0 still catches a dead
                        # nightshift inside a day and keeps winston's genuine
                        # 26.5h finding.


def gap_hours(events_ms: list[int]) -> list[float]:
    ts = sorted(events_ms)
    return [g for g in ((ts[i + 1] - ts[i]) / 3_600_000 for i in range(len(ts) - 1)) if g > 0]


def actor_history(root: Path) -> dict[str, list[int]]:
    """actor -> every event timestamp (ms) across every space on this box."""
    import json as _json
    out: dict[str, list[int]] = {}
    for space in sorted(root.glob("[0-9]-*")):
        ev = space / ".datacore" / "events"
        if not ev.is_dir():
            continue
        for log in sorted(ev.glob("*.jsonl")):
            for line in log.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    hlc = _json.loads(line).get("hlc") or ""
                    out.setdefault(log.stem, []).append(int(hlc.split(".")[0]))
                except (ValueError, TypeError):
                    continue
    return out


def silence_verdict(stamps: list[int], now_ms: int) -> tuple[str, float, float]:
    """(verdict, silent_hours, threshold_hours).

    'unknown' when there is too little history to learn a cadence — reported,
    never alarmed. Claiming a threshold from four samples would be inventing
    one, and this detector's whole value is that its alarms mean something.
    """
    if not stamps:
        return "no-data", 0.0, 0.0
    silent = (now_ms - max(stamps)) / 3_600_000
    gaps = gap_hours(stamps)
    if len(gaps) < MIN_SAMPLES:
        return "unknown", silent, 0.0
    # THE LONGEST GAP, not a percentile. These actors are BURSTY: 95% of mac's
    # events are seconds apart, so its p95 is ~0 and any percentile-based
    # threshold collapses onto the arbitrary floor — which then fires on a
    # perfectly normal overnight quiet. Measured: p95 0.00h, real max 8.4h, and
    # a 6h floor flagged mac, nightshift and winston on the first run while all
    # three were healthy.
    #
    # What the alarm should mean is "quieter than this actor has EVER been",
    # which is the max gap, times a margin. It costs lateness — an actor that
    # dies right after a long idle spell is caught late — and buys the alarm
    # meaning something, which is the only reason to have it.
    threshold = max(max(gaps) * MARGIN, FLOOR_HOURS)
    return ("silent" if silent > threshold else "ok"), silent, threshold


def _default_root() -> Path:
    """Root from DATACORE_ROOT, then ~/Data — NEVER from this file's location.

    A second checkout exists for scheduled runs (~/.datacore/v2-runner). A
    location-derived root would make this scan THAT tree, which holds zero
    spaces, and report "0 findings" — a false green, and the same defect
    seq_gap shipped once already as a parents[] off-by-one.
    """
    return Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=_default_root())
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        expected = roster(args.root)
    except Exception as exc:  # noqa: BLE001 — an unreadable roster is an ERROR
        print(f"  ERROR cannot read roster: {type(exc).__name__}: {exc}")
        return 2

    seen = observed(args.root)
    history = actor_history(args.root)
    now_ms = int(datetime.now().timestamp() * 1000)
    prev = {}
    if STATE.exists():
        try:
            prev = json.loads(STATE.read_text()).get("actors", {})
        except ValueError:
            prev = {}          # unreadable state: re-baseline rather than crash
    first_run = not prev

    rows: list[dict] = []
    sil: dict[str, tuple] = {}
    for machine, actors in sorted(expected.items()):
        for actor in actors:
            here = seen.get(actor)
            if not here or not here["spaces"]:
                # An actor may legitimately have written nothing ANYWHERE yet, so
                # only a PREVIOUSLY-OBSERVED actor going absent is a failure. The
                # baseline must therefore record observations, never expectations
                # — an earlier version stored every rostered actor including the
                # ones that had never written, so on the next run all of them
                # looked like they had vanished: 5 MISSING instead of 1.
                status = "missing" if actor in prev else "no-log-yet"
            else:
                status = "ok"
                for space, seq in here["spaces"].items():
                    was = (prev.get(actor, {}).get("spaces", {}) or {}).get(space)
                    if was is not None and seq is not None and seq < was:
                        status = "stalled"
                        break
                # SILENT is checked only for actors that are otherwise fine: a
                # stalled log is a louder finding and must not be masked by it.
                if status == "ok":
                    verdict, silent_h, thr = silence_verdict(history.get(actor, []), now_ms)
                    if verdict == "silent":
                        status = "silent"
                    sil[actor] = (verdict, silent_h, thr)
            rows.append({"machine": machine, "actor": actor, "status": status,
                         "spaces": (here or {}).get("spaces", {})})

    # SILENT counts as failing: an actor that has stopped doing work is the
    # condition this exists to surface, and miles sat 32h quiet with nothing
    # flagging it before this was added.
    bad = [r for r in rows if r["status"] in ("missing", "stalled", "silent")]

    # SCANNING NOTHING IS NOT A PASS. An empty roster means the registry was not
    # found — usually a wrong root — and "0 rostered actors, 0 failing" reads as
    # perfect health in every summary above this one.
    if not rows:
        print(f"ERROR: no rostered actors resolved under {args.root} — "
              "refusing to report clean")
        return 2

    if args.json:
        print(json.dumps({"rows": rows, "failures": len(bad),
                          "first_run": first_run}, indent=2))
    else:
        for r in rows:
            where = ", ".join(f"{s}:{q}" for s, q in sorted(r["spaces"].items())) or "—"
            tag = {"ok": "ok   ", "no-log-yet": "new  ", "silent": "SILENT ",
                   "missing": "MISSING", "stalled": "STALLED"}[r["status"]]
            v = sil.get(r["actor"])
            note = ""
            if v and v[0] == "silent":
                note = f"  [silent {v[1]:.1f}h > {v[2]:.1f}h threshold]"
            elif v and v[0] == "unknown":
                note = f"  [cadence unknown: <{MIN_SAMPLES} gaps]"
            print(f"  {tag} {r['actor']:<12} ({r['machine']:<10}) {where}{note}")
        # SURFACE THE NEVER-WRITTEN. An actor with no log is "no-log-yet", not
        # "missing", and that is right — a new actor has not failed. But folded
        # into "0 failing" it is indistinguishable from an actor that has been
        # rostered for months and never worked, which is exactly what `tris` was
        # on 2026-08-12: code deployed, hooks installed, zero events ever. The
        # count is reported separately rather than alarmed on, because a genuinely
        # new actor must not page anyone.
        never = [r["actor"] for r in rows if r["status"] == "no-log-yet"]
        print(f"\nactor-presence: {len(rows)} rostered actor(s), {len(bad)} failing"
              + (f", {len(never)} never written ({', '.join(never)})" if never else "")
              + ("  [first run — baseline established, STALLED cannot fire]" if first_run else ""))

    # A missing actor KEEPS its prior baseline. Dropping it would make the next
    # run see an unknown actor, report "no-log-yet", and exit 0 — the detector
    # silently healing the very deletion it just caught. Observed on the first
    # fault injection: red, then green on re-run with the log still gone.
    new_state = {a: v for a, v in prev.items()}
    for r in rows:
        if r["status"] in ("ok", "stalled"):
            new_state[r["actor"]] = {"spaces": r["spaces"]}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"actors": new_state}, indent=2))

    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
