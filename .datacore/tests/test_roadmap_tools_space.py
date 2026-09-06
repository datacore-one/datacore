"""The roadmap tools serve one space at a time, selected by --space.

They were born hardcoded to 5-plur. A second roadmap (2-datacore, 2026-09-06)
validated against PLUR's intent graph would pass or fail for the wrong
reasons, so every path now derives from the selected space — and a bad item
must still be refused there, or the parameter has only moved the blindness.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import roadmap_drift  # noqa: E402
import roadmap_render  # noqa: E402
import roadmap_validate  # noqa: E402

INTENTS = """* Example wins  :vision:
  :PROPERTIES:
  :INTENT_ID: example-wins
  :END:
** Users stay  :intent:
   :PROPERTIES:
   :INTENT_ID: users-stay
   :END:
"""


def _space(tmp: str) -> Path:
    root = Path(tmp) / "7-example"
    (root / "org").mkdir(parents=True)
    (root / "org" / "intents.org").write_text(INTENTS)
    return root


def _roadmap(serves="users-stay") -> str:
    return json.dumps({
        "version": 1, "updated": "2026-09-06",
        "north_star": {"metric": "retained users", "target": "100", "by": "M2"},
        "tracks": {"core": "the product"},
        "items": [{
            "id": "X-001", "track": "core", "title": "Users stay",
            "outcome": "Nobody who installs leaves in the first week",
            "serves": [serves], "horizon": "now", "status": "ready",
            "shipped": False,
            "done_when": {"condition": "week-one retention above 90%",
                          "evidence": "metric", "verify": "select ... from installs"},
        }],
    })


def _run(tool, *args):
    return subprocess.run([sys.executable, str(LIB / tool), *args],
                          capture_output=True, text=True)


def test_configure_derives_every_path_from_the_space():
    try:
        roadmap_validate.configure("2-datacore")
        assert roadmap_validate.DEFAULT_ROADMAP.parts[-2:] == ("2-datacore", "roadmap.yaml")
        assert roadmap_validate.INTENTS_ORG.parts[-3:] == ("2-datacore", "org", "intents.org")
        assert roadmap_validate.SPACE_TAG == "datacore"
        assert roadmap_validate.ORG_FILES[0].endswith("2-datacore/org/next_actions.org")
        assert "0-personal/org/next_actions.org" in roadmap_validate.ORG_FILES

        roadmap_render.configure("2-datacore")
        assert roadmap_render.ROADMAP.parts[-2:] == ("2-datacore", "roadmap.yaml")
        assert (roadmap_render.NAME, roadmap_render.SRC) == ("Datacore", "2-datacore/roadmap.yaml")
        roadmap_render.configure("5-plur")
        assert roadmap_render.NAME == "PLUR"

        roadmap_drift.configure("2-datacore")
        assert roadmap_drift.ROADMAP.parts[-2:] == ("2-datacore", "roadmap.yaml")
        assert roadmap_drift.ORG[2].endswith("2-datacore/org/inbox.org")
    finally:
        for mod in (roadmap_validate, roadmap_render, roadmap_drift):
            mod.configure("5-plur")


def test_absolute_space_path_is_accepted():
    with tempfile.TemporaryDirectory() as tmp:
        root = _space(tmp)
        roadmap_validate.configure(str(root))
        try:
            assert roadmap_validate.INTENTS_ORG == root / "org" / "intents.org"
            assert roadmap_validate.SPACE_TAG == "example"
        finally:
            roadmap_validate.configure("5-plur")


def test_cli_validates_in_another_space_and_still_refuses_bad_items():
    with tempfile.TemporaryDirectory() as tmp:
        root = _space(tmp)
        (root / "roadmap.yaml").write_text(_roadmap())
        ok = _run("roadmap_validate.py", "--space", str(root))
        assert ok.returncode == 0, ok.stdout + ok.stderr
        assert "1 items, 0 error(s)" in ok.stdout

        (root / "roadmap.yaml").write_text(_roadmap(serves="no-such-intent"))
        bad = _run("roadmap_validate.py", "--space", str(root))
        assert bad.returncode == 1
        assert "no such INTENT_ID" in bad.stdout


def test_render_and_drift_follow_the_space():
    with tempfile.TemporaryDirectory() as tmp:
        root = _space(tmp)
        (root / "roadmap.yaml").write_text(_roadmap())
        js = _run("roadmap_render.py", "--space", str(root), "--json")
        assert js.returncode == 0, js.stderr
        assert [i["id"] for i in json.loads(js.stdout)] == ["X-001"]
        md = _run("roadmap_render.py", "--space", str(root), "--md")
        assert md.returncode == 0, md.stderr
        assert md.stdout.startswith("# Example Roadmap")
        assert "7-example/roadmap.yaml" in md.stdout
        drift = _run("roadmap_drift.py", "--space", str(root), "--no-github")
        assert drift.returncode == 0, drift.stdout + drift.stderr
        assert "1 epics checked" in drift.stdout
