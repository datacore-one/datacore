"""MOD-5: Every module declares what it sends outside Datacore, and an undeclared outward
action is blocked.

Kind: deterministic. (1) The real egress scanner (egress_scan.scan_module, DIP-0047)
over every installed module: each outbound write it can see must be declared or
exempted in that module's module.yaml. (2) The gate that blocks (egress_scan.py
--enforce, which the pre-commit hook runs) against tmp modules with a new,
undeclared outward write.

Scope note, from the scanner's own docstring: detection is syntactic (HTTP verbs,
urlopen, sendmail); SDK and subprocess egress are invisible, so (1) is a lower bound.

Seeded failure: a module that has never declared anything grows an outbound POST
and nothing stops it -- the scanner reports modules "not yet declaring" but does
not fail them, and the pre-commit hook only runs it for modules whose module.yaml
already has an `egress:` block (gigs/lib/ra_client.py:_post today).
"""
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
sys.path.insert(0, str(LIB))

import egress_scan as E  # noqa: E402

SENDER = "import requests\n\ndef shout(msg):\n    return requests.post('https://example.test/hook', json={'m': msg})\n"


def test_every_installed_module_declares_its_outbound_writes():
    missing = []
    for mod in sorted(p for p in (ROOT / ".datacore" / "modules").iterdir() if p.is_dir()):
        r = E.scan_module(mod)
        if "error" in r:
            missing.append(f"{mod.name}: module.yaml unreadable ({r['error'][:60]})")
            continue
        for site in r["writes"]:
            if site not in r["declared"] and not E._exempted(site, r["exempt"]):
                missing.append(f"{mod.name}/{site}")
    assert not missing, f"outbound writes no module.yaml declares: {missing}"


def _enforce(modules_dir):
    return subprocess.run([sys.executable, str(LIB / "egress_scan.py"), "--enforce", "--modules", str(modules_dir)],
                          capture_output=True, text=True, timeout=60, cwd=str(LIB))


def _module(root, name, manifest):
    d = root / name / "lib"
    d.mkdir(parents=True)
    (root / name / "module.yaml").write_text(manifest)
    (d / "sender.py").write_text(SENDER)


def test_an_undeclared_write_in_a_declaring_module_is_blocked(tmp_path):
    _module(tmp_path, "declaring", "name: declaring\negress:\n  - fn: lib/other.py:send\n    kind: post\n")
    r = _enforce(tmp_path)
    assert r.returncode != 0, f"an undeclared POST in an opted-in module passed the gate:\n{r.stdout[-800:]}"


def test_an_undeclared_write_in_a_module_that_never_declared_is_blocked(tmp_path):
    _module(tmp_path, "newcomer", "name: newcomer\nversion: 0.1.0\n")
    r = _enforce(tmp_path)
    assert r.returncode != 0, (
        "a module that has never declared its egress sends a POST and the gate lets it through "
        f"(reported as 'not yet declaring' only):\n{r.stdout[-800:]}")
