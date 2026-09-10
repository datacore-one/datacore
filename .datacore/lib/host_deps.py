#!/usr/bin/env python3
"""Which declared Python dependencies this host can actually import.

WHY THIS EXISTS. Twice now a dependency has been missing on the box and
announced itself only as a section quietly not appearing: `gTTS` (the voice
briefing never arrived) and `pandas` (the health fragment composed with
`readiness: None`, which the gate then reported as "no reading landed yet"
forever). Neither raised an alarm, because a module whose import fails at the
top does not announce itself — it just stops contributing while the surrounding
workflow keeps rendering.

So this asks the only question that matters: for the work THIS host does, is
every declared dependency importable right now? It tests the import, not the
presence of a wheel or a line in a requirements file — those can both be true
while `import pandas` still fails under the interpreter cron actually uses.

RUN IT WITH THE INTERPRETER THE JOBS USE. On the box that is
`/usr/bin/python3`, because the CoS scripts hardcode
`PYTHON="${COS_ROUTE_PYTHON:-/usr/bin/python3}"`. Checking with a venv
interpreter that has the package is how you conclude "installed" about a
package the 04:00 job cannot see.

    python3 .datacore/lib/host_deps.py --profile briefing
    python3 .datacore/lib/host_deps.py --profile briefing --pip-args

EXCLUSIONS ARE DECLARED, NOT SILENT. `EXCLUDE` below names every dependency
deliberately not wanted on a briefing host and why. A dependency that is simply
forgotten looks identical to one that was considered and declined, which is how
"all dependencies are installed" gets reported about a host missing four of
them. Anything excluded is printed under its reason, so the claim is auditable.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import re
import sys
from pathlib import Path

DATACORE_ROOT = Path(__file__).resolve().parents[1]

#: Requirement files whose contents a briefing host must be able to import.
#: The box runs the morning pipeline: core lib, the health fragment, news,
#: and the org/GTD read path. It does not run nightshift, forge or voice.
PROFILES: dict[str, tuple[str, ...]] = {
    "briefing": (
        "lib/requirements.txt",
        "modules/health/requirements.txt",
        "modules/crm/requirements.txt",
        "modules/ventures/requirements.txt",
    ),
    "all": ("**/requirements.txt",),
}

#: dist name -> import name, where they differ.
IMPORT_NAME = {
    "pyyaml": "yaml",
    "gtts": "gtts",
    "org-workspace": "org_workspace",
    "python-dateutil": "dateutil",
    "pillow": "PIL",
    "python-louvain": "community",
    "python-telegram-bot": "telegram",
    "python-dotenv": "dotenv",
    "google-generativeai": "google.generativeai",
    "claude-agent-sdk": "claude_agent_sdk",
    "faster-whisper": "faster_whisper",
    "piper-tts": "piper",
    "fpdf2": "fpdf",
    "presidio-analyzer": "presidio_analyzer",
    "presidio-anonymizer": "presidio_anonymizer",
    "sqlcipher3": "sqlcipher3",
    "pytesseract": "pytesseract",
    "pdf2image": "pdf2image",
    "colpali-engine": "colpali_engine",
}

#: Deliberately NOT installed on a briefing host, with the reason. Printed, so
#: the exclusion is a decision on the record rather than an omission.
EXCLUDE = {
    "torch": "nightshift's embedding stack; ~2GB, and nightshift runs on its own host",
    "lancedb": "nightshift vector store; not part of the morning pipeline",
    "colpali-engine": "nightshift vision embeddings; pulls torch",
    "playwright": "forge browser automation; needs a browser runtime",
    "faster-whisper": "voice-terminal STT; the box has no microphone",
    "openwakeword": "voice-terminal wake word; the box has no microphone",
    "piper-tts": "voice-terminal local TTS; gTTS is the box's path",
    "sounddevice": "voice-terminal audio I/O; the box has no sound device",
    "sqlcipher3": "health medical records; needs libsqlcipher, not used by the fragment",
    "pytesseract": "health OCR; needs the tesseract binary",
    "pydicom": "health imaging; not used by the fragment",
    "pdf2image": "health OCR; needs poppler",
    "presidio-analyzer": "health PII scrub; pulls spacy models, dashboard-only",
    "presidio-anonymizer": "health PII scrub; dashboard-only",
    "pdfplumber": "health record ingest; not used by the fragment",
    "flask": "health dashboard; the box serves no dashboard",
    "pytest": "test-only",
    "playwright==1.49.1": "forge browser automation",
}

_NAME = re.compile(r"^([A-Za-z0-9._-]+)")


def parse(path: Path) -> list[str]:
    """Distribution names declared in one requirements file."""
    out: list[str] = []
    try:
        text = path.read_text()
    except OSError:
        return out
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = _NAME.match(line)
        if m:
            out.append(m.group(1).lower())
    return out


def files_for(profile: str) -> list[Path]:
    base = DATACORE_ROOT
    pats = PROFILES.get(profile) or PROFILES["briefing"]
    found: list[Path] = []
    for pat in pats:
        if "*" in pat:
            found.extend(sorted(base.glob(pat)))
        else:
            found.append(base / pat)
    return [p for p in found if p.is_file()]


def _venv_site_packages() -> str | None:
    """`.datacore/venv`'s site-packages, via the module that already decides it.

    NOT OPTIONAL, AND NOT COSMETIC. On the Mac, `feedparser` and `gTTS` live
    only in that venv and are reached by scripts calling
    `venv_bootstrap.activate()` — the documented arrangement, because Homebrew's
    python is externally managed. A checker that ignores it reports two
    perfectly working dependencies as missing, and a report with known false
    entries is one nobody reads the true entries of.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from venv_bootstrap import venv_site_packages
        return venv_site_packages(str(DATACORE_ROOT))
    except Exception:                                     # noqa: BLE001
        return None


