"""Filed decision-board answers also land in a decisions dataset.

An answers file holds only {row id: choice, note}; the question, its options and
the suggestion live in the board page, which can be regenerated or deleted. To
learn from the owner's decisions later (rule extraction, training), each
decision is stored once, joined with the row it answered (owner, 2026-10-23).
"""
import json
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


def _board(state: Path, slug: str, build: str = "b1"):
    data = {"meta": {"slug": slug, "title": "T", "eyebrow": "Weekly review", "build": build},
            "sections": [{"key": "review", "label": "Review", "rows": [
                {"id": "A1", "title": "Accept the fix", "context": "why it matters",
                 "meta": ["5-plur", "REVIEW"], "suggested": "accept",
                 "options": [{"value": "accept", "label": "Accept", "consequence": "Marked DONE."},
                             {"value": "drop", "label": "Drop", "consequence": "CANCELLED."}]},
                {"id": "A2", "title": "Second", "context": "", "suggested": "drop",
                 "options": [{"value": "accept", "label": "Accept"}, {"value": "drop", "label": "Drop"}]}]}]}
    html = ('<html><body><script type="application/json" id="data">'
            + json.dumps(data) + '</script></body></html>')
    p = state / "decision-boards" / f"2026-10-23-{slug}.html"
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    p.write_text(html)
    return p


def _saved(downloads: Path, slug: str, build: str = "b1"):
    downloads.mkdir(parents=True, exist_ok=True)
    f = downloads / f"{slug}.decisions.json"
    f.write_text(json.dumps({"board": slug, "build": build, "savedAt": "2026-10-23T09:00:00Z",
                             "decisions": {"A1": {"choice": "accept", "note": ""},
                                           "A2": {"choice": "accept", "note": "keep it"}}}))
    return f


def _rows(state: Path):
    p = state / "decision-boards" / "dataset" / "decisions.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def test_filing_answers_appends_one_joined_record_per_decision(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    import decision_board_collect as c
    _board(state, "morning-review")
    _saved(tmp_path / "dl", "morning-review")

    assert c.collect(tmp_path / "dl", None, False) == 1
    rows = _rows(state)
    assert len(rows) == 2
    a1 = next(r for r in rows if r["row"] == "A1")
    assert a1["board"] == "morning-review" and a1["build"] == "b1"
    assert a1["title"] == "Accept the fix" and a1["context"] == "why it matters"
    assert a1["suggested"] == "accept" and a1["choice"] == "accept"
    assert a1["followed_suggestion"] is True
    assert [o["value"] for o in a1["options"]] == ["accept", "drop"]
    assert a1["kind"] == "review" and a1["schema"] == 1
    a2 = next(r for r in rows if r["row"] == "A2")
    assert a2["followed_suggestion"] is False and a2["note"] == "keep it"
    mode = (state / "decision-boards" / "dataset" / "decisions.jsonl").stat().st_mode & 0o777
    assert mode == 0o600


def test_backfill_is_idempotent_and_keeps_answers_without_a_board(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    import decision_board_collect as c
    _board(state, "morning-review")
    c.collect(_saved(tmp_path / "dl", "morning-review").parent, None, False)
    _saved(tmp_path / "dl2", "gone-board")
    c.collect(tmp_path / "dl2", None, False)

    before = _rows(state)
    c.backfill()
    c.backfill()
    after = _rows(state)
    assert len(after) == len(before) == 4
    orphan = [r for r in after if r["board"] == "gone-board"]
    assert orphan and all(r["title"] is None and r["board_found"] is False for r in orphan)
