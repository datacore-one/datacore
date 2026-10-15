"""promise_board.py reviews another product's promises, not only Datacore's.

The PLUR evals (2026-10-14) keep their promises in one file, evals/promises.yaml,
mark open product decisions with `decide:`, and have a scoreboard showing what
the evals found. The board must name the right product, show the decision, and
show the eval result beside each promise -- a review of PLUR's promises that
says "Datacore will not promise this" is reviewing the wrong thing.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]

PROMISES = """capabilities:
- key: recall
  name: Finding memories
  what_it_does: Finds the memories that answer a question.
  promises:
  - id: REC-1
    promise: The memory that answers comes back in the top five.
    today: unknown
    evidence: never measured
  - id: REC-6
    promise: A replaced memory is not presented as current.
    today: broken
    decide: Hide it, mark it, or show it?
    evidence: inject only
"""


def board_data(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def run_board(tmp_path: Path, *extra: str) -> dict:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    out = subprocess.run([sys.executable, str(LIB / "promise_board.py"), *extra],
                         capture_output=True, text=True, env={**os.environ, "DATACORE_STATE": str(state)})
    assert out.returncode == 0, out.stderr
    return board_data(Path(json.loads(out.stdout)["out"]).read_text())


def test_a_single_file_with_a_product_name(tmp_path):
    f = tmp_path / "promises.yaml"
    f.write_text(PROMISES)
    data = run_board(tmp_path, "--file", str(f), "--product", "PLUR", "--slug", "plur-promises")
    text = json.dumps(data)
    assert "PLUR" in data["meta"]["lede"] and "Datacore" not in text
    rows = {r["id"]: r for r in data["sections"][0]["rows"]}
    assert "PLUR will not promise this" in json.dumps(rows["REC-1"]["options"])
    assert "Hide it, mark it, or show it?" in rows["REC-6"]["context"]


def test_eval_results_from_a_scoreboard_are_shown(tmp_path):
    f = tmp_path / "promises.yaml"
    f.write_text(PROMISES)
    sb = tmp_path / "scoreboard.json"
    sb.write_text(json.dumps({"promises": {"REC-1": "green", "REC-6": "red"},
                              "details": {"REC-6": "x.test.ts: replaced memory returned first"}}))
    data = run_board(tmp_path, "--file", str(f), "--product", "PLUR", "--scoreboard", str(sb))
    rows = {r["id"]: r for r in data["sections"][0]["rows"]}
    assert "eval passes" in rows["REC-1"]["context"].lower()
    assert "eval fails" in rows["REC-6"]["context"].lower()
    assert "replaced memory returned first" in rows["REC-6"]["context"]


def test_the_datacore_folder_form_still_works(tmp_path):
    (tmp_path / "promises").mkdir()
    (tmp_path / "promises" / "p.yaml").write_text(PROMISES)
    data = run_board(tmp_path, "--folder", str(tmp_path))
    assert "Datacore" in data["meta"]["lede"]
