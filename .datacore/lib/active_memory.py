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
"""
import argparse, json, sys, os

sys.path.insert(0, os.path.join(os.path.expanduser("~/Data"), ".datacore", "lib"))
from engram_selector import select_engrams, format_injection
from session_state import read_session, _debug

def get_stdin_json():
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return {}

def handle_plan_mode(input_data):
    """Full engram injection for planning — architecture, patterns, mistakes."""
    # Use session context if available for better targeting
    session = read_session()
    task_desc = "architecture planning design patterns decisions"
    if session and session.get("first_prompt"):
        task_desc = f"{session['first_prompt']} {task_desc}"

    engrams = select_engrams(scope="global", task_desc=task_desc, limit=20)
    if not engrams:
        return None
    _debug(f"plan_mode: injecting {len(engrams)} engrams")
    context = format_injection(engrams)
    return f"[Datacore Active Memory — plan mode]\n\n{context}"

def handle_skill(input_data):
    """Domain injection based on skill name."""
    tool_input = input_data.get("tool_input", {})
    skill_name = tool_input.get("skill", "")
    if not skill_name:
        return None

    # Extract domain from skill name (e.g., "trading:log-trade" → "trading")
    domain = skill_name.split(":")[0] if ":" in skill_name else skill_name
    engrams = select_engrams(scope=f"command:{skill_name}", task_desc=domain, limit=10)
    if not engrams:
        # Fallback: try global scope with domain as task
        engrams = select_engrams(scope="global", task_desc=domain, limit=10)
    if not engrams:
        return None
    _debug(f"skill({skill_name}): injecting {len(engrams)} engrams")
    context = format_injection(engrams)
    return f"[Datacore Active Memory — skill: {skill_name}]\n\n{context}"

def handle_agent(input_data):
    """Agent-scoped injection based on agent type."""
    tool_input = input_data.get("tool_input", {})
    agent_type = tool_input.get("subagent_type", "")
    if not agent_type:
        return None

    engrams = select_engrams(scope=f"agent:{agent_type}", task_desc=agent_type, limit=10)
    if not engrams:
        return None
    _debug(f"agent({agent_type}): injecting {len(engrams)} engrams")
    context = format_injection(engrams)
    return f"[Datacore Active Memory — agent: {agent_type}]\n\n{context}"

def handle_subagent(input_data):
    """Inject agent-scoped engrams into subagent context."""
    agent_type = input_data.get("agent_type", "")
    if not agent_type:
        return None

    engrams = select_engrams(scope=f"agent:{agent_type}", task_desc=agent_type, limit=10)
    if not engrams:
        return None
    _debug(f"subagent({agent_type}): injecting {len(engrams)} engrams")
    context = format_injection(engrams)
    return f"[Datacore Active Memory — subagent: {agent_type}]\n\n{context}"

def handle_rehydrate(input_data):
    """Re-inject all high-strength engrams after context compaction."""
    summary = input_data.get("compact_summary", "")
    # Also use session first prompt for better targeting
    session = read_session()
    if session and session.get("first_prompt"):
        summary = f"{session['first_prompt']} {summary}"

    engrams = select_engrams(scope="global", task_desc=summary, limit=20)
    if not engrams:
        return None
    _debug(f"rehydrate: injecting {len(engrams)} engrams")
    context = format_injection(engrams)
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
