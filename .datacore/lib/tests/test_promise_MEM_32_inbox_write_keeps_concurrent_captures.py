"""MEM-32: Writing to my inbox never overwrites captures that another machine
or agent added in the meantime.

Kind: deterministic. Two real inbox writers against a tmp inbox.org, with the
second writer's capture landing inside the first writer's read-modify-write
window:
  * the task adapter (org_workspace_adapter add -- every agent, the MCP and the
    CLI go through it): another agent appends a capture after the adapter has
    loaded the inbox and before it saves. The foreign capture must survive
    (the adapter merges or refuses; it never writes its stale copy over it).
  * the tab-capture native host (host.capture_tabs) holds an open handle on
    inbox.org while the adapter replaces the file (atomic rename). The tab
    capture and the adapter's task must both be in the inbox afterwards.

Seeded failure: the adapter's stale-overwrite check made vacuous (every
digest compares equal) -- the foreign capture is then silently overwritten.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
HOST = LIB.parent / "modules" / "tab-capture" / "lib" / "host.py"
INBOX = "#+TITLE: Inbox\n\n* Inbox\n** TODO Earlier capture\n"


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    state = (tmp_path / "state").resolve()
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    org = (tmp_path / "0-personal" / "org").resolve()
    org.mkdir(parents=True)
    f = org / "inbox.org"
    f.write_text(INBOX, encoding="utf-8")
    return f


def _adapter():
    import org_workspace_adapter as A
    return A


def _add(A, f: Path, heading: str):
    args = A.build_parser().parse_args(["add", "--file", str(f), "--heading", heading])
    try:
        return A.cmd_add(args)
    except Exception as exc:  # a refusal is allowed; silent loss is not
        return {"error": f"{type(exc).__name__}: {exc}"}


def test_adapter_never_overwrites_a_capture_added_after_it_read(inbox, monkeypatch):
    A = _adapter()
    real_load = A._load_ws

    def load_then_other_agent_captures(*paths, **kw):
        ws = real_load(*paths, **kw)
        with open(inbox, "a", encoding="utf-8") as fh:   # another agent / a sync pull
            fh.write("** TODO Foreign capture from the laptop\n")
        return ws

    monkeypatch.setattr(A, "_load_ws", load_then_other_agent_captures)
    result = _add(A, inbox, "Adapter capture")
    text = inbox.read_text(encoding="utf-8")
    assert "Foreign capture from the laptop" in text, (
        f"the adapter overwrote a capture added while it was writing; result={result}")
    assert "Earlier capture" in text
    if not result.get("error"):
        assert "Adapter capture" in text, "adapter reported added but its task is not in the file"


def test_tab_capture_and_adapter_interleaved_keep_both(inbox, monkeypatch):
    spec = importlib.util.spec_from_file_location("tab_host_mem32", HOST)
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    A = _adapter()
    real_insert = host.insert_under_inbox
    outcome = {}

    def adapter_writes_meanwhile(content, entries):
        # The tab host has read inbox.org under its flock; the adapter (which
        # does not share that lock) writes its capture now.
        outcome["adapter"] = _add(A, inbox, "Adapter capture during tab save")
        return real_insert(content, entries)

    monkeypatch.setattr(host, "insert_under_inbox", adapter_writes_meanwhile)
    cfg = {"inbox_path": str(inbox), "filtered_prefixes": ["chrome://"]}
    res = host.capture_tabs([{"title": "Tab article", "url": "https://example.org/tab"}], cfg)
    text = inbox.read_text(encoding="utf-8")
    adapter_ok = not outcome["adapter"].get("error")
    lost = []
    if res.get("success") and res.get("count") and "https://example.org/tab" not in text:
        lost.append("the tab capture (host reported success)")
    if adapter_ok and "Adapter capture during tab save" not in text:
        lost.append("the adapter's capture (adapter reported added)")
    assert not lost, f"inbox lost {lost}; adapter={outcome['adapter']} tab={res}"
    assert "Earlier capture" in text
