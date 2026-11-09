"""A doer releasing the owner's request is told once, with the doer's reason.

Found 2026-11-09 by replaying the real events of the incident-note request
(incident-note-agent-server-disk-full-20261109-9270) through report_requests:
after Miles's first release the owner was told "Not picked up 1 h after you
asked" -- wrong, it had been picked up -- and the second and third releases
said nothing. The release is now an event the request-releases subscriber
reports, and a request that has been claimed is never "not picked up".
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import delegation_watch as dw  # noqa: E402

T0 = 1794245207217


def item(status="created", asked_by="gregor", claimed=True, title="Incident note: disk outage"):
    hist = [f"{T0}.0000.winston winston item.create: applied"]
    if claimed:
        hist += [f"{T0 + 60_000}.0000.miles miles item.claim: applied",
                 f"{T0 + 120_000}.0000.miles miles item.release: applied"]
    p = {"title": title, "assignee": "miles"}
    if asked_by:
        p["asked_by"] = asked_by
    return SimpleNamespace(id="req-1", status=status, title=title, payload=p, history=hist,
                           owner=None, closed_reason=None)


def release(**extra):
    return {"type": "item.release", "alias": "agent.run.failed", "space": "2-datacore",
            "actor": "miles", "seq": 9, "payload": {"id": "req-1", "owner": "miles",
                                                    "error": "check failed: test -s note.md", **extra}}


def state_of(it):
    return SimpleNamespace(items={it.id: it})


def test_a_release_of_the_owners_request_is_told_once_with_the_doers_reason(tmp_path):
    sent = []
    env = release(doer_said="No record of the outage, so writing it would mean inventing it.")
    for _ in range(3):                       # three releases of the same request
        dw.on_release(env, state_for=lambda space: state_of(item()), send=lambda t: sent.append(t) or True,
                      state_path=tmp_path / "told.json")
    assert len(sent) == 1
    msg = sent[0]
    assert "Miles" in msg and "released" in msg and "Incident note" in msg
    assert "inventing it" in msg and "req-1" in msg


def test_without_the_doers_words_the_check_error_is_the_reason(tmp_path):
    sent = []
    dw.on_release(release(), state_for=lambda s: state_of(item()), send=lambda t: sent.append(t) or True,
                  state_path=tmp_path / "told.json")
    assert "check failed" in sent[0]


def test_a_release_of_an_item_nobody_asked_for_is_not_told(tmp_path):
    sent = []
    dw.on_release(release(), state_for=lambda s: state_of(item(asked_by=None)),
                  send=lambda t: sent.append(t) or True, state_path=tmp_path / "told.json")
    assert sent == []


def test_a_failed_send_raises_so_the_runner_retries(tmp_path):
    with pytest.raises(RuntimeError):
        dw.on_release(release(), state_for=lambda s: state_of(item()), send=lambda t: False,
                      state_path=tmp_path / "told.json")
    sent = []
    dw.on_release(release(), state_for=lambda s: state_of(item()), send=lambda t: sent.append(t) or True,
                  state_path=tmp_path / "told.json")
    assert len(sent) == 1


def test_a_request_that_was_claimed_is_never_not_picked_up():
    stages = [s for s, _ in dw._stages_due(item(claimed=True), now_ms=T0 + 3_600_000)]
    assert "late_pickup" not in stages


def test_a_request_never_claimed_is_still_not_picked_up():
    stages = [s for s, _ in dw._stages_due(item(claimed=False), now_ms=T0 + 3_600_000)]
    assert "late_pickup" in stages
