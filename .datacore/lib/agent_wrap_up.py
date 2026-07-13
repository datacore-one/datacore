#!/usr/bin/env python3
"""Agent session wrap-up: write the journals, land the work, push it.

The missing trigger
-------------------
The Firm's agents already had the memory half of wrap-up and none of the git
half. Tris's PLUR store held engrams dated 07-06, 07-08, 07-12, 07-13 — perfectly
current — while his 53 competitor scans sat uncommitted for two months.
`plur_session_end` ran. `/wrap-up` never did.

That is why nobody noticed. The system had a pulse. Memory flowed, engrams
accumulated, the agents looked alive and learning — while every actual artifact
stayed on disk. The half that ran MASKED the half that didn't.

What this does
--------------
One journal per SPACE that had work, plus the agent's personal journal. Not two
tiers — a session can touch several spaces and each gets its own professional
entry, which is the pattern journal-coordinator already uses (discover [0-9]-*/,
write per space that had work).

  personal   verbose: reasoning, dead ends, what I got wrong, what I learned
  per-space  professional: what changed, why it matters, what's next, links

Then every changed path is committed through knowledge_commit, so knowledge lands
on the DEFAULT branch no matter which branch the agent happens to be standing on,
and code is left on the working branch where it belongs. Without that routing,
this script would only make the old disaster better-documented: 610 commits went
to a stray branch not because nobody wrote journals, but because nobody checked
where they were being written to.

Tiers, because a heartbeat is not a session
-------------------------------------------
Miles heartbeats every few minutes. Running a full 20-step wrap-up per tick costs
more than the work it wraps. So:

  tick     commit the artifact, one journal line. No panel, no learning pass.
  session  full: per-space journals, personal journal, engram capture, push.
  batch    same as session, run once at batch end, not per task.

Usage:
    agent_wrap_up.py --agent miles --tier session \
        --summary "what I did"                        # same entry everywhere
    agent_wrap_up.py --agent tris --tier session \
        --entries entries.json                        # {"5-plur": "...", ...}
    agent_wrap_up.py --agent data --tier tick --summary "cadence tick" --dry-run
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from knowledge_commit import (  # noqa: E402
    classify, commit_knowledge, current_branch, default_branch,
)


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True)
    return (r.stdout or '').strip() if r.returncode == 0 else ''


def _porcelain(repo: Path) -> str:
    """Changed paths, as FILES.

    Two traps here, both of which produce a silent no-op rather than an error:

    1. `--porcelain` alone COLLAPSES untracked directories: a brand-new
       `3-knowledge/` full of zettels reports as the single line `?? 3-knowledge/`.
       knowledge_commit only commits files, so every one of those zettels would be
       filtered out and the wrap-up would cheerfully report "(no change)".
       `-uall` expands them.

    2. Leading whitespace is significant — ' M path' means modified-not-staged.
       Stripping the line shifts every column and eats the first character of the
       path.
    """
    r = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=all'],
                       cwd=repo, capture_output=True, text=True)
    return (r.stdout or '').rstrip('\n') if r.returncode == 0 else ''


def discover_spaces(data_dir: Path) -> list:
    """Every space repo, plus the root. Same discovery journal-coordinator uses."""
    spaces = []
    if (data_dir / '.git').exists():
        spaces.append(data_dir)
    for sub in sorted(data_dir.iterdir()):
        if sub.is_dir() and sub.name[:1].isdigit() and (sub / '.git').exists():
            spaces.append(sub)
    return spaces


def changed_paths(repo: Path) -> list:
    """Paths the agent touched in this repo, junk excluded."""
    out = []
    for line in _porcelain(repo).splitlines():
        if not line.strip():
            continue
        path = line[3:].strip().strip('"')
        name = Path(path).name
        if '.bak' in name or name.endswith(('.pyc', '.swp', '.orig')):
            continue
        if '.local.' in name:          # DIP-0002 private layer — never shared
            continue
        if any(p in ('__pycache__', 'node_modules', '.venv', 'dist')
               for p in Path(path).parts):
            continue
        out.append(path)
    return out


def journal_path(space: Path, date: str) -> Path:
    """0-personal keeps journals under notes/journals/; team spaces use journal/.
    Detection, not memory — CLAUDE.md says check for notes/journals/ first."""
    if (space / 'notes' / 'journals').is_dir():
        return space / 'notes' / 'journals' / f'{date}.md'
    return space / 'journal' / f'{date}.md'


def _strip_heading(text: str, date: str) -> str:
    """Drop a leading '# YYYY-MM-DD' title so it isn't duplicated when merging."""
    lines = text.splitlines()
    if lines and lines[0].strip() == f'# {date}':
        return '\n'.join(lines[1:]).strip('\n')
    return text.strip('\n')


