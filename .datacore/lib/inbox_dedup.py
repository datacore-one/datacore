#!/usr/bin/env python3
"""Remove inbox.org entries that are already routed to a destination org file.

Why this exists
---------------
`/process-inbox` routes entries out of inbox.org into next_actions.org and
research_learning.org, then leaves inbox.org empty. A later capture that writes
a *stale snapshot* of inbox.org silently resurrects everything the routing had
removed — the entries then live in both places, and because a subsequent
`ensure-ids` assigns the resurrected copies fresh IDs, org-workspace's
`dedup_ids()` cannot see them as duplicates. Headings are the only reliable
join key.

This happened on 2026-07-26: /process-inbox (250072f7) routed 187 entries and
returned inbox.org to empty; capture 1b2d7233 re-added 1512 lines 90 minutes
later, restoring 176 already-routed entries.

Matching
--------
An inbox entry is a duplicate when its *normalised heading* also appears as a
heading in one of the destination files. Normalisation strips the leading stars,
TODO state, priority cookie and trailing tag string, then collapses whitespace.
Deliberately conservative: only exact heading matches are removed, so a
reworded entry survives and gets processed normally.

Usage
-----
    python3 .datacore/lib/inbox_dedup.py --space 0-personal            # dry run
    python3 .datacore/lib/inbox_dedup.py --space 0-personal --apply
    python3 .datacore/lib/inbox_dedup.py --space 0-personal --apply --tag sprint_s1
    python3 .datacore/lib/inbox_dedup.py --space-all --exact-only --apply   # morning job

`--space-all --exact-only` is the automatic mode the chief-of-staff morning
inbox job runs before processing (promise INB-5). For every space with an
org/inbox.org it removes a capture only when its normalised heading is
IDENTICAL to a heading in that space's other live org files (archives never
count). A near-match (same words ignoring case and punctuation) is ambiguous:
it is kept and reported. A space with nothing to remove is not rewritten.

`--tag` restricts removal to entries carrying that org tag, for when you want to
clean one batch rather than the whole inbox. Default is a dry run: nothing is
written unless `--apply` is passed. With `--apply` the original is copied to
`<inbox>.bak` before rewriting, and the read and the rewrite happen under the
org transaction lock (`org_transaction.serialized`, decision Q12).
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import org_transaction  # noqa: E402

HEADING_RE = re.compile(
    r"^(\*+)\s+(?:(TODO|NEXT|WAITING|REVIEW|DONE|DEFERRED|SOMEDAY|CANCELLED)\s+)?(.*?)$")
PRIORITY_RE = re.compile(r"^\[#[A-C]\]\s*")
TAGS_RE = re.compile(r"\s+(:[\w:@#%-]+:)\s*$")

DEFAULT_DESTINATIONS = ("org/next_actions.org", "org/research_learning.org")


def split_heading(line: str) -> tuple[int, str, str] | None:
    """Return (level, normalised_title, tag_string) for an org heading line."""
    parsed = _split(line)
    return parsed[:3] if parsed else None


def _split(line: str) -> tuple[int, str, str, str | None] | None:
    """(level, normalised_title, tag_string, todo_state) for a heading line."""
    m = HEADING_RE.match(line.rstrip())
    if not m:
        return None
    level = len(m.group(1))
    rest = m.group(3)
    tags = ""
    tm = TAGS_RE.search(rest)
    if tm:
        tags = tm.group(1)
        rest = rest[: tm.start()]
    title = PRIORITY_RE.sub("", rest).strip()
    title = re.sub(r"\s+", " ", title)
    return level, title, tags, m.group(2)


def destination_titles(paths: list[Path]) -> set[str]:
    titles: set[str] = set()
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            parsed = split_heading(line)
            if parsed and parsed[1]:
                titles.add(parsed[1])
    return titles


def dedup(inbox: Path, dests: list[Path], tag: str | None) -> tuple[list[str], list[tuple[str, str]]]:
    """Return (kept_lines, removed_entries) where removed_entries is [(title, tags)]."""
    known = destination_titles(dests)
    lines = inbox.read_text(encoding="utf-8").splitlines()

    kept: list[str] = []
    removed: list[tuple[str, str]] = []

    # Entries are top-level-plus headings under the "* Inbox" root. We drop a
    # heading and every line beneath it until the next heading at the same or a
    # shallower level, so PROPERTIES drawers and bodies go with their heading.
    i = 0
    n = len(lines)
    while i < n:
        parsed = split_heading(lines[i])
        if parsed is None:
            kept.append(lines[i])
            i += 1
            continue

        level, title, tags = parsed
        is_entry = level >= 2 and bool(title)
        tag_ok = tag is None or (tag in tags)

        if is_entry and tag_ok and title in known:
            start = i
            i += 1
            while i < n:
                nxt = split_heading(lines[i])
                if nxt and nxt[0] <= level:
                    break
                i += 1
            removed.append((title, tags))
            # Trailing blank lines belonging to the removed block go too.
            while kept and kept[-1].strip() == "" and i < n:
                break
            del start  # block consumed
            continue

        kept.append(lines[i])
        i += 1

    return kept, removed


def loose_key(title: str) -> str:
    """Near-match key: case and punctuation ignored. Never used to remove."""
    return " ".join(re.sub(r"[^\w\s]", " ", title.casefold()).split())


def space_destinations(space: Path, inbox: Path) -> list[Path]:
    """The space's other live org files: every org/*.org except the inbox
    itself and anything named like an archive (what left the inbox as
    finished is not proof a live item was routed)."""
    org = space / "org"
    return sorted(p for p in org.glob("*.org")
                  if p.resolve() != inbox.resolve() and "archive" not in p.name.lower())


def dedup_exact(inbox: Path, dests: list[Path]) -> tuple[list[str], list[str], list[str]]:
    """Return (kept_lines, removed_titles, ambiguous_titles).

    An entry is a level-2+ heading, or a level-1 heading carrying a TODO
    state (a top-level capture; a stateless level-1 is a container such as
    "* Inbox" and is never removed). It is removed with its whole subtree
    only on an exact normalised-heading match; a loose-key match is kept
    and returned as ambiguous."""
    exact = destination_titles(dests)
    loose = {loose_key(t) for t in exact}
    lines = inbox.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    removed: list[str] = []
    ambiguous: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        parsed = _split(lines[i])
        if parsed is None:
            kept.append(lines[i])
            i += 1
            continue
        level, title, _tags, state = parsed
        is_entry = bool(title) and (level >= 2 or state is not None)
        if is_entry and title in exact:
            i += 1
            while i < n:
                nxt = _split(lines[i])
                if nxt and nxt[0] <= level:
                    break
                i += 1
            removed.append(title)
            continue
        if is_entry and loose_key(title) in loose:
            ambiguous.append(title)
        kept.append(lines[i])
        i += 1
    return kept, removed, ambiguous


def dedup_space(space: Path, apply: bool, inbox_rel: str = "org/inbox.org") -> dict:
    """Exact-only repair of one space. With `apply`, back up to
    <inbox>.bak and rewrite under the org transaction lock, but only when
    something is removed."""
    inbox = space / inbox_rel
    dests = space_destinations(space, inbox)

    def run() -> dict:
        if apply:
            org_transaction.watch_file(inbox)
        kept, removed, ambiguous = dedup_exact(inbox, dests)
        backup = None
        if apply and removed:
            backup = inbox.with_suffix(inbox.suffix + ".bak")
            shutil.copy2(inbox, backup)
            org_transaction.write_org_text(inbox, "\n".join(kept) + "\n")
        return {"space": space.name, "removed": removed, "ambiguous": ambiguous,
                "backup": str(backup) if backup else None}

    return org_transaction.serialized(run)() if apply else run()


def run_all_spaces(root: Path, apply: bool) -> int:
    """--space-all --exact-only: every space with an inbox. One space's
    failure is reported and does not stop the others; the exit code is
    non-zero only if a space failed."""
    failed = 0
    for space in sorted(p for p in root.glob("[0-9]-*") if (p / "org" / "inbox.org").is_file()):
        try:
            r = dedup_space(space, apply)
        except Exception as e:  # report, keep going
            failed += 1
            print(f"[inbox-dedup] {space.name}: FAILED, inbox left as is: {e}")
            continue
        if not r["removed"] and not r["ambiguous"]:
            continue
        verb = "removed" if apply else "would remove"
        print(f"[inbox-dedup] {space.name}: {verb} {len(r['removed'])} routed copies"
              + (f" (backup {r['backup']})" if r["backup"] else ""))
        for t in r["removed"]:
            print(f"  - {t[:100]}")
        for t in r["ambiguous"]:
            print(f"  ? near-match kept, needs a look: {t[:100]}")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--space", default="0-personal", help="space directory (default: 0-personal)")
    ap.add_argument("--root", default=str(Path.home() / "Data"), help="datacore root")
    ap.add_argument("--inbox", default="org/inbox.org", help="inbox path relative to space")
    ap.add_argument("--dest", action="append", default=None,
                    help="destination org file relative to space (repeatable). "
                         f"Default: {', '.join(DEFAULT_DESTINATIONS)}")
    ap.add_argument("--tag", default=None, help="only remove entries carrying this org tag")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--space-all", action="store_true",
                    help="every space under --root that has org/inbox.org (requires --exact-only)")
    ap.add_argument("--exact-only", action="store_true",
                    help="remove only exact heading matches found in the space's other live org "
                         "files; report near-matches and leave them")
    args = ap.parse_args()

    if args.space_all or args.exact_only:
        if not (args.space_all and args.exact_only):
            ap.error("--space-all and --exact-only go together")
        return run_all_spaces(Path(args.root), args.apply)

    space = Path(args.root) / args.space
    inbox = space / args.inbox
    if not inbox.exists():
        print(f"error: no inbox at {inbox}", file=sys.stderr)
        return 1

    dests = [space / d for d in (args.dest or DEFAULT_DESTINATIONS)]
    missing = [str(d) for d in dests if not d.exists()]
    if missing:
        print(f"warning: destination not found, skipping: {', '.join(missing)}", file=sys.stderr)

    if args.apply:
        return org_transaction.serialized(_run)(inbox, dests, space, args)
    return _run(inbox, dests, space, args)


def _run(inbox: Path, dests: list[Path], space: Path, args) -> int:
    """Report, and with --apply rewrite, under the org lock (decision Q12,
    2026-09-23): the inbox is watched before it is read and rewritten with
    `write_org_text`, so a capture that commits concurrently is neither lost
    nor overwritten by this tool's stale copy. A dry run takes no lock."""
    if args.apply:
        org_transaction.watch_file(inbox)
    kept, removed = dedup(inbox, dests, args.tag)

    scope = f" tagged :{args.tag}:" if args.tag else ""
    print(f"inbox: {inbox}")
    print(f"destinations: {', '.join(str(d.relative_to(space)) for d in dests if d.exists())}")
    print(f"already-routed entries{scope}: {len(removed)}")
    for title, tags in removed:
        print(f"  - {title[:88]}  {tags}")

    if not removed:
        print("\nnothing to remove.")
        return 0

    if not args.apply:
        print(f"\nDRY RUN — {len(removed)} entries would be removed "
              f"({len(inbox.read_text(encoding='utf-8').splitlines())} → {len(kept)} lines). "
              "Re-run with --apply to write.")
        return 0

    backup = inbox.with_suffix(inbox.suffix + ".bak")
    shutil.copy2(inbox, backup)
    org_transaction.write_org_text(inbox, "\n".join(kept) + "\n")
    print(f"\nremoved {len(removed)} entries. backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
