"""KNW-2: Links I queue for research are processed overnight into literature and atomic
notes, and each queue item is marked done with links to them.

Kind: deterministic (the real orchestrator main() on a tmp queue, fetch and model
replaced) + production contract (the last overnight run on winston actually
processed the queue).

Seeded failure: mark_done drops the :OUTPUT:/:ZETTELS: links (or the run stops
writing notes); on the fleet, a night that processes 0 items with a non-empty
queue (2026-09-25 and 2026-09-26: "Processed: 0").
"""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _research_harness as H  # noqa: E402

QUEUE = """#+TITLE: Research
* Queue
** TODO [#A] Read: Alpha paper
   :PROPERTIES:
   :ID: knw2-alpha
   :CREATED: [{today}]
   :END:
   Link: https://example.test/alpha
** TODO [#B] Read: Beta post
   :PROPERTIES:
   :ID: knw2-beta
   :CREATED: [{today}]
   :END:
   Link: https://example.test/beta
"""


def _section(text: str, item_id: str) -> str:
    lines = text.split("\n")
    at = next(i for i, l in enumerate(lines) if l.strip() == f":ID: {item_id}")
    head = max(i for i in range(at + 1) if re.match(r"^\*+\s", lines[i]))
    end = next((j for j in range(head + 1, len(lines)) if re.match(r"^\*+\s", lines[j])), len(lines))
    return "\n".join(lines[head:end])


def test_each_queued_link_becomes_notes_and_its_item_is_done_with_links(tmp_path, monkeypatch):
    R = H.load()
    t = H.point_at(R, tmp_path, monkeypatch)
    t.org.write_text(QUEUE.format(today=date.today().isoformat()), encoding="utf-8")
    H.capture_http(monkeypatch)
    monkeypatch.setattr(R, "fetch_url", lambda url: "Article body " * 50)
    titles = {"https://example.test/alpha": "Alpha paper", "https://example.test/beta": "Beta post"}
    monkeypatch.setattr(R, "process_item", lambda item, content: H.analysis(titles[item["url"]]))
    monkeypatch.setattr(R, "send_telegram_summary", lambda *a, **k: True)

    H.run_main(R, monkeypatch, "--no-podcast")

    text = t.org.read_text(encoding="utf-8")
    for item_id in ("knw2-alpha", "knw2-beta"):
        sec = _section(text, item_id)
        assert re.match(r"^\*+ DONE ", sec), f"{item_id} is not marked DONE:\n{sec}"
        out = re.search(r":OUTPUT:\s+\[\[([^\]]+)\]\]", sec)
        assert out, f"{item_id} has no :OUTPUT: link to its literature note:\n{sec}"
        lit = t.data / out.group(1)
        assert lit.is_file(), f"the :OUTPUT: link of {item_id} points nowhere: {lit}"
        zet = re.search(r":ZETTELS:\s+(.+)", sec)
        assert zet, f"{item_id} has no :ZETTELS: links:\n{sec}"
        names = re.findall(r"\[\[([^\]]+)\]\]", zet.group(1))
        assert names, sec
        for name in names:
            assert list(t.personal.rglob(f"{name}.md")), f"zettel [[{name}]] of {item_id} was not written"


LOG = "~/.datacore/cos/research.log"   # expanded by the remote shell


@pytest.mark.production
def test_last_overnight_run_on_winston_processed_the_queue():
    """The overnight run is cos_research.sh on winston at 05:30 (crontab)."""
    cmd = ("grep -nE '^\\[research\\] (Starting research processing for|Processed:|"
           "No TODO items found)' " + LOG + " | tail -4")
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "winston", cmd],
                       capture_output=True, text=True, timeout=45)
    assert r.returncode == 0, f"could not read {LOG} on winston: {r.stderr.strip()[:200]}"
    lines = r.stdout.strip().splitlines()
    starts = [i for i, l in enumerate(lines) if "Starting research processing for" in l]
    assert starts, f"no run recorded in {LOG}"
    last = lines[starts[-1]:]
    day = re.search(r"for (\d{4}-\d{2}-\d{2})", last[0]).group(1)
    age = date.today() - datetime.strptime(day, "%Y-%m-%d").date()
    assert age <= timedelta(days=1), f"the last overnight research run was {day}"
    if any("No TODO items found" in l for l in last):
        return
    processed = [int(m.group(1)) for l in last for m in [re.search(r"Processed: (\d+)", l)] if m]
    assert processed and processed[-1] > 0, (
        f"the overnight run of {day} processed 0 items with a non-empty queue: {last}")
