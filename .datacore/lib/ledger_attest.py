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


def _ensure_ledger_importable() -> None:
    """Put whichever tree actually holds the `ledger` package on sys.path.

    This module and the package it needs are not always siblings. On plur-claw
    the space carries `.datacore/lib/ledger_attest.py` but the core — including
    `ledger/` — lives in `~/.datacore/v2-runner`, so importing this file
    succeeded and importing `ledger.log` from it did not. attest() catches
    everything by design, so the ModuleNotFoundError surfaced as a silent
    "FAILED" and Data's posts went unrecorded.

    Resolved HERE rather than in each caller: comms computes the lib path from
    DATACORE_ROOT, and every other future caller would invent its own guess.
    One module knows where the ledger is; nobody else should have to.
    """
    for cand in (LIB,
                 Path(os.environ.get("DATACORE_RUNNER",
                                     str(Path.home() / ".datacore" / "v2-runner")))
                 / ".datacore" / "lib"):
        if (cand / "ledger" / "log.py").is_file():
            if str(cand) not in sys.path:
                # APPEND, never insert(0). The runner tree holds 200-odd
                # modules, not just `ledger`; putting it first means one
                # attest() call in a long-lived agent permanently repoints
                # every later `import config_plane` or `import
                # org_workspace_adapter` at whatever copy that tree happens to
                # carry. Appending resolves `ledger` without outranking
                # anything the caller already had.
                sys.path.append(str(cand))
            return


def _identity() -> dict:
    """Per-machine identity, DECLARED rather than inferred.

    `~/.datacore/identity.env`, KEY=VALUE per line:

        DATACORE_ACTOR=data
        DATACORE_ATTEST_SPACE=$HOME/spaces/5-plur   # absolute; see below

    Every identity bug this file has had came from inferring what should have
    been stated. The hostname lookup filed tris's events under `transporter`.
    The registry lookup returned `holodeck` on plur-claw because that machine's
    registry copy is four months stale. The space glob picked
    `~/Data/1-datacore-space` because it globbed one root before reaching the
    one holding the space that was wanted.

    None of those are fixable by better inference — a machine's identity is not
    derivable from its filesystem, and every heuristic that appears to work is
    one layout change from being silently wrong. Written once at provisioning,
    it is checkable; guessed at call time, it is not.

    PARSED BY config_plane.load(), not by hand. The first version rolled its own
    loop and got two things wrong that config_plane already had right: it did
    not strip a leading `export `, so `export DATACORE_ACTOR=data` was stored
    under the key `export DATACORE_ACTOR` and the declaration silently did
    nothing — falling straight back to the hostname inference this exists to
    replace. And it stripped quotes unmatched and repeatedly, mangling values
    that legitimately contain them.

    Both failures are silent and produce a plausible result, which is the
    signature of every bug in this file's history.
    """
    f = Path.home() / ".datacore" / "identity.env"
    try:
        import config_plane
        return config_plane.load(f)
    except ImportError:
        # config_plane IS THE PREFERRED PARSER, NOT A REQUIRED ONE.
        #
        # Reusing it was right — it already handles `export `, matched quotes
        # and key validation. But it is not deployed everywhere: plur-claw's
        # space tree has no copy, so `import config_plane` raised, the broad
        # except returned {}, and identity silently reverted to hostname
        # inference — actor `holodeck` again, the precise bug the declaration
        # was introduced to end. Caught by re-running the end-to-end check on
        # the machine rather than trusting the local test pass.
        #
        # Identity must not depend on another module being present, so the
        # fallback is inline and deliberately handles the same `export ` case.
        pass
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    try:
        # errors="replace": a stray non-UTF-8 byte raises UnicodeDecodeError,
        # and this call sits outside _actor()'s own guard — one bad byte
        # disabled every attestation on the machine.
        for line in f.read_text(errors="replace").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):]
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]          # matched quotes only
            out[k.strip()] = v
    except OSError:
        return {}
    return out


def _actor() -> str:
    """This machine's ledger identity. Never inferred from a hostname — see
    DIP-0044: winston's hostname is `bridge`, hermes runs `tris`.

    The env var and this module's own identity file are consulted first (the
    attest path must keep working with the identity file it was told to use);
    everything else is the shared resolver in actor_identity.py."""
    explicit = os.environ.get("DATACORE_ACTOR") or _identity().get("DATACORE_ACTOR")
    if explicit:
        return explicit.strip().lower()
    try:
        from actor_identity import this_actor
    except ImportError:
        import importlib.util as _ilu, pathlib as _pl
        _spec = _ilu.spec_from_file_location("actor_identity", _pl.Path(__file__).resolve().parent / "actor_identity.py")
        _m = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_m)
        this_actor = _m.this_actor
    return this_actor()


