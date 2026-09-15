"""Canonical job-verification evidence shared by delegation and health views.

These are cooperative ledger attestations, not proof of OS identity. Invalid
or unreadable evidence is an explicit unknown; a filename is never a principal.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from actor_identity import base_writer, principal_of
from ledger.hlc import parse
from ledger.log import CorruptLogError, read_events
from ledger.verify import verify_events


@dataclass(frozen=True)
class Attestation:
    hlc: str
    timestamp: float
    ok: bool


def latest_jobs(root: Path, now: float, *, registry: Path | None = None) -> dict[str, dict[str, Attestation]]:
    """Latest eligible attestation per principal/contract across all chains.

    Future stamps cannot extend freshness. HLC counter orders same-millisecond
    observations; simultaneous contradictory observations conservatively fail.
    A malformed complete record raises, preserving the reader's unknown state.
    """
    latest: dict[str, dict[str, Attestation]] = {}
    root = Path(root)
    spaces = [root, *sorted(p for p in root.glob('[0-9]-*') if p.is_dir())]
    for space in spaces:
        events = read_events(space)
        chains: dict[str, list] = {}
        for event in events:
            chains.setdefault(event.log, []).append(event)
        for chain in chains.values():
            # read_events sorts by HLC. Recover physical chain order by seq;
            # malformed types and missing/duplicate links are not evidence.
            if any(type(event.seq) is not int for event in chain):
                raise CorruptLogError('invalid event sequence')
            chain.sort(key=lambda event: event.seq)
            if verify_events(list(enumerate(chain, 1))):
                raise CorruptLogError('invalid verification event chain')
        for event in events:
            if event.type != 'metric.attest':
                continue
            payload = event.payload
            if not isinstance(payload, dict):
                raise CorruptLogError('metric attestation payload is not an object')
            if payload.get('metric') != 'job.verify':
                continue
            try:
                ms, counter, writer = parse(event.hlc)
                job, ok, failures = payload.get('job'), payload.get('ok'), payload.get('failures')
                valid = (isinstance(job, str) and bool(job.strip()) and type(ok) is bool
                         and isinstance(failures, list) and all(isinstance(f, str) for f in failures)
                         and ok == (not failures) and 0 <= ms < 10**13 and 0 <= counter <= 9999
                         and event.hlc == f"{ms:013d}.{counter:04d}.{writer}"
                         and writer == event.actor and base_writer(event.log) == base_writer(event.actor))
                if not valid:
                    raise ValueError('invalid job verification evidence')
            except (ValueError, TypeError, AttributeError, OverflowError) as exc:
                raise CorruptLogError('invalid job verification evidence') from exc
            timestamp = ms / 1000
            if timestamp > now:
                continue
            name, _ = principal_of(event.actor, path=registry)
            if name is None:
                continue
            jobs = latest.setdefault(name, {})
            previous = jobs.get(job)
            if previous is None or event.hlc > previous.hlc:
                jobs[job] = Attestation(event.hlc, timestamp, ok)
            elif event.hlc == previous.hlc and not ok:
                jobs[job] = Attestation(event.hlc, timestamp, False)
    return latest
