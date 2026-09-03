#!/usr/bin/env python3
"""The feature-idea queue: what awaits a verdict, and what is due for re-check.

One door into the roadmap (R-086). Ideas land in
`5-plur/1-tracks/product/feature-ideas.md`, get exactly one verdict — ADOPT,
TRIAL, WATCH or DROP — and only ADOPT produces a roadmap item.

The queue exists because `roadmap.yaml` is guarded: a commit touching it runs
roadmap_validate.py, which refuses an item with no definition of done. Most
ideas cannot state one yet, and forcing one produces fiction. So they wait here
instead, costing nothing.

    python3 .datacore/lib/idea_promote.py            # the queue
    python3 .datacore/lib/idea_promote.py --due      # WATCH items due
    python3 .datacore/lib/idea_promote.py --stale 21 # awaiting a verdict too long
"""
import argparse, re, sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IDEAS = REPO / "5-plur/1-tracks/product/feature-ideas.md"
STALE_DEFAULT = 21


def parse(text):
    """Ideas are `### heading` blocks carrying `- **key:** value` lines.

    Fenced code is stripped first: the file documents its own entry format in a
    ```markdown block, and that template parsed as a real idea — a queue that
    reports its own instructions as pending work is a queue nobody trusts.
    """
    text = re.sub(r"^```.*?^```", "", text, flags=re.M | re.S)
    out = []
    for m in re.finditer(r"^### (.+?)$\n(.*?)(?=^### |^## |^---\s*$|\Z)",
                         text, re.M | re.S):
        body = m.group(2)
        # `[ \t]*` and NOT `\s*` — \s matches newlines, so an empty verdict
        # swallowed the following horizontal rule and reported itself as "---".
        f = dict(re.findall(r"^- \*\*(\w+[\w -]*):\*\*[ \t]*(.*)$", body, re.M))
        out.append({"title": m.group(1).strip(),
                    "added": f.get("added", "").strip(),
                    "verdict": f.get("verdict", "").strip(),
                    "source": f.get("source", "").strip(),
                    "recheck": f.get("re-check", "").strip()})
    return out


def days_since(s):
    m = re.search(r"(20\d\d)-(\d\d)-(\d\d)", s or "")
    if not m:
        return None
    try:
        return (date.today() - date(*map(int, m.groups()))).days
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--due", action="store_true",
                    help="WATCH items whose re-check date has arrived")
    ap.add_argument("--stale", type=int, nargs="?", const=STALE_DEFAULT,
                    help="ideas awaiting a verdict longer than N days")
    args = ap.parse_args()

    if not IDEAS.exists():
        print(f"no idea queue at {IDEAS.relative_to(REPO)}")
        return 1
    text = IDEAS.read_text()
    ideas = parse(text)
    waiting = [i for i in ideas if not i["verdict"]]

    if args.due:
        due = [i for i in ideas
               if i["verdict"].upper().startswith("WATCH")
               and (days_since(i["recheck"]) or -1) >= 0]
        print(f"{len(due)} WATCH item(s) due for re-check")
        for i in due:
            print(f"  {i['title']}  (re-check {i['recheck']})")
        return 0

    if args.stale is not None:
        old = [i for i in waiting if (days_since(i["added"]) or 0) > args.stale]
        print(f"{len(old)} idea(s) awaiting a verdict for more than {args.stale} days")
        for i in old:
            print(f"  {days_since(i['added']):3}d  {i['title']}")
        if old:
            print("\nA verdict deferred twice IS the decision. Write it as DROP or "
                  "WATCH with a date.")
        return 0

    counts = {}
    for section in ("ADOPT", "TRIAL", "WATCH", "DROP"):
        m = re.search(rf"^## {section}\b(.*?)(?=^## |\Z)", text, re.M | re.S)
        body = m.group(1) if m else ""
        rows = [r for r in re.findall(r"^\|(.+)$", body, re.M)
                if not re.match(r"^[\s|:-]+$", r) and "became" not in r]
        counts[section] = len(rows) or len(re.findall(r"^### ", body, re.M))

    print(f"{len(waiting)} idea(s) awaiting a verdict\n")
    for i in waiting:
        age = days_since(i["added"])
        print(f"  {(str(age) + 'd') if age is not None else '  ?':>5}  {i['title']}")
        if i["source"]:
            print(f"         source: {i['source'][:70]}")
    print()
    print("  settled: " + " · ".join(f"{k} {v}" for k, v in counts.items()))
    print("\n  Every idea gets exactly one verdict. DROP is a success — the "
          "expensive\n  outcome is re-deriving the same no three times.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
