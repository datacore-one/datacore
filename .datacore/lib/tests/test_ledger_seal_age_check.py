"""The hub's seal-age job contract (plan 2026-10-31, phase D part 2 (b)).

Every hub space's newest seal must be under one hour old, made by the space's
sequencer (the active grantor the owner's records name, which is the `grantor`
of its ledger-hub.json), and verify. Written before the code.
"""
from __future__ import annotations

import time

import pytest

from tests import test_ledger_hub as T
from ledger import hub as H


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "runtime"))
    e = T.Env(tmp_path, monkeypatch)
    e.clock.now = int(time.time() * 1000)
    yield e
    e.close()


def _check(env, now_ms=None, **kw):
    import ledger_seal_age_check as C
    return C.check_space(env.hub_dir, now_ms=now_ms or int(time.time() * 1000), registry_path=env.registry, **kw)


def test_a_fresh_verified_seal_by_the_grantor_passes(env):
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t1", "title": "one"})
    h.seal()
    r = _check(env)
    assert r["ok"] is True, r
    assert r["sealer"] == T.GRANTOR and r["age_s"] < 3600


def test_a_seal_older_than_an_hour_with_work_waiting_unsealed_fails(env):
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t1", "title": "one"})
    h.seal()
    own.append("item.create", {"id": "t2", "title": "admitted after the seal, never sealed"})
    r = _check(env, now_ms=int(time.time() * 1000) + 3601 * 1000)
    assert r["ok"] is False and "old" in r["detail"] and "unsealed" in r["detail"], r


def test_an_old_seal_that_covers_everything_passes_on_a_quiet_space(env):
    """The hub writes no seal when nothing changed (`unchanged`): a quiet
    space keeps its old seal, and that is not a stopped hub."""
    h = env.hub()
    env.writer("own", H.LocalTransport(h)).append("item.create", {"id": "t1", "title": "one"})
    h.seal()
    assert h.seal()["status"] == "unchanged"
    r = _check(env, now_ms=int(time.time() * 1000) + 5 * 3600 * 1000)
    assert r["ok"] is True and "nothing admitted since" in r["detail"], r


def test_no_seal_at_all_fails(env):
    env.hub()
    r = _check(env)
    assert r["ok"] is False and "no seal" in r["detail"], r


def test_a_seal_that_does_not_verify_fails(env, monkeypatch):
    h = env.hub()
    env.writer("own", H.LocalTransport(h)).append("item.create", {"id": "t1", "title": "one"})
    h.seal()
    import ledger.seal as S
    monkeypatch.setattr(S, "verify_seal", lambda events, kw=None: (False, "state root mismatch"))
    r = _check(env)
    assert r["ok"] is False and "verify" in r["detail"], r


def test_a_grantor_that_is_not_the_named_sequencer_fails(env):
    h = env.hub()
    env.writer("own", H.LocalTransport(h)).append("item.create", {"id": "t1", "title": "one"})
    h.seal()
    r = _check(env, expected="winston")
    assert r["ok"] is False and "winston" in r["detail"], r


def test_main_writes_ok_last_only_when_every_hub_space_passes(env, tmp_path, capsys):
    import ledger_seal_age_check as C
    h = env.hub()
    env.writer("own", H.LocalTransport(h)).append("item.create", {"id": "t1", "title": "one"})
    h.seal()
    root = env.hub_dir.parent
    target = root / "9-hubspace"
    env.hub_dir.rename(target)
    (root / "8-plain" / ".datacore" / "events").mkdir(parents=True)
    rc = C.main(["--root", str(root), "--registry", str(env.registry)])
    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 0, out
    assert out[-1].startswith("OK "), out
    assert any("9-hubspace" in ln for ln in out) and not any("8-plain" in ln for ln in out)


def test_main_with_no_hub_space_is_never_a_pass(tmp_path, capsys):
    import ledger_seal_age_check as C
    (tmp_path / "1-x" / ".datacore" / "events").mkdir(parents=True)
    rc = C.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out.strip().splitlines()
    assert rc != 0 and not out[-1].startswith("OK "), out
