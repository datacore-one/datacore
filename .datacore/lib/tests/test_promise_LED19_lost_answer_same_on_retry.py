"""LED-19: A request whose answer was lost gets the same answer when retried,
however late.

Kind: deterministic (temporary hub spaces; never the live installation).

Seeded failure: the real round-7 audit finding R9, on the round-7 audited
head (0758f855): a writer whose lease answer was lost on the way back threw
the answered request away once it was five minutes old and asked afresh, so
it never learned it held the task (the hub answered "held") and kept no
receipt of the grant it had been given. (No Lean theorem covers this promise
yet.)

Promise, as evals: a lease request reaches the hub and its answer is lost;
the writer retries after the freshness window and receives the hub's
original signed grant (the same attempt), keeping a receipt of it; an hour
later, when that grant has run out, the hub still answers with the same
grant and the writer refuses to start on it. A request the hub never
answered and that has gone stale is replaced by one fresh request, once, and
granted.
"""
from __future__ import annotations

import json

import pytest

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import env  # noqa: F401
from ledger import hub as H


class _LosesFirstAnswer(H.LocalTransport):
    """The request reaches the hub; its answer is lost on the way back, once."""

    def __init__(self, hub):
        super().__init__(hub)
        self.answers = []

    def request_lease(self, req):
        answer = super().request_lease(req)
        self.answers.append(answer)
        if len(self.answers) == 1:
            raise H.HubUnavailable("the answer was lost")
        return answer


@pytest.mark.parametrize("late_s", [H.REQUEST_WINDOW_MS / 1000 + 1, 600])
def test_a_lost_answer_is_the_same_answer_on_retry_however_late(env, late_s):
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t"})
    lossy = _LosesFirstAnswer(h)
    wa = env.writer("wa", lossy)
    with pytest.raises(H.HubUnavailable):
        wa.request_lease("t")
    env.clock.advance(late_s)
    answer = wa.request_lease("t")
    assert answer["status"] == "granted", f"retried {late_s:.0f} s later, the writer got {answer.get('status')!r}"
    first = lossy.answers[0]
    assert first.get("status") == "granted", first
    assert answer["lease"]["payload"]["attempt"] == first["lease"]["payload"]["attempt"], \
        "the retry got a different grant from the one the hub gave first"
    assert wa.receipts(), "the writer kept no receipt of the grant it was given"


def test_an_unanswered_stale_request_is_replaced_once(env):
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t"})
    wa = env.writer("wa", H.LocalTransport(h))
    req = H.sign_request("wa", "lease.request", {"hub": wa.cfg.hub_id, "space": wa.cfg.space_id, "item": "t"},
                         keys_dir=env.keys_dir, clock=env.clock)
    wa._pending_path("lease.request", "t").write_text(json.dumps(req, sort_keys=True))
    env.clock.advance(H.REQUEST_WINDOW_MS / 1000 + 1)
    assert wa.request_lease("t")["status"] == "granted", "a stale unanswered request was never replaced"


def test_a_retry_after_the_grant_ran_out_gets_the_same_answer_and_does_not_start(env):
    """An hour later the original grant has expired: the hub still answers with
    that same grant (never a new one to someone's surprise), and the writer
    refuses to start work on an expired grant."""
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t"})
    lossy = _LosesFirstAnswer(h)
    wa = env.writer("wa", lossy)
    with pytest.raises(H.HubUnavailable):
        wa.request_lease("t")
    env.clock.advance(3600)
    try:
        answer = wa.request_lease("t")
    except H.HubRefused as exc:
        assert "expired" in str(exc), f"refused for another reason: {exc}"
        answer = None
    assert len(lossy.answers) == 2, "the retry never reached the hub"
    first, second = lossy.answers
    assert second.get("status") == first.get("status") == "granted", (first.get("status"), second.get("status"))
    assert second["lease"]["payload"]["attempt"] == first["lease"]["payload"]["attempt"], \
        "an hour later the hub answered with a different grant"
    assert answer is None, "the writer started work on a grant that had already expired"
