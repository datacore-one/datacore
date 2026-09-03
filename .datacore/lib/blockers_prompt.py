#!/usr/bin/env python3
"""Generate the blockers brief — the epics only Gregor can move.

Reads `blocked_on: human` out of roadmap.yaml and writes a document meant to be
pasted into a fresh session.

Grouped by EVIDENCE KIND rather than by topic, deliberately: what differs
between these is the mode of work, not the subject. Four decisions in a row is
one sitting; four signatures is a different one. Grouping by track would mix
them and make the list feel longer than it is.

    python3 .datacore/lib/blockers_prompt.py [--out PATH]
"""
import argparse, datetime, re, sys, textwrap
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
ROADMAP = REPO / "5-plur" / "roadmap.yaml"

KIND = {
    "decision": "A decision, written down",
    "signed": "A signature or an executed document",
    "url": "Something live at an address",
    "test": "A check that passes",
    "metric": "A number that moves",
    "artifact": "A file that exists",
    "merged-pr": "A merge",
    "screenshot": "Something recorded",
    "—": "Unclassified — these have no definition of done yet",
}


def flat(text):
    """Collapse folded YAML scalars to one line."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    r = yaml.safe_load(ROADMAP.read_text())
    blocked = [i for i in r["items"] if i.get("blocked_on") == "human"]
    blocked.sort(key=lambda i: (i.get("horizon") != "now",
                                str(i.get("milestone")), i["id"]))

    groups = {}
    for i in blocked:
        k = (i.get("done_when") or {}).get("evidence", "—")
        groups.setdefault(k, []).append(i)

    n = len(blocked)
    out = [f"""# Blockers — the {n} epics that only you can move

Generated {datetime.date.today()} from `5-plur/roadmap.yaml` (`blocked_on: human`).
Regenerate with `python3 .datacore/lib/blockers_prompt.py`.

## How to use this

Paste the prompt below into a fresh session. The list is grouped by **what kind
of answer each one needs**, not by topic, because the mode of work is what
actually differs.

Nothing here is engineering. Every item waits on a judgement, an approval or an
account only you have — which is why no agent picked any of it up, and why none
of it appears in a sprint.

---

## The prompt

> I want to clear the blockers on the PLUR roadmap. There are {n} epics marked
> `blocked_on: human` in `5-plur/roadmap.yaml`, each waiting on a decision, a
> signature or an approval from me.
>
> Work through them with me one group at a time, in the order below. For each:
> tell me what the epic is, what is already known that bears on it, and what the
> roadmap says would make it unblocked — then ask me the one question that
> settles it. Do not propose the answer before asking.
>
> When I answer, write the decision into the right place — `roadmap.yaml`,
> `org/intents.org`, or a decision record — and change `blocked_on` so the item
> stops claiming to be blocked. If my answer opens a new question, say so rather
> than closing it.
>
> Where an epic turns out not to need me at all, say so and hand it back to the
> pool with `:AI:`.

---
"""]
    for kind, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        out.append(f"\n## {KIND.get(kind, kind)} — {len(items)}\n")
        for i in items:
            dw = i.get("done_when") or {}
            outcome = flat(i["outcome"])
            cond = flat(dw.get("condition", ""))
            out.append(f"### {i['id']} · {i['title']}\n")
            out.append(f"**Outcome** {outcome}\n")
            if cond:
                out.append(f"**Unblocked when** {cond}\n")
            note = re.sub(r"\s+", " ", str(i.get("note", ""))).strip()
            if note:
                out.append("**Context** "
                           f"{textwrap.shorten(note, 400, placeholder=' …')}\n")
            out.append("")

    dest = Path(args.out) if args.out else (
        REPO / "5-plur/1-tracks/ops" / f"blockers-{datetime.date.today()}.md")
    dest.write_text("\n".join(out))
    print(f"wrote {dest.relative_to(REPO)} — {n} blockers in {len(groups)} groups")
    for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(v):3}  {KIND.get(k, k)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
