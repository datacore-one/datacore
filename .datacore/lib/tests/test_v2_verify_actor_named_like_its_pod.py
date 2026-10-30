"""An actor that shares its machine's hostname is fine when the actor is a declared writer.

The DIP-0044 trap the two identity checks look for is a FALLBACK: the registry
lookup failed and the hostname became the actor, so events land under a writer
nobody declared. In the pods the hostname is the agent's own name -- Winston's
pod is `winston`, and `winston` is the writer his principal declares -- so both
checks failed on a correct identity (Winston's self-test, 2026-10-30, two of
its ten failures). Equal names are only a fault when the name is not a
declared writer.
"""
import socket
import sys
import types
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import v2_verify  # noqa: E402


class _Report:
    def __init__(self):
        self.rows = []

    def add(self, dip, name, ok, detail="", skipped=False):
        self.rows.append((name, ok, detail))


def _fake_attest(monkeypatch, actor, declared):
    mod = types.ModuleType("ledger_attest")
    mod._actor = lambda: actor
    mod._identity = lambda: {"DATACORE_ACTOR": actor} if declared else {}
    mod.attest = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "ledger_attest", mod)


def _fake_principals(monkeypatch, writers):
    import actor_identity
    monkeypatch.setattr(actor_identity, "principal_of",
                        lambda a, path=None: ("p", {}) if a in writers else (None, {}))


def _identity_ok(monkeypatch, actor, host, writers, declared=True):
    _fake_attest(monkeypatch, actor, declared)
    _fake_principals(monkeypatch, writers)
    monkeypatch.setattr(socket, "gethostname", lambda: host)
    rep = _Report()
    v2_verify.check_declared_identity(rep)
    return {n: ok for n, ok, _ in rep.rows}["identity declared"]


def test_a_declared_writer_named_like_its_pod_passes_both_checks(monkeypatch):
    _fake_principals(monkeypatch, {"winston"})
    assert v2_verify.hostname_fallback("winston", "winston") is False  # the attest check's question
    assert _identity_ok(monkeypatch, "winston", "winston", {"winston"}) is True


def test_an_undeclared_hostname_actor_still_fails_both_checks(monkeypatch):
    _fake_principals(monkeypatch, {"winston"})
    assert v2_verify.hostname_fallback("somebox", "somebox") is True
    assert v2_verify.hostname_fallback("mac", "mac") is False
    assert v2_verify.hostname_fallback("miles", "dayshift") is False
    assert _identity_ok(monkeypatch, "somebox", "somebox", {"winston"}) is False
