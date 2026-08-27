#!/usr/bin/env python3
"""Resolve the fleet-sync conflicts left on this host, per DIP-0046 semantics.

Resolution rules, by file class:
  *.jsonl               per-writer append-only ledger -> union of both sides, deduped
  heartbeat.json        contested field is one timestamp -> keep the LATER one
  .datacore/checkpoints/  regenerable snapshot -> keep HEAD
  *.org                 two tasks appended at one spot -> union, keep BOTH
  everything else       keep HEAD (this host's own output)

Never silently drops a side: every choice is printed.
"""
import json
import pathlib
import re
import subprocess
import sys

REPOS = sys.argv[1:] or ["1-datafund", "6-meridian", "8-firm"]

CONFLICT_RE = re.compile(
    r"^<<<<<<< .*?\n(?P<head>.*?)^=======\n(?P<other>.*?)^>>>>>>> .*?\n",
    re.M | re.S,
)


def unmerged(repo):
    r = subprocess.run(
        ["git", "-C", repo, "diff", "--name-only", "--diff-filter=U"],
        capture_output=True, text=True,
    )
    return [x for x in r.stdout.splitlines() if x.strip()]


def keep_both(text):
    return CONFLICT_RE.sub(lambda m: m.group("head") + m.group("other"), text)


def keep_head(text):
    return CONFLICT_RE.sub(lambda m: m.group("head"), text)


def union_jsonl(text):
    seen, out = set(), []
    for line in keep_both(text).splitlines(True):
        if line.strip() and line not in seen:
            seen.add(line)
            out.append(line)
    return "".join(out)


def resolve_heartbeat(text):
    times = sorted(set(re.findall(r'"last_fire":\s*"([^"]+)"', text)))
    new = keep_head(text)
    if times:
        new = re.sub(r'("last_fire":\s*")[^"]+(")',
                     lambda m: m.group(1) + times[-1] + m.group(2), new, count=1)
    json.loads(new)  # validate
    return new, times


total = 0
for repo in REPOS:
    files = unmerged(repo)
    if not files:
        print(f"{repo}: nothing unmerged")
        continue
    print(f"=== {repo}: {len(files)} conflicted file(s)")
    for f in files:
        p = pathlib.Path(repo) / f
        try:
            t = p.read_text()
        except OSError as e:
            print(f"  SKIP {f}: {e}")
            continue
        if f.endswith(".jsonl"):
            new, how = union_jsonl(t), "union+dedupe (per-writer log)"
        elif "heartbeat.json" in f:
            new, times = resolve_heartbeat(t)
            how = f"later timestamp {times[-1] if times else '-'}"
        elif "/checkpoints/" in f:
            new, how = keep_head(t), "keep HEAD (regenerable snapshot)"
        elif (f.endswith(".org") or f.startswith(("journal/", "journals/"))
              or "/journal/" in f or "/journals/" in f):
            # Append-only per writer: two writers each add an entry at the same
            # offset, so a union keeps both and choosing a side silently deletes
            # one. On 2026-08-27 the old keep-HEAD default was applied to
            # 5-plur/journal/2026-08-27.md and discarded four Miles wrap-up
            # entries plus a nightshift run record — recovered from origin, but
            # only because the merge had not been pushed yet.
            #
            # f is REPO-RELATIVE (git diff --name-only): a journal at the repo
            # root arrives as `journal/2026-08-16.md`, with no leading slash for
            # `"/journal" in f` to match — so the 2026-08-27 fix missed the very
            # path it was written for, and on 2026-08-28 keep-HEAD dropped
            # origin's side of 1-datacore-space/journal/2026-08-16.md on
            # plur-claw. startswith catches the root-anchored case.
            new, how = keep_both(t), "union — BOTH sides kept"
        else:
            # keep-HEAD is only safe for regenerable artifacts. If you are about
            # to add a file class here, ask first whether losing the other side
            # is recoverable; if it is not, it belongs in the union branch above.
            new, how = keep_head(t), "keep HEAD (regenerable)"
        if "<<<<<<<" in new or ">>>>>>>" in new:
            print(f"  REFUSED {f}: markers survived")
            continue
        p.write_text(new)
        print(f"  {f[:60]} -> {how}")
        total += 1
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "--no-edit", "-q"], check=False)
    print(f"  committed {repo}")

print(f"\nresolved {total} file(s)")
