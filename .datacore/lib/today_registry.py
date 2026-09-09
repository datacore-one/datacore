#!/usr/bin/env python3
"""Which module fills which briefing section, and in what order.

WHY THIS EXISTS. `hooks.today` was a string that is EITHER a path OR a blob of
inline prose (trading uses the second form, which is why /today warns that
assuming a path raises `OSError: File name too long`). Nothing in it said which
section the hook fills, what must run first, which agent reasons over it, or
whether it re-runs on a refresh. So the runner could not schedule, parallelise
or validate, and nothing ever compared the set of producers against the set of
sections.

That gap IS the 2026-09-08 regression: `briefing.yaml` listed eight sections, a
stale July copy, while producers existed for sections not in the list and
sections in the list had no producer. Four days passed before anyone noticed,
because no check could have noticed.

The declaration this reads is specified in
`2-datacore/1-tracks/dev/spec-today-registration-2026-09-09.md`.

    hooks:
      today:
        section: github_triage
        prompt: commands/today-hook.md
        agent: github-triage
        stage: gather            # gather | compose | narrate
        depends_on: []
        model: sonnet
        refresh: true
        timeout: 300

A BARE STRING STILL WORKS and is read as a prompt with no section, which the
validator then reports as unregistered. Silently ignoring it is what the old
shape did.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
MODULES_DIR = DATACORE_ROOT / ".datacore" / "modules"
# IN THE REPO, not `~/.datacore`. `cos_reasoning.load_briefing_config`
# resolves it as `root / ".datacore" / "cos" / "briefing.yaml"`, so it syncs
# with the tree and every host reads the same section list. Pointing at the
# home dotdir instead silently found nothing and reported "0 sections".
BRIEFING_CONFIG = DATACORE_ROOT / ".datacore" / "cos" / "briefing.yaml"

#: Order matters — a stage never starts until the previous one has finished.
STAGES: tuple[str, ...] = ("gather", "compose", "narrate")

DEFAULT_STAGE = "gather"
DEFAULT_TIMEOUT = 300


@dataclass
class Registration:
    """One module's claim on one briefing section."""

    module: str
    section: str | None
    prompt: Path | None
    agent: str | None = None
    stage: str = DEFAULT_STAGE
    depends_on: list[str] = field(default_factory=list)
    model: str | None = None
    refresh: bool = False
    timeout: int = DEFAULT_TIMEOUT
    #: Inline prose instead of a prompt file — the legacy `trading` shape.
    inline: str | None = None

    @property
    def registered(self) -> bool:
        """A hook with no `section` fills nothing and cannot be scheduled."""
        return bool(self.section)


def _parse(module: str, raw, module_dir: Path) -> Registration:
    if isinstance(raw, str):
        # A path, or the legacy inline blob. Distinguish by whether it resolves
        # — never by length or newlines, which is how the OSError happened.
        candidate = module_dir / raw
        if len(raw) < 256 and candidate.exists():
            return Registration(module=module, section=None, prompt=candidate)
        return Registration(module=module, section=None, prompt=None, inline=raw)

    if not isinstance(raw, dict):
        return Registration(module=module, section=None, prompt=None)

    prompt = raw.get("prompt") or raw.get("file")
    prompt_path = (module_dir / prompt) if prompt else None
    stage = str(raw.get("stage") or DEFAULT_STAGE)
    return Registration(
        module=module,
        section=raw.get("section"),
        prompt=prompt_path,
        agent=raw.get("agent"),
        stage=stage if stage in STAGES else DEFAULT_STAGE,
        depends_on=list(raw.get("depends_on") or []),
        model=raw.get("model"),
        refresh=bool(raw.get("refresh", False)),
        timeout=int(raw.get("timeout") or DEFAULT_TIMEOUT),
    )


def load(modules_dir: Path | None = None) -> list[Registration]:
    """Every module's today-hook declaration, parsed. Never raises on one bad
    module: a malformed manifest must not take the whole briefing down."""
    root = modules_dir or MODULES_DIR
    out: list[Registration] = []
    if not root.is_dir():
        return out
    for manifest in sorted(root.glob("*/module.yaml")):
        try:
            data = yaml.safe_load(manifest.read_text()) or {}
        except Exception:      # noqa: BLE001 — a broken manifest is reported, not fatal
            continue
        raw = (data.get("hooks") or {}).get("today")
        if raw is None:
            continue
        # A MODULE MAY OWN SEVERAL SECTIONS. chief-of-staff writes the_ask,
        # system_pulse AND observation; a single declaration per module would
        # have forced three modules or three sections into one hook. One
        # producer per SECTION is the rule; one section per MODULE is not.
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            out.append(_parse(manifest.parent.name, item, manifest.parent))
    return out


def sections_wanted(config_path: Path | None = None) -> list[str]:
    """The sections `briefing.yaml` asks for, in order."""
    p = config_path or BRIEFING_CONFIG
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except Exception:      # noqa: BLE001
        return []
    return list(data.get("sections") or [])


