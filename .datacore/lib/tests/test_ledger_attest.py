"""Agents must record what they do in the outside world (DIP-0038/0046).

The ledger metered spend to the cent while an irreversible, publicly visible
action by an autonomous agent — posting to X — left no trace at all. A task can
be re-derived from org; a tweet cannot be un-sent.
"""
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from ledger.log import read_events  # noqa: E402


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "1-datafund" / ".datacore" / "events").mkdir(parents=True)
    monkeypatch.setenv("DATACORE_ROOT", str(tmp_path))
    monkeypatch.setenv("DATACORE_ACTOR", "data")
    import importlib, ledger_attest
    importlib.reload(ledger_attest)
    return tmp_path, ledger_attest


def test_an_external_action_lands_in_the_ledger(root):
    tmp, mod = root
    h = mod.attest("x.post", ref="123", detail="hello world")
    assert h
    evs = read_events(tmp / "1-datafund")
    assert len(evs) == 1
    e = evs[0]
    assert e.type == "artifact.attest" and e.actor == "data"
    assert e.payload["kind"] == "x.post" and e.payload["ref"] == "123"


def test_it_never_fails_the_caller(root):
    """A tweet that went out but could not be recorded is still a tweet that
    went out. Turning an accounting gap into a publishing outage is worse."""
    tmp, mod = root
    import importlib, os
    os.environ["DATACORE_ROOT"] = "/nonexistent/path/xyz"
    importlib.reload(mod)
    assert mod.attest("x.post", ref="9") is None      # degraded, not raised


def test_detail_is_truncated(root):
    tmp, mod = root
    mod.attest("x.post", ref="1", detail="x" * 900)
    e = read_events(tmp / "1-datafund")[0]
    assert len(e.payload["detail"]) <= 280


def test_actor_is_not_guessed_from_hostname(root, monkeypatch):
    """DIP-0044: winston's hostname is `bridge`, hermes runs `tris`."""
    tmp, mod = root
    monkeypatch.setenv("DATACORE_ACTOR", "winston")
    import importlib
    importlib.reload(mod)
    mod.attest("x.post", ref="2")
    assert read_events(tmp / "1-datafund")[-1].actor == "winston"


# --- identity declaration (DIP-0044/0047) ------------------------------------
#
# These encode the rules the identity mechanism was ADDED to enforce. A code
# review verified each of them failing against the first version of that
# mechanism: a declaration that outranked an explicit DATACORE_ROOT, an
# `export ` prefix that silently disabled the declaration, and a declared space
# that fell through to guessing when it did not resolve. Each failure mode
# returned no error and produced a plausible-looking result, which is precisely
# why they need tests rather than review.

@pytest.fixture
def declared(tmp_path, monkeypatch):
    """A machine with an identity.env, isolated from the real ~/.datacore."""
    home = tmp_path / "home"
    (home / ".datacore").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    space = tmp_path / "spaces" / "5-plur"
    (space / ".datacore" / "events").mkdir(parents=True)

    def write(text):
        (home / ".datacore" / "identity.env").write_text(text)
        import importlib, ledger_attest
        importlib.reload(ledger_attest)
        return ledger_attest

    return write, space


def test_explicit_root_outranks_a_declared_space(declared, monkeypatch):
    """A bogus DATACORE_ROOT must not be overridden by identity.env.

    The declaration is machine config; DATACORE_ROOT is what this process was
    told. Letting the file win means a deliberately-scoped run writes into a
    space it was pointed away from -- and makes a nonexistent root silently
    succeed against a real ledger.
    """
    write, space = declared
    monkeypatch.setenv("DATACORE_ROOT", "/nonexistent/path/xyz")
    monkeypatch.delenv("DATACORE_ACTOR", raising=False)
    mod = write(f"DATACORE_ACTOR=data\nDATACORE_ATTEST_SPACE={space}\n")
    assert mod.attest("x.post", ref="r") is None


def test_export_prefix_is_understood(declared, monkeypatch):
    """`export FOO=bar` is how people write env files, and it must work.

    Parsed naively the key becomes 'export DATACORE_ACTOR', the declaration is
    ignored, and the actor falls back to hostname inference -- filing data's
    events under `holodeck`, the exact misattribution this file prevents.
    """
    write, space = declared
    monkeypatch.delenv("DATACORE_ACTOR", raising=False)
    mod = write(f"export DATACORE_ACTOR=data\nexport DATACORE_ATTEST_SPACE={space}\n")
    assert mod._actor() == "data"


def test_undecodable_identity_file_never_raises(declared, monkeypatch):
    """A stray non-UTF-8 byte must not take attestation down machine-wide."""
    write, space = declared
    monkeypatch.delenv("DATACORE_ACTOR", raising=False)
    write("DATACORE_ACTOR=data\n")
    (Path.home() / ".datacore" / "identity.env").write_bytes(b"DATACORE_ACTOR=d\xffata\n")
    import importlib, ledger_attest
    importlib.reload(ledger_attest)
    ledger_attest._actor()          # must not raise


def test_a_declaration_that_does_not_resolve_returns_none(declared, monkeypatch):
    """Stated-and-wrong config must not fall through to guessing.

    Silently reverting to the glob heuristic leaves the operator believing
    identity is declared while events land somewhere else entirely.
    """
    write, _ = declared
    monkeypatch.delenv("DATACORE_ROOT", raising=False)
    monkeypatch.delenv("DATACORE_ACTOR", raising=False)
    mod = write("DATACORE_ACTOR=data\nDATACORE_ATTEST_SPACE=5-plur\n")
    assert mod._space(None) is None


def test_an_absolute_space_argument_still_resolves(declared, monkeypatch):
    """Callers pass absolute space paths; the roots loop must not break them."""
    write, space = declared
    monkeypatch.setenv("DATACORE_ROOT", "/nonexistent/path/xyz")
    mod = write("DATACORE_ACTOR=data\n")
    assert mod._space(str(space)) == space


def test_ledger_path_is_appended_not_prepended(declared):
    """Resolving the ledger must not shadow the caller's other imports.

    Inserting the runner tree at sys.path[0] repoints every later
    `import config_plane` / `import org_workspace_adapter` in a long-lived
    agent at whatever copy that tree happens to hold.
    """
    write, _ = declared
    mod = write("DATACORE_ACTOR=data\n")
    before = list(sys.path)
    mod._ensure_ledger_importable()
    added = [p for p in sys.path if p not in before]
    for p in added:
        assert sys.path.index(p) > 0
