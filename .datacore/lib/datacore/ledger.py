"""Attestation: recording what an agent did to the outside world (DIP-0047).

    from datacore.ledger import attest, attests

    @attests("x.post", ref=lambda r: r["data"]["id"])
    def post_tweet(text: str) -> dict: ...

Two ways in, and the decorator is the one to reach for. `attest()` is the
imperative form for places where the action is not a single function call.

WHY A DECORATOR AND NOT A MANIFEST THAT PATCHES FUNCTIONS AT IMPORT

Deriving the wiring from `module.yaml` and monkeypatching at load time was
considered and rejected. It fails invisibly — a rename, a load-order change, a
module imported by a path the loader does not scan, and the attestation quietly
stops happening. Invisible failure is the exact thing this exists to defeat; a
mechanism whose own failure mode is silence cannot be the one guarding against
silence.

The decorator sits in the diff, greps like any other call, and breaks loudly if
the function it wraps disappears. The manifest still declares the egress — but
for the conformance test to CHECK, not for the runtime to apply. Declaration and
implementation are deliberately two artifacts that must agree, because a single
artifact cannot detect its own absence.
"""
from __future__ import annotations

import functools
import inspect
import os
import sys
from pathlib import Path
from typing import Any, Callable

__all__ = ["attest", "attests", "EGRESS_KINDS", "CREDENTIAL_KINDS",
           "FUND_KINDS", "ATTEST_KINDS"]

# The vocabulary. A `kind` outside this set is a typo until someone adds it here
# deliberately — which is the point: the conformance check reads this, so a
# misspelled kind fails a test rather than producing an event nobody queries.
EGRESS_KINDS = frozenset({
    "x.post", "x.reply",
    "email.sent", "email.reply",
    "telegram.sent",
    "github.issue", "github.pr", "github.comment",
    "trade.order",
    "spend.llm", "spend.search", "spend.image", "spend.video",
    # Added by the first full module sweep. Each names an action that reaches a
    # third party and cannot be undone locally: a WhatsApp message delivered, a
    # Notion page created in someone's workspace, a Readwise document destroyed.
    "whatsapp.sent",
    "notion.page",
    "readwise.delete",
    # Forge sells things. `etsy.publish` is separate from `etsy.listing` on
    # purpose: creating a draft is private and revisable, activating it puts a
    # product in front of buyers. Collapsing them would make the ledger unable
    # to answer "when did this go on sale?", which is the question that matters.
    "etsy.listing", "etsy.publish",
})

# Credential access. NOT egress — none of it reaches a third party — which is
# why it is a separate set: `egress_scan` requires every EGRESS_KINDS use to be
# declared in a module manifest, and credential access happens in core, not in
# modules. Conflating them would demand manifest entries that cannot exist.
#
# It is attested for a different reason: on 2026-08-17 five copies of one token
# drifted across a host and nobody could say which process had written which
# store. Reconstructing it took an hour of file mtimes and inference, and the
# answer — an operator's own manual sync run — was a guess until confirmed.
# `credential.write` makes that a query.
#
# `read` is included deliberately, not just `write`. "Which processes actually
# consume this credential" is the question that decides whether a fix reaching
# four of five stores is complete, and today it is answered by grepping and
# hoping.
CREDENTIAL_KINDS = frozenset({
    "credential.read",     # a value was served to a consumer
    "credential.write",    # a store was updated
    "credential.refresh",  # a rotating credential was renewed — the single-use
                           # operation two processes must never race
    "credential.verify",   # liveness was checked with a real call
})

