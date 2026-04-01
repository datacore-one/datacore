#!/usr/bin/env python3
"""Active memory injection — called by PreToolUse/SubagentStart/PostCompact hooks.

Events:
  --event plan_mode   Full injection for planning (EnterPlanMode)
  --event skill       Domain injection by skill name (Skill tool)
  --event agent       Agent-scoped injection (Agent tool)
  --event subagent    Subagent context injection (SubagentStart)
  --event rehydrate   Re-inject all active engrams (PostCompact)

Input: JSON on stdin (Claude Code hook input)
Output: JSON on stdout with {additionalContext} or empty (exit 0)

Uses PLUR CLI (npx @plur-ai/cli inject) for engram selection.
"""
import argparse, json, sys, os, subprocess
from pathlib import Path

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
sys.path.insert(0, str(DATACORE_ROOT / ".datacore" / "lib"))
from session_state import read_session, _debug


def plur_inject(task_desc, limit=15):
    """Call PLUR CLI inject and return formatted context, or None."""
    if not task_desc or not task_desc.strip():
        return None
    try:
        result = subprocess.run(
            ['npx', '@plur-ai/cli', 'inject', task_desc, '--json'],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            _debug(f"plur_inject failed: {result.stderr.strip()}")
            return None

        data = json.loads(result.stdout)
        count = data.get('count', 0)
        if count == 0:
            return None

        parts = []
        for key in ('directives', 'constraints', 'consider'):
            text = data.get(key, '')
            if text:
                parts.append(text)
        context = '\n'.join(parts)
        return (context, count) if context else None
    except Exception as e:
        _debug(f"plur_inject error: {e}")
        return None


def get_stdin_json():
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return {}


def handle_plan_mode(input_data):
    """Full engram injection for planning — architecture, patterns, decisions."""
    session = read_session()
    task_desc = "architecture planning design patterns decisions"
    if session and session.get("first_prompt"):
        task_desc = f"{session['first_prompt']} {task_desc}"

    result = plur_inject(task_desc, limit=20)
    if not result:
        return None
    context, count = result
    _debug(f"plan_mode: injected {count} engrams")
    return f"[Datacore Active Memory — plan mode]\n\n{context}"


def handle_skill(input_data):
    """Domain injection based on skill name."""
    tool_input = input_data.get("tool_input", {})
    skill_name = tool_input.get("skill", "")
    if not skill_name:
        return None

    domain = skill_name.split(":")[0] if ":" in skill_name else skill_name
    result = plur_inject(domain)
    if not result:
        return None
    context, count = result
    _debug(f"skill({skill_name}): injected {count} engrams")
    return f"[Datacore Active Memory — skill: {skill_name}]\n\n{context}"


def handle_agent(input_data):
    """Agent-scoped injection based on agent type."""
    tool_input = input_data.get("tool_input", {})
    agent_type = tool_input.get("subagent_type", "")
    if not agent_type:
        return None

    result = plur_inject(agent_type)
    if not result:
        return None
    context, count = result
    _debug(f"agent({agent_type}): injected {count} engrams")
    return f"[Datacore Active Memory — agent: {agent_type}]\n\n{context}"


def handle_subagent(input_data):
    """Inject agent-scoped engrams into subagent context."""
    agent_type = input_data.get("agent_type", "")
    if not agent_type:
        return None

    result = plur_inject(agent_type)
    if not result:
        return None
    context, count = result
    _debug(f"subagent({agent_type}): injected {count} engrams")
    return f"[Datacore Active Memory — subagent: {agent_type}]\n\n{context}"


def handle_rehydrate(input_data):
    """Re-inject high-relevance engrams after context compaction."""
    summary = input_data.get("compact_summary", "")
    session = read_session()
    if session and session.get("first_prompt"):
        summary = f"{session['first_prompt']} {summary}"
    if not summary:
        summary = "general context rehydration"

    result = plur_inject(summary, limit=20)
    if not result:
        return None
    context, count = result
    _debug(f"rehydrate: injected {count} engrams")
    return f"[Datacore Active Memory — rehydrated after compaction]\n\n{context}"


HANDLERS = {
    "plan_mode": handle_plan_mode,
    "skill": handle_skill,
    "agent": handle_agent,
    "subagent": handle_subagent,
    "rehydrate": handle_rehydrate,
}


def main():
    parser = argparse.ArgumentParser(description="Datacore active memory injection (DIP-0024)")
    parser.add_argument("--event", required=True, choices=HANDLERS.keys())
    args = parser.parse_args()

    input_data = get_stdin_json()
    context = HANDLERS[args.event](input_data)

    if context:
        json.dump({"additionalContext": context}, sys.stdout)
    sys.exit(0)


if __name__ == "__main__":
    main()
