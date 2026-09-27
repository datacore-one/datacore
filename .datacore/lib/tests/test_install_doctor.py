"""install_doctor: the placeholder identity and unlabelled spaces (INS-4).

Unit level, on a tmp root; the promise eval runs the whole CLI on a fresh
install built by the guide.
"""
from __future__ import annotations

import pytest

import actor_identity
import install_doctor as D


@pytest.fixture
def identity(tmp_path, monkeypatch):
    monkeypatch.delenv("DATACORE_ACTOR", raising=False)
    f = tmp_path / "identity.env"
    monkeypatch.setattr(actor_identity, "IDENTITY_FILE", f)

    def declare(name: str):
        f.write_text(f"DATACORE_ACTOR={name}\n")
    return declare


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "Data"
    r.mkdir()
    return r


def _personal(root, *, events=True, marker=None):
    sp = root / "0-personal"
    (sp / "org").mkdir(parents=True)
    if events:
        (sp / ".datacore" / "events").mkdir(parents=True)
    if marker is not None:
        (sp / ".datacore").mkdir(exist_ok=True)
        (sp / ".datacore" / "config.yaml").write_text(marker)
    return sp


@pytest.mark.parametrize("name", ["your-name", "<your-name>", "{{AUTHOR_ID}}", "YOUR_NAME"])
def test_the_guides_placeholder_is_not_an_identity(root, identity, name):
    identity(name)
    it = D.check_identity(root)
    assert it["ok"] is False
    assert "placeholder" in it["detail"] and name.lower() in it["detail"]
    assert "identity.env" in it["fix"]


def test_a_real_name_is_an_identity(root, identity):
    identity("alice")
    assert D.check_identity(root)["ok"] is True


def test_an_unlabelled_space_is_seen_by_the_ledger_check(root):
    _personal(root)
    it = D.check_ledger(root)
    assert it["ok"] is True, it
    assert "no space" not in it["detail"]


def test_an_unlabelled_space_without_a_log_is_named(root):
    _personal(root, events=False)
    it = D.check_ledger(root)
    assert it["ok"] is False
    assert "0-personal" in it["detail"] and "no space" not in it["detail"]


def test_an_unlabelled_space_is_a_gap_with_its_fix(root):
    _personal(root)
    it = D.check_space_labels(root)
    assert it["ok"] is False
    assert "0-personal" in it["detail"] and "unlabelled" in it["detail"]
    assert "0-personal/.datacore/config.yaml" in it["fix"] and "type: personal" in it["fix"]


def test_a_marker_without_a_type_is_unlabelled(root):
    _personal(root, marker="space:\n  name: personal\n")
    it = D.check_space_labels(root)
    assert it["ok"] is False and "0-personal" in it["detail"]


def test_labelled_spaces_pass(root):
    _personal(root, marker="space:\n  name: personal\n  type: personal\n")
    team = root / "1-acme"
    (team / ".datacore").mkdir(parents=True)
    (team / ".datacore" / "config.yaml").write_text("space:\n  name: acme\n  type: team\n")
    assert D.check_space_labels(root)["ok"] is True


def test_a_declared_other_type_keeps_its_own_ledger_rule(root):
    _personal(root, marker="space:\n  name: personal\n  type: personal\n")
    client = root / "1-client"
    (client / ".datacore").mkdir(parents=True)
    (client / ".datacore" / "config.yaml").write_text("space:\n  name: client\n  type: client\n")
    assert [p.name for p in D._spaces(root)] == ["0-personal"]


def test_every_gap_carries_a_fix(root, identity):
    identity("your-name")
    _personal(root, events=False)
    items = D.run(root)
    bad = [it for it in items if it["ok"] is not True]
    assert {"identity", "space labels", "ledger"} <= {it["name"] for it in bad}
    assert all(it.get("fix") and it["fix"] != "see INSTALL.md" for it in bad), bad
