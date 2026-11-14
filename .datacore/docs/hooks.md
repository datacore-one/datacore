# Datacore hooks: a reference for module developers

Everything in Datacore that runs **because something else happened** is a hook. They are all declared in one registry, `.datacore/registry/hooks.yaml` (DIP-0024, amended 2026-11-09). This page explains:

- the five families;
- when each one fires in the life of a session, a briefing, an agent run and a task;
- how a module declares its own hooks;
- every hook installed today.

The registry is the source of truth. The tables at the end are generated from it, so if this page and the registry disagree, the registry wins.

## The five families at a glance

| Family | Fires on | Run by | A module declares it in | Typical use |
|---|---|---|---|---|
| **Claude Code session hooks** | Events in an interactive or headless Claude Code session (start, each prompt, each tool call, stop, end) | Claude Code itself, from `.claude/settings.json` built by `lib/hooks_composer.py` | `module.yaml` → `hooks.claude_code` | Guards (refuse a write), memory injection, receipts, auto-fixes |
| **Git hooks** | `git commit` in the system repo | git, from `.datacore/hooks/` | not open to modules | Privacy, secrets and date checks before a commit |
| **Module hook kinds** | A Datacore command reaching its extension point | the command's runner (`today_registry.py` for `today`, `hooks_composer.py` for `claude_code`) | `module.yaml` → `hooks.<kind>` | A section in the morning briefing; session hooks |
| **Agent lifecycle hooks** | An agent run starting, finishing, being validated or failing | `lib/hooks.py` (`HookExecutor`), used by the nightshift runner | `registry/agents.yaml`: `defaults`, `profiles`, or the agent's own `hooks:` | Load context before a run, log metrics and extract learnings after, retry or escalate on error |
| **Ledger-event subscribers** | An event appended to a space's ledger (`item.release`, …) | `lib/ledger_subscribers.py` | `registry/hooks.yaml` → `ledger_events` | Tell someone when work changes state, without polling |

The registry also lists two kinds that **nothing runs any more**: `post_install`, and the nightshift queue hooks. They were removed from every module. `module_manifest_check.py` reports a module that declares a kind that is not in `module_hook_kinds`: "nothing runs it" is now a finding, not a silent no-op.

## The lifecycle: when each hook fires

### 1. A Claude Code session

```text
claude starts ─► SessionStart (matcher: startup | resume | compact)
                    │  bootstrap, knowledge sync, context check, memory remind
                    ▼
user types ────► UserPromptSubmit           (every prompt)
                    │  memory injection, redaction block, command suggestion, time awareness
                    ▼
model calls a tool ─► PreToolUse (matcher = tool name regex)   ◄── can REFUSE the call
                    │  guards: space isolation, org files, dates, evals, publish, memory gate
                    ├─► PermissionRequest  (when Claude Code asks the user)
                    ▼
               tool runs
                    ├─► PostToolUse        (success)    auto-fix dates, clear gates, observe
                    └─► PostToolUseFailure (failure)    report a failing MCP server
                    │
   spawns agent ─► SubagentStart ... SubagentStop   memory for the subagent, receipts
                    │
   context full ─► PostCompact (auto | manual)        re-inject memory
                    ▼
model finishes ─► Stop / StopFailure                  learning reminder, cost, receipts
                    ▼
session closes ─► SessionEnd                          archive transcript, clean state
```

What a session hook can do depends on its event:

- **PreToolUse** can deny the tool call. It prints `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "…"}}` and exits 0. This is how every guard works.
- **UserPromptSubmit, SessionStart and PostCompact** can add context. They print `{"hookSpecificOutput": {"hookEventName": "…", "additionalContext": "…"}}`. This is how memory reaches the model.
- **Stop** can block the stop and send the model back to work, as the goal and loop hooks do.
- **Everything else** observes. Exit codes and output are logged, and the session goes on.

Hooks on the same event and matcher run in `priority` order, lower first (default 500). Project hooks come from the registry plus every installed module; user hooks are wired in `~/.claude/settings.json` (see "Scope" below).

### 2. The morning briefing (`/today`)

