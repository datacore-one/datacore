"""Writers are bound to author-email HASHES, never to addresses.

datacore-one/datacore is public. On 2026-09-06 principals.yaml listed the
principals' email addresses plainly and the repository's own PII scan
(validate-boundaries.yml) went red on main for every PR. The registry now
carries sha256 prefixes; these tests keep an address from coming back and
keep the authorship check matching a commit author through the hash.
"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".datacore" / "lib"))
from actor_identity import allowed_emails, email_hash  # noqa: E402

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def test_hash_is_stable_case_insensitive_and_short():
    h = email_hash("Someone@Example.com ")
    assert h == email_hash("someone@example.com")
    assert re.fullmatch(r"[0-9a-f]{16}", h)
    assert h != email_hash("someone.else@example.com")


def test_registry_binds_by_hash_and_accepts_a_plain_overlay():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "principals.yaml"
        p.write_text(
            "principals:\n"
            "  alpha:\n"
            "    kind: agent\n"
            "    writes_as: [alpha, alpha-host]\n"
            f"    email_sha256: [{email_hash('alpha@example.com')}]\n"
            "  beta:\n"
            "    kind: human\n"
            "    writes_as: [beta]\n"
            "    emails: [Beta@Example.com]\n"
            "  gamma:\n"
            "    kind: agent\n"
            "    writes_as: [gamma]\n"
        )
        assert allowed_emails("alpha-host", p) == {email_hash("alpha@example.com")}
        assert allowed_emails("beta", p) == {email_hash("beta@example.com")}
        assert allowed_emails("gamma", p) == set()          # unbound, not wrong


def test_committed_registry_contains_no_address():
    text = (ROOT / ".datacore" / "registry" / "principals.yaml").read_text()
    live = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert not EMAIL.search(live), "an email address is back in the public registry"
    assert "email_sha256:" in live


def test_every_writer_in_the_registry_is_bound_or_declared_unbound():
    import yaml
    ps = yaml.safe_load((ROOT / ".datacore" / "registry" / "principals.yaml").read_text())["principals"]
    for name, p in ps.items():
        if p.get("writes_as"):
            assert allowed_emails(name) or name == "practice", f"{name}: writers with no author bound"
