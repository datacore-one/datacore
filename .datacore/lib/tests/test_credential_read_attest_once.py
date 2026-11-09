"""A credential read is attested once per credential, consumer and value per day (2026-10-24).

WHY. Every `creds get` wrote a `credential.read` attestation. Short-lived
trading scripts on nightshift each fetch the Red Alert pair, so Miles's
telemetry in 1-datafund grew by about 12,000 identical lines a day, reached
97 MB and passed the 50 MB sharing limit: the log stopped reaching any other
machine on 2026-10-16. The audit question -- who read which credential, and
which value -- is answered by the first read of the day; a new consumer, a
changed value or a new day is attested again.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import credential_access as ca  # noqa: E402
from test_credential_access import store  # noqa: E402,F401  (fixture)


def _capture(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(ca, "attest", lambda kind, **kw: seen.append((kind, kw)))
    monkeypatch.setattr(ca, "READ_ATTEST_STATE", tmp_path / "state" / "credential-read-attested.json")
    return seen


def test_repeated_reads_by_one_consumer_are_attested_once_a_day(store, monkeypatch, tmp_path):
    seen = _capture(monkeypatch, tmp_path)
    for _ in range(5):
        assert ca.get_value("SOLO_TOKEN", consumer="red_alert") == "solo-value"
    assert len(seen) == 1 and seen[0][0] == "credential.read"


def test_a_new_consumer_a_new_value_or_a_new_day_is_attested_again(store, monkeypatch, tmp_path):
    seen = _capture(monkeypatch, tmp_path)
    ca.get_value("SOLO_TOKEN", consumer="red_alert")
    ca.get_value("SOLO_TOKEN", consumer="kill_switch")
    assert len(seen) == 2
    env = store / ".datacore" / "env" / ".env"
    env.write_text(env.read_text().replace("SOLO_TOKEN=solo-value", "SOLO_TOKEN=rotated-value"))
    ca.get_value("SOLO_TOKEN", consumer="red_alert")
    assert len(seen) == 3
    monkeypatch.setattr(ca, "_today", lambda: (dt.date.today() + dt.timedelta(days=1)).isoformat())
    ca.get_value("SOLO_TOKEN", consumer="red_alert")
    assert len(seen) == 4


def test_an_unreadable_state_file_still_attests(store, monkeypatch, tmp_path):
    seen = _capture(monkeypatch, tmp_path)
    state = tmp_path / "state" / "credential-read-attested.json"
    state.parent.mkdir(parents=True)
    state.write_text("{not json")
    ca.get_value("SOLO_TOKEN", consumer="red_alert")
    assert len(seen) == 1
