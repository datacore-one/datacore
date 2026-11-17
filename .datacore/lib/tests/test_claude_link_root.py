"""`DATACORE_ROOT` means the installation root everywhere, this script included.

Audit 2026-11-15 ("one resolver of the installation root"): 244 files read the
variable; `claude_link.py` alone took it to mean the `.datacore` folder. Every
scheduled job exports the installation root, so run from one this script looked
for `<root>/agents`, found nothing, and its sync would have removed every link
it manages.
"""
from __future__ import annotations

import importlib
import sys


def _load(monkeypatch, root=None):
    if root is None:
        monkeypatch.delenv("DATACORE_ROOT", raising=False)
    else:
        monkeypatch.setenv("DATACORE_ROOT", str(root))
    sys.modules.pop("claude_link", None)
    return importlib.import_module("claude_link")


def test_the_variable_names_the_installation_root(tmp_path, monkeypatch):
    (tmp_path / ".datacore" / "agents").mkdir(parents=True)
    (tmp_path / ".datacore" / "agents" / "helper.md").write_text("an agent\n")
    cl = _load(monkeypatch, tmp_path)
    assert cl.DATACORE_ROOT == tmp_path / ".datacore"
    assert cl.DATA_ROOT == tmp_path
    desired, _collisions = cl.collect_desired()
    assert [p.name for p in desired] == ["helper.md"], "the agent under <root>/.datacore/agents is found"
    assert cl._safe_source(tmp_path / ".datacore" / "agents" / "helper.md")


def test_without_the_variable_it_is_the_home_installation(monkeypatch):
    cl = _load(monkeypatch)
    assert cl.DATACORE_ROOT == cl.HOME / "Data" / ".datacore" and cl.DATA_ROOT == cl.HOME / "Data"