```text
/today ─► briefing.yaml says which sections are wanted, in order
          today_registry.plan() groups every module's hooks.today by stage:

   stage: gather   ─►  all gather hooks in parallel   (meetings, mail, triage, nightshift…)
   stage: compose  ─►  all compose hooks in parallel
   stage: narrate  ─►  all narrate hooks in parallel  (the closing observation…)

   Within a stage: topological order over depends_on; a stage starts only when
   the previous one has finished.

   slot: inline (default) → the output fills the module's section in the briefing
   slot: post             → runs after the briefing is written (audio, sending)
```

A `depends_on` naming a section no installed module produces is skipped by the planner. The module's own test (`test_depends_on_names_sections_that_someone_produces`) still flags it, because a typo there means a section silently never waits for what it needs.

### 3. An agent run (nightshift and agent dispatch)

```text
HookExecutor.execute_pre_hooks(agent, task)      phase "pre"
    context-inject · validate-preconditions · discover-spaces
    → may ABORT the run (returns should_continue = False)
        │
     agent runs
        │
HookExecutor.execute_validate_hooks(agent, result)   phase "validate"
    output-exists · quality-gate          → may FAIL the result
        │
HookExecutor.execute_post_hooks(agent, result)       phase "post"
    metrics-log · learning-extract · journal-append · embed-outputs
        │
   on exception:
HookExecutor.execute_error_hooks(agent, error)       phase "on_error"
    classify-error · retry-schedule · escalate
```

Hooks resolve in this order: `defaults` → the agent's `profile` → the agent's own `hooks:`. An entry `inherit: defaults` pulls the default list into a profile. The default set today is context injection before, metrics logging after, and error classification on failure.

### 4. A task on the ledger

```text
any writer appends an event ─► .datacore/events/<writer>.jsonl  (signed, chained)
                                   │
ledger_subscribers.py run  (from the job that converges the space)
   for each declared subscriber, each space, each writer's log:
       read past this subscriber's cursor
       call  module:function(envelope)       ── raises? → record the error, stop
                                                           this log, retry next run
       returned? → move the cursor past the event
```

Delivery is **at least once**, in order within one writer's log, with no order across logs. A subscriber gets a copy of the event and cannot veto the write. A new subscriber (or one new to a space) starts at the current end of each log, so it gets what happens from now on, not the history. A writer that appears later is read from its first event. A name not declared in the registry is refused.

The envelope a subscriber receives:

```python
{"type": "item.release",          # the ledger event type
 "alias": "agent.run.failed",     # the Paperclip-style alias, when there is one
 "space": "<space folder name>", "space_dir": "<absolute path>",
 "actor": "<writer>", "seq": 412, "hash": "…", "hlc": "…",
 "payload": {...}}                # a deep copy
```

