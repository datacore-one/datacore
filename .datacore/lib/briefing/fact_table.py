"""Fact table: deterministic, adapter-built facts every grounded briefing traces to.

The trust model for grounded briefings (Datacore v2 Phase 4) is: every
number a briefing shows must trace to a `Fact` built here, not asserted by
an LLM. `build_facts` runs a list of adapters -- plain functions
`AdapterCtx -> dict[str, Fact]` -- against one `root` directory and merges
their outputs into a single fact dict, which `write_facts` persists to
JSON and `emit_facts` records as ledger events for auditability.

Adapter isolation is the core safety property: one adapter raising must
never abort the others (`_build_one` catches it and folds the adapter's
name into a synthetic `_meta.adapter_errors` fact instead), while a
duplicate fact id produced by two DIFFERENT adapters is treated as a
config bug rather than a runtime surprise -- that DOES raise (`FactError`),
naming both adapters.

Two built-in adapters are provided and form the default list:

- `git_status_counts` -- shells out to `git -C <root> status --porcelain`
  (dirty file count) and `git -C <root> branch --show-current` (branch
  name). ANY subprocess failure (nonzero exit, timeout, git not installed,
  `root` isn't a git repo at all) is treated as "nothing to report", not
  an error: it returns `{}` and never raises. Absence is honest here --
  a missing git fact is not the same claim as "zero dirty files".
- `ledger_item_counts` -- folds `root`'s ledger events
  (`ledger.log.read_events` + `ledger.fold.fold`) into per-status item
  counts. A missing events directory returns `{}` (never ran ledger here,
  as opposed to "ran ledger, found zero items"); an existing-but-empty
  events directory legitimately produces `items.total` = "0" with no
  per-status facts (every status count is zero).

`Fact.value` is ALWAYS `str` -- even for counts (`str(count)`, never the
raw `int`) -- because the renderer that will consume facts substitutes
`value` verbatim into briefing text; it must never need to know or guess
a fact's underlying type.

The only clock read in this module is the single, injectable `now` in
`build_facts` (defaulting to `time.time()` when the caller doesn't pass
one, mirroring `jobs.checks.run_check`'s pattern -- this is the runner
side, not a pure fold). That one `now` is threaded through `AdapterCtx`
to every adapter and into every `Fact.computed_at`, so a whole
`build_facts` call shares one timestamp rather than each adapter reading
the clock independently.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ledger.fold import fold
from ledger.log import EventLog, read_events

_GIT_TIMEOUT_SECONDS = 10


class FactError(ValueError):
    """Raised by `build_facts` when two DIFFERENT adapters produce the same
    fact id. This is a configuration bug (two adapters were never supposed
    to own the same fact id) rather than a runtime surprise an adapter can
    encounter on its own, so unlike adapter exceptions it is NOT swallowed
    into `_meta.adapter_errors` -- it propagates, naming both adapters and
    the colliding id.
    """


@dataclass
class Fact:
    """One deterministically-computed fact. `value` is always `str`."""

    id: str
    value: str
    unit: str
    source: str
    computed_at: str


@dataclass
class AdapterCtx:
    """Context passed to every adapter: the directory to inspect and the
    single `now` (epoch seconds) shared by the whole `build_facts` call.
    """

    root: Path
    now: float


Adapter = Callable[[AdapterCtx], dict[str, Fact]]


def _computed_at(now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).isoformat()


def _adapter_name(adapter: Adapter) -> str:
    return getattr(adapter, "__name__", repr(adapter))


# --- built-in adapters -------------------------------------------------------


def git_status_counts(ctx: AdapterCtx) -> dict[str, Fact]:
    """`git status --porcelain` dirty-file count + current branch name.

    Both subprocess calls happen inside one try/except: any failure from
    either one (nonzero exit -- e.g. `root` is not a git repo -- a
    timeout, or git not being installed at all) means the WHOLE adapter
    returns `{}`, never a partial result and never a raise. Absence is
    honest: no facts is a truthful "couldn't determine this", never a
    fabricated zero.
    """
    try:
        status = subprocess.run(
            ["git", "-C", str(ctx.root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=True,
        )
        branch = subprocess.run(
            ["git", "-C", str(ctx.root), "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return {}

    dirty_count = len([line for line in status.stdout.splitlines() if line.strip()])
    computed_at = _computed_at(ctx.now)

    return {
        "git.status.dirty_count": Fact(
            id="git.status.dirty_count",
            value=str(dirty_count),
            unit="count",
            source="git_status_counts",
            computed_at=computed_at,
        ),
        "git.branch": Fact(
            id="git.branch",
            value=branch.stdout.strip(),
            unit="name",
            source="git_status_counts",
            computed_at=computed_at,
        ),
    }


def ledger_item_counts(ctx: AdapterCtx) -> dict[str, Fact]:
    """Item counts by status, folded from `ctx.root`'s ledger events.

    A missing events directory returns `{}` -- ledger was never consulted,
    which is a different claim from "consulted it, found zero items". An
    existing (possibly empty) events directory always yields at least
    `items.total`; per-status facts (`items.by_status.<status>`) are
    included only for statuses with a nonzero count.
    """
    events_dir = ctx.root / ".datacore" / "events"
    if not events_dir.exists():
        return {}

    state = fold(read_events(ctx.root))
    computed_at = _computed_at(ctx.now)

    counts: dict[str, int] = {}
    for item in state.items.values():
        counts[item.status] = counts.get(item.status, 0) + 1

    facts = {
        "items.total": Fact(
            id="items.total",
            value=str(len(state.items)),
            unit="count",
            source="ledger_item_counts",
            computed_at=computed_at,
        )
    }
    for status, count in counts.items():
        if not count:
            continue
        fact_id = f"items.by_status.{status}"
        facts[fact_id] = Fact(
            id=fact_id,
            value=str(count),
            unit="count",
            source="ledger_item_counts",
            computed_at=computed_at,
        )

    return facts


DEFAULT_ADAPTERS: list[Adapter] = [git_status_counts, ledger_item_counts]


# --- build / write / emit ----------------------------------------------------


def build_facts(
    root: Path,
    adapters: list[Adapter] | None = None,
    now: float | None = None,
) -> dict[str, Fact]:
    """Run `adapters` (default `DEFAULT_ADAPTERS`) against `root` and merge
    their `Fact` dicts into one.

    Each adapter runs in its own try/except: an adapter that raises never
    aborts the others -- its name is collected and, once every adapter has
    run, folded into one synthetic `_meta.adapter_errors` fact (comma-joined
    names, `source="build_facts"`) IF at least one adapter failed. A
    duplicate fact id produced by two DIFFERENT adapters is a config bug,
    not a runtime surprise -- that raises `FactError` naming both adapters
    and the id, rather than being swallowed like an adapter exception.

    `now` defaults to `time.time()` (the only clock read in this module)
    and is shared by every adapter via `AdapterCtx.now` and by the
    `_meta.adapter_errors` fact's own `computed_at`.
    """
    if adapters is None:
        adapters = DEFAULT_ADAPTERS
    if now is None:
        now = time.time()

    ctx = AdapterCtx(root=root, now=now)
    facts: dict[str, Fact] = {}
    fact_owner: dict[str, str] = {}
    errored_adapters: list[str] = []

    for adapter in adapters:
        name = _adapter_name(adapter)
        try:
            # `dict(...)` (not a bare `adapter(ctx)`) so a misbehaving
            # adapter that violates its own return-type contract (e.g.
            # returns `None`) is also isolated here -- the whole point of
            # this try/except is that NOTHING an adapter does can escape
            # build_facts, not just exceptions it explicitly raises.
            adapter_facts = dict(adapter(ctx))
        except Exception:  # noqa: BLE001 -- deliberate: see module docstring
            errored_adapters.append(name)
            continue

        for fact_id, fact in adapter_facts.items():
            existing_owner = fact_owner.get(fact_id)
            if existing_owner is not None:
                raise FactError(
                    f"duplicate fact id {fact_id!r}: produced by both "
                    f"{existing_owner!r} and {name!r}"
                )
            fact_owner[fact_id] = name
            facts[fact_id] = fact

    if errored_adapters:
        facts["_meta.adapter_errors"] = Fact(
            id="_meta.adapter_errors",
            value=",".join(errored_adapters),
            unit="names",
            source="build_facts",
            computed_at=_computed_at(now),
        )

    return facts


def write_facts(facts: dict[str, Fact], path: Path) -> None:
    """Write `facts` to `path` as a JSON dict `{id: {value, unit, source,
    computed_at}}`, sorted keys, trailing newline.
    """
    payload = {
        fact_id: {
            "value": fact.value,
            "unit": fact.unit,
            "source": fact.source,
            "computed_at": fact.computed_at,
        }
        for fact_id, fact in facts.items()
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def emit_facts(facts: dict[str, Fact], space_dir: Path, actor: str) -> int:
    """Emit one `metric.attest` ledger event per fact, unsigned by default
    (`EventLog`'s own default: explicit `sign=` wins, else
    `DATACORE_LEDGER_SIGN=1`, else unsigned). Returns the count emitted.
    """
    log = EventLog(space_dir, actor)
    count = 0
    for fact in facts.values():
        log.append(
            "metric.attest",
            {
                "metric": "fact",
                "id": fact.id,
                "value": fact.value,
                "unit": fact.unit,
                "source": fact.source,
            },
        )
        count += 1
    return count
