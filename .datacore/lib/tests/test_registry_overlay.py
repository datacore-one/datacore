"""Installation-private registry entries live in an untracked overlay.

agents.yaml / commands.yaml are public; entries whose definitions sit in a
private space go to agents.local.yaml / commands.local.yaml beside them
(gitignored). Every reader sees the union; with no overlay, only the public part.
"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from registry_overlay import load_registry_file, local_path  # noqa: E402


def _w(p: Path, doc):
    p.write_text(yaml.safe_dump(doc))


def test_no_overlay_is_the_public_file(tmp_path):
    reg = tmp_path / "agents.yaml"
    _w(reg, {"agents": {"a": {"source": "x.md"}}})
    assert load_registry_file(reg) == {"agents": {"a": {"source": "x.md"}}}


def test_overlay_entries_are_added_per_section(tmp_path):
    reg = tmp_path / "agents.yaml"
    _w(reg, {"version": 2, "agents": {"a": {}}, "module_agents": {"m": {}}})
    _w(local_path(reg), {"agents": {"private": {"source": "1-team/x.md"}},
                         "module_agents": {"pm": {}}})
    data = load_registry_file(reg)
    assert set(data["agents"]) == {"a", "private"}
    assert set(data["module_agents"]) == {"m", "pm"}
    assert data["version"] == 2


def test_overlay_never_overrides_a_public_entry(tmp_path):
    reg = tmp_path / "commands.yaml"
    _w(reg, {"commands": {"c": {"source": "public.md"}}})
    _w(local_path(reg), {"commands": {"c": {"source": "private.md"}}})
    assert load_registry_file(reg)["commands"]["c"]["source"] == "public.md"


def test_missing_public_file_is_empty(tmp_path):
    assert load_registry_file(tmp_path / "agents.yaml") == {}


def test_local_path_name():
    assert local_path(Path("/r/agents.yaml")).name == "agents.local.yaml"


def test_shipped_registry_names_no_private_space_source():
    root = Path(__file__).resolve().parents[3]
    for name in ("agents.yaml", "commands.yaml"):
        data = yaml.safe_load((root / ".datacore" / "registry" / name).read_text()) or {}
        for section in ("agents", "module_agents", "commands", "module_commands"):
            for key, entry in (data.get(section) or {}).items():
                src = str((entry or {}).get("source") or "")
                assert not (src[:1].isdigit() and src[1:2] == "-"), f"{name}:{section}/{key} sources a space: {src}"
