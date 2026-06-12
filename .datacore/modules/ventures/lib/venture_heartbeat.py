#!/usr/bin/env python3
"""Venture Heartbeat — autonomous 24/7 venture operator.

Runs every 15 minutes. For each active venture:
1. SENSE: read venture state (zero LLM cost — just files)
2. DECIDE: if attention needed, wake the role agent (one LLM call)
3. ACT: agent decides what to do and does it
4. LEARN: update state, log results

The agent doesn't just check cadences — it reads its full role context,
venture state, hypotheses, and signals, then decides the highest-value
action. Cadences are the floor (minimum operations), but the agent
can identify its own work: run experiments, follow up on results,
notice patterns, and act on opportunities.

Architecture:
- Heartbeat = awareness + execution (continuous, 24/7)
- Nightshift = human-scheduled heavy tasks (overnight batch)
- These are separate systems with different purposes.

Usage:
    # Single tick (for systemd timer)
    python3 venture_heartbeat.py --once

    # Continuous loop (for development)
    python3 venture_heartbeat.py --interval=900

    # Specific venture only
    python3 venture_heartbeat.py --once --venture=forge
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

# Ensure module imports work
_module_root = Path(__file__).resolve().parent.parent
if str(_module_root.parent) not in sys.path:
    sys.path.insert(0, str(_module_root.parent))


# ---------------------------------------------------------------------------
# Phase 1: SENSE — zero LLM cost, just read files
# ---------------------------------------------------------------------------

def _load_seen_issues(state_path: Path) -> set:
    """Load set of issue keys we've already seen (org/repo#number)."""
    if state_path.exists():
        return set(state_path.read_text().strip().splitlines())
    return set()


def _save_seen_issues(state_path: Path, seen: set):
    """Persist seen issues. Keep last 500 to avoid unbounded growth."""
    trimmed = sorted(seen)[-500:]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("\n".join(trimmed) + "\n")