# Fund governance. NOT egress — a NAV mark, an approval, a halt, a limit change
# and a strategy going live all stay inside the building — which is why this is
# a third set rather than an addition to EGRESS_KINDS: `egress_scan` demands a
# module manifest entry for every egress kind, and these have no third party to
# declare.
#
# They exist because Meridian is run as an incorporated fund would be, and the
# questions an LP asks are not answerable from `trade.order` alone. What was it
# worth on the 14th. On what evidence was this approved, and what was waived.
# When did a control stop it trading. Who moved the position cap, and from what.
# Which vehicle produced this result.
#
# ATTRIBUTION IS MANDATORY, not decorative. Every fund.* event carries
# `extra={"vehicle": ...}` naming the pool whose money moved, and where the
# event belongs to one strategy rather than the whole pool, `extra["strategy"]`
# names it too. Without both, a multi-strategy fund cannot attribute a result,
# and attribution is the difference between a track record and a balance.
#
#     vehicle    "meridian" | "index-fund"        the capital pool
#     strategy   "hlbot-SOL" | "hlbot-ETH" | ...  the thing being run
#
# `fund.limit_change` earns its keep soonest. On 2026-08-22 the position cap,
# the tranche sizing curve and the equity floor were each edited inside a
# single session — every one the sort of change that later explains a result —
# and none left a record beyond a shell history.
FUND_KINDS = frozenset({
    "fund.nav",           # a NAV mark, with its account breakdown
    "fund.decision",      # an approval, a waiver, or a policy change
    "fund.halt",          # a risk control stopped trading, and why
    "fund.limit_change",  # a risk limit moved: what, from, to, by whom
    "fund.strategy",      # a vehicle or strategy was registered, started,
                          # paused or retired — the lifecycle a track record
                          # is measured against
})

# Everything `attest` accepts. Kept as a union so a caller cannot pass a kind
# from neither vocabulary without it being a plain typo.
ATTEST_KINDS = EGRESS_KINDS | CREDENTIAL_KINDS | FUND_KINDS


def _core_lib() -> Path | None:
    """Where the ledger implementation lives on THIS machine.

    Kept here so that exactly one piece of code searches for the core. Every
    caller that used to do this had its own idea, and one of them was wrong on
    one machine, which is the whole reason this module exists.
    """
    here = Path(__file__).resolve().parent.parent          # .../.datacore/lib
    runner = Path(os.environ.get(
        "DATACORE_RUNNER", str(Path.home() / ".datacore" / "v2-runner"))
    ) / ".datacore" / "lib"
    for cand in (here, runner):
        if (cand / "ledger" / "log.py").is_file():
            return cand
    return None


def attest(kind: str, *, ref: str = "", detail: str = "",
           space: str | None = None, extra: dict | None = None) -> str | None:
    """Record an external action. Returns the event hash, or None on failure.

    NEVER RAISES, and never blocks the action it records. An accounting gap is
    strictly better than turning a publishing outage out of a bookkeeping
    failure.
    """
    try:
        lib = _core_lib()
        if lib and str(lib) not in sys.path:
            sys.path.insert(0, str(lib))
        from ledger_attest import attest as _attest      # noqa: PLC0415
        return _attest(kind, ref=ref, detail=detail, space=space, extra=extra)
    except Exception:  # noqa: BLE001 — see docstring
        return None


def attests(kind: str, *,
            ref: Callable[..., str] | str = "",
            detail: Callable[..., str] | str = "",
            space: str | None = None) -> Callable:
    """Attest an egress function's result, after it returns.

    `ref` and `detail` may be constants or callables. A callable receives the
    RETURN VALUE as its first argument, so the attestation records the id the
    remote system actually assigned rather than anything guessed beforehand.

    AFTER, NEVER BEFORE. Attesting an intended action writes a record of
    something that may not have happened, and that record reads as
    authoritative. If the wrapped call raises, nothing is attested — because
    nothing went out.
    """
    def _record(result: Any) -> None:
        try:
            r = ref(result) if callable(ref) else ref
            d = detail(result) if callable(detail) else detail
        except Exception:  # noqa: BLE001 — a bad extractor must not eat the result
            r, d = "", ""
        attest(kind, ref=str(r or ""), detail=str(d or ""), space=space)

    def decorate(fn: Callable) -> Callable:
        # ASYNC SENDERS NEED THEIR OWN WRAPPER. Calling an `async def` returns a
        # coroutine immediately, so a sync wrapper would attest that object --
        # recording ref="" for a message that had not been sent yet, and
        # recording it even if the send later raised. Both halves of the
        # contract broken at once: attest after the fact, and never attest an
        # action that did not happen.
        #
        # Found wiring whatsapp_gateway.send_reply, which is `async def`. Two of
        # the four WhatsApp senders are.
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrapper(*args: Any, **kwargs: Any) -> Any:
                result = await fn(*args, **kwargs)
                _record(result)
                return result
            awrapper.__datacore_egress__ = kind
            return awrapper

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = fn(*args, **kwargs)
            _record(result)
            return result

        wrapper.__datacore_egress__ = kind   # what the conformance scan reads
        return wrapper

    return decorate
