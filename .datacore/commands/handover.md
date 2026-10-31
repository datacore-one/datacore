---
name: handover
description: Move this running session to dayshift — wind down, commit and push, checkpoint, then the owner runs one command and the same conversation carries on there
recall:
  scopes:
    - command:handover
  tags:
    - handover
    - dayshift
    - continuation
  query:
    - "/handover move session to dayshift"
---

# Handover

Use when a session on the Mac has grown into work the Mac cannot carry —
evals, promise runs, several agents in parallel. The same conversation, with
its full history, continues on dayshift in tmux; the owner reattaches with
`ds <name>` from any machine. Files the dayshift session shows the owner
(`mac-open`) open on the Mac.

This is not `/continue --save`, which leaves a written summary for later.
Here the conversation itself moves, now.

**Spec:** `2-datacore/1-tracks/dev/superpowers/specs/2026-10-28-handover-design.md`

## The two halves, and why

You (the agent) wind the session down and write the checkpoint. The owner
copies the transcript: the permission check refuses an agent copying its own
transcript, and that refusal is not to be routed around — no `osascript`,
no new terminal window, no subprocess that does it for you.

## Steps

1. **Stop in-flight work.** List every background shell and subagent you
   started that is still running. Stop each one. For each, write down what it
   was doing, how far it got (files written, last result seen), and the exact
   prompt or command that restarts it. Save the list as JSON in the
   scratchpad: `[{"what": "...", "progress": "...", "relaunch": "..."}]`
   (`[]` when nothing was running).

2. **Name the permission mode you are in**, as the system reminders state it
   (for example `auto`). Omit it only when no mode is stated. The resumed
   session gets the same mode and the Mac session's launch flags — never
   wider.

3. **Record every file this session wrote**, including files written through
   Bash, which the transcript does not count:
   `python3 ~/Data/.datacore/lib/wrap_up_mechanics.py files --add <path>...`

4. **Checkpoint:**
   `python3 ~/Data/.datacore/lib/handover.py checkpoint --topic "<3-6 words>" --mode <mode> --inflight <json file>`

   It commits and pushes the session's files with `/wrap-up`'s rules
   (session files only, default branch only, others' changes reported and
   left alone). If it prints `handover stopped:`, show the owner each line in
   plain words and stop here — something would be missing on dayshift. Code
   repos under `2-projects/` are never committed by it: commit and push those
   (or open the PR) first, then rerun.

   A long session may have touched repos that have nothing to do with the work
   being handed over. When the owner says such a repo stays on the Mac, rerun
   with `--leave <repo path under Data>` (repeatable): its unpushed work is
   listed under `left_on_mac_by_owner`, not blocking. Only a repo the owner has
   named — never one you decide is irrelevant.

5. **Hand over.** Tell the owner, in this order:
   - what was stopped and will be relaunched on dayshift;
   - anything it reported as unversioned (exists on the Mac only);
   - the command, as printed: exit Claude, then run `handover <session-id>`.

   Then stop. Do not keep working in this session: once the owner runs the
   command, two copies of the conversation must not both write.

## On dayshift, after the move

The resumed session's first message tells it to read
`~/.datacore/state/handover/<session-id>.json`. Relaunch each in-flight item
from its `relaunch` field, then continue from where the conversation
stopped. Show the owner files with `mac-open <path>`, never `open`/Typora.
