#!/usr/bin/env python3
"""Resolve 0-personal <-> nightshift-server merge conflicts losing nothing.

Two conflict classes arise when the laptop (interactive sessions) and the
nightshift server (autonomous overnight commits) diverge on the same files:

  1. Journals (add/add): both sides authored a journal for the same date.
     Resolution = UNION. Keep our (local) entry, append the server's entry
     below a divider (frontmatter stripped to avoid a duplicate block).

  2. GTD org files (content): the server is the nightshift-maintained, current
     state (gtd-hygiene / process-inbox already cleaned completed tasks). A
     blind union would resurrect archived tasks. Resolution = take SERVER
     (theirs) version, then re-append the local-only capture subtrees the
     server does not yet have.

Reads from git merge stages (:2: = ours/local, :3: = theirs/server) so it is
order-independent and needs no conflict-marker parsing. Reusable for future
divergences — pass --journals and --org-readd lists, or use the defaults.

Usage (run inside the repo, mid-merge):
    python3 reconcile_sync_merge.py
"""
import re
import subprocess
import sys


def blob(stage, path):
    # stage 2 = ours (HEAD), stage 3 = theirs (MERGE_HEAD). Read from the
    # commit refs rather than index stages, so this still works after the
    # conflicted paths have been `git add`ed (which collapses the stages).
    ref = "HEAD" if stage == 2 else "MERGE_HEAD"
    r = subprocess.run(["git", "show", f"{ref}:{path}"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def strip_frontmatter(text):
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].lstrip("\n")
    return text


def union_journal(path):
    ours = blob(2, path)
    theirs = blob(3, path)
    if not theirs.strip():
        return False
    merged = (ours.rstrip()
              + "\n\n---\n\n> _Merged from the nightshift server — it held a "
              "separate entry for this date:_\n\n"
              + strip_frontmatter(theirs).strip() + "\n")
    with open(path, "w") as f:
        f.write(merged)
    return True


def extract_subtrees(text, markers):
    """Return list of org subtree strings whose heading line contains any marker."""
    lines = text.split("\n")
    out = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = re.match(r"^(\*+)\s", line)
        if m and any(mk in line for mk in markers):
            level = len(m.group(1))
            block = [line]
            j = i + 1
            while j < n:
                m2 = re.match(r"^(\*+)\s", lines[j])
                if m2 and len(m2.group(1)) <= level:
                    break
                block.append(lines[j])
                j += 1
            out.append("\n".join(block).rstrip())
            i = j
        else:
            i += 1
    return out


def readd_org(path, markers, banner):
    ours = blob(2, path)
    theirs = blob(3, path)
    subtrees = extract_subtrees(ours, markers)
    base = theirs.rstrip()
    if subtrees:
        base += ("\n\n* " + banner + "\n"
                 + "\n\n".join(subtrees) + "\n")
    with open(path, "w") as f:
        f.write(base + "\n")
    return len(subtrees)


def main():
    union_files = [
        "notes/journals/2026-06-17.md",
        "notes/journals/2026-06-18.md",
        "notes/journals/2026-06-19.md",
        "notes/journals/2026-06-21.md",
    ]
    for p in union_files:
        ok = union_journal(p)
        print(f"union  {p}  {'ok' if ok else 'theirs-empty (kept ours)'}")

    # inbox.org local-only captures the server lacks
    inbox_markers = [
        "AGENTPOST", "PLUR LTD post-incorporation",
        "meeting transcription front-end", "Daily News Digest - Jun 17",
    ]
    c1 = readd_org("org/inbox.org", inbox_markers,
                   "CAPTURE merged back from local (not yet on server) :merge:")
    print(f"org    org/inbox.org  re-added {c1} local subtree(s)")

    # next_actions.org local-only captures the server lacks
    na_markers = [
        "scope-metadata spec", "plur #322", "require_last_push_approval",
    ]
    c2 = readd_org("org/next_actions.org", na_markers,
                   "Merged back from local (not yet on server) :merge:")
    print(f"org    org/next_actions.org  re-added {c2} local subtree(s)")


if __name__ == "__main__":
    sys.exit(main())