The aliases (for tools that speak Paperclip's vocabulary) are:

- `item.create` → `issue.created`
- `item.claim` → `agent.run.started`
- `item.complete` and `item.verify` → `agent.run.finished`
- `item.release` → `agent.run.failed`
- `item.dismiss` → `issue.closed`
- `approval.grant` → `approval.decided`
- `spend.record` → `cost_event.created`

## How a module declares hooks

All of it goes in the module's `module.yaml` under `hooks:`.

### A session hook (`hooks.claude_code`)

```yaml
hooks:
  claude_code:
    - event: PreToolUse            # any Claude Code event
      matcher: "Edit|Write"        # regex on the tool name (optional)
      type: command
      command: "python3 \"${CLAUDE_PROJECT_DIR:-$HOME/Data}\"/.datacore/modules/mymod/hooks/guard.py"
      purpose: "Refuse writes into mymod's generated folder"
      priority: 300                # lower runs first; default 500
      timeout: 5                   # seconds; default 10
```

Then rebuild and check the composed settings:

```bash
python3 .datacore/lib/hooks_composer.py rebuild --dry-run   # see what would change
python3 .datacore/lib/hooks_composer.py rebuild             # write .claude/settings.json
python3 .datacore/lib/hooks_composer.py validate            # missing scripts, conflicts
```

Rules that keep a hook from hurting a session:

- **Never crash the session.** Exit 0 on your own bug; fail open unless you are a guard whose whole job is to refuse. A guard that bricks a session on its own state is worse than the leak it prevents.
- **Stay fast.** Keep `timeout` low: PreToolUse hooks run on every matching tool call.
- **Read the event from stdin.** Claude Code passes the event as JSON (`session_id`, `tool_name`, `tool_input`, `prompt`, …). Do not shell-parse a command line you did not write.
- **Say why when you deny.** Put a fix in the reason too: the model reads it and acts on it.

### A briefing section (`hooks.today`)

```yaml
hooks:
  today:
    - section: your_agenda          # the section you fill (must be in briefing.yaml to run)
      prompt: commands/today-hook.md
      agent: my-agent               # optional: who reasons over it
      stage: gather                 # gather | compose | narrate
      depends_on: [good_morning]    # sections that must finish first
      model: sonnet                 # optional
      refresh: true                 # re-run on a /today refresh
      timeout: 300
      slot: inline                  # inline (default) | post
```

A bare string still works, but the validator then reports the hook as unregistered.

### Agent lifecycle hooks

These are declared on the agent in `registry/agents.yaml`, either on the agent itself or through a profile:

```yaml
module_agents:
  my-agent:
    profile: gtd-processor          # inherits the profile's hooks
    hooks:
      post:
        - inherit: defaults
        - type: learning-extract
      validate:
        - type: output-exists
```

Phases are `pre`, `validate`, `post` and `on_error`. The action types are:

| Phase | Action types |
|---|---|
| `pre` | `context-inject`, `validate-preconditions`, `discover-spaces` |
| `validate` | `output-exists`, `quality-gate` |
| `post` | `metrics-log`, `learning-extract`, `journal-append`, `embed-outputs` |
| `on_error` | `classify-error`, `retry-schedule`, `escalate` |

### A ledger subscriber

Subscribers are declared in the system registry, not in `module.yaml`, because they see every space's events:

```yaml
# .datacore/registry/hooks.yaml
ledger_events:
  - name: my-subscriber
    types: [item.complete]
    call: my_module_lib:on_complete     # module:function, imported with only .datacore/lib on sys.path
    purpose: "…"
    declared_by: mymod
```

```python
def on_complete(envelope: dict) -> None:
    # Idempotent: the same event can arrive twice. Raise to have it retried.
    ...
```

Check the subscriber and its cursors:

```bash
python3 .datacore/lib/ledger_subscribers.py run --only my-subscriber
python3 .datacore/lib/ledger_subscribers.py status
```

## Scope: project vs user hooks

- **project** (the default) hooks are composed into the repository's `.claude/settings.json`, from the registry plus every installed module. Every session in this installation gets them.
- **user** hooks are wired into `~/.claude/settings.json` by an installer named in `installed_by`: `plur init`, `install_redaction_guards.py --only <guard>`, `agent_host_setup.sh`, `bootstrap/configure-hooks.py`, or by hand. They are listed in the registry so it is complete. They are never composed into the project settings.

`lib/tests/test_hooks_registry_matches_wiring.py` fails when a hook wired on the machine is missing from the registry. A hook you wire by hand must be added to the registry too.

## How hooks are tested

| Test | What it holds |
|---|---|
| `test_hooks_registry_matches_wiring.py` | Every wired hook is in the registry, and the composer accepts the registry it would rebuild from |
| `test_hook_kinds_registry.py` | Every module hook kind is one something runs; dead kinds are reported |
| `test_today_hooks_grounded.py`, a module's own `test_module_yaml.py` | The briefing's slot table and post-hook table match what modules declare; `depends_on` names a real section |
| `test_ledger_subscribers.py` | At least once, a cursor per subscriber, space and writer, failures retried, a new subscriber starts at the end, an undeclared name is refused |
| `test_delegation_watch_release.py` | The one subscriber shipped today: a release is told once, with the doer's reason |
| `test_decisions_guards.py`, `test_guards_formal.py` | The guard hooks (memory gate, Bash-while-armed rules), against the formal model |

## Every hook installed today

Generated from `registry/hooks.yaml`, 2026-11-14.

### Git hooks (pre-commit)

| Hook | Declared by | Prio | What it does |
|---|---|---|---|
| `.datacore/hooks/pre-commit` | DIP-0002 | 100 | Privacy, PII, secrets and root-allowlist scan |
| `.datacore/hooks/org-date-fix.sh` | DIP-0009 | 200 | Validate org-mode date accuracy |

### Module hook kinds

| Kind | Run by | Purpose |
|---|---|---|
| `today` | `lib/today_registry.py` | A module's section in the `/today` briefing |
| `claude_code` | `lib/hooks_composer.py` | A module's Claude Code hooks, composed into `.claude/settings.json` |

### Agent lifecycle

Run by `lib/hooks.py`, declared by DIP-0016, used today by the nightshift runner (`modules/nightshift/lib/run.py`). Phases: `pre`, `validate`, `post`, `on_error`. The registry names them `pre_execution`, `validation`, `post_execution` and `error`.

### Ledger-event subscribers

| Name | On | Calls | What it does |
|---|---|---|---|
| `request-releases` | `item.release` | `delegation_watch:on_release` | Tell the owner once when a doer releases one of their requests unfinished, with the doer's reason |

### Claude Code session hooks (81), in lifecycle order

`scope: project` hooks come first in each table, in priority order. The `user` hooks follow.

#### SessionStart (7)

| Matcher | Scope | Declared / installed by | Prio | Timeout | What it does |
|---|---|---|---|---|---|
| `startup` | project | DIP-0019 | 10 | 10 | Initialize session, clean stale state, output journal + candidates |
| `startup` | project | DIP-0004 | 20 | 30 | Sync knowledge database on session start |
| `all` | user | plur init |  | 3 | PLUR: remind to start the session |
| `all` | user | install_redaction_guards.py --only context |  | 20 | Rebuild a stale composed CLAUDE.md; report a hand-edited one |
| `all` | user | hand-wired |  |  | iTerm2 tab status: show the session state in the terminal |
| `resume` | user | plur init |  | 3 | PLUR: resume the session's memory lifecycle |
| `compact` | user | plur init |  | 20 | PLUR: re-inject engrams after compaction |

#### UserPromptSubmit (9)

| Matcher | Scope | Declared / installed by | Prio | Timeout | What it does |
|---|---|---|---|---|---|
| `all` | project | DIP-0019 | 10 | 5 | First message: inject engrams via PLUR CLI. Subsequent: skip |
| `all` | project | DIP-0019 | 20 | 5 | Track session duration and prompt time awareness |
| `all` | project | DIP-0024 | 30 | 5 | Suggest a matching slash command for the prompt |
| `all` | project | DIP-0019 | 40 | 90 | Per-prompt PLUR engram injection (async); skipped when the user-level copy is wired |
| `all` | user | hand-wired |  | 90 | Per-prompt PLUR engram injection (user level) |
| `all` | user | install_redaction_guards.py --only redaction |  | 5 | Re-inject the redaction block every turn; demo mode (MEM-12/14) |
| `all` | user | hand-wired |  |  | iTerm2 tab status: show the session state in the terminal |
| `all` | user | hand-wired (modules/provenance/README.md snippet) |  | 15 | Provenance: snapshot dirty repo files at turn start |
| `all` | user | plur init |  | 20 | PLUR: per-prompt engram injection |

#### PreToolUse (31)

| Matcher | Scope | Declared / installed by | Prio | Timeout | What it does |
|---|---|---|---|---|---|
| `EnterPlanMode` | project | DIP-0019 | 10 | 10 | Full engram injection when entering plan mode |
| `Skill` | project | DIP-0019 | 20 | 10 | Domain engrams based on skill name |
| `Skill` | project | DIP-0024 | 30 | 3 | Record that a command or skill was invoked (receipt) |
| `Agent` | project | DIP-0016 | 40 | 10 | Agent-scoped engrams for spawned agent |
| `Edit\|Write` | project | DIP-0009 | 50 | 5 | Refuse raw edits of org/ task files; use org_workspace_adapter |
| `Edit\|Write` | project | DIP-0009 | 60 | 5 | Refuse a write whose org date has the wrong day name |
| `EnterPlanMode` | project | DIP-0019 | 70 | 10 | PLUR hook-inject on plan mode |
| `Skill` | project | DIP-0019 | 80 | 10 | PLUR hook-inject on skill |
| `Agent` | project | DIP-0019 | 90 | 10 | PLUR hook-inject on agent |
| `Bash\|Edit\|Write\|Agent` | project | plur | 100 | 3 | Record tool calls for PLUR observation (pre) |
| `Bash` | project | DIP-0009 | 110 | 5 | Refuse raw edits of org/ task files; use org_workspace_adapter |
| `Bash` | user | hand-wired |  |  | Refuse network git/ssh to restricted hosts (MEM-01) |
| `Edit\|Write\|MultiEdit` | user | hand-wired |  |  | Suggest compaction on long sessions |
| `Edit\|Write\|MultiEdit` | user | DIP-0009 |  |  | Refuse a write whose org date has the wrong day name |
| `Edit\|Write\|MultiEdit` | user | install_redaction_guards.py --only memory |  | 5 | No name, amount, host or secret on a line of always-loaded auto-memory |
| `mcp__plur__plur_session_end` | user | hand-wired |  |  | Token-cost gate before plur_session_end |
| `mcp__plur__plur_session_end` | user | hand-wired |  |  | Wrap-up checklist before plur_session_end |
| `Skill\|SlashCommand\|Agent` | user | bootstrap/configure-hooks.py |  | 3 | Inject command-scoped engrams on Skill/SlashCommand/Agent (DIP-0029) |
| `Artifact\|Bash` | user | install_redaction_guards.py --only publish |  | 5 | An Artifact publish, gist or deploy asks first (MEM-17) |
| `Edit\|Write\|MultiEdit\|Bash` | user | install_redaction_guards.py --only evals |  | 5 | Changing an existing promise eval asks the owner first (MEM-30) |
| `Bash\|Edit\|Write\|MultiEdit` | user | install_redaction_guards.py --only guards |  | 5 | A guard cannot be unplugged, stubbed or bypassed in git (MEM-09) |
| `Bash\|Edit\|Write\|Read\|MultiEdit` | user | install_redaction_guards.py --only client |  | 5 | Client guards: a client session stays inside; person docs ask (MEM-13/15) |
| `*` | user | plur init |  | 3 | PLUR session guard |
| `*` | user | install_redaction_guards.py --only redaction |  | 5 | A truncated injection must be read before other tools run |
| `*` | user | agent_host_setup.sh (install_redaction_guards.py --only policy) |  | 8 | Tool-policy guard before every tool call on an agent machine (AGT-11) |
| `all` | user | hand-wired |  |  | iTerm2 tab status: show the session state in the terminal |
| `Bash\|Edit\|Write\|Read\|MultiEdit` | user | hand-wired |  | 5 | Space isolation: refuse reads and writes across spaces that do not share |
| `EnterPlanMode` | user | plur init |  | 10 | PLUR: inject engrams on plan mode |
| `Skill` | user | plur init |  | 10 | PLUR: inject engrams on skill use |
| `Agent` | user | plur init |  | 10 | PLUR: inject engrams on agent spawn |
| `Bash\|Edit\|Write\|Agent` | user | plur init |  | 3 | PLUR: observe tool calls |

#### PermissionRequest (1)

| Matcher | Scope | Declared / installed by | Prio | Timeout | What it does |
|---|---|---|---|---|---|
| `all` | user | hand-wired |  |  | iTerm2 tab status: show the session state in the terminal |

#### PostToolUse (10)

| Matcher | Scope | Declared / installed by | Prio | Timeout | What it does |
|---|---|---|---|---|---|
| `Skill\|Bash` | project | ralph-loop | 10 | 3 | Ralph loop: bind the loop to this session |
| `Edit\|Write` | project | DIP-0009 | 20 | 10 | Auto-fix org-mode date day-of-week after edits |
| `Bash\|Edit\|Write\|Agent` | project | plur | 30 | 3 | Record tool results for PLUR observation (post) |
| `Edit\|Write\|MultiEdit` | user | hand-wired |  |  | Fix org day names after a write |
| `Read\|Bash\|Grep\|Glob` | user | install_redaction_guards.py --only redaction |  | 5 | Clear the injection gate once the spilled file is read |
| `mcp__plur__plur_session_start` | user | plur init |  | 3 | PLUR: mark session started |
| `mcp__plur__plur_session_start` | user | install_redaction_guards.py --only redaction |  | 5 | Arm the injection gate after plur_session_start |
| `all` | user | hand-wired |  |  | iTerm2 tab status: show the session state in the terminal |
| `mcp__.*__plur_session_start` | user | plur init |  | 3 | PLUR: mark the session started |
| `Bash\|Edit\|Write\|Agent` | user | plur init |  | 3 | PLUR: observe tool results |

#### PostToolUseFailure (1)

| Matcher | Scope | Declared / installed by | Prio | Timeout | What it does |
|---|---|---|---|---|---|
| `mcp__*` | user | hand-wired |  |  | Report a failing MCP server after a tool failure |

#### SubagentStart (3)

| Matcher | Scope | Declared / installed by | Prio | Timeout | What it does |
|---|---|---|---|---|---|
| `.*` | project | DIP-0016 | 10 | 10 | Inject agent-scoped engrams into subagent context |
| `.*` | project | DIP-0019 | 20 | 10 | PLUR hook-inject into subagent |
| `.*` | user | plur init |  | 10 | PLUR: inject engrams into a subagent |

#### SubagentStop (2)

| Matcher | Scope | Declared / installed by | Prio | Timeout | What it does |
|---|---|---|---|---|---|
| `all` | user | hand-wired |  |  | iTerm2 tab status: show the session state in the terminal |
| `all` | user | hand-wired (modules/provenance/README.md snippet) |  | 10 | Provenance: queue an agent receipt for the turn (SubagentStop) |

#### Notification (1)

| Matcher | Scope | Declared / installed by | Prio | Timeout | What it does |
|---|---|---|---|---|---|
| `all` | user | hand-wired |  |  | iTerm2 tab status: show the session state in the terminal |

#### PostCompact (2)

| Matcher | Scope | Declared / installed by | Prio | Timeout | What it does |
|---|---|---|---|---|---|
| `auto\|manual` | project | DIP-0019 | 10 | 15 | Re-inject critical engrams after context compaction |
| `auto\|manual` | project | DIP-0019 | 20 | 15 | PLUR re-injection after compaction |

#### Stop (9)

| Matcher | Scope | Declared / installed by | Prio | Timeout | What it does |
|---|---|---|---|---|---|
| `*` | project | ralph-loop | 10 | 3 | Ralph loop: stop guard |
| `*` | project | DIP-0019 | 20 | 3 | Remind to capture learnings before stopping |
| `*` | user | hand-wired |  | 5 | Desktop notification when Claude stops |
| `*` | user | hand-wired |  | 5 | Record session cost when Claude stops |
| `all` | user | hand-wired |  |  | iTerm2 tab status: show the session state in the terminal |
| `all` | user | hand-wired |  | 20 | Refuse a medical-record reply that has no Verification status block |
| `all` | user | hand-wired (modules/provenance/README.md snippet) |  | 10 | Provenance: queue an agent receipt for the turn (Stop) |
| `*` | user | plur init |  | 2 | PLUR: remind to learn before stopping |
| `*` | user | plur init |  | 10 | PLUR: rate injected engrams |

#### StopFailure (1)

| Matcher | Scope | Declared / installed by | Prio | Timeout | What it does |
|---|---|---|---|---|---|
| `all` | user | hand-wired |  |  | iTerm2 tab status: show the session state in the terminal |

#### SessionEnd (4)

| Matcher | Scope | Declared / installed by | Prio | Timeout | What it does |
|---|---|---|---|---|---|
| `all` | project | DIP-0019 | 10 | 60 | Archive the session transcript at session end |
| `all` | project | DIP-0019 | 20 | 5 | Clean up session state file |
| `all` | user | plur init |  | 5 | PLUR: end the session |
| `all` | user | hand-wired |  |  | iTerm2 tab status: show the session state in the terminal |
