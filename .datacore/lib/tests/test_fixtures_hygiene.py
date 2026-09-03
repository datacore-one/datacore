"""Fixtures are committed to a PUBLIC repository. These are the invariants.

On 2026-09-03 the harvested fixture for box-briefing was the daily journal
itself -- 20 KB carrying HRV and readiness readings, three named people, an
invoice, and market positions -- committed to a branch of a public repo. It had
not been pushed only because the push was blocked by an unrelated allowlist.
The PII pre-commit scan never saw it: the scan covered .md/.yaml/.json/.toml
and the fixture was .txt.

Two controls now hold, and each has a test that fails without it:

  size     a fixture body may not exceed MAX_BODY_BYTES. This is the real
           control -- you cannot leak a journal in 2 KB.
  content  no email, no home path, no login. Defence in depth behind size.

Plus the workflow trap: --harvest must not clobber a curated fixture.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
FIXTURES = ROOT / ".datacore" / "lib" / "tests" / "fixtures" / "jobs"


def _load():
    spec = importlib.util.spec_from_file_location(
        "fixtures", ROOT / ".datacore" / "lib" / "jobs" / "fixtures.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


F = _load()
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HOME_PATH = re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+")


def _bodies():
    for f in sorted(FIXTURES.glob("*.txt")):
        text = f.read_text()
        yield f.name, text.split("# ---\n", 1)[-1]


@pytest.mark.parametrize("name,body", list(_bodies()), ids=lambda v: v if isinstance(v, str) and v.endswith(".txt") else "")
def test_fixture_body_is_within_the_size_cap(name, body):
    assert len(body.encode()) <= F.MAX_BODY_BYTES, (
        f"{name} body is {len(body.encode())} bytes; cap is {F.MAX_BODY_BYTES}. "
        f"A fixture pins a FORMAT. Anything larger is production data.")


@pytest.mark.parametrize("name,body", list(_bodies()), ids=lambda v: v if isinstance(v, str) and v.endswith(".txt") else "")
def test_fixture_carries_no_identity(name, body):
    assert not EMAIL.search(body), f"{name} contains an email address"
    assert not HOME_PATH.search(body), f"{name} contains a home-directory path"
    if F._USER:
        assert not re.search(rf"(?<![A-Za-z0-9_]){re.escape(F._USER)}(?![A-Za-z0-9_])", body), (
            f"{name} contains the login name")


def test_minimal_body_keeps_only_matching_lines_and_caps():
    text = "noise line one\n" + "## Your Agenda\n" + ("x" * 5000) + "\nmore noise\n"
    body = F._minimal_body(text, r"^#{2,3}\s+Your Agenda", [])
    assert body == "## Your Agenda\n"
    big = F._minimal_body("A" * 10000, r"A+", [])
    assert len(big.encode()) <= F.MAX_BODY_BYTES


def test_size_cap_is_measured_in_bytes_not_characters():
    text = "é" * 3000 + "\n"
    body = F._minimal_body(text, r"é+", [])
    assert len(body.encode()) <= F.MAX_BODY_BYTES


def test_every_success_rule_names_a_live_regex_check():
    """A derive rule for a check that no longer exists is a fact with no
    binding; it prints `skip: no fixture` forever and nobody reads it."""
    live = {f"{job}.{idx}" for job, _m, idx, _p, _r in F.regex_checks()}
    dead = sorted(k for k in F.SUCCESS_RULES if k not in live)
    assert not dead, f"derive rules for checks that no longer exist: {dead}"


def test_minimal_body_keeps_derive_rule_lines_when_unhealthy():
    """So --derive-success still has the summary line to rewrite."""
    text = "detail\n5 cadence(s) overdue\n"
    body = F._minimal_body(text, r"^0 cadence\(s\) overdue$", [r"^\d+ cadence\(s\) overdue$"])
    assert body == "5 cadence(s) overdue\n"


def test_minimal_body_falls_back_to_a_short_tail_not_the_whole_file():
    text = "\n".join(f"line {i}" for i in range(500)) + "\n"
    body = F._minimal_body(text, r"NEVER MATCHES", [])
    assert body.count("\n") <= F.TAIL_LINES


def test_redact_strips_home_paths_emails_and_login(monkeypatch):
    monkeypatch.setattr(F, "_USER", "alice")
    # /Users/runner and /home/user are the pre-commit hook's own template
    # usernames; anything else trips its personal-path scan (correctly).
    out = F._redact("/Users/runner/x /home/user/y alice@example.com by alice; malice ok")
    assert "/Users/runner" not in out and "/home/user" not in out
    assert "alice@example.com" not in out
    assert "by <user>;" in out
    assert "malice" in out, "redaction must be whole-word, not substring"


def test_harvest_does_not_clobber_a_curated_fixture_without_force(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "FIXTURES", tmp_path)
    monkeypatch.setattr(F, "regex_checks", lambda: [("j", "mac", 0, "~/nope.log", r"^OK")])
    curated = tmp_path / "j.0.txt"
    curated.write_text("# fixture\n# represents: SUCCESS (derived)\n# ---\nOK derived\n")
    monkeypatch.setattr(F, "_read_artifact", lambda p, m: "LIVE UNHEALTHY OUTPUT\n")
    F.harvest(force=False)
    assert curated.read_text().endswith("OK derived\n"), "curation was overwritten"
    F.harvest(force=True)
    assert "OK derived" not in curated.read_text(), "--force must re-harvest"


def test_remote_read_quotes_the_path_but_leaves_the_tilde_bare(monkeypatch):
    """A manifest path with a shell metacharacter must be an argument, never
    code -- and the tilde must stay outside the quotes, or the remote shell
    looks for a directory literally named "~". Both failure modes were live:
    the first is an injection, the second reported nine artifacts unreadable
    on 2026-09-03 after the first was fixed with a bare shlex.quote."""
    captured = {}

    class R:
        returncode = 0
        stdout = b"x\n"

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return R()

    monkeypatch.setattr(F.subprocess, "run", fake_run)
    F._read_artifact("~/a b;rm -rf /.log", "box")
    remote = captured["cmd"][-1]
    assert "~/'a b;rm -rf /.log'" in remote, remote
    assert "'~/" not in remote, "tilde quoted: remote shell will not expand it"


def test_quote_remote_forms():
    assert F._quote_remote("~/Data/x y.log") == "~/'Data/x y.log'"
    assert F._quote_remote("~/Data/plain.log") == "~/Data/plain.log"
    assert F._quote_remote("/abs/x y.log") == "'/abs/x y.log'"
    assert F._quote_remote("~/x;rm -rf /") == "~/'x;rm -rf /'"


def test_every_fixture_has_a_live_regex_check():
    """A fixture nothing refreshes keeps whatever it last held, forever.
    Two 7.7 KB orphans survived a manifest reclassification this way."""
    live = {f"{job}.{idx}.txt" for job, _m, idx, _p, _r in F.regex_checks()}
    orphans = sorted(f.name for f in FIXTURES.glob("*.txt") if f.name not in live)
    assert not orphans, f"fixtures with no regex check in the manifest: {orphans}"


def test_prune_orphans_removes_only_orphans(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "FIXTURES", tmp_path)
    monkeypatch.setattr(F, "regex_checks", lambda: [("keep", "mac", 0, "p", "r")])
    (tmp_path / "keep.0.txt").write_text("x"); (tmp_path / "gone.0.txt").write_text("x")
    gone = F.prune_orphans()
    assert [g.name for g in gone] == ["gone.0.txt"]
    assert (tmp_path / "keep.0.txt").exists()
