"""Promise CAP-1:

    Anything I capture by typing, from Telegram, from the app, from a browser
    tab or through an agent lands in the right space's inbox within minutes.

Kind: deterministic. Exercises the real capture writers against a tmp Data
tree with two spaces (0-personal and 5-team):
  - typing / agent: `org_workspace_adapter.py add --file <space>/org/inbox.org`
    (the one choke point the MCP add_task and agents share);
  - agent (GitHub triage): `task_creator.create_tasks_from_scan` routes an issue
    of a team org to that team space's inbox;
  - browser tab: `tab-capture/lib/host.py capture_tabs` writes to the configured
    inbox, and the shipped config names the personal inbox.
The app path is covered by the app repo's eval
(daemon/tests/test_promise_CAP_1_app_capture_in_active_space.py). Telegram
capture goes through an agent session that calls the adapter (covered by the
adapter part); "within minutes" across machines is a sync property not tested
here.

Seeded failure: the capture lands in the wrong space (GitHub triage mapping
falls back to 0-personal) or outside the "* Inbox" section / not as an open
top-level capture the inbox processor counts.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
ADAPTER = LIB / "org_workspace_adapter.py"
HOST = ROOT / ".datacore" / "modules" / "tab-capture" / "lib" / "host.py"
GH_TASKS = ROOT / ".datacore" / "modules" / "github" / "lib" / "task_creator.py"

INBOX = "#+TITLE: Inbox\n\n* Inbox\n** TODO Earlier capture\n:PROPERTIES:\n:ID: earlier-1\n:END:\n"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tree(tmp_path: Path) -> Path:
    data = tmp_path / "Data"
    for s in ("0-personal", "5-team"):
        org = data / s / "org"
        org.mkdir(parents=True)
        (org / "inbox.org").write_text(INBOX, encoding="utf-8")
        (org / "next_actions.org").write_text("#+TITLE: Next\n\n* Work\n", encoding="utf-8")
    return data


def _adapter(*args: str) -> dict:
    r = subprocess.run([sys.executable, str(ADAPTER), *args], capture_output=True,
                       text=True, timeout=60, cwd=str(LIB))
    assert r.returncode == 0, r.stderr or r.stdout
    return json.loads(r.stdout)


def _captures(inbox: Path) -> int:
    """Open captures as the inbox processor sees them: an open (TODO/NEXT)
    heading that is top-level or a direct child of the "* Inbox" section."""
    n, in_inbox = 0, False
    for line in inbox.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(\*+)\s+(.*)$", line)
        if not m:
            continue
        level, rest = len(m.group(1)), m.group(2)
        if level == 1:
            in_inbox = rest.strip().lower() == "inbox"
        if (level == 1 or (level == 2 and in_inbox)) and re.match(r"(TODO|NEXT)\b", rest):
            n += 1
    return n


def test_typed_or_agent_capture_lands_in_the_named_space_inbox(tmp_path):
    data = _tree(tmp_path)
    team, personal = data / "5-team/org/inbox.org", data / "0-personal/org/inbox.org"
    before_team, before_personal = _captures(team), _captures(personal)
    out = _adapter("add", "--file", str(team), "--heading", "Call the venue about the offsite")
    assert out.get("added") is True, out
    text = team.read_text(encoding="utf-8")
    assert "Call the venue about the offsite" in text
    # it is an open capture the morning inbox run counts
    assert _captures(team) == before_team + 1
    assert _captures(personal) == before_personal, "a team capture must not land in the personal inbox"
    assert "Call the venue" not in (data / "5-team/org/next_actions.org").read_text(encoding="utf-8")


def test_github_agent_capture_lands_in_the_repo_owners_space(tmp_path):
    data = _tree(tmp_path)
    tc = _load("gh_task_creator_cap1", GH_TASKS)
    scan = {"mentions": [{"repo": "team-org/widget", "number": 42, "title": "Please review",
                          "url": "https://github.com/team-org/widget/issues/42"}]}
    res = tc.create_tasks_from_scan(scan, data, {"team-org": ["5-team"]})
    assert res["created"] == 1 and res["errors"] == 0, res
    team = (data / "5-team/org/inbox.org").read_text(encoding="utf-8")
    assert "team-org/widget#42" in team
    assert "team-org/widget#42" not in (data / "0-personal/org/inbox.org").read_text(encoding="utf-8")
    assert _captures(data / "5-team/org/inbox.org") == 2


def test_browser_tabs_land_in_the_personal_inbox(tmp_path):
    data = _tree(tmp_path)
    host = _load("tab_host_cap1", HOST)
    inbox = data / "0-personal/org/inbox.org"
    res = host.capture_tabs([{"title": "An article", "url": "https://example.org/a"}],
                            {"inbox_path": str(inbox), "filtered_prefixes": ["chrome://"]})
    assert res["success"] and res["count"] == 1
    assert "https://example.org/a" in inbox.read_text(encoding="utf-8")
    assert _captures(inbox) == 2, "the tab must be an open top-level capture under * Inbox"
    # the shipped config points at the personal space's inbox
    cfg = json.loads((HOST.parent / "config.json").read_text(encoding="utf-8"))
    assert Path(cfg["inbox_path"]).expanduser().as_posix().endswith("/Data/0-personal/org/inbox.org")
