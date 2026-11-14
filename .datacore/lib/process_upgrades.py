"""process_upgrades — the proposed version of each process on the processes page.

Owner, 2026-10-31: "review them also yourself and suggest improvements in
processes. Update the processes page so that each process also has a toggle
Current / Upgrade."

Each entry is a proposal, not the running system. It reuses the drawing format of
process_views.FLOWS (same lanes-and-rows grid, same marks); a step whose kind is
"new" is new or changed in the proposal and is drawn dashed. Every change names
what it needs before it can happen:

    your decision   a product or policy call that is yours
    DIP amendment   the DIP says otherwise today; a draft goes to you to ratify
    code fix        a defect or a missing call; no decision needed
    install         the code exists; nothing schedules or wires it

Proposals come from the review of the current drawings on 2026-10-31 and the
DIP check in 2-datacore/1-tracks/dev/reports/2026-10-31-task-delegation-true-state-and-dip-check-report.md.
Nothing here has been changed in the running fleet.

The proposals describe this installation's own processes, so they live in its
gitignored .datacore/config/processes.local.yaml (`upgrades`, `plan_progress`;
process_data.py, INS-3). Each change is (what, why, needs[, (status, evidence)]).
"""
from process_data import DATA

# step: (id, lane, row, name, technical line, state, "focal"|"new"|"", marks)
UPGRADES = DATA["upgrades"]

# (phase, what, status, evidence). Status is one of process_views.STATUS_ICON.
PLAN_PROGRESS = DATA["plan_progress"]