def scan_github_issues(github_config: dict, space_dir: Path) -> list:
    """Scan venture's GitHub repos for new open issues.

    Returns list of issue dicts for issues not yet seen by the heartbeat.
    Zero LLM cost — just `gh` CLI calls.
    """
    org = github_config.get("org")
    repos = github_config.get("repos", [])
    if not org or not repos:
        return []

    state_path = space_dir / ".datacore" / "state" / "venture" / "seen-issues.txt"
    seen = _load_seen_issues(state_path)
    new_issues = []

    for repo_entry in repos:
        repo_name = repo_entry if isinstance(repo_entry, str) else repo_entry.get("name", "")
        if not repo_name:
            continue

        full_repo = f"{org}/{repo_name}"
        try:
            result = subprocess.run(
                ["gh", "issue", "list", "--repo", full_repo, "--state", "open",
                 "--json", "number,title,labels,createdAt,url", "--limit", "20"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                continue

            issues = json.loads(result.stdout or "[]")
            for issue in issues:
                key = f"{full_repo}#{issue['number']}"
                if key in seen:
                    continue
                # New issue — flag it
                label_names = [l.get("name", "") for l in issue.get("labels", [])]
                new_issues.append({
                    "key": key,
                    "repo": full_repo,
                    "number": issue["number"],
                    "title": issue["title"],
                    "url": issue.get("url", ""),
                    "labels": label_names,
                    "critical": any(l in ("bug", "critical", "production", "urgent")
                                    for l in label_names),
                })
                seen.add(key)
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            continue

    # Persist updated seen set
    if new_issues:
        _save_seen_issues(state_path, seen)

    return new_issues


def sense_venture(space_dir: Path, today: date = None) -> dict:
    """Read venture state and determine if attention is needed.

    Returns a state dict with all context the agent needs to decide.
    Zero LLM cost — only reads YAML/org files.
    """
    import yaml
    from ventures.lib.cadence_engine import (
        cadence_log_path_for,
        collect_active_cadences,
        find_overdue_cadences,
        load_cadence_log_safe,
    )
    from ventures.lib.budget_tracker import load_ledger, get_remaining

    if today is None:
        today = date.today()

    venture_file = space_dir / "venture.yaml"
    with open(venture_file) as f:
        raw = yaml.safe_load(f)

    name = raw.get("name", space_dir.name)
    stage = raw.get("stage", "unknown")
    autonomy = raw.get("autonomy", 0)

    # Budget check
    budget_raw = raw.get("budget", {})
    ceiling = budget_raw.get("ceiling", 0)
    ledger_path = space_dir / budget_raw.get("ledger", ".datacore/state/venture/budget-ledger.yaml")
    ledger = load_ledger(ledger_path, current_month=today.strftime("%Y-%m"))
    remaining = get_remaining(ledger, ceiling, budget_raw.get("ai_tokens", 0), budget_raw.get("real_spend", 0))

    if remaining.get("ai", 0) <= 0:
        return {"needs_attention": False, "reason": "budget_exhausted", "venture": name}

    # Overdue cadences. Path resolution handles legacy-log migration (A8);
    # the safe loader quarantines corrupt YAML instead of raising (A3 —
    # a parse error here used to become a permanent sense_error).
    roles_raw = raw.get("roles", {})
    cadence_log_path = cadence_log_path_for(space_dir)
    cadence_log = load_cadence_log_safe(cadence_log_path)
    overdue = []
    if isinstance(roles_raw, dict):
        overdue = find_overdue_cadences(roles_raw, cadence_log, today)

    # Check for pending tasks (already queued). Only OPEN tasks suppress
    # re-fires — DONE/CANCELLED cadence tasks must not block forever (A4).
    org_file = space_dir / "org" / "next_actions.org"
    pending_venture_tasks = 0
    existing_cadences = set()
    if org_file.exists():
        content = org_file.read_text()
        pending_venture_tasks = content.count(":AI:venture:")
        existing_cadences = collect_active_cadences(content)

    # Filter overdue to truly new (not already queued)
    new_overdue = [c for c in overdue if c.cadence_name not in existing_cadences]

    # Hypotheses status — only trigger for urgent or unchecked hypotheses
    hyp_file = space_dir / "hypotheses.yaml"
    active_hypotheses = 0
    hypothesis_needs_attention = False
    if hyp_file.exists():
        with open(hyp_file) as f:
            hyp_data = yaml.safe_load(f) or {}
        board = hyp_data.get("board", {})
        active_list = board.get("active", [])
        active_hypotheses = len(active_list)

        if active_hypotheses > 0:
            # Check if any hypothesis has deadline within 7 days
            for hyp in active_list:
                deadline_str = hyp.get("deadline") if isinstance(hyp, dict) else None
                if deadline_str:
                    try:
                        deadline = date.fromisoformat(str(deadline_str))
                        if (deadline - today).days <= 7:
                            hypothesis_needs_attention = True
                            break
                    except (ValueError, TypeError):
                        pass

            # Check if hypotheses were already checked today
            if not hypothesis_needs_attention:
                hyp_check_path = (space_dir.parent / ".datacore" / "state"
                                  / "venture" / "hypothesis-check.yaml")
                last_check = None
                if hyp_check_path.exists():
                    with open(hyp_check_path) as f:
                        hyp_checks = yaml.safe_load(f) or {}
                    last_check = hyp_checks.get(name)
                if last_check != today.isoformat():
                    hypothesis_needs_attention = True

    # Signals: GitHub issues, inbox accumulation
    inbox_dir = space_dir / "0-inbox"
    inbox_count = len(list(inbox_dir.glob("*"))) if inbox_dir.exists() else 0

    # GitHub issue scanning — zero LLM cost, just `gh` CLI
    github_issues = scan_github_issues(raw.get("github", {}), space_dir)

    # Decision: does this venture need an agent wake?
    needs_attention = (
        len(new_overdue) > 0 or          # Overdue cadences not yet queued
        hypothesis_needs_attention or     # Hypothesis deadline within 7d or not checked today
        inbox_count > 3 or               # Inbox accumulating
        len(github_issues) > 0           # New GitHub issues to address
    )

    return {
        "needs_attention": needs_attention,
        "venture": name,
        "space": str(space_dir),
        "stage": stage,
        "autonomy": autonomy,
        "budget_remaining": remaining,
        "overdue_cadences": [
            {"role": c.role, "name": c.cadence_name, "frequency": c.frequency, "days_overdue": c.days_overdue}
            for c in new_overdue
        ],
        "pending_venture_tasks": pending_venture_tasks,
        "active_hypotheses": active_hypotheses,
        "inbox_count": inbox_count,
        "github_issues": github_issues,
        "roles": list(roles_raw.keys()) if isinstance(roles_raw, dict) else [],
    }


# ---------------------------------------------------------------------------
# Phase 2+3: WAKE + ACT — invoke Claude with role context
# ---------------------------------------------------------------------------

def build_agent_prompt(state: dict, space_dir: Path) -> str:
    """Build the prompt that wakes the role agent.

    The agent receives its full context and decides what to do.
    """
    venture = state["venture"]
    stage = state["stage"]

    # Load role context files
    role_context = ""
    roles_dir = space_dir / ".datacore" / "roles"
    if roles_dir.exists():
        for role_file in sorted(roles_dir.glob("*.md")):
            role_context += f"\n\n--- Role: {role_file.stem} ---\n"
            role_context += role_file.read_text()

    # Build state summary
    overdue_text = ""
    if state["overdue_cadences"]:
        overdue_text = "Overdue cadences:\n"
        try:
            from ventures.lib.cadence_engine import load_cadence_template
        except ImportError:
            from cadence_engine import load_cadence_template
        for c in state["overdue_cadences"]:
            template = load_cadence_template(c['name'])
            if template:
                overdue_text += f"\n### {c['role']}: {c['name']} ({c['frequency']}, {c['days_overdue']}d overdue)\n\n{template}\n"
            else:
                overdue_text += f"  - {c['role']}: {c['name']} ({c['frequency']}, {c['days_overdue']}d overdue) — no template, use role context\n"

    budget = state["budget_remaining"]
    budget_text = f"Budget remaining: ${budget.get('ai', 0):.0f} AI / ${budget.get('real', 0):.0f} real"

    # GitHub issues section
    github_text = ""
    github_issues = state.get("github_issues", [])
    if github_issues:
        critical = [i for i in github_issues if i.get("critical")]
        normal = [i for i in github_issues if not i.get("critical")]
        github_text = "\n\nNew GitHub issues:\n"
        for i in critical:
            github_text += f"  - **CRITICAL** {i['key']}: {i['title']} ({', '.join(i['labels'])})\n"
        for i in normal:
            github_text += f"  - {i['key']}: {i['title']}\n"

    prompt = f"""You are the autonomous operator for the **{venture}** venture (stage: {stage}).

## Your Role Context
{role_context}

## Current State
{overdue_text}
Active hypotheses: {state['active_hypotheses']}
Pending tasks in queue: {state['pending_venture_tasks']}
Inbox items: {state['inbox_count']}
{budget_text}{github_text}

## Your Job

You are waking up for a heartbeat check. Read your role context and the current state above.

**Decide what is the single highest-value action you can take right now.**

Consider:
1. **Critical GitHub issues** — production bugs and urgent issues take priority over everything
2. Overdue cadences (the minimum operational work that must happen)
3. Active hypotheses that need their next experiment step
4. Non-critical GitHub issues — investigate and fix or propose solutions
5. Patterns you notice in the data — opportunities or risks

**Pick ONE action and do it well.** Use your own judgment about scope — there is no fixed time target. The heartbeat is a monitor-and-dispatch pattern, not a long-running worker. Pick what fits naturally in a single tick.

Classify the action by **response shape**, not time:

- **Inline action** (single-file change, status report, simple comment, reconciliation, a small fix you can ship and commit) — do it, commit, report.
- **Triage / comment** — leave a comment or note that unblocks the next step (links, scope clarification, decision request) and assign/label/tag appropriately.
- **Substantive work that belongs elsewhere** (multi-hour research, large multi-file refactor, anything that's truly nightshift-batch material — deep paper synthesis, full strategy promotion, anything needing isolation + retries + evaluation) — open a `:AI:venture:` tagged sub-task in `org/next_actions.org` with enough context for nightshift to pick it up. For GitHub issues, also comment with the plan and link the sub-task.

The routing rule: heartbeat does what fits a single working session. Nightshift handles work that benefits from isolation, longer budgets, retries, and evaluator review. Both venues are first-class — choose the right one for the work.

For **cadences**: most cadences fit naturally in a tick (status reports, reconciliations, triage). For cadences that imply nightshift-batch work (research, multi-paper evaluation, deep audit), emit a sub-task — the cadence-log gets updated either way. Don't try to clear multiple overdue cadences in one tick; prioritize the highest-value one.

For GitHub issues: use `gh issue view <number> --repo <owner/repo>` to read the full issue, then act per the response-shape rules above.

Then **execute that action**. Do the actual work — don't just plan or describe it.

After executing, report what you did in this format:
```
ACTION: [what you did — one line]
RESULT: [outcome — one line]
CADENCE_COMPLETED: [role.cadence_name if this was a cadence, or "none"]
LEARNING: [what you learned, if anything, or "none"]
NEXT: [what should happen next time, or "none"]
```

**Rules:**
- Pick ONE action, do it well. Use judgment about scope — the heartbeat has a generous ceiling (30 min) but most real ticks finish in 1-5 min. If you genuinely need longer, route it to nightshift via a sub-task instead.
- If nothing needs attention, just reply: HEARTBEAT_OK
- Read files before acting. Don't assume — verify.
- If you need to create content, use the venture's voice and style.
- Stay within the role's budget authority.
- Do NOT write journal entries. Journal logging is handled by the heartbeat system. Just do the work and report in the structured format.
- Working directory: {space_dir}
"""
    return prompt


def wake_agent(state: dict, space_dir: Path, data_dir: Path, dry_run: bool = False) -> dict:
    """Wake the venture agent via Claude CLI. Returns execution result."""
    prompt = build_agent_prompt(state, space_dir)

    if dry_run:
        return {
            "status": "dry_run",
            "venture": state["venture"],
            "prompt_length": len(prompt),
            "would_act": True,
        }

    start_time = time.time()

    try:
        import tempfile
        # Write prompt to temp file (CLI arg has length limits)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(prompt)
            prompt_file = f.name

        # Read prompt from file and pass as arg (Claude CLI expects positional arg, not stdin)
        with open(prompt_file) as f:
            prompt_text = f.read()

        result = subprocess.run(
            ['claude', '-p', '--dangerously-skip-permissions', '--model', 'sonnet', prompt_text],
            cwd=str(space_dir),
            capture_output=True,
            text=True,
            timeout=1800,  # 30 minute timeout (raised from 600 — see ENG-2026-0512 / heartbeat-bound-removal)
            env=os.environ.copy(),
        )

        # Clean up temp file
        try:
            os.unlink(prompt_file)
        except OSError:
            pass

        duration = time.time() - start_time
        output = result.stdout or ""
        stderr = result.stderr or ""

        # Log errors for debugging
        if result.returncode != 0 or (not output and stderr):
            import logging
            logging.warning(f"Claude CLI error for {state['venture']}: rc={result.returncode} stdout_len={len(output)} stderr_len={len(stderr)} stderr={stderr[:500]} stdout={output[:200]}")

        # Parse structured response
        action = ""
        cadence_completed = ""
        learning = ""
        is_idle = "HEARTBEAT_OK" in output

        for line in output.split("\n"):
            if line.startswith("ACTION:"):
                action = line[7:].strip()
            elif line.startswith("CADENCE_COMPLETED:"):
                cadence_completed = line[18:].strip()
            elif line.startswith("LEARNING:"):
                learning = line[9:].strip()

        return {
            "status": "idle" if is_idle else ("ok" if result.returncode == 0 else "error"),
            "venture": state["venture"],
            "action": action,
            "cadence_completed": cadence_completed,
            "learning": learning,
            "duration_seconds": round(duration, 1),
            "output_length": len(output),
            "error": (stderr[:200] if result.returncode != 0 else None) or (stderr[:200] if not output else None),
        }

    except subprocess.TimeoutExpired:
        return {"status": "timeout", "venture": state["venture"], "duration_seconds": 600}
    except FileNotFoundError:
        return {"status": "error", "venture": state["venture"], "error": "claude CLI not found", "duration_seconds": 0}
    except Exception as e:
        return {"status": "error", "venture": state["venture"], "error": str(e)[:200], "duration_seconds": 0}


# ---------------------------------------------------------------------------
# Phase 4: LEARN — update state after execution
# ---------------------------------------------------------------------------

def _sanitize_cadence_key(raw: str) -> list:
    """Validate and sanitize cadence key(s) from agent output.

    Accepts: "role.cadence-name" (lowercase, dots, hyphens only).
    Strips parenthetical comments: "ceo.strategy-review (weekly)" → "ceo.strategy-review".
    Splits comma-separated keys: "ceo.a, cmo.b" → ["ceo.a", "cmo.b"].
    Rejects keys with spaces, "none", or invalid format.

    Returns list of valid cadence keys (may be empty).
    """
    valid_pattern = re.compile(r'^[a-z][a-z0-9]*\.[a-z][a-z0-9-]*$')
    keys = []
    # Split on commas first (agent may report multiple)
    for part in raw.split(","):
        # Strip parenthetical comments: "ceo.review (weekly)" → "ceo.review"
        cleaned = re.sub(r'\s*\(.*?\)', '', part).strip()
        # Skip obvious non-keys
        if not cleaned or 'none' in cleaned.lower():
            continue
        if valid_pattern.match(cleaned):
            keys.append(cleaned)
    return keys


def post_execution(result: dict, space_dir: Path):
    """Update venture state after agent execution."""
    from ventures.lib.cadence_engine import (
        cadence_log_path_for,
        load_cadence_log_safe,
        save_cadence_log,
    )

    today_iso = date.today().isoformat()

    # Update cadence log if a cadence was completed
    cadence_raw = result.get("cadence_completed", "")
    if cadence_raw and cadence_raw.strip().lower() != "none":
        cadence_keys = _sanitize_cadence_key(cadence_raw)
        if cadence_keys:
            cadence_log_path = cadence_log_path_for(space_dir)
            log = load_cadence_log_safe(cadence_log_path)
            for key in cadence_keys:
                log[key] = {
                    "last_run": today_iso,
                    "result": "ok" if result["status"] == "ok" else "failed",
                }
            save_cadence_log(log, cadence_log_path)

    # Append one-line action log to daily heartbeat journal
    action = result.get("action", "")
    if action and result.get("status") not in ("idle", "dry_run"):
        journal_dir = space_dir / "journal"
        journal_dir.mkdir(parents=True, exist_ok=True)
        log_file = journal_dir / f"heartbeat-{today_iso}.log"
        now_hm = datetime.now().strftime("%H:%M")
        cadence_str = cadence_raw.strip() if cadence_raw and cadence_raw.strip().lower() != "none" else "adhoc"
        duration = result.get("duration_seconds", 0)
        with open(log_file, "a") as f:
            f.write(f"{now_hm} {cadence_str} — {action} ({duration}s)\n")

    # Update hypothesis check timestamp (prevents re-waking for hypotheses today)
    import yaml
    hyp_check_path = (space_dir.parent / ".datacore" / "state"
                      / "venture" / "hypothesis-check.yaml")
    hyp_check_path.parent.mkdir(parents=True, exist_ok=True)
    hyp_checks = {}
    if hyp_check_path.exists():
        with open(hyp_check_path) as f:
            hyp_checks = yaml.safe_load(f) or {}
    venture_name = result.get("venture", "")
    if venture_name:
        hyp_checks[venture_name] = today_iso
        with open(hyp_check_path, "w") as f:
            yaml.safe_dump(hyp_checks, f, default_flow_style=False)

    # Store learning via PLUR if available
    learning = result.get("learning", "")
    if learning and learning != "none":
        try:
            # Best effort — PLUR may not be available on server
            subprocess.run(
                ['plur', 'learn', '--statement', learning, '--type', 'behavioral',
                 '--domain', f"ventures.{result.get('venture', 'unknown')}"],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass  # PLUR not available — learnings still in agent output


# ---------------------------------------------------------------------------
# Main heartbeat loop
# ---------------------------------------------------------------------------

def heartbeat_tick(data_dir: Path, venture_filter: str = None, dry_run: bool = False) -> dict:
    """Single heartbeat tick across all active ventures.

    Returns summary dict.
    """
    from ventures.lib.venture_discovery import discover_ventures

    today = date.today()
    ventures = discover_ventures(data_dir, nightshift_only=True)

    if venture_filter:
        ventures = [v for v in ventures if v.config.name == venture_filter or v.space_dir.name == venture_filter]

    if not ventures:
        return {"status": "idle", "reason": "no active ventures", "ticks": []}

    ticks = []

    for vs in ventures:
        # Phase 1: SENSE
        try:
            state = sense_venture(vs.space_dir, today)
        except Exception as e:
            ticks.append({"venture": vs.config.name, "status": "sense_error", "error": str(e)[:100]})
            continue

        if not state["needs_attention"]:
            ticks.append({"venture": vs.config.name, "status": "idle"})
            continue

        # Phase 2+3: WAKE + ACT
        result = wake_agent(state, vs.space_dir, data_dir, dry_run=dry_run)
        ticks.append(result)

        # Phase 4: LEARN
        if result["status"] == "ok" and not dry_run:
            try:
                post_execution(result, vs.space_dir)
            except Exception as e:
                result["post_error"] = str(e)[:100]

    # Log heartbeat
    log_dir = data_dir / ".datacore" / "state" / "venture"
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().isoformat(timespec="seconds")
    active = sum(1 for t in ticks if t.get("status") not in ("idle", "sense_error"))
    idle = sum(1 for t in ticks if t.get("status") == "idle")

    with open(log_dir / "heartbeat.log", "a") as f:
        f.write(f"{timestamp} active={active} idle={idle} ventures={len(ventures)}\n")

    # ---- Self-report layer for the desktop app's Firm Status panel ----
    # Per contract specified by the parallel datacore-app session:
    # write per-venture heartbeat.json + crew miles.json after each tick.
    # Wrapped in try/except — must never break the main heartbeat loop.
    try:
        from datetime import timezone as _tz
        utc_now = datetime.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 6-meridian is monitored separately (trading state out of scope here).
        # Single definition lives in cadence_engine (audit A9).
        from ventures.lib.cadence_engine import (
            HEARTBEAT_SKIP_VENTURES as HEARTBEAT_SKIP,
        )

        for vs in ventures:
            if vs.space_dir.name in HEARTBEAT_SKIP:
                continue
            try:
                tick = next((t for t in ticks if t.get("venture") == vs.config.name), {})
                tick_status = tick.get("status") or "ok"
                if tick_status in ("sense_error", "error", "timeout"):
                    last_status = "error"
                    last_error = (tick.get("error") or tick_status)[:300]
                elif tick_status == "blocked":
                    last_status = "blocked"
                    last_error = tick.get("reason", "blocked")
                else:
                    last_status = "ok"
                    last_error = None

                hb_path = vs.space_dir / ".datacore" / "state" / "heartbeat.json"
                hb_path.parent.mkdir(parents=True, exist_ok=True)
                # Preserve fields written by cadence_runner (next_due, fire counts,
                # decisions_pending) when present — only update what the heartbeat
                # owns: last_fire + last_status + last_error.
                existing = {}
                if hb_path.exists():
                    try:
                        with open(hb_path) as f:
                            existing = json.load(f)
                    except Exception:
                        existing = {}
                payload = {
                    "venture": vs.space_dir.name,
                    "last_fire": utc_now,
                    "last_status": last_status,
                    "last_error": last_error,
                    "next_due": existing.get("next_due"),
                    "cadences_fired_24h": existing.get("cadences_fired_24h", 0),
                    "cadences_overdue": existing.get("cadences_overdue", 0),
                    "decisions_pending": existing.get("decisions_pending", []),
                }
                tmp = hb_path.with_suffix(".json.tmp")
                with open(tmp, "w") as f:
                    json.dump(payload, f, indent=2)
                    f.write("\n")
                tmp.replace(hb_path)
            except Exception:
                pass  # never break main loop on a single venture's write failure

        # Crew self-report: Miles is the heartbeat agent itself
        miles_path = data_dir / ".datacore" / "state" / "agents" / "miles.json"
        miles_path.parent.mkdir(parents=True, exist_ok=True)
        summary = f"Heartbeat tick: {active} active, {idle} idle, {len(ventures)} ventures observed"
        miles_payload = {
            "name": "Miles",
            "last_activity": utc_now,
            "last_status": "ok",
            "last_error": None,
            "last_summary": summary,
        }
        tmp = miles_path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(miles_payload, f, indent=2)
            f.write("\n")
        tmp.replace(miles_path)
    except Exception:
        pass  # self-report layer is always best-effort

    return {
        "timestamp": timestamp,
        "status": "active" if active > 0 else "idle",
        "ventures_checked": len(ventures),
        "ventures_active": active,
        "ventures_idle": idle,
        "ticks": ticks,
    }


def run_loop(data_dir: Path, interval: int = 900, venture_filter: str = None):
    """Continuous heartbeat loop. Production uses systemd timer with --once."""
    print(f"Venture heartbeat starting (interval: {interval}s, ventures: {venture_filter or 'all active'})")
    print(f"Data directory: {data_dir}")
    print()

    while True:
        try:
            result = heartbeat_tick(data_dir, venture_filter)
            ts = datetime.now().strftime('%H:%M:%S')

            if result["status"] == "idle":
                print(f"[{ts}] HEARTBEAT_OK ({result['ventures_checked']} ventures, all idle)")
            else:
                print(f"[{ts}] {result['ventures_active']} ventures active, {result['ventures_idle']} idle")
                for tick in result["ticks"]:
                    if tick.get("status") not in ("idle",):
                        action = tick.get("action", "") or tick.get("error", "")
                        print(f"  {tick['venture']}: {tick['status']} — {action[:100]}")
        except KeyboardInterrupt:
            print("\nHeartbeat stopped.")
            break
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ERROR: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Venture heartbeat — autonomous 24/7 operator")
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "Data")
    parser.add_argument("--interval", type=int, default=900, help="Seconds between ticks (default: 900 = 15min)")
    parser.add_argument("--once", action="store_true", help="Single tick then exit (for systemd timer)")
    parser.add_argument("--venture", type=str, default=None, help="Only this venture")
    parser.add_argument("--dry-run", action="store_true", help="Sense only, don't wake agents")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    # Debug: log key env vars on startup
    print(f"ENV DEBUG: HOME={os.environ.get('HOME', 'UNSET')} ANTHROPIC_API_KEY={'set' if os.environ.get('ANTHROPIC_API_KEY') else 'MISSING'} PATH={os.environ.get('PATH', 'UNSET')[:80]}")

    if args.once:
        result = heartbeat_tick(args.data_dir, args.venture, args.dry_run)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if result["status"] == "idle":
                print("HEARTBEAT_OK")
            else:
                for tick in result["ticks"]:
                    if tick.get("status") not in ("idle",):
                        print(f"{tick['venture']}: {tick.get('action', tick['status'])}")
    else:
        run_loop(args.data_dir, args.interval, args.venture)
