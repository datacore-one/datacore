"""KNW-7: Search never shows the same note twice and never loses a note to a name clash.

Kind: deterministic. The real indexer (zettel_processor.scan_space, twice, as the
nightly reindex does), zettel_db.sync_to_root and zettel_db.search_fts, run in a
subprocess against a tmp DATACORE_ROOT holding two spaces.

Seeded failure: a note indexed twice appears twice in the results; or two notes with
the same file name -- in two folders of one space, or in two spaces (every space
has a journals/2026-09-25.md) -- collapse into one, because the file id is the
file stem and a second note with that stem overwrites the first (ON CONFLICT(id)
DO UPDATE).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]

NOTES = {
    "1-alpha/3-knowledge/zettel/Ideas.md": "Kiwifruit orchard notes about ripening. " * 20,
    "1-alpha/3-knowledge/literature/Ideas.md": "Pomegranate harvest literature summary. " * 20,
    "1-alpha/3-knowledge/zettel/Unique.md": "Tamarind is a singular fruit. " * 20,
    "1-alpha/journal/2026-09-25.md": "# 2026-09-25\n\nAlpha journal mentions quincejam today. " * 5,
    "2-beta/journal/2026-09-25.md": "# 2026-09-25\n\nBeta journal mentions quincejam too. " * 5,
}

SCRIPT = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import zettel_db as D, zettel_processor as P
for space in D.SPACES:
    D.init_database(space)
D.init_database(None)
for _ in range(2):                       # the nightly job reindexes what is already indexed
    for space in D.SPACES:
        P.scan_space(space, verbose=False)
for space in D.SPACES:
    D.sync_to_root(space)
out = {}
for q in ("kiwifruit", "pomegranate", "tamarind", "quincejam"):
    out[q] = {"alpha": [r["path"] for r in D.search_fts(q, space="alpha")],
              "root": [r["path"] for r in D.search_fts(q, space=None)]}
print("RESULT" + json.dumps(out))
"""


def _run(tmp_path):
    root = tmp_path / "Data"
    (root / ".datacore").mkdir(parents=True)
    for rel, body in NOTES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    env = dict(os.environ, DATACORE_ROOT=str(root), HOME=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", SCRIPT, str(LIB)], capture_output=True, text=True,
                       env=env, timeout=60, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr[-2000:]
    line = next(l for l in r.stdout.splitlines() if l.startswith("RESULT"))
    return root, json.loads(line[len("RESULT"):])


def test_search_shows_each_note_once_and_loses_none_to_a_name_clash(tmp_path):
    root, res = _run(tmp_path)

    # never twice: a note indexed on two runs is still one hit
    for where in ("alpha", "root"):
        hits = res["tamarind"][where]
        assert len(hits) == len(set(hits)) == 1, f"{where}: 'Unique.md' found {len(hits)} times: {hits}"

    # never lost: same file name in two folders of one space
    for q, rel in (("kiwifruit", "1-alpha/3-knowledge/zettel/Ideas.md"),
                   ("pomegranate", "1-alpha/3-knowledge/literature/Ideas.md")):
        for where in ("alpha", "root"):
            assert str(root / rel) in res[q][where], (
                f"{where}: {rel} is lost to a name clash with the other Ideas.md; "
                f"search for {q!r} returned {res[q][where]}")

    # never lost: same journal date in two spaces, in the all-spaces (root) index
    hits = res["quincejam"]["root"]
    for rel in ("1-alpha/journal/2026-09-25.md", "2-beta/journal/2026-09-25.md"):
        assert str(root / rel) in hits, f"root: {rel} is lost to a name clash; got {hits}"
    assert len(hits) == len(set(hits)), f"root: a journal page shows twice: {hits}"
