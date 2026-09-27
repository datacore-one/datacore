"""Space roles: which space an install gives a duty to, read from install.yaml.

Shipped code names no space of ours (INS-3). It asks `space_for("system")` or
`space_for("product")`, and the answer is the gitignored install.yaml's
`roles:` key. The tracked install.yaml.example ships neutral roles.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import spaces  # noqa: E402

DATA = LIB.parents[1]


def _root(tmp_path: Path, roles: dict | None) -> Path:
    doc = {"meta": {"name": "t"}}
    if roles is not None:
        doc["roles"] = roles
        # a role resolves to a space this install really has, so make them
        for v in roles.values():
            for x in v if isinstance(v, list) else [v]:
                if isinstance(x, str) and "/" not in x and ".." not in x:
                    (tmp_path / x / ".datacore").mkdir(parents=True, exist_ok=True)
    (tmp_path / "install.yaml").write_text(yaml.safe_dump(doc))
    return tmp_path


def test_single_role_is_read_from_install_yaml(tmp_path):
    root = _root(tmp_path, {"system": "1-sys", "product": "3-prod"})
    assert spaces.space_for("system", root=root) == "1-sys"
    assert spaces.space_for("product", root=root) == "3-prod"


def test_unset_role_is_none_or_the_callers_default(tmp_path):
    root = _root(tmp_path, {"system": "1-sys"})
    assert spaces.space_for("product", root=root) is None
    assert spaces.space_for("product", root=root, default="0-personal") == "0-personal"
    assert spaces.space_for_all("held", root=root) == []


def test_no_install_yaml_means_no_roles(tmp_path):
    assert spaces.space_for("system", root=tmp_path) is None
    assert spaces.space_for_all("attest", root=tmp_path) == []


def test_list_role_keeps_order(tmp_path):
    root = _root(tmp_path, {"attest": ["2-b", "1-a", "0-personal"], "held": "6-x"})
    assert spaces.space_for_all("attest", root=root) == ["2-b", "1-a", "0-personal"]
    assert spaces.space_for("attest", root=root) == "2-b"
    assert spaces.space_for_all("held", root=root) == ["6-x"]


def test_a_role_cannot_point_outside_the_install(tmp_path):
    root = _root(tmp_path, {"system": "/etc", "product": "../elsewhere", "ok": "1-a/nested"})
    assert spaces.space_for("system", root=root) is None
    assert spaces.space_for("product", root=root) is None
    # a role names one space, never a path inside one
    assert spaces.space_for("ok", root=root) is None


def test_malformed_install_yaml_is_no_roles(tmp_path):
    (tmp_path / "install.yaml").write_text("roles: [not, a, mapping]\n")
    assert spaces.space_for("system", root=tmp_path) is None
    (tmp_path / "install.yaml").write_text(":\n  - {unbalanced")
    assert spaces.space_for("system", root=tmp_path) is None


def test_root_defaults_to_datacore_root(tmp_path, monkeypatch):
    root = _root(tmp_path, {"system": "1-sys"})
    monkeypatch.setenv("DATACORE_ROOT", str(root))
    assert spaces.space_for("system") == "1-sys"


def test_cli_for_shell_callers(tmp_path):
    root = _root(tmp_path, {"system": "1-sys", "attest": ["1-a", "2-b"]})
    run = lambda *a: subprocess.run([sys.executable, str(LIB / "spaces.py"), *a, "--root", str(root)],
                                    capture_output=True, text=True)
    assert run("role", "system").stdout.strip() == "1-sys"
    assert run("role", "attest").stdout.split() == ["1-a", "2-b"]
    unset = run("role", "nope")
    assert unset.returncode == 0 and unset.stdout.strip() == ""


def test_example_ships_neutral_roles():
    doc = yaml.safe_load((DATA / "install.yaml.example").read_text())
    roles = doc.get("roles")
    assert isinstance(roles, dict) and roles, "install.yaml.example declares roles"
    for name in ("system", "product"):
        assert name in roles, f"example names the {name} role"
    for value in roles.values():
        for v in value if isinstance(value, list) else [value]:
            assert str(v) in ("myteam", "personal"), f"example role value {v!r} is not neutral"
            assert not str(v).partition("-")[0].isdigit(), (
                f"example role {v!r} names a folder number; roles name the space (ENG-2026-08-03-047)")


def test_plain_reader_agrees_with_yaml_for_shell_callers(tmp_path):
    """launchd and sudo run a bare system python with no PyYAML; the role
    lookup must give the same answer there."""
    text = ("meta:\n  name: t\nroles:\n  system: 1-sys   # comment\n  held: [6-x, '7-y']\n"
            "  attest:\n    - 1-a\n    - 0-personal\n  empty: []\nspaces: {}\n")
    assert spaces._plain_roles(text) == yaml.safe_load(text)["roles"]
    example = (DATA / "install.yaml.example").read_text()
    assert spaces._plain_roles(example) == yaml.safe_load(example)["roles"]


def test_cli_answers_without_pyyaml(tmp_path):
    root = _root(tmp_path, {"system": "1-sys", "held": ["6-x"]})
    code = ("import sys, builtins; real = builtins.__import__\n"
            "def imp(n, *a, **k):\n"
            "    if n == 'yaml' or n.startswith('yaml.'): raise ImportError(n)\n"
            "    return real(n, *a, **k)\n"
            "builtins.__import__ = imp\n"
            f"sys.path.insert(0, {str(LIB)!r}); sys.argv = ['spaces.py', 'role', 'held', '--root', {str(root)!r}]\n"
            f"import runpy; runpy.run_path({str(LIB / 'spaces.py')!r}, run_name='__main__')\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["6-x"]


def test_a_role_names_the_space_not_its_local_number(tmp_path):
    """The number prefix is added per install: the product space is 5-plur on one
    host and 3-plur on another. A role says `plur` and finds whichever folder this
    host has; a role written with another host's number still resolves
    (ENG-2026-08-03-047, owner correction 2026-09-27)."""
    import spaces
    for d in ("0-personal", "3-plur", "4-firm"):
        (tmp_path / d / ".datacore").mkdir(parents=True)
    (tmp_path / "install.yaml").write_text("roles:\n  product: plur\n  firm: 8-firm\n  held: [meridian]\n")
    assert spaces.space_for("product", root=tmp_path) == "3-plur"
    assert spaces.space_for("firm", root=tmp_path) == "4-firm"
    assert spaces.space_for_all("held", root=tmp_path) == []


def test_the_space_config_name_wins_over_the_folder_name(tmp_path):
    import spaces
    (tmp_path / "7-work" / ".datacore").mkdir(parents=True)
    (tmp_path / "7-work" / ".datacore" / "config.yaml").write_text("space:\n  name: plur\n  type: team\n")
    (tmp_path / "install.yaml").write_text("roles:\n  product: plur\n")
    assert spaces.space_for("product", root=tmp_path) == "7-work"