def _roots() -> list[Path]:
    """Where spaces might live on THIS machine.

    Not every box keeps them under ~/Data. plur-claw holds its spaces in
    ~/spaces and uses ~/Data for the OpenClaw workspace, so a DATACORE_ROOT-only
    lookup found no ledger and attest silently returned None — meaning Data,
    the agent most likely to publish, was the one machine whose posts would not
    have been recorded. Found by being asked "will the system know?" rather than
    by any check, which is why the possible shapes are enumerated here.
    """
    home = Path.home()
    env = os.environ.get("DATACORE_ROOT")
    if env:
        # AN EXPLICIT SETTING WINS OUTRIGHT. Discovery is for machines that
        # said nothing; falling back past a value the operator set would write
        # events somewhere they did not choose — and would make a deliberately
        # bogus root silently succeed against the real ledger, which a test
        # caught immediately.
        cands = [Path(env)]
    else:
        cands = [home / "Data", home / "spaces"]
    # Dedup on the RESOLVED path: ~/Data is a symlink into the OpenClaw
    # workspace on plur-claw, so two entries can name one directory while
    # comparing unequal. Comparing the literal paths deduped nothing that could
    # actually collide.
    seen, out = set(), []
    for c in cands:
        if not c.is_dir():
            continue
        key = c.resolve()
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _space(space: str | None) -> Path | None:
    if space:
        # ABSOLUTE PATHS FIRST. `root / space` discards root when space is
        # absolute, so the old single-expression form handled this by accident;
        # looping over _roots() removed the accident, and an absolute space
        # stopped resolving whenever _roots() was empty — which is exactly the
        # bogus-DATACORE_ROOT case a caller would be debugging.
        direct = Path(space).expanduser()
        if direct.is_absolute():
            return direct if (direct / ".datacore" / "events").is_dir() else None
        for r in _roots():
            cand = r / space
            if (cand / ".datacore" / "events").is_dir():
                return cand
        return None

    declared = _identity().get("DATACORE_ATTEST_SPACE")
    if declared:
        # A DECLARATION IS SUBORDINATE TO AN EXPLICIT DATACORE_ROOT, and is
        # never a licence to guess.
        #
        # Checked before _roots() in the first version, so a deliberately bogus
        # DATACORE_ROOT was overridden by the file and attest wrote a real event
        # into the declared space — breaking the invariant asserted a dozen
        # lines above, and passing on the mac only because no identity.env
        # exists there. Verified failing by test.
        cand = Path(declared).expanduser()
        if not cand.is_absolute():
            # A RELATIVE DECLARATION RESOLVES AGAINST cwd, which is wherever
            # cron, systemd or a shell happened to start the process. Running
            # from ~/Data, `DATACORE_ATTEST_SPACE=5-plur` finds a real space and
            # looks correct; the same config from /tmp finds nothing, and from
            # another checkout finds the WRONG space. Identity that depends on
            # the working directory is not declared identity.
            return None
        roots = _roots()
        if os.environ.get("DATACORE_ROOT") and not any(
                cand == r or r in cand.parents for r in roots):
            return None
        if (cand / ".datacore" / "events").is_dir():
            return cand
        # Stated and wrong is not the same as unstated. A relative or mistyped
        # declaration that quietly reverts to the glob heuristic leaves the
        # operator believing identity is declared while events land elsewhere;
        # attest is already never-fatal, so None is the honest answer.
        return None
    # No space given: attest into the space that owns comms work if it exists,
    # else the first space with a ledger. Deliberately explicit rather than
    # silently picking one at random — the caller can always name it.
    # NAMED SPACES ACROSS EVERY ROOT BEFORE ANY GLOB. Interleaving them let a
    # root's glob fallback outrank another root's exact match: on plur-claw
    # `~/Data/1-datacore-space` won over `~/spaces/5-plur`, so attestations
    # would have landed in a space nobody was looking at.
    roots = _roots()
    for name in ("1-datafund", "5-plur", "0-personal"):
        for r in roots:
            cand = r / name
            if (cand / ".datacore" / "events").is_dir():
                return cand
    for r in roots:
        for cand in sorted(r.glob("[0-9]-*")):
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
        _ensure_ledger_importable()
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
