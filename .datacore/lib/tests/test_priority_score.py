"""Tests for the intent graph.

The failure being guarded against is structural, not cosmetic: for months the
scorer read a flat 5-row keyword projection of a 4-level graph, so stating a
priority steered nothing. The tests that matter are therefore about STRUCTURE
— does the frontier move, do cross-links resolve, is placement declarative —
not about whether yaml parses.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from priority_score import (HIGH_LEVERAGE, NEUTRAL, ON_GRAPH,  # noqa: E402
                            SPOTLIT, IntentGraph)

ORG = """\
* Vision
  :PROPERTIES:
  :INTENT_ID: vision
  :END:
** Runs itself
   :PROPERTIES:
   :INTENT_ID: autonomy
   :LEVEL: 1
   :END:
*** Nightshift executes tasks
    :PROPERTIES:
    :INTENT_ID: nightshift-goal
    :LEVEL: 2
    :SUCCESS: >80%% of tasks complete overnight
    :END:
**** Evaluator pipeline ensures quality
     :PROPERTIES:
     :INTENT_ID: evaluator
     :LEVEL: 3
     :END:
**** Learning extractor captures improvements
     :PROPERTIES:
     :INTENT_ID: extractor
     :LEVEL: 3
     :END:
** Empowering
   :PROPERTIES:
   :INTENT_ID: empowering
   :LEVEL: 1
   :END:
*** Community extensions reach users
    :PROPERTIES:
    :INTENT_ID: extensions
    :LEVEL: 2
    :SERVES: autonomy
    :END:
"""


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".datacore" / "cos").mkdir(parents=True)
    (tmp_path / ".datacore" / "intents.org").write_text(ORG)
    (tmp_path / ".datacore" / "cos" / "priorities.yaml").write_text(
        "priorities:\n"
        "  - id: nightshift-goal\n    rank: 1\n    priority: Ship nightshift\n"
        "    keywords: [nightshift]\n")
    (tmp_path / ".datacore" / "tags.yaml").write_text(
        "domains:\n"
        "  cos:\n    description: CoS\n    org: ':cos:'\n    intent: autonomy\n")
    return tmp_path


def test_graph_parses_nesting_and_crosslinks(root):
    g = IntentGraph.load(root)
    assert "nightshift-goal" in g.nodes
    assert g.nodes["evaluator"].parent == "nightshift-goal"
    # :SERVES: is an ADDITIONAL parent, making this a DAG rather than a tree.
    assert "autonomy" in g.parents("extensions")
    assert "autonomy" in g.ancestors("extensions")


def test_frontier_moves_up_as_work_completes(root):
    """A node is an aggregator while children are open and becomes the
    actionable thing itself once they are done. THE structural rule."""
    g = IntentGraph.load(root)
    front = {n.id for n in g.frontier()}
    assert {"evaluator", "extractor"} <= front
    assert "nightshift-goal" not in front        # children still open

    # Complete both children: the parent advances to the frontier.
    front2 = {n.id for n in g.frontier(done={"evaluator", "extractor"})}
    assert "nightshift-goal" in front2
    assert "evaluator" not in front2


def test_high_leverage_is_multi_intent(root):
    g = IntentGraph.load(root)
    assert g.is_high_leverage("extensions")
    assert not g.is_high_leverage("evaluator")


def test_tag_placement_beats_keyword_guessing(root):
    """A task tagged :cos: is placed by declaration via the DIP-0014 registry,
    even when its wording points somewhere else entirely."""
    g = IntentGraph.load(root)
    assert g.tag_map.get("cos") == "autonomy"
    node = g.match("community extensions reach users", "", ("cos",))
    assert node.id == "autonomy"


def test_spotlight_beats_high_leverage(root):
    g = IntentGraph.load(root)
    assert g.score_10("nightshift executes tasks") == SPOTLIT
    assert g.score_10("community extensions reach users") == HIGH_LEVERAGE


def test_unmatched_is_neutral_not_penalised(root):
    assert IntentGraph.load(root).score_10("dentist appointment") == NEUTRAL


def test_spotlight_keywords_do_not_match_the_container(root):
    """Regression: matching spotlight keywords against the space directory
    scored every item in that space at 10.0 and flattened the ordering."""
    g = IntentGraph.load(root)
    assert g.score_10("", container="9-nightshift") == NEUTRAL


def test_token_boundaries(root):
    g = IntentGraph.load(root)
    assert g.score_10("nightshifts-are-over") == NEUTRAL


def test_spotlight_off_graph_is_reported(root):
    """A priority that serves no stated goal is the finding, not a silent 0."""
    (root / ".datacore" / "cos" / "priorities.yaml").write_text(
        "priorities:\n  - rank: 1\n    priority: PLUR Enterprise\n"
        "    keywords: [enterprise]\n")
    kinds = {x["kind"] for x in IntentGraph.load(root).gaps()}
    assert "spotlight_off_graph" in kinds


def test_frontier_gap_needs_task_data(root):
    """Without a task index the coverage checks are SKIPPED, never guessed."""
    g = IntentGraph.load(root)
    assert not [x for x in g.gaps() if x["kind"] == "frontier_no_work"]
    gaps = g.gaps(task_index={"evaluator": 2})
    ids = {x["id"] for x in gaps if x["kind"] == "frontier_no_work"}
    assert "extractor" in ids and "evaluator" not in ids


def test_missing_graph_degrades_quietly(tmp_path):
    g = IntentGraph.load(tmp_path)
    assert g.nodes == {}
    assert g.score_10("anything") == NEUTRAL


def test_bands_ordered():
    assert SPOTLIT > HIGH_LEVERAGE > ON_GRAPH > NEUTRAL
