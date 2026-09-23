#!/usr/bin/env python3
"""The one time a Datacore install says hello.

`datacore init` finishes in a terminal, prints what it built, and tells the
user to run `cd ~/Data && claude`. That next command is where they meet their
assistant for the first time -- and until now it opened on a blank prompt,
identical to the ten-thousandth session. The installer knew what it had just
built; the assistant did not.

So the installer leaves a marker naming what exists, and the SessionStart hook
spends it. Spends, not reads: the marker is renamed before the greeting is
emitted, so a second session -- or a crash mid-greeting -- cannot produce a
second welcome. A greeting that repeats is not a greeting.

This is deliberately NOT an animation. Claude Code renders markdown into a
transcript; there is no cursor to move and no frames to draw, so anything
claiming to animate here would just be a static block pretending. The moment
is carried by the assistant knowing who it is and what it has, which is a thing
a terminal cannot do at all.

Written by `datacore init`; consumed by session_bootstrap.py.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
MARKER = DATACORE_ROOT / ".datacore" / "state" / "first-run.json"
SPENT = MARKER.with_suffix(".json.done")


def write_marker(facts: dict) -> Path:
    """Record what an install just built. Called by the installer."""
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    tmp = MARKER.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(facts, indent=2))
    tmp.replace(MARKER)  # atomic: a half-written marker greets nobody
    return MARKER


def consume() -> str | None:
    """Return the greeting brief exactly once, or None.

    The rename happens BEFORE the text is built. Two sessions opening together
    is normal on this machine -- one of them loses the rename and gets None,
    which is the correct outcome. Doing it the other way round would greet both.
    """
    if not MARKER.exists():
        return None
    try:
        os.replace(MARKER, SPENT)
    except OSError:
        return None  # someone else got there first

    try:
        facts = json.loads(SPENT.read_text())
    except (OSError, ValueError):
        return None

    return _brief(facts)


def _brief(f: dict) -> str:
    cos = f.get("cosName") or "Winston"
    spaces = f.get("spaces") or []
    modules = f.get("modules") or []
    name = f.get("userName") or ""

    known = [f"You are {cos}, and this is the first time this person has opened Datacore."]
    if name:
        known.append(f"They are {name}.")
    known.append(
        f"Their knowledge base is at {f.get('dataDir', DATACORE_ROOT)}, "
        f"holding {len(spaces)} space(s): {', '.join(spaces) if spaces else 'none yet'}."
    )
    known.append(
        f"{len(modules)} module(s) installed: {', '.join(modules)}."
        if modules else
        "No modules are installed yet."
    )
    if not f.get("memoryConnected", True):
        known.append(
            "PLUR is NOT connected, so nothing said here will survive the session. "
            "Mention it once, plainly, and offer to fix it."
        )
    if not f.get("hooksArmed", True):
        known.append("Git safety hooks are not armed — worth saying once.")

    return (
        "[Datacore — first run]\n\n"
        + "\n".join(known)
        + "\n\nGreet them once, in your own voice, in a few sentences. Introduce yourself by "
        "name, say briefly what you can do with what is actually installed above, and offer "
        "ONE concrete first thing to do — capturing something to the inbox, or /today.\n\n"
        "Do not list features, do not print a banner or ASCII art, and do not describe "
        "capabilities that the modules above do not provide. If they typed a real request "
        "along with this, answer that first and keep the greeting to a single line — they "
        "came to do something, not to be welcomed.\n\n"
        "This fires exactly once, ever. It will not repeat."
    )


if __name__ == "__main__":  # manual check: python3 first_run.py
    out = consume()
    print(out if out else "no pending first run")