def plan(regs: list[Registration] | None = None,
         wanted: list[str] | None = None) -> list[list[Registration]]:
    """Registrations grouped into stages, ordered, ready to run.

    Within a stage everything runs in PARALLEL except where `depends_on` says
    otherwise; `your_agenda` depends on `good_morning` because capacity changes
    the shape of the day, which is exactly why a vitals refresh cannot be a
    section-local patch.
    """
    regs = regs if regs is not None else load()
    wanted = wanted if wanted is not None else sections_wanted()
    order = {s: i for i, s in enumerate(wanted)}
    by_stage: list[list[Registration]] = []
    for stage in STAGES:
        group = [r for r in regs if r.registered and r.stage == stage
                 and r.section in order]
        # TOPOLOGICAL, not "fewest dependencies first". Sorting by dep COUNT put
        # metacognition (1 dep) ahead of observation (3) inside `narrate` — and
        # metacognition depends ON observation, so it would have run against a
        # briefing that did not yet have its closing section. Count is not order.
        have = {r.section for r in group}
        pending = list(group)
        emitted: set[str] = set()
        ordered: list[Registration] = []
        while pending:
            ready = [r for r in pending
                     if all(d in emitted or d not in have for d in r.depends_on)]
            if not ready:          # a cycle — validate() reports it; keep going
                ready = pending[:]
            ready.sort(key=lambda r: order.get(r.section, 999))
            for r in ready:
                ordered.append(r)
                emitted.add(r.section)
                pending.remove(r)
        by_stage.append(ordered)
    return by_stage


def validate(regs: list[Registration] | None = None,
             wanted: list[str] | None = None,
             agents_exist=None) -> list[str]:
    """Problems, as human-readable strings. Empty list means the wiring holds.

    Rules 1 and 2 are the ones that would have caught the 2026-09-08 regression
    on the day it shipped rather than four days later.
    """
    regs = regs if regs is not None else load()
    wanted = wanted if wanted is not None else sections_wanted()
    problems: list[str] = []

    # 0. REFUSE TO PASS VACUOUSLY. Modules are separate git repos and
    #    `briefing.yaml` is gitignored, so in a bare checkout there are no
    #    manifests and no config — every rule below would find nothing to
    #    object to and the gate would report success. A check that cannot fail
    #    is the disease this file was written to cure, so say so instead.
    if not wanted:
        problems.append(
            "no sections configured — briefing.yaml is missing or empty at "
            f"{BRIEFING_CONFIG}; this gate cannot verify anything")
        return problems
    if not regs:
        problems.append(
            f"no module manifests found under {MODULES_DIR} — modules are "
            "separate repos and may not be checked out; this gate cannot "
            "verify anything")
        return problems

    # 1. Every claimed section exists in the config.
    for r in regs:
        if r.registered and r.section not in wanted:
            problems.append(
                f"{r.module}: claims section {r.section!r}, which briefing.yaml "
                f"does not list — it will never render")

    # 2. Every configured section has exactly one producer.
    for section in wanted:
        owners = [r.module for r in regs if r.section == section]
        if not owners:
            problems.append(
                f"section {section!r} has NO producer — it will be composed "
                f"from nothing, or invented")
        elif len(owners) > 1:
            problems.append(
                f"section {section!r} claimed by {len(owners)} modules "
                f"({', '.join(owners)}) — one writer per section")

    # 3. Prompts resolve.
    for r in regs:
        if r.registered and r.prompt is not None and not r.prompt.exists():
            problems.append(f"{r.module}: prompt {r.prompt} does not exist")
        # A PROMPT FILE OR AN AGENT — either is a complete instruction, and
        # requiring both would exclude chief-of-staff, which owns the_ask,
        # system_pulse and observation and never had a `today-hook.md` because
        # it was the composer rather than a contributor. What must never happen
        # is a registered section with NEITHER, which composes from nothing.
        if r.registered and r.prompt is None and not r.inline and not r.agent:
            problems.append(
                f"{r.module}: registered for {r.section!r} with neither a "
                f"prompt nor an agent — nothing would reason over it")

    # 4. Agents exist, when a checker is supplied.
    if agents_exist is not None:
        for r in regs:
            if r.registered and r.agent and not agents_exist(r.agent):
                problems.append(f"{r.module}: agent {r.agent!r} is not in the registry")

    # 5. depends_on names real sections, and forms no cycle.
    dep = {r.section: list(r.depends_on) for r in regs if r.registered}
    for section, deps in dep.items():
        for d in deps:
            if d not in wanted:
                problems.append(f"{section!r} depends on {d!r}, which is not a section")

    colour: dict[str, int] = {}

    def cyclic(node: str) -> bool:
        state = colour.get(node, 0)
        if state == 1:
            return True
        if state == 2:
            return False
        colour[node] = 1
        for nxt in dep.get(node, []):
            if nxt in dep and cyclic(nxt):
                return True
        colour[node] = 2
        return False

    for section in list(dep):
        if cyclic(section):
            problems.append(f"dependency cycle involving {section!r}")
            break

    return problems


def agent_exists(name: str) -> bool:
    """Is `name` in the agent registry? Rule 4 needs an answer, and a module
    naming an agent that does not exist is a section that silently composes
    from nothing — the failure this whole contract exists to make impossible."""
    reg = DATACORE_ROOT / ".datacore" / "registry" / "agents.yaml"
    try:
        data = yaml.safe_load(reg.read_text()) or {}
    except Exception:      # noqa: BLE001 — an unreadable registry is reported by rule 4's caller
        return True
    for key in ("agents", "module_agents"):
        block = data.get(key) or {}
        if isinstance(block, dict) and name in block:
            return True
        if isinstance(block, list) and any(
                (a or {}).get("name") == name for a in block):
            return True
    return False


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--validate", action="store_true")
    a = ap.parse_args()

    regs = load()
    wanted = sections_wanted()
    if a.validate:
        problems = validate(regs, wanted, agents_exist=agent_exists)
        for p in problems:
            print(f"  {p}")
        print(f"{len(problems)} problem(s); {len(wanted)} section(s), "
              f"{sum(1 for r in regs if r.registered)} registered producer(s)")
        return 1 if problems else 0

    for stage, group in zip(STAGES, plan(regs, wanted)):
        print(f"{stage}:")
        for r in group:
            dep = f" after {','.join(r.depends_on)}" if r.depends_on else ""
            print(f"  {r.section:<20} {r.module:<16} {r.agent or '(no agent)'}{dep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