def importable(dist: str) -> tuple[bool, str, str]:
    """Does `import <dist>` actually succeed under this interpreter?

    IT MUST BE A REAL IMPORT, IN A SUBPROCESS. The first version of this called
    `importlib.util.find_spec`, which only locates the module and never runs it
    — so it reported pandas as importable on the box at a moment when
    `import pandas` died with `ValueError: numpy.dtype size changed` (apt's
    pandas 2.1.4 compiled against numpy 1.x, pip's numpy 2.x shadowing it).
    A checker that passes on a broken install is worse than no checker, because
    it converts a loud failure into a confident all-clear.

    The subprocess matters too: an ABI mismatch can abort the interpreter
    outright rather than raise, and that must be reported, not take the report
    down with it.
    """
    mod = IMPORT_NAME.get(dist, dist.replace("-", "_"))
    venv = _venv_site_packages()
    env = dict(os.environ)
    if venv:
        env["PYTHONPATH"] = os.pathsep.join(
            [p for p in (venv, env.get("PYTHONPATH")) if p])
    code = (f"import {mod} as _m, sys; "
            f"print(getattr(_m, '__file__', '') or 'builtin')")
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=180, env=env)
    if proc.returncode == 0:
        where = (proc.stdout or "").strip()
        loc = "venv" if venv and where.startswith(venv) else "system"
        return True, loc, ""
    tail = (proc.stderr or "").strip().splitlines()
    return False, "", (tail[-1] if tail else f"exit {proc.returncode}")[:96]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--profile", default="briefing", choices=sorted(PROFILES))
    ap.add_argument("--pip-args", action="store_true",
                    help="print just the missing dists, space-separated, for pip")
    a = ap.parse_args()

    wanted: dict[str, list[str]] = {}
    for f in files_for(a.profile):
        for dist in parse(f):
            wanted.setdefault(dist, []).append(
                str(f.relative_to(DATACORE_ROOT)))

    missing, excluded, ok = [], [], []
    why: dict[str, str] = {}
    for dist in sorted(wanted):
        if dist in EXCLUDE:
            excluded.append(dist)
            continue
        good, loc, err = importable(dist)
        if good:
            ok.append(dist if loc == "system" else f"{dist} (venv)")
        else:
            missing.append(dist)
            why[dist] = err

    if a.pip_args:
        print(" ".join(missing))
        return 1 if missing else 0

    print(f"interpreter: {sys.executable}  ({sys.version.split()[0]})")
    print(f"profile: {a.profile}  ({len(files_for(a.profile))} requirement file(s))")
    print(f"\nimportable ({len(ok)}): {', '.join(ok) or 'none'}")
    if excluded:
        print(f"\nexcluded by policy ({len(excluded)}):")
        for d in excluded:
            print(f"  {d:<22} {EXCLUDE[d]}")
    if missing:
        print(f"\nMISSING or BROKEN ({len(missing)}):")
        for d in missing:
            print(f"  {d:<22} declared in {', '.join(wanted[d])}")
            if why.get(d):
                print(f"  {'':<22} -> {why[d]}")
    else:
        print("\nnothing missing for this profile")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
