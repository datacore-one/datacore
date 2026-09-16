#!/usr/bin/env python3
"""A restorable checkpoint of the ledger, and proof that it restores.

After the Phase 1 flip the org file is GENERATED, which quietly removes the
fallback everyone assumes is still there: re-running genesis against it would
re-derive the ledger from a file the ledger just produced. Corruption gets
laundered rather than repaired. Re-genesis is only cheap while something
authored-shaped survives, and this is that thing.

Two operations, and the second is the point:

  write    render the ledger to a checkpoint org file, tracked in git so it
           survives losing the machine.

  verify   import that checkpoint into a THROWAWAY space, fold it, and compare
           the result against the live ledger item by item. This answers "could
           we actually rebuild from this?" — which is not the same question as
           "does a checkpoint file exist", and is the only one worth asking. A
           backup nobody has restored is a claim (DIP-0046 F2a, same lesson).

WHERE IT LIVES, AND WHY IT MATTERS MORE THAN IT LOOKS.

`.datacore/checkpoints/`, NEVER inside `org/`. A checkpoint reproduces every
`:ID:` by construction — that is what makes it restorable — so putting it beside
the authored file is precisely the bug that cost 1,204 rewritten ids on
2026-08-12: any tool loading more than one org file from that directory sees
every id twice, `dedup_ids()` regenerates on load, a save persists it, and
autosave commits and pushes it. Tooling globs `<space>/org/*.org`; this
directory is not on that path.

Deliberately NOT gitignored, unlike the projection. The projection is derived
and disposable; this is the restore point, and a restore point that dies with
the machine is not one.

    ledger_checkpoint.py write  [--space NAME] [--root DIR]
    ledger_checkpoint.py verify [--space NAME] [--root DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

from ledger.fold import fold  # noqa: E402
from ledger.genesis import import_space  # noqa: E402
from ledger.log import read_events  # noqa: E402
from ledger.projector import _clean_title_and_tags, _org_stamp, project, projected_items  # noqa: E402

# The projector's own notion of live. Since 2026-09-06 `completed` is live
# (an agent finished, nobody signed off: REVIEW in org), so a round-trip
# through the projection carries those items and this side must count them
# too — with the state the projector renders, or every completed item comes
# back "invented" and 2-datacore reports 58 of them.
from ledger.projector import LIVE_STATUSES as LIVE  # noqa: E402
from org_transaction import serialized, watch_file, write_org_text  # noqa: E402
CHECKPOINT_REL = Path(".datacore") / "checkpoints" / "next_actions.org"
SNAPSHOT_REL = Path(".datacore") / "checkpoints" / "ledger.json"
VIEW_FIELDS = ('title', 'state', 'tags', 'scheduled', 'deadline', 'body', 'properties', 'priority', 'created')


def checkpoint_paths(space: Path) -> tuple[Path, Path]:
    """Disjoint backup files for each declared writer, as for event chains."""
    from actor_identity import this_actor
    actor = this_actor(strict=True)
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', actor):
        raise ValueError('checkpoint requires a declared safe writer identity')
    directory = space / '.datacore/checkpoints' / actor
    return directory / 'next_actions.org', directory / 'ledger.json'


def _default_root() -> Path:
    return Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))


def _rendered_tags(state, known: set[str], iid: str) -> set[str]:
    """The tags the projection carries for `iid`, derived from the LEDGER.

    Org tag inheritance follows physical nesting, and the projector nests a
    child under its parent whenever that parent is itself projected. So the
    tags an item comes back with are its own (as rendered) plus every rendered
    ancestor's own -- and that is computable from the ledger's `parent` links
    without trusting anything recorded at import time.

    The recorded `effective_tags` snapshot is NOT that. It is what the source
    file said when the item was ingested, and nothing refreshes it when the
    parent's tags change or the item is re-filed: 0-personal's
    org-20260811-191903 was captured in inbox.org, later parented under
    `* Routed from inbox :inbox:routed:`, and kept a snapshot without those two
    tags -- so a projection that correctly nested it under that heading was
    reported as having corrupted it (2026-08-31, together with five 5-plur
    items whose parent had gained `AI`). Comparing the snapshot asked the
    restore to reproduce a state the ledger no longer describes.

    The one place the snapshot IS the truth is a promoted orphan: its parent is
    not in the file, so the projector writes the snapshot as the item's own tags
    (see projector.py). Mirrored here, for the same reason -- and mirrored for
    ANCESTORS too: a child under a promoted parent inherits that parent's
    rendered snapshot, not its declared tags. 3-fds org-2cd3c1c78434 (level 4,
    under a level-3 task whose own parent had closed) came back with the four
    tags its promoted parent now carries on its heading; a walk that read the
    parent's declared tags reported that correct inheritance as an alteration.
    """
    tags = _heading_tags(state, known, iid)
    cur, seen = (state.items[iid].payload or {}).get("parent"), set()
    while cur and cur in known and cur not in seen:
        seen.add(cur)
        tags |= _heading_tags(state, known, cur)
        cur = (state.items[cur].payload or {}).get("parent")
    return tags


def _heading_tags(state, known: set[str], iid: str) -> set[str]:
    """The tags the projector writes on `iid`'s own heading: its declared tags,
    or -- when its parent is not projected and it is promoted to top level --
    its recorded effective set, so the tags it used to inherit travel with it.
    Same rule as projector.project(); same normalisation as render_item()."""
    item = state.items[iid]
    p = item.payload or {}
    parent = p.get("parent")
    base = p.get("tags")
    if parent and parent not in known:
        base = p.get("effective_tags") or p.get("tags") or []
    return set(_clean_title_and_tags(item.title, list(base or []) + list(p.get('filetags') or []))[1])


def _fingerprint(state, space_filetags: set | None = None,
                 space: str | None = None) -> dict[str, tuple]:
    """Editable task-view fidelity, separate from complete snapshot restoration.

    This diagnostic also covers notes, properties, priority and creation time.
    It does not claim Org can preserve ledger-only authority or history; verify()
    restores those from the full saved event snapshot and checks its state root.
    A fresh import
    writes new events with new hashes and hlcs, so the CHAIN differs by design
    -- what must survive is the ITEMS.

    Both sides are reduced to what the PROJECTION renders, because that is the
    only form a restore can travel through. Title and tags go through the
    projector's own normalisation (`_clean_title_and_tags`): a tag block the
    original parser left inside a title is split out, and characters the
    parser cannot read become `_`. Tags are the RENDERED effective set --
    derived from the ledger's parent links, see `_rendered_tags` -- not the
    per-item snapshot recorded at import.

    Source-file tags are part of each task's effective tags. A combined view
    puts only their intersection in its header, so they must also be compared
    when represented directly on individual headings. An explicitly supplied
    common space tag set may be removed symmetrically for legacy diagnostics.
    """
    known = {i.id for i in projected_items(state, space=space)}
    out = {}
    for iid, item in state.items.items():
        if item.status not in LIVE:
            continue
        p = item.payload or {}
        # SECTIONS ARE DERIVED, NOT STORED. genesis imports a plain heading only
        # as the ANCESTOR of a task that lives under it (`_section_payload`),
        # and its own docstring is explicit that sections are "re-derived, not
        # re-imported". A section whose children have all been closed therefore
        # has nothing to re-derive it, and correctly does not come back.
        #
        # Measuring it as lost was measuring structure as if it were content:
        # one such heading in 2-datacore was the sole reason a space reported
        # "would NOT restore" while every task in it restored perfectly.
        if p.get("section"):
            continue
        eff = _rendered_tags(state, known, iid)
        # Remove only an explicitly supplied common space constant, never
        # discard a source-specific tag that could be lost or reassigned.
        eff -= (space_filetags or set())
        title = _clean_title_and_tags(item.title, p.get("tags"))[0]
        # Normalise timestamps before comparing. Some writers store a bare
        # `2026-08-14`; the projector renders the valid org form
        # `<2026-08-14 Fri>`. Those denote the SAME date, so comparing the raw
        # strings reported eight items as altered by a restore that preserved
        # them exactly -- the mirror of the bug just fixed in the projector.
        # Normalise STATE the way the projector does. `projector.py` renders
        # `payload.get("state") or "TODO"`, so an item created without a state
        # -- every task the nightshift executor admits, for one -- projects as
        # TODO and re-imports as TODO, while the live side still reads None.
        # Comparing the raw values reported 30 such items in winston's
        # 0-personal as "altered" by a restore that preserved them exactly
        # (2026-08-30), the same false-alarm class as the timestamp and
        # filetag asymmetries above. A missing state MEANS TODO here.
        rendered_state = "REVIEW" if item.status == "completed" else (p.get("state") or "TODO")
        org = p.get('org') or {}
        created = org.get('created')
        genesis = p.get('genesis') or {}
        if not created and genesis.get('date') and genesis.get('rung') not in ('genesis_fallback', 'section'):
            created = f"[{genesis['date']}]"
        out[iid] = (title, rendered_state,
                    tuple(sorted(eff)),
                    _org_stamp(p.get("scheduled")), _org_stamp(p.get("deadline")),
                    (org.get('body') or '').rstrip(), org.get('properties') or {},
                    org.get('priority'), created or None)
    return out


@serialized
def write(space: Path) -> Path:
    """Save the complete observed event set alongside the human-readable view.

    Org alone cannot restore terminal history, approvals, claims, unknown event
    types or fields that its renderer does not represent. A versioned snapshot
    carries every original chain; neither file is acknowledged before both are
    durably published in the recoverable filesystem transaction.
    """
    from ledger.events import to_line
    events = read_events(space)
    state = fold(events)
    text = project(state, space=space.name).text
    dest, snapshot_path = checkpoint_paths(space)
    chains = {}
    for event in events:
        chains.setdefault(event.log + '.jsonl', []).append(event)
    chains = {name: ''.join(to_line(event) + '\n' for event in sorted(chain, key=lambda e: e.seq))
              for name, chain in chains.items()}
    document = {'version': 1, 'state_root': state.state_root(), 'chains': chains,
                'org_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}
    saved = watch_file(snapshot_path)['before']
    previous_org = watch_file(dest)['before']
    legacy_org = watch_file(space / CHECKPOINT_REL)['before']
    if saved is not None:
        previous = json.loads(saved)
        _restore(previous, space.name)
        if previous_org is None or hashlib.sha256(previous_org.encode()).hexdigest() != previous['org_sha256']:
            raise ValueError('previous checkpoint was changed; preserve and reconcile it before replacement')
        # Append-only history cannot shrink. A lost/rewound live log must not
        # replace the last good backup with the damaged state.
        for filename, old_chain in previous['chains'].items():
            if not chains.get(filename, '').startswith(old_chain):
                raise ValueError('live ledger lost or replaced saved history; refusing checkpoint replacement')
    elif previous_org is not None or legacy_org is not None:
        # First format upgrade retains the old Org-only restore point.
        previous_org = previous_org if previous_org is not None else legacy_org
        digest = hashlib.sha256(previous_org.encode()).hexdigest()
        archive = dest.with_name(f'legacy-{digest}.org')
        before_archive = watch_file(archive)['before']
        if before_archive not in (None, previous_org):
            raise ValueError('legacy checkpoint archive conflicts')
        if before_archive is None:
            write_org_text(archive, previous_org)
    # Test the saved representation before replacing the previous checkpoint.
    _restore(document, space.name)
    write_org_text(dest, text)
    write_org_text(snapshot_path, json.dumps(document, sort_keys=True) + '\n')
    return dest


def _restore(document, name):
    """Actually restore a saved snapshot in disposable storage and verify it.

    These are integrity checks, not independent signer authentication. The
    checkpoint must be obtained from the deployment's trusted backup source.
    """
    from ledger.events import body_dict, compute_hash, from_line
    from ledger.exceptions import is_recorded
    if (not isinstance(document, dict) or document.get('version') != 1
            or not isinstance(document.get('chains'), dict)
            or not isinstance(document.get('state_root'), str)
            or not isinstance(document.get('org_sha256'), str)):
        raise ValueError('invalid checkpoint metadata')
    with tempfile.TemporaryDirectory(prefix='ledger-restore-') as temporary:
        scratch = Path(temporary) / name
        folder = scratch / '.datacore/events'
        folder.mkdir(parents=True)
        for filename, text in document['chains'].items():
            if (not isinstance(filename, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.jsonl', filename)
                    or not isinstance(text, str) or not text.endswith('\n')):
                raise ValueError('invalid saved chain')
            previous = 'GENESIS'
            for sequence, line in enumerate(text.splitlines()):
                event = from_line(line)
                computed = compute_hash(body_dict(event.seq, event.hlc, event.actor,
                                                  event.type, event.payload, event.prev))
                if event.seq != sequence or event.prev != previous:
                    raise ValueError('saved event chain fails integrity verification')
                if event.hash != computed and not is_recorded(
                        name, filename, event.seq, event.hash, computed):
                    # Refusing the whole space over one already-written event
                    # left it with NO restore point, forever -- the opposite of
                    # what this check is for. A reviewed exception pins both
                    # hashes, so it excuses only that event exactly as written;
                    # any edit to it, and every other mismatch, still fails here.
                    raise ValueError('saved event chain fails integrity verification')
                previous = event.hash
            (folder / filename).write_text(text, encoding='utf-8')
        restored = fold(read_events(scratch))
        if restored.state_root() != document['state_root']:
            raise ValueError('restored state differs from saved state root')
        return restored


def _space_filetags(space: Path) -> set:
    """The filetags of the file the round-trip re-imports through. Both sides
    are compared with these removed, so an item that never lived in
    next_actions.org is not penalised for lacking a `filetags` record only
    the projection could have given it."""
    na = space / "org" / "next_actions.org"
    try:
        for _l in na.read_text(encoding="utf-8", errors="replace").splitlines()[:10]:
            if _l.startswith("#+FILETAGS:"):
                return {x for x in _l.split(":", 1)[1].split(":") if x.strip()}
    except OSError:
        pass
    return set()


def round_trip(state, space_name: str, space_ft: set | None = None):
    """Project `state`, re-import the projection into a throwaway space, and
    return (live_fingerprint, restored_fingerprint, fresh_projection_text).

    The comparison core, separated from the on-disk checkpoint so it can be
    exercised on a synthetic state -- every false alarm this tool has raised
    was a fingerprint asymmetry, and those need a test that does not depend on
    what happens to be in a real space today.
    """
    space_ft = space_ft or set()
    live = _fingerprint(state, space_ft, space=space_name)
    fresh = project(state, space=space_name).text
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td) / space_name
        (scratch / ".datacore" / "events").mkdir(parents=True)
        (scratch / "org").mkdir()
        (scratch / "org" / "next_actions.org").write_text(fresh, encoding="utf-8")
        import_space(scratch, org_file=scratch / "org" / "next_actions.org")
        restored = _fingerprint(fold(read_events(scratch)), space_ft, space=space_name)
    return live, restored, fresh


def compare(live: dict, restored: dict) -> tuple[bool, str]:
    missing = sorted(set(live) - set(restored))
    extra = sorted(set(restored) - set(live))
    changed = sorted(i for i in (set(live) & set(restored)) if live[i] != restored[i])
    if not (missing or extra or changed):
        return True, f"{len(live)} item(s) restore identically"
    parts = []
    if missing:
        parts.append(f"{len(missing)} lost (e.g. {missing[0]})")
    if extra:
        parts.append(f"{len(extra)} invented")
    if changed:
        parts.append(f"{len(changed)} altered (e.g. {changed[0]})")
    return False, "; ".join(parts)


def verify(space: Path) -> tuple[bool, str]:
    """Verify the files actually saved, without first regenerating a backup."""
    cp, snapshot_path = checkpoint_paths(space)
    if not cp.is_file():
        return False, "no checkpoint written yet"

    try:
        document = json.loads(snapshot_path.read_text(encoding='utf-8'))
        if hashlib.sha256(cp.read_bytes()).hexdigest() != document.get('org_sha256'):
            return False, 'saved Org checkpoint differs from its snapshot checksum'
        restored = _restore(document, space.name)
    except FileNotFoundError:
        return False, 'legacy Org-only checkpoint has no complete ledger snapshot; write a new checkpoint'
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return False, f'saved checkpoint cannot be restored ({type(exc).__name__})'
    detail = f'{len(restored.items)} item(s), including full payloads and history, restored from saved checkpoint'
    if restored.state_root() != fold(read_events(space)).state_root():
        detail += ' [valid older restore point; current ledger has changed — run: write]'
    return True, detail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["write", "verify"])
    ap.add_argument("--root", type=Path, default=_default_root())
    ap.add_argument("--space")
    a = ap.parse_args()

    spaces = [s for s in sorted(a.root.glob("[0-9]-*"))
              if (s / ".datacore" / "events").is_dir()
              and (not a.space or s.name == a.space)]
    if not spaces:
        print(f"ERROR: no spaces with a ledger under {a.root} — refusing to report success")
        return 2

    bad = 0
    for space in spaces:
        if a.op == "write":
            dest = write(space)
            print(f"  {space.name:<14} checkpoint -> {dest.relative_to(space)}")
        else:
            ok, detail = verify(space)
            print(f"  {'ok  ' if ok else 'FAIL'} {space.name:<14} {detail}")
            bad += 0 if ok else 1

    if a.op == "verify":
        print(f"\ncheckpoint-verify: {len(spaces)} space(s), {bad} that would NOT restore")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
