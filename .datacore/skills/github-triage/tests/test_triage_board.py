"""The nightly GitHub triage hands the owner a decision board. The board data
must be valid before it reaches him, and it renders only locally.

Owner decisions, 2026-09-29: full 09-11 triage skill, run by Miles nightly;
public repos and enterprise in separate runs; read-only on GitHub; the board
travels through git as data and is rendered on the Mac, never hosted.
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "triage_board.py"


def _board(**over):
    b = {
        "meta": {"slug": "github-triage-2026-09-30-public", "title": "GitHub triage — public repos",
                 "h1": "GitHub triage", "eyebrow": "GITHUB · public · 2026-09-30", "lede": "What waits on you.",
                 "asOf": "2026-09-30 03:40 UTC", "sources": ["notes/github-triage/2026-09-30-public.md"],
                 "path": [["Triage", "incremental"], ["Your calls", "2 decisions"], ["Applied", "after you say apply"]]},
        "sections": [{"key": "waiting", "label": "Waiting on you", "hint": "", "noted": [], "rows": [
            {"id": "W1", "area": "plur-ai/plur #1353", "title": "Your PR has 4 failing checks — fix or close?",
             "context": "Checks failing on the head commit.", "suggested": "fix",
             "options": [{"value": "fix", "label": "Fix the failing checks", "consequence": "Task created."},
                         {"value": "close", "label": "Close the PR", "consequence": "Closed after you apply."}],
             "meta": ["From: plur9 · 2026-09-29", "https://github.com/plur-ai/plur/pull/1353"]},
        ]}],
        "prefill": {},
    }
    b.update(over)
    return b


def _run(*args, env=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)


def test_valid_board_passes(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(json.dumps(_board()))
    r = _run("validate", str(p))
    assert r.returncode == 0, r.stderr


def test_suggested_must_be_an_option(tmp_path):
    b = _board()
    b["sections"][0]["rows"][0]["suggested"] = "merge"
    p = tmp_path / "b.json"
    p.write_text(json.dumps(b))
    r = _run("validate", str(p))
    assert r.returncode != 0 and "suggested" in (r.stdout + r.stderr)


def test_links_must_be_http(tmp_path):
    b = _board()
    b["sections"][0]["rows"][0]["meta"][1] = "javascript:alert(1)"
    p = tmp_path / "b.json"
    p.write_text(json.dumps(b))
    r = _run("validate", str(p))
    assert r.returncode != 0


def test_duplicate_ids_rejected(tmp_path):
    b = _board()
    row = b["sections"][0]["rows"][0]
    b["sections"][0]["rows"].append(dict(row))
    p = tmp_path / "b.json"
    p.write_text(json.dumps(b))
    assert _run("validate", str(p)).returncode != 0


def test_render_writes_private_local_page(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(json.dumps(_board()))
    env = dict(os.environ, DATACORE_STATE=str(tmp_path / "state"))
    r = _run("render", str(p), env=env)
    assert r.returncode == 0, r.stderr
    out = Path(r.stdout.strip().splitlines()[-1])
    assert out.exists() and out.parent == tmp_path / "state" / "decision-boards"
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    html = out.read_text()
    assert "__BOARD_DATA__" not in html and "</script" not in html.split('id="data">', 1)[1].split("</script>", 1)[0]
    assert '"build"' in html  # a build identity is always set