def write_journal(space: Path, agent: str, text: str, date: str,
                  stamp: str) -> Path:
    """Append this agent's entry to the space's journal for today.

    The journal lives on the DEFAULT BRANCH, and that is the only authoritative
    copy. The working tree frequently does not have it: knowledge_commit deletes
    the local copy after landing it on main, because leaving it untracked there is
    what stops git from switching branches.

    So a naive `if not jp.exists(): write fresh` destroys history. Concretely:
    Miles wraps up (journal -> main, local copy removed); Tris wraps up, finds no
    local journal, writes a fresh one containing only his own entry, and commits
    it over main. Miles's entry is gone. That is the very data loss this whole
    exercise exists to prevent, reintroduced by the fix for it.

    Base on the branch copy. Never drop it.
    """
    jp = journal_path(space, date)
    jp.parent.mkdir(parents=True, exist_ok=True)
    rel = jp.relative_to(space).as_posix()

    branch_text = _git(space, 'show', f'{default_branch(space)}:{rel}')
    wt_text = jp.read_text() if jp.exists() else ''

    if branch_text and wt_text.startswith(branch_text.rstrip('\n')):
        base = wt_text                      # working tree = branch + local additions
    elif branch_text and not wt_text:
        base = branch_text                  # local copy was cleaned up after landing
    elif branch_text and wt_text:
        # Divergent. Keep BOTH — never silently drop committed history.
        base = (branch_text.rstrip('\n') + '\n\n'
                + _strip_heading(wt_text, date))
    else:
        base = wt_text or f'# {date}\n'

    entry = f'\n## {agent.title()} — {stamp}\n\n{text.strip()}\n'
    jp.write_text(base.rstrip('\n') + '\n' + entry)
    return jp


def wrap_up(data_dir: Path, agent: str, tier: str, summary: str,
            entries: dict, dry_run: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    date, stamp = now.strftime('%Y-%m-%d'), now.strftime('%H:%M')

    report = {'agent': agent, 'tier': tier, 'spaces': [], 'skipped': []}

    for space in discover_spaces(data_dir):
        changed = changed_paths(space)
        if not changed:
            continue

        name = space.name
        split = classify(changed)

        # A space that only saw code changes gets no journal — the code is the
        # record, and it belongs on the working branch, not main.
        text = entries.get(name, summary)
        wrote_journal = None

        if split['knowledge'] or tier != 'tick':
            if text:
                jp = write_journal(space, agent, text, date, stamp)
                wrote_journal = str(jp.relative_to(space))
                changed = changed_paths(space)   # re-read: the journal is new
                split = classify(changed)

        entry = {
            'space': name,
            'branch': current_branch(space),
            'default': default_branch(space),
            'journal': wrote_journal,
            'knowledge': split['knowledge'],
            'code_left_on_branch': split['code'],
            'commit': '',
        }

        if dry_run:
            entry['commit'] = '(dry-run)'
        elif split['knowledge']:
            msg = (f"{agent}: session wrap-up {date} {stamp}\n\n"
                   f"{(text or '').strip()[:400]}\n")
            try:
                r = commit_knowledge(space, split['knowledge'], msg, push=True)
                entry['commit'] = r['commit'] or '(no change)'
            except Exception as e:                      # noqa: BLE001
                entry['commit'] = f'FAILED: {e}'
                report['skipped'].append(f'{name}: {e}')

        report['spaces'].append(entry)

    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--agent', required=True, help='miles | tris | data')
    ap.add_argument('--tier', default='session', choices=['tick', 'session', 'batch'])
    ap.add_argument('--summary', default='', help='entry used for every space')
    ap.add_argument('--entries', help='JSON file: {"5-plur": "...", "0-personal": "..."}')
    ap.add_argument('--data-dir', default=str(Path.home() / 'Data'))
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    entries = {}
    if a.entries:
        entries = json.loads(Path(a.entries).read_text())

    if not a.summary and not entries:
        print('nothing to write: pass --summary or --entries', file=sys.stderr)
        return 2

    r = wrap_up(Path(a.data_dir).expanduser(), a.agent, a.tier,
                a.summary, entries, dry_run=a.dry_run)

    if not r['spaces']:
        print(f"{a.agent}: nothing to wrap up — no changes in any space.")
        return 0

    print(f"{a.agent} wrap-up ({a.tier}){' [DRY RUN]' if a.dry_run else ''}")
    for s in r['spaces']:
        head = s['branch']
        note = '' if head == s['default'] else f"  [HEAD on {head} — knowledge still routed to {s['default']}]"
        print(f"\n  {s['space']}{note}")
        if s['journal']:
            print(f"    journal: {s['journal']}")
        for p in s['knowledge']:
            print(f"    + {p}")
        for p in s['code_left_on_branch']:
            print(f"    . {p}  (code — stays on {head})")
        print(f"    commit: {s['commit']}")

    if r['skipped']:
        print('\n  FAILURES:')
        for f in r['skipped']:
            print(f'    {f}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
