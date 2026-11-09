"""History written before voids were retired, for test fixtures ONLY.

Since the DIP-0034 amendment of 2026-10-31 (items 2 and 3, in force from
2026-11-01) no new `ledger.void` is written or admitted: nothing is cancelled
after the fact, and every correction is a new forward event. The voids already
in the history (5-plur: 1, 6-meridian: 2) keep their effect and fold exactly as
before.

A test whose scenario needs such a history -- a void written, or a reversal a
hub admitted, BEFORE the retirement -- writes it inside `legacy_void_rules()`.
That lifts, for the duration of the `with` block only and visibly, exactly the
two things the retirement added:

  * the write-time refusal (`ledger.voids.refuse_new_void`), so the library
    writes the void the way it did then (every older guard, such as "control
    records are never reversible", still runs);
  * the retirement moment (`ledger.voids.VOID_RETIRED_MS`), moved past every
    test clock, so a hub judges the reversal by the rule it had then (the
    owner only, inside the correction window, before the seal).

Nothing in production imports this module.
"""
from __future__ import annotations

import contextlib
import sys

from ledger import voids

#: Later than any clock a test uses: "the retirement has not happened yet".
_NEVER = sys.maxsize


@contextlib.contextmanager
def legacy_void_rules():
    """Inside the block, voids are written and judged as before 2026-11-01."""
    missing = object()
    saved_refusal = getattr(voids, "refuse_new_void", missing)
    saved_moment = getattr(voids, "VOID_RETIRED_MS", missing)
    voids.refuse_new_void = lambda type_: None
    voids.VOID_RETIRED_MS = _NEVER
    try:
        yield
    finally:
        for name, value in (("refuse_new_void", saved_refusal), ("VOID_RETIRED_MS", saved_moment)):
            if value is missing:
                delattr(voids, name)
            else:
                setattr(voids, name, value)


def append_legacy_void(writer, payload: dict):
    """`writer.append("ledger.void", payload)` as history was written before the
    retirement (an `EventLog` or a `HubWriter`)."""
    with legacy_void_rules():
        return writer.append(voids.VOID_TYPE, payload)
