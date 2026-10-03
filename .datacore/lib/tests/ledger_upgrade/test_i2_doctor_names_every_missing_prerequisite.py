"""I2 (deterministic): `ledger_cli.py doctor` names every missing prerequisite.

Ledger upgrade Phase 3, eval I2 (PLAN.md; audit C4, C9 item 1, C18). On a clean
machine (`_install_kit`), `ledger_cli.py doctor --space team` must name each
prerequisite of a profile A install that is missing, each with a fix (a
command or instruction on the same line), and exit non-zero:

  * the Python packages: `cryptography` and `PyYAML`, checked with a bare venv
    that has no packages at all -- and doctor itself must still run there and
    answer, not die with an ImportError traceback (the CLI-doctor false alarm
    of 2026-09-24 picked such a Python and called the ledger broken);
  * identity: no ~/.datacore/identity.env and no DATACORE_ACTOR;
  * principals: no principals.yaml declaring the writer;
  * the space itself: no ledger in it yet;
  * keys: when signing is switched on (DATACORE_LEDGER_SIGN=1), a writer with
    no key file. With signing off (owner decision 7, 2026-10-04: signing is
    Phase 6) a missing key is NOT a gap, and doctor must not invent one.

And the control: right after `init`, doctor on the same machine exits 0, so a
doctor that always fails does not pass this eval.

Seeded failure: no doctor (today: argparse "invalid choice"); a doctor that
imports the ledger (and so cryptography) before checking it, crashing in the
bare venv; a doctor that skips the principals check.
"""
from __future__ import annotations

import re

import pytest

import _install_kit as K


@pytest.fixture
def machine(tmp_path):
    return K.clean_machine(tmp_path)


def _gap(out: str, *words: str) -> str | None:
    """The first line naming one of `words` that also says how to fix it."""
    for line in out.splitlines():
        low = line.lower()
        if any(w in low for w in words) and re.search(r"\bfix\b", low):
            return line
    return None


def test_doctor_on_a_bare_machine_names_every_gap_with_a_fix(machine):
    m = machine
    p = m.cli("doctor --space team", python=m.bare_python())
    out = p.stdout + p.stderr
    assert "Traceback" not in out, f"doctor crashed on a machine without packages instead of naming them:\n{out[-800:]}"
    assert p.returncode != 0, f"doctor exited 0 on a machine with nothing installed:\n{out[-800:]}"
    missing = [name for name, words in {
        "the cryptography package": ("cryptography",),
        "the PyYAML package": ("pyyaml", "yaml"),
        "this machine's identity": ("identity",),
        "the principals registry": ("principal",),
        "the space's ledger": ("ledger", "init"),
    }.items() if not _gap(out, *words)]
    assert not missing, (f"doctor did not name, each with a fix: {', '.join(missing)}\n"
                         f"doctor said:\n{out[-1200:]}")


def test_with_packages_present_doctor_still_names_identity_principals_and_space(machine):
    m = machine
    p = m.cli("doctor --space team")
    out = p.stdout + p.stderr
    assert p.returncode != 0, f"doctor exited 0 with no identity, principals or ledger:\n{out[-800:]}"
    for name, words in {"identity": ("identity",), "principals": ("principal",),
                        "the space's ledger": ("ledger", "init")}.items():
        assert _gap(out, *words), f"doctor did not name {name} with a fix:\n{out[-1200:]}"
    assert not _gap(out, "cryptography"), f"doctor named cryptography missing where it is installed:\n{out}"


def test_signing_on_without_a_key_is_named_and_signing_off_is_not_a_gap(machine):
    m = machine
    init = m.cli("init --space team --actor alice")
    assert init.returncode == 0, f"init failed: {init.stderr[-600:]}"

    healthy = m.cli("doctor --space team")
    assert healthy.returncode == 0, (
        f"doctor fails right after init (signing off, nothing missing):\n{healthy.stdout + healthy.stderr}")
    assert not _gap(healthy.stdout + healthy.stderr, "key"), \
        f"doctor invented a missing key while signing is off:\n{healthy.stdout + healthy.stderr}"

    signing = dict(m.env, DATACORE_LEDGER_SIGN="1")
    p = m.cli("doctor --space team", env=signing)
    out = p.stdout + p.stderr
    assert p.returncode != 0, f"doctor exited 0 with signing on and no key for alice:\n{out}"
    line = _gap(out, "key")
    assert line and "alice" in line, f"doctor did not name alice's missing key with a fix:\n{out}"
    assert not list((m.home / ".datacore" / "keys").glob("*.key")), "doctor generated a key; it must only report"
