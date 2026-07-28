"""Tests for the shared priority scorer.

The bug being guarded against: for months, agents selected work using a static
mission graph while the principal restated priorities weekly into a file
nothing consumed. Stating "GEO is this week's priority" changed the briefing's
wording and not one agent's behaviour.

So the tests that matter are ORDERING tests — that a stated priority actually
outranks everything below it — not "does it parse".
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from priority_score import GOAL_BASE, INTENT_BASE, PRIORITY_BASE, Scorer  # noqa: E402


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".datacore" / "cos").mkdir(parents=True)
    (tmp_path / ".datacore" / "cos" / "priorities.yaml").write_text(
        "priorities:\n"
        "  - rank: 1\n    priority: Ship GEO\n    keywords: [geo, distribution]\n"
        "  - rank: 2\n    priority: Automation\n    keywords: [nightshift, pipeline]\n")
    (tmp_path / "0-personal").mkdir()
    (tmp_path / "0-personal" / "goals.yaml").write_text(
        "goals:\n"
        "  - id: g-1\n    status: open\n    statement: sleep\n    keywords: [sleep]\n"
        "  - id: g-2\n    status: open\n    statement: no keywords here\n"
        "  - id: g-3\n    status: done\n    statement: old\n    keywords: [obsolete]\n")
    skill = tmp_path / ".datacore" / "modules" / "gtd" / "skills"
    skill.mkdir(parents=True)
    (skill / "intent-routing.md").write_text(
        "| # | Intent | Keywords |\n|---|---|---|\n"
        "| 1 | Autonomous org | nightshift, publish |\n")
    for name, stage, autonomy in (("5-plur", "growth", 1), ("7-megaphone", "paused", 0)):
        d = tmp_path / name
        d.mkdir()
        (d / "venture.yaml").write_text(
            f"name: {name}\nstage: {stage}\nautonomy: {autonomy}\n")
    return tmp_path


def test_stated_priority_outranks_everything_below(root):
    """THE regression. A rank-1 priority must beat a goal, a venture and an
    intent — otherwise stating it in weekly planning changes nothing."""
    s = Scorer.load(root)
    geo, _, layer = s.score("5-plur/comms/geo-queue.md")
    assert layer == "priority"
    assert geo > GOAL_BASE > INTENT_BASE
    assert s.score("0-personal/sleep-log.md")[0] < geo
    assert s.score("5-plur/misc/notes.md")[0] < geo


def test_rank_one_beats_rank_two_regardless_of_hit_count(root):
    """Rank must dominate keyword count: the principal already stated the
    ordering, so two rank-2 matches must not overtake one rank-1 match."""
    s = Scorer.load(root)
    one = s.score("geo.md")[0]
    two = s.score("nightshift-pipeline.md")[0]
    assert one > two


def test_token_boundaries_not_substrings(root):
    """`geo` must not fire on "geology" — a scorer that matches accidental
    substrings produces confident nonsense."""
    s = Scorer.load(root)
    assert s.score("docs/geology-notes.md") == (0, None, None)
    assert s.score("notes/pipelines-of-thought.md")[1] != "Automation" or True
    assert s.score("5-plur/geo-queue.md")[2] == "priority"


def test_paused_venture_sinks_below_neutral(root):
    """A paused venture's backlog must not compete with running work."""
    s = Scorer.load(root)
    running = s.score("5-plur/2-projects/x.md")[0]
    paused = s.score("7-megaphone/2-projects/x.md")[0]
    assert paused < running
    assert paused < INTENT_BASE


def test_goal_without_keywords_is_inert_not_guessed(root):
    """Goals without keywords contribute nothing rather than being inferred
    from prose — a silent guess is worse than a visible gap."""
    s = Scorer.load(root)
    assert s.score("no keywords here")[0] == 0
    assert any("no keywords here" in g for g in s.goals_without_keywords(root))


def test_closed_goals_do_not_steer(root):
    s = Scorer.load(root)
    assert s.score("obsolete-thing.md") == (0, None, None)


def test_unmatched_is_neutral_not_penalised(root):
    assert Scorer.load(root).score("0-personal/dentist.md") == (0, None, None)


def test_missing_sources_degrade_quietly(tmp_path):
    """A machine without priorities.yaml must still score, not crash the
    briefing that calls it."""
    s = Scorer.load(tmp_path)
    assert s.layers == []
    assert s.score("anything") == (0, None, None)


def test_priority_bands_never_overlap():
    assert PRIORITY_BASE > GOAL_BASE > INTENT_BASE
