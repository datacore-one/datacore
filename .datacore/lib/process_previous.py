"""How each process ran before the tasks-and-ledger rebuild began (2026-10-31).

Frozen from process_views.py at commit 67413746, the last drawing before the rebuild. The page compares
each with its Current drawing: where they differ it draws this one under Previous, otherwise it says the
process is unchanged. Never edit by hand: this is the before-picture (owner, 2026-11-01).

The drawings are this installation's own, so they live in its gitignored
.datacore/config/processes.local.yaml under `previous` (process_data.py, INS-3).
"""
from process_data import DATA

AS_OF = DATA["previous"]["as_of"]
COMMIT = DATA["previous"]["commit"]
PREVIOUS = DATA["previous"]["flows"]
