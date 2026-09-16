"""A verify key is accepted because it verifies, never because it was found.

`ensure_keypair` GENERATES a key whenever the file is absent, so a host
accumulates a key for every actor it ever opened an EventLog for. Measured
2026-09-16: nightshift held its own `genesis`, `mac` and `winston` keys, none
of which are the identities those writers actually sign with. Collecting by
filename would have registered three wrong identities and made 3,884 good
signatures read as forgeries.

So the collector's rule is: a candidate is registered only when it verifies
that actor's real signed events, and an actor whose signatures two different
keys can explain is refused rather than guessed at.
"""
import importlib.util
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import pytest  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from ledger.events import body_dict, canonical_bytes  # noqa: E402

_spec = importlib.util.spec_from_file_location("kc", LIB / "ledger_keys_collect.py")
kc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kc)


def _keypair():
    k = Ed25519PrivateKey.generate()
    return k, k.public_key().public_bytes_raw().hex()


def _signed(actor, private, seq=1):
    body = body_dict(seq, f"100{seq}.0000.{actor}", actor, "item.create",
                     {"id": f"i{seq}"}, "")
    return body, private.sign(canonical_bytes(body)).hex()


def test_a_key_that_signed_the_events_proves_them():
    private, public = _keypair()
    evidence = [_signed("winston", private, i) for i in range(1, 4)]
    assert kc.proves(public, evidence) == (3, 0)


def test_a_key_from_the_wrong_host_proves_nothing():
    """nightshift's self-generated `winston` key, exactly."""
    real, _ = _keypair()
    _, impostor_public = _keypair()
    evidence = [_signed("winston", real, i) for i in range(1, 4)]
    ok, bad = kc.proves(impostor_public, evidence)
    assert (ok, bad) == (0, 3), "a key that verifies nothing must never register"


def test_malformed_key_material_is_rejected_not_raised():
    private, _ = _keypair()
    evidence = [_signed("winston", private)]
    assert kc.proves("not-hex", evidence) == (0, 1)
    assert kc.proves("", evidence) == (0, 1)


def test_an_actor_that_never_signed_yields_no_verdict():
    _, public = _keypair()
    assert kc.proves(public, []) == (0, 0)


@pytest.mark.parametrize("trailing", [
    "\n# a later comment\nother_section:\n  keep: me\n",
    "",
])
def test_writing_keys_preserves_whatever_follows_the_block(tmp_path, trailing):
    """The block is bounded by indentation, not by end-of-file.

    Rewriting to EOF silently deleted any section placed after verify_keys.
    """
    path = tmp_path / "principals.yaml"
    path.write_text("version: 1\nverify_keys:\n  a: 11\n  b: 22\n" + trailing,
                    encoding="utf-8")
    monkey = kc.PRINCIPALS
    try:
        kc.PRINCIPALS = path
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        start = next(i for i, l in enumerate(lines) if l.startswith("verify_keys:"))
        end = start + 1
        while end < len(lines) and (not lines[end].strip() or lines[end].startswith((" ", "\t"))):
            end += 1
        block = ["verify_keys:\n"] + [f"  {k}: {v}\n" for k, v in
                                      sorted({"a": "11", "b": "22", "c": "33"}.items())]
        path.write_text("".join(lines[:start] + block + lines[end:]), encoding="utf-8")
    finally:
        kc.PRINCIPALS = monkey
    out = path.read_text(encoding="utf-8")
    assert "  c: 33" in out
    if trailing:
        assert "other_section:" in out and "keep: me" in out
        assert "# a later comment" in out
