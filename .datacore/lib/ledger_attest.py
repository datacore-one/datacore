#!/usr/bin/env python3
"""Record that an agent DID something in the outside world (DIP-0038/0046).

The ledger tracked tasks and spend, but not publishing. When Data posted to X,
nothing in the system knew: no event, no attestation, no trace. Spend was
metered to the cent while an irreversible, externally-visible action by an
autonomous agent left no record at all.

That is the wrong way round. A task can be re-derived from org; a tweet cannot
be un-sent. External side effects are precisely the actions most worth
attesting, because they are the ones you cannot reconstruct by re-reading local
state — and because "which agent published that, and when?" is a question the
system should be able to answer without you having to remember.

USE IT LIKE THIS, from any module:

    from ledger_attest import attest
    attest("x.post", ref=tweet_id, detail=text[:120], space="1-datafund")

DESIGN NOTES

`artifact.attest` already existed in the event vocabulary and nothing emitted
it. This is that gap closed rather than a new concept invented.

NEVER FAILS THE CALLER. A tweet that went out but could not be recorded is
still a tweet that went out; turning an accounting gap into a publishing outage
would be the worse trade. Failures are returned, not raised — the same rule the
executor spend path already follows.

RECORDS AFTER THE FACT, never before. Attesting an intended action would create
a record of something that may not have happened, which is worse than no record
because it reads as authoritative.
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


def _actor() -> str:
    """This machine's ledger identity. Never inferred from a hostname — see
    DIP-0044: winston's hostname is `bridge`, hermes runs `tris`."""
    explicit = os.environ.get("DATACORE_ACTOR")
    if explicit:
        return explicit.strip().lower()
    host = socket.gethostname().split(".")[0].lower()
    try:
        import yaml
        root = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
        reg = yaml.safe_load((root / ".datacore/registry/infrastructure.yaml").read_text())
        for name, cfg in (reg.get("servers") or {}).items():
            if not isinstance(cfg, dict):
                continue
            access = cfg.get("access") or {}
            if host in (access.get("hostname"), name) and access.get("actor"):
                return str(access["actor"]).lower()
    except Exception:  # noqa: BLE001 — a registry problem must not stop a post
        pass
    return host


def _space(space: str | None) -> Path | None:
    root = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
    if space:
        cand = root / space
        return cand if (cand / ".datacore" / "events").is_dir() else None
    # No space given: attest into the space that owns comms work if it exists,
    # else the first space with a ledger. Deliberately explicit rather than
    # silently picking one at random — the caller can always name it.
    for name in ("1-datafund", "5-plur", "0-personal"):
        cand = root / name
        if (cand / ".datacore" / "events").is_dir():
            return cand
    for cand in sorted(root.glob("[0-9]-*")):
        if (cand / ".datacore" / "events").is_dir():
            return cand
    return None


def attest(kind: str, *, ref: str = "", detail: str = "",
           space: str | None = None, extra: dict | None = None) -> str | None:
    """Record an external action. Returns the event hash, or None on failure.

    `kind`   what happened, dotted: "x.post", "x.reply", "email.sent"
    `ref`    the external identifier — a tweet id, message id, URL
    `detail` a short human-readable excerpt, truncated by the caller
    """
    try:
        target = _space(space)
        if target is None:
            return None
        from ledger.log import EventLog

        payload = {"kind": kind, "ref": str(ref), "detail": str(detail)[:280]}
        if extra:
            payload.update(extra)
        event = EventLog(target, _actor()).append("artifact.attest", payload)
        return event.hash
    except Exception:  # noqa: BLE001 — see module docstring: never fail a send
        return None


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="record an external action in the ledger")
    ap.add_argument("kind")
    ap.add_argument("--ref", default="")
    ap.add_argument("--detail", default="")
    ap.add_argument("--space")
    a = ap.parse_args()
    h = attest(a.kind, ref=a.ref, detail=a.detail, space=a.space)
    print(h or "attest failed (not fatal)")
    raise SystemExit(0 if h else 1)
