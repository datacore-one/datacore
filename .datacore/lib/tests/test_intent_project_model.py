"""The intent graph's project model (owner, 2026-11-05).

Free depth, stopping at a GTD project. A leaf is work (a project or a recurring duty)
or a rule (a constraint). Every project has a canvas and a completion check. When all
children of a node close, the node itself must be checked: that is the combine step,
and a failed check is how a new project gets identified.

A space opts in by having a project node. Other spaces stay silent, so their gap
reports do not fill with noise before they migrate.
"""
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
import roadmap_render  # noqa: E402
import roadmap_validate  # noqa: E402
from priority_score import IntentGraph  # noqa: E402

ORG = """\
* Vision  :vision:
  :PROPERTIES:
  :INTENT_ID: vision
  :END:
** Direction  :intent:
   :PROPERTIES:
   :INTENT_ID: direction
   :LEVEL: 1
   :END:
*** Goal  :goal:
    :PROPERTIES:
    :INTENT_ID: goal
    :LEVEL: 2
    :SUCCESS: it works
    :END:
**** Complete project  :initiative:project:
     :PROPERTIES:
     :INTENT_ID: complete
     :LEVEL: 3
     :CANVAS: 2-projects/complete/CANVAS.md
     :DONE_WHEN: a check passes
     :END:
**** Roadmap project  :project:
     :PROPERTIES:
     :INTENT_ID: roadmapped
     :LEVEL: 3
     :ROADMAP: D-001
     :CANVAS: 2-projects/roadmapped/CANVAS.md
     :END:
**** Bare project  :project:
     :PROPERTIES:
     :INTENT_ID: bare
     :LEVEL: 3
     :CANVAS: none yet
     :END:
**** Unclassified leaf  :initiative:
     :PROPERTIES:
     :INTENT_ID: unclassified
     :LEVEL: 3
     :END:
**** A rule  :constraint:
     :PROPERTIES:
     :INTENT_ID: rule
     :LEVEL: 3
     :END:
*** Finished goal  :goal:
    :PROPERTIES:
    :INTENT_ID: finished-goal
    :LEVEL: 2
    :SUCCESS: it works
    :END:
**** Closed one  :project:
     :PROPERTIES:
     :INTENT_ID: closed-one
     :LEVEL: 3
     :STATUS: done
     :END:
**** Closed two  :project:
     :PROPERTIES:
     :INTENT_ID: closed-two
     :LEVEL: 3
     :STATUS: cancelled
     :END:
"""
UNMIGRATED = """\
* Vision  :vision:
  :PROPERTIES:
  :INTENT_ID: vision
  :END:
** Direction  :intent:
   :PROPERTIES:
   :INTENT_ID: direction
   :LEVEL: 1
   :END:
*** Old leaf  :initiative:
    :PROPERTIES:
    :INTENT_ID: old-leaf
    :LEVEL: 2
    :SUCCESS: it works
    :END:
"""


def load(tmp_path, org):
    (tmp_path / ".datacore").mkdir(exist_ok=True)
    (tmp_path / ".datacore" / "intents.org").write_text(org)
    return IntentGraph.load(tmp_path)


def kinds(graph, kind):
    return {g["id"] for g in graph.gaps() if g["kind"] == kind}


def test_node_kind_is_read_from_tags(tmp_path):
    g = load(tmp_path, ORG)
    assert g.nodes["complete"].kind == "project"       # transitional double tag
    assert g.nodes["bare"].kind == "project"
    assert g.nodes["rule"].kind == "constraint"
    assert g.nodes["goal"].kind == "goal"


def test_project_needs_a_canvas(tmp_path):
    missing = kinds(load(tmp_path, ORG), "project_no_canvas")
    assert "bare" in missing                           # "none yet" is a blank, not a path
    assert "complete" not in missing and "roadmapped" not in missing


def test_project_needs_a_completion_check(tmp_path):
    missing = kinds(load(tmp_path, ORG), "project_no_done_when")
    assert "bare" in missing
    assert "complete" not in missing
    assert "roadmapped" not in missing                 # the roadmap item owns its check


def test_leaf_must_be_work_or_a_rule(tmp_path):
    found = kinds(load(tmp_path, ORG), "leaf_unclassified")
    assert found == {"unclassified"}


def test_closed_project_is_not_reported_as_missing_anything(tmp_path):
    g = load(tmp_path, ORG)
    assert "closed-one" not in kinds(g, "project_no_canvas")
    assert "closed-two" not in kinds(g, "project_no_done_when")


def test_all_children_closed_asks_the_combine_question(tmp_path):
    found = kinds(load(tmp_path, ORG), "children_closed")
    assert found == {"finished-goal"}


def test_unmigrated_graph_stays_silent(tmp_path):
    g = load(tmp_path, UNMIGRATED)
    for kind in ("leaf_unclassified", "project_no_canvas", "project_no_done_when", "children_closed"):
        assert not kinds(g, kind)


def test_validator_expects_roadmap_items_on_projects_but_not_on_roadmap_nodes(tmp_path):
    path = tmp_path / "intents.org"
    path.write_text(ORG)
    bearing = roadmap_validate.item_bearing(path)
    assert {"complete", "bare", "goal"} <= bearing
    assert "roadmapped" not in bearing                 # it IS the roadmap item
    assert "rule" not in bearing


def test_render_shows_only_direct_children_of_an_intent():
    nodes = [{"depth": 2, "kind": "intent", "id": "i"},
             {"depth": 3, "kind": "goal", "id": "g"},
             {"depth": 4, "kind": "constraint", "id": "deep-rule"},
             {"depth": 3, "kind": "constraint", "id": "top-rule"}]
    assert [k["id"] for k in roadmap_render.intent_chips(nodes, 0)] == ["g", "top-rule"]
