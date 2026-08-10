"""Tests for briefing.fact_table -- the fact table every grounded briefing
number must trace to.

`build_facts` runs a list of adapters (default: `git_status_counts` +
`ledger_item_counts`) against one `root`/`space_dir` and merges their
`Fact` dicts. Adapters are isolated from each other: one adapter raising
never aborts the others, it just adds itself to a `_meta.adapter_errors`
fact. A duplicate fact id produced by two DIFFERENT adapters is a config
bug, not a runtime surprise, so that one case DOES raise (`FactError`).

`Fact.value` is always `str` -- `write_facts`/`emit_facts`/the renderer
treat it as an opaque string to substitute verbatim, never as a number to
re-format.

`now` is injected everywhere in these tests (a fixed epoch, `NOW`) so
nothing here depends on the real wall clock, matching the pattern used by
`jobs.checks` and `ledger.log` tests.
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from briefing.fact_table import (
    AdapterCtx,
    Fact,
    FactError,
    build_facts,
    emit_facts,
    git_status_counts,
    ledger_item_counts,
    write_facts,
)
from ledger.log import EventLog, read_events

# Fixed instant used everywhere `now` is injected -- arbitrary but stable,
# so `computed_at` strings are reproducible across runs/timezones.
NOW = 1_750_000_000.0
EXPECTED_ISO = datetime.fromtimestamp(NOW, tz=timezone.utc).isoformat()


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _mk_log(tmp_path: Path, space: Path, actor: str) -> EventLog:
    return EventLog(
        space,
        actor,
        keys_dir=tmp_path / "keys",
        registry_path=tmp_path / "registry.yaml",
        sign=False,
    )


# --- Fact / AdapterCtx shapes -----------------------------------------------


def test_fact_shape_and_value_is_str():
    fact = Fact(id="a.b", value="3", unit="count", source="stub", computed_at=EXPECTED_ISO)

    assert fact.id == "a.b"
    assert fact.value == "3"
    assert isinstance(fact.value, str)
    assert fact.unit == "count"
    assert fact.source == "stub"
    assert fact.computed_at == EXPECTED_ISO


def test_adapter_ctx_shape(tmp_path):
    ctx = AdapterCtx(root=tmp_path, now=NOW)

    assert ctx.root == tmp_path
    assert ctx.now == NOW


# --- build_facts: merge, duplicate, error isolation -------------------------


def test_build_facts_merges_two_stub_adapters(tmp_path):
    def adapter_one(ctx: AdapterCtx) -> dict[str, Fact]:
        return {
            "one.count": Fact(
                id="one.count", value="1", unit="count", source="adapter_one", computed_at=EXPECTED_ISO
            )
        }

    def adapter_two(ctx: AdapterCtx) -> dict[str, Fact]:
        return {
            "two.count": Fact(
                id="two.count", value="2", unit="count", source="adapter_two", computed_at=EXPECTED_ISO
            )
        }

    facts = build_facts(tmp_path, adapters=[adapter_one, adapter_two], now=NOW)

    assert set(facts) == {"one.count", "two.count"}
    assert facts["one.count"].value == "1"
    assert facts["two.count"].value == "2"


def test_build_facts_duplicate_id_across_adapters_raises_naming_both(tmp_path):
    def adapter_one(ctx: AdapterCtx) -> dict[str, Fact]:
        return {"dup.id": Fact(id="dup.id", value="1", unit="count", source="adapter_one", computed_at=EXPECTED_ISO)}

    def adapter_two(ctx: AdapterCtx) -> dict[str, Fact]:
        return {"dup.id": Fact(id="dup.id", value="2", unit="count", source="adapter_two", computed_at=EXPECTED_ISO)}

    with pytest.raises(FactError) as exc_info:
        build_facts(tmp_path, adapters=[adapter_one, adapter_two], now=NOW)

    message = str(exc_info.value)
    assert "adapter_one" in message
    assert "adapter_two" in message
    assert "dup.id" in message


def test_build_facts_raising_adapter_becomes_meta_fact_other_facts_intact(tmp_path):
    def bad_adapter(ctx: AdapterCtx) -> dict[str, Fact]:
        raise RuntimeError("boom")

    def good_adapter(ctx: AdapterCtx) -> dict[str, Fact]:
        return {
            "good.count": Fact(
                id="good.count", value="7", unit="count", source="good_adapter", computed_at=EXPECTED_ISO
            )
        }

    facts = build_facts(tmp_path, adapters=[bad_adapter, good_adapter], now=NOW)

    assert facts["good.count"].value == "7"
    meta = facts["_meta.adapter_errors"]
    assert meta.value == "bad_adapter"
    assert meta.source == "build_facts"
    assert meta.computed_at == EXPECTED_ISO


def test_build_facts_multiple_raising_adapters_comma_joins_names(tmp_path):
    def bad_one(ctx: AdapterCtx) -> dict[str, Fact]:
        raise RuntimeError("boom one")

    def bad_two(ctx: AdapterCtx) -> dict[str, Fact]:
        raise ValueError("boom two")

    facts = build_facts(tmp_path, adapters=[bad_one, bad_two], now=NOW)

    assert facts["_meta.adapter_errors"].value == "bad_one,bad_two"


def test_build_facts_adapter_returning_non_dict_is_isolated_not_a_crash(tmp_path):
    """An adapter that violates its own `dict[str, Fact]` contract (e.g.
    returns `None`) must be isolated the same as one that raises -- the
    isolation guarantee covers misbehavior, not only explicit exceptions.
    """

    def malformed_adapter(ctx: AdapterCtx) -> dict[str, Fact]:
        return None  # type: ignore[return-value]

    def good_adapter(ctx: AdapterCtx) -> dict[str, Fact]:
        return {
            "good.count": Fact(
                id="good.count", value="9", unit="count", source="good_adapter", computed_at=EXPECTED_ISO
            )
        }

    facts = build_facts(tmp_path, adapters=[malformed_adapter, good_adapter], now=NOW)

    assert facts["good.count"].value == "9"
    assert facts["_meta.adapter_errors"].value == "malformed_adapter"


def test_build_facts_default_adapters_on_empty_dir_produce_no_facts(tmp_path):
    # No git repo, no ledger events dir under tmp_path: both default
    # adapters (git_status_counts, ledger_item_counts) return {} honestly,
    # neither raises, so there's no _meta.adapter_errors fact either.
    facts = build_facts(tmp_path, now=NOW)

    assert facts == {}


def test_build_facts_now_defaults_to_current_time_when_not_injected(tmp_path):
    captured: dict[str, float] = {}

    def capture_adapter(ctx: AdapterCtx) -> dict[str, Fact]:
        captured["now"] = ctx.now
        return {}

    before = time.time()
    build_facts(tmp_path, adapters=[capture_adapter])
    after = time.time()

    assert before <= captured["now"] <= after


# --- git_status_counts -------------------------------------------------------


def test_git_status_counts_real_repo_dirty_file_and_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["symbolic-ref", "HEAD", "refs/heads/feat-test"], repo)
    (repo / "dirty.txt").write_text("hello")

    facts = git_status_counts(AdapterCtx(root=repo, now=NOW))

    assert facts["git.status.dirty_count"].value == "1"
    assert facts["git.status.dirty_count"].source == "git_status_counts"
    assert facts["git.status.dirty_count"].computed_at == EXPECTED_ISO
    assert facts["git.branch"].value == "feat-test"
    assert facts["git.branch"].source == "git_status_counts"


def test_git_status_counts_non_repo_dir_returns_empty(tmp_path):
    non_repo = tmp_path / "not-a-repo"
    non_repo.mkdir()

    facts = git_status_counts(AdapterCtx(root=non_repo, now=NOW))

    assert facts == {}


def test_git_status_counts_missing_dir_returns_empty(tmp_path):
    missing = tmp_path / "does-not-exist"

    facts = git_status_counts(AdapterCtx(root=missing, now=NOW))

    assert facts == {}


# --- ledger_item_counts -------------------------------------------------------


def test_ledger_item_counts_missing_events_dir_returns_empty(tmp_path):
    space = tmp_path / "space"
    space.mkdir()

    facts = ledger_item_counts(AdapterCtx(root=space, now=NOW))

    assert facts == {}


def test_ledger_item_counts_mixed_statuses(tmp_path):
    space = tmp_path / "space"
    log = _mk_log(tmp_path, space, "actor1")

    log.append("item.create", {"id": "t1", "title": "One"})
    log.append("item.claim", {"id": "t1"})
    log.append("item.complete", {"id": "t1"})

    log.append("item.create", {"id": "t2", "title": "Two"})
    log.append("item.claim", {"id": "t2"})

    log.append("item.create", {"id": "t3", "title": "Three"})

    facts = ledger_item_counts(AdapterCtx(root=space, now=NOW))

    assert facts["items.total"].value == "3"
    assert facts["items.by_status.completed"].value == "1"
    assert facts["items.by_status.claimed"].value == "1"
    assert facts["items.by_status.created"].value == "1"
    assert "items.by_status.verified" not in facts
    assert "items.by_status.dismissed" not in facts
    for fact in facts.values():
        assert fact.source == "ledger_item_counts"
        assert fact.computed_at == EXPECTED_ISO


# --- write_facts --------------------------------------------------------------


def test_write_facts_json_round_trip(tmp_path):
    facts = {
        "b.name": Fact(id="b.name", value="main", unit="name", source="stub", computed_at=EXPECTED_ISO),
        "a.count": Fact(id="a.count", value="3", unit="count", source="stub", computed_at=EXPECTED_ISO),
    }
    path = tmp_path / "facts.json"

    write_facts(facts, path)

    text = path.read_text()
    assert text.endswith("\n")
    data = json.loads(text)
    assert data == {
        "a.count": {"value": "3", "unit": "count", "source": "stub", "computed_at": EXPECTED_ISO},
        "b.name": {"value": "main", "unit": "name", "source": "stub", "computed_at": EXPECTED_ISO},
    }
    # sorted keys -- fact ids come out alphabetically regardless of dict
    # insertion order above ("b.name" was inserted first).
    assert list(data.keys()) == ["a.count", "b.name"]


# --- emit_facts -----------------------------------------------------------------


def test_emit_facts_writes_one_metric_attest_per_fact_readable_via_read_events(tmp_path, monkeypatch):
    monkeypatch.delenv("DATACORE_LEDGER_SIGN", raising=False)
    space = tmp_path / "space"
    facts = {
        "a.count": Fact(id="a.count", value="3", unit="count", source="stub_a", computed_at=EXPECTED_ISO),
        "b.count": Fact(id="b.count", value="5", unit="count", source="stub_b", computed_at=EXPECTED_ISO),
    }

    emitted = emit_facts(facts, space, "actor1")

    assert emitted == 2
    events = [e for e in read_events(space) if e.type == "metric.attest"]
    assert len(events) == 2
    payloads = {e.payload["id"]: e.payload for e in events}
    assert payloads["a.count"] == {
        "metric": "fact",
        "id": "a.count",
        "value": "3",
        "unit": "count",
        "source": "stub_a",
    }
    assert payloads["b.count"] == {
        "metric": "fact",
        "id": "b.count",
        "value": "5",
        "unit": "count",
        "source": "stub_b",
    }


def test_emit_facts_empty_dict_emits_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("DATACORE_LEDGER_SIGN", raising=False)
    space = tmp_path / "space"

    emitted = emit_facts({}, space, "actor1")

    assert emitted == 0
