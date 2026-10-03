#!/usr/bin/env python3
"""Three-way merge of an Org file task by task on :ID:, for a sync conflict.

Owner decision, 2026-09-30: when two hosts changed the same .org file, "merge by
task ID, that's why we have org-workspace". A line merge sees a task both hosts
touched as two versions of some lines and, with git's union rule, keeps both:
two copies of one task, one :ID: twice, and org-workspace then refuses to load
the file at all. So a sync merges an .org file here instead, one task at a time.

HOW. Tasks are the flat heading blocks `org_union_merge.split` finds (byte-exact,
keyed by :ID:). For a task both sides have and changed, the fields are read and
written with org-workspace's parser (the vendored orgparse its saves use, which
leaves every untouched line byte-for-byte) and merged against the common base:

  * a field only one side changed takes that side's value;
  * a recorded closure beats an open state (the rule org_union_merge applies:
    a DONE/CANCELLED with a CLOSED stamp is strictly later information);
  * a field both sides changed differently keeps both where that loses nothing
    -- tags become the union, the body keeps both sides' lines -- and otherwise
    (state, heading, priority, dates, a property) keeps THIS host's value and
    records the other in `notes`, which the sync's conflict task names. Never a
    second copy of the task, never a second heading.

That one merged block is put in place of the task in the base and in both
sides, so the three-way line merge that follows sees no disagreement about the
task at all -- only where it sits, which a line merge does handle: a task one
side added, moved or removed lands exactly once. Text outside any task with an
:ID: (a section heading, a heading without an id, the preamble) is left to the
line merge, falling back to git's union rule (both sides' lines) only for what
still overlaps.

The result must load in org-workspace, carry no :ID: twice, and hold every task
either side still has. If it would not -- or a side is already unloadable,
say it carries a duplicate id -- nothing is merged by id: the file stays this
host's copy (the other side is in git history) and a person is asked.

`needs_person` is True when anything was decided or doubled for a person: a
field noted, a body or other lines kept from both sides, or the fallback.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from org_union_merge import split

#: DIP-0009 v2.0 terminal states (DEFERRED is closed-but-wakeable, not terminal).
TERMINAL = ("DONE", "CANCELLED")
HOW = "org: merged task by task on :ID:"


@dataclass
class Merge:
    text: str
    notes: list[dict] = field(default_factory=list)
    needs_person: bool = False
    how: str = HOW


# ── parsing: org-workspace's own parser ──────────────────────────────────────

def _env():
    from org_workspace._types import StateConfig
    from org_workspace._vendor.orgparse.node import OrgEnv
    env = OrgEnv(filename="<string>")
    env.add_todo_keys(*StateConfig.default().env_keys())
    return env


def _node(block: str):
    from org_workspace._vendor.orgparse import loads
    root = loads(block, env=_env())
    return root, root.children[0]


def _date(d) -> str:
    return str(d) if d else ""


def _props(node) -> dict[str, str]:
    from org_workspace._compat import get_multiline_property
    return {k: str(get_multiline_property(node, k)) for k in node.properties}


def _fields(node) -> dict:
    return {"todo": node.todo or "", "priority": node.priority or "", "heading": node.heading,
            "tags": frozenset(node.shallow_tags), "scheduled": _date(node.scheduled),
            "deadline": _date(node.deadline), "closed": _date(node.closed), "body": node.body,
            "props": _props(node)}


def loads_cleanly(text: str) -> list[str] | None:
    """The ids org-workspace finds in `text`, or None when it refuses the file
    (a duplicate :ID: included)."""
    from org_workspace import OrgWorkspace
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "merge.org"
        f.write_text(text)
        try:
            ws = OrgWorkspace()
            ws.load(f)
            return [n.id() for n in ws.all_nodes() if n.id()]
        except Exception:  # noqa: BLE001 -- any refusal means: not mergeable by id
            return None


# ── merging ──────────────────────────────────────────────────────────────────

def _merge_lines(base: str, ours: str, theirs: str) -> tuple[str, bool]:
    """(merged, clean). A three-way line merge; where it overlaps, both sides'
    lines (git's union rule, the fleet's answer for text since 2026-09-02)."""
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for name, data in (("ours", ours), ("base", base), ("theirs", theirs)):
            f = Path(tmp) / name
            f.write_text(data)
            files.append(str(f))
        clean = subprocess.run(["git", "merge-file", "-p", *files], capture_output=True,
                               text=True, timeout=60)
        if clean.returncode == 0:
            return clean.stdout, True
        union = subprocess.run(["git", "merge-file", "-p", "--union", *files], capture_output=True,
                               text=True, timeout=60)
        return union.stdout, False


def _closed(f: dict) -> bool:
    return f["todo"] in TERMINAL and bool(f["closed"])


def _pick(b, o, t):
    """(value, both-changed-differently)."""
    if o == t:
        return o, False
    if b is not None and o == b:
        return t, False
    if b is not None and t == b:
        return o, False
    return o, True


def _merge_block(iid: str, base: str | None, ours: str, theirs: str) -> tuple[str, list[dict], bool]:
    """(merged block, notes, lines doubled) for one task both sides have."""
    root, node = _node(ours)
    fo, ft = _fields(node), _fields(_node(theirs)[1])
    fb = _fields(_node(base)[1]) if base is not None else None
    g = (lambda k: fb[k]) if fb is not None else (lambda k: None)
    notes, doubled, want = [], False, dict(fo)

    def note(name: str, o, t) -> None:
        notes.append({"id": iid, "task": fo["heading"], "field": name, "ours": str(o), "theirs": str(t)})

    # State and its closure travel together: a recorded closure beats an open state.
    if _closed(ft) and not _closed(fo) and fo["todo"] not in TERMINAL:
        want["todo"], want["closed"] = ft["todo"], ft["closed"]
    elif not (_closed(fo) and not _closed(ft) and ft["todo"] not in TERMINAL):
        for k in ("todo", "closed"):
            want[k], clash = _pick(g(k), fo[k], ft[k])
            if clash:
                note("state" if k == "todo" else k, fo[k], ft[k])
    for k in ("heading", "priority", "scheduled", "deadline"):
        want[k], clash = _pick(g(k), fo[k], ft[k])
        if clash:
            note(k, fo[k], ft[k])
    # Tags: a three-way set merge -- a tag stays unless one side removed it.
    tb = g("tags") or frozenset()
    want["tags"] = frozenset(x for x in fo["tags"] | ft["tags"]
                             if (x in fo["tags"] and x in ft["tags"]) or x not in tb)
    # Body: a line merge; overlapping edits keep both sides' lines.
    if fo["body"] != ft["body"]:
        b = g("body")
        if b is not None and fo["body"] == b:
            want["body"] = ft["body"]
        elif not (b is not None and ft["body"] == b):
            want["body"], clean = _merge_lines(b or "", fo["body"], ft["body"])
            if not clean:
                doubled = True
                note("body", "both versions' lines kept", "both versions' lines kept")
    # Properties, key by key.
    pb = g("props")
    props = dict(fo["props"])
    for key in sorted(set(fo["props"]) | set(ft["props"])):
        o, t = fo["props"].get(key), ft["props"].get(key)
        v, clash = _pick(pb.get(key) if pb is not None else None, o, t)
        if clash and (o is None or t is None):
            v = o if o is not None else t        # removed on one side, changed on the other: the edit stays
        elif clash:
            note(f"property {key}", o, t)
        if v is None:
            props.pop(key, None)
        else:
            props[key] = v

    if want == fo and props == fo["props"]:
        return ours, notes, doubled
    if want["todo"] != fo["todo"]:
        node.todo = want["todo"] or None
    if want["priority"] != fo["priority"]:
        node.priority = want["priority"] or None
    if want["heading"] != fo["heading"]:
        node.heading = want["heading"]
    if want["tags"] != fo["tags"]:
        node.tags = sorted(want["tags"], key=lambda x: (x not in fo["tags"], x))
    other = _node(theirs)[1]
    for k in ("scheduled", "deadline", "closed"):
        if want[k] != fo[k]:
            setattr(node, k, getattr(other, k) if want[k] == ft[k] else None)
    if props != fo["props"]:
        from org_workspace._compat import set_multiline_property
        node.properties = {k: ("|" if "\n" in v else v) for k, v in props.items()}
        for k, v in props.items():
            if "\n" in v:
                set_multiline_property(node, k, v)
    if want["body"] != fo["body"]:
        node.body = want["body"]
    from org_workspace._compat import dumps
    trailing = ours[len(ours.rstrip("\n")):] or "\n"
    return dumps(root).rstrip("\n") + trailing, notes, doubled


def merge3(base: str, ours: str, theirs: str) -> Merge:
    """Merge `theirs` into `ours` against their common `base` ("" when none)."""
    ids_o, ids_t = loads_cleanly(ours), loads_cleanly(theirs)
    if ids_o is None or ids_t is None:
        return _fallback(ours, "a side org-workspace cannot load (a duplicate :ID:, or unparseable)")

    pre_b, blocks_b = split(base)
    pre_o, blocks_o = split(ours)
    pre_t, blocks_t = split(theirs)
    bb = {i: blk for i, blk in blocks_b if i}
    bo = {i: blk for i, blk in blocks_o if i}
    bt = {i: blk for i, blk in blocks_t if i}

    notes: list[dict] = []
    doubled = False
    merged: dict[str, str] = {}
    try:
        for iid in sorted(bo.keys() & bt.keys()):
            # A task only one side changed needs no field merge: that side's
            # text, byte for byte, stands for it on every side (so a move by
            # the other side still lands once).
            if bo[iid] != bt[iid] and bb.get(iid) == bo[iid]:
                merged[iid] = bt[iid]
            elif bo[iid] != bt[iid] and bb.get(iid) == bt[iid]:
                merged[iid] = bo[iid]
            elif bo[iid] != bt[iid]:
                merged[iid], n, d = _merge_block(iid, bb.get(iid), bo[iid], bt[iid])
                notes += n
                doubled = doubled or d
    except Exception as exc:  # noqa: BLE001 -- a task the parser cannot take: fall back
        return _fallback(ours, f"a task could not be merged by field ({type(exc).__name__}: {exc})")

    def seq(pre: str, blocks) -> list[tuple[str | None, str]]:
        return ([(None, pre)] if pre else []) + [(i, merged.get(i, blk) if i else blk) for i, blk in blocks]

    text, clean, hunk_notes = _merge_blocks(seq(pre_b, blocks_b), seq(pre_o, blocks_o),
                                            seq(pre_t, blocks_t))
    notes += hunk_notes
    if not clean:
        doubled = True
        notes.append({"id": None, "task": None, "field": "lines outside a task with an :ID:",
                      "ours": "both versions' lines kept", "theirs": "both versions' lines kept"})
    text = _drop_repeats(text)

    # A task either side still has is in the result exactly once; nothing else is.
    removed = {i for i in bb if (i not in bo and bt.get(i) == bb[i])
               or (i not in bt and bo.get(i) == bb[i])}
    expected = (set(bo) | set(bt)) - removed
    found = loads_cleanly(text)
    if found is None or len(found) != len(set(found)) or set(found) != expected:
        return _fallback(ours, "the merged file failed its check (a task missing, doubled or extra)")
    how = HOW + ("; lines outside tasks kept from both sides" if not clean else "")
    return Merge(text, notes, bool(notes) or doubled, how)


def _token(iid: str | None, blk: str) -> str:
    import hashlib
    digest = hashlib.sha1(blk.encode()).hexdigest()
    return f"I:{iid}:{digest}" if iid else "N:" + digest


def _merge_blocks(base, ours, theirs) -> tuple[str, bool, list[dict]]:
    """(merged text, clean, notes). A three-way merge over BLOCKS, not lines.

    Each block becomes one token line (a task by its :ID:, any other block by
    its content) and git's diff3 runs over the tokens, so a task is only ever
    kept, moved or dropped whole -- a line merge interleaves the lines of two
    tasks added at the same place into one broken heading. Where both sides
    changed the same stretch: every task of ours, then every task only theirs
    has; the blocks without an :ID: (headings, notes, preamble) are merged by
    lines, with both sides' lines where they overlap (the only unclean case).
    """
    blocks: dict[str, str] = {}
    toks: dict[str, list[str]] = {}
    # A task's text comes from ours, else theirs, never from base: a task only
    # one side still has is that side's version.
    for name, side in (("ours", ours), ("theirs", theirs), ("base", base)):
        toks[name] = []
        for iid, blk in side:
            t = _token(iid, blk)
            blocks.setdefault(t, blk)
            toks[name].append(t)
    files = []
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("ours", "base", "theirs"):
            f = Path(tmp) / name
            f.write_text("".join(t + "\n" for t in toks[name]))
            files.append(str(f))
        r = subprocess.run(["git", "merge-file", "-p", "--diff3", "-L", "ours", "-L", "base",
                            "-L", "theirs", *files], capture_output=True, text=True, timeout=60)
    out: list[str] = []
    notes: list[dict] = []
    clean = True
    hunk: dict[str, list[str]] | None = None
    part = ""
    for line in r.stdout.splitlines():
        if line.startswith("<<<<<<< "):
            hunk, part = {"ours": [], "base": [], "theirs": []}, "ours"
        elif hunk is not None and line.startswith("||||||| "):
            part = "base"
        elif hunk is not None and line == "=======":
            part = "theirs"
        elif hunk is not None and line.startswith(">>>>>>> "):
            text, ok, n = _resolve_hunk(hunk, blocks)
            out.append(text)
            notes += n
            clean = clean and ok
            hunk = None
        elif hunk is not None:
            hunk[part].append(line)
        else:
            out.append(blocks[line])
    return "".join(out), clean, notes


def _resolve_hunk(h: dict[str, list[str]], blocks: dict[str, str]) -> tuple[str, bool, list[dict]]:
    """Both sides changed one stretch of blocks: ours' tasks in ours' order,
    then the tasks only theirs has; the blocks without an id merged by lines,
    placed where ours (or else theirs) had its first one.

    A task one side removed and the other edited is kept (an edit beats a
    removal, as for whole files) and noted: the removal may have been an
    archive or a refile on the other host, which a person should see."""
    def ids(side: str) -> dict[str, str]:
        return {t[2:].rsplit(":", 1)[0]: t for t in h[side] if t.startswith("I:")}
    notes = []
    for iid, t in ids("base").items():
        for kept, gone, who in (("ours", "theirs", "the other host"), ("theirs", "ours", "this host")):
            if iid not in ids(gone) and ids(kept).get(iid) not in (None, t):
                notes.append({"id": iid, "task": _title(blocks[ids(kept)[iid]]),
                              "field": f"removed on {who}, edited on the other: kept",
                              "ours": "", "theirs": ""})
    def text(side: str) -> str:
        return "".join(blocks[t] for t in h[side] if t.startswith("N:"))
    lines, ok = _merge_lines(text("base"), text("ours"), text("theirs"))
    mine = set(ids("ours"))
    # A task one side removed from this stretch and the other left exactly as
    # in base is removed (a processed inbox capture, a refile), as merge3 says
    # for the whole file. Keeping every task of ours here brought back twelve
    # processed captures in 0-personal on 2026-10-03 when the other host had
    # only appended a capture beside them.
    base_ids = ids("base")
    removed = {iid for iid, t in base_ids.items()
               if (iid not in ids("theirs") and ids("ours").get(iid) == t)
               or (iid not in ids("ours") and ids("theirs").get(iid) == t)}
    out, placed = [], False
    for side in ("ours", "theirs"):
        for t in h[side]:
            if t.startswith("N:"):
                if not placed:
                    out.append(lines)
                    placed = True
            elif t[2:].rsplit(":", 1)[0] in removed:
                continue
            elif side == "ours" or t[2:].rsplit(":", 1)[0] not in mine:
                out.append(blocks[t])
    if not placed:
        out.append(lines)
    return "".join(out), ok, notes


def _title(block: str) -> str:
    return block.splitlines()[0].lstrip("*").strip() if block else ""


def _drop_repeats(text: str) -> str:
    """Drop a later copy of a task block identical to an earlier one.

    Every task both sides have is the same merged block on each side, so a
    second copy can only be the same task placed twice (both hosts moved it to
    different places): byte-identical, and dropping it loses nothing. A copy
    that differs is left, and the check in merge3 then refuses the result."""
    pre, blocks = split(text)
    seen: dict[str, str] = {}
    out = []
    for iid, blk in blocks:
        if iid and iid in seen and seen[iid] == blk:
            continue
        if iid:
            seen.setdefault(iid, blk)
        out.append(blk)
    return pre + "".join(out)


def _fallback(ours: str, why: str) -> Merge:
    return Merge(ours, [{"id": None, "task": None, "field": "whole file",
                         "ours": "this host's copy kept", "theirs": f"not merged: {why}"}],
                 True, f"kept this host's copy: {why}")
