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

__all__ = ["attest", "attests", "EGRESS_KINDS"]

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
