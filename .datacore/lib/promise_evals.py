#!/usr/bin/env python3
"""Run every promise eval and score each promise: green, red, or no eval.

Evals-first (owner, 2026-09-26): each promise in
2-datacore/1-tracks/dev/datacore-upgrade/promises/*.yaml has evals behind it,
in files named `test_promise_<ID>_<what>.py`, where <ID> is the promise id with
or without its dash (TSK-9 -> TSK9 or TSK_9). They live in the repo whose code
they test: the root (.datacore/lib/tests), module repos (.datacore/modules/*/tests)
and the app (2-datacore/2-projects/datacore-app/daemon/tests).

A promise is green only when it has at least one eval and every eval behind it
passes. `n-a` is never a pass: a promise with no eval is reported as such.

Usage:
    promise_evals.py [--json] [--only ID,ID] [--list]
Exit 0 only when every promise with an eval is green AND none lacks one.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PROMISES = ROOT / "2-datacore" / "1-tracks" / "dev" / "datacore-upgrade" / "promises"
PY = sys.executable
SUITE_TIMEOUT_S = 900   # an eval that hangs must not hang the scoreboard
SUITES = [  # (name, cwd, test dir relative to cwd, extra env)
    ("root", ROOT / ".datacore" / "lib", "tests", {}),
    ("app", ROOT / "2-datacore" / "2-projects" / "datacore-app" / "daemon", "tests", {}),
]
for mod in sorted((ROOT / ".datacore" / "modules").glob("*/tests")):
    SUITES.append((f"module:{mod.parent.name}", mod.parent, "tests", {"DATACORE_ROOT": str(ROOT)}))

_FILE = re.compile(r"^test_promise_([A-Za-z]+)_?(\d+)(?:_([0-9]+))?_")


def norm(pid: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", pid.upper())


def promises() -> dict[str, str]:
    out: dict[str, str] = {}
    for f in sorted(PROMISES.glob("*.yaml")):
        for cap in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("capabilities") or []:
            for p in cap.get("promises") or []:
                out[str(p["id"])] = str(p["promise"])
    return out


def eval_files() -> dict[str, list[tuple[str, Path]]]:
    """{normalized promise id: [(suite, file)]}"""
    found: dict[str, list[tuple[str, Path]]] = defaultdict(list)
    for name, cwd, tdir, _ in SUITES:
        for f in sorted((cwd / tdir).glob("test_promise_*.py")):
            m = _FILE.match(f.name)
            if m:
                found[norm(m.group(1) + m.group(2))].append((name, f))
    return found


def run_suite(cwd: Path, files: list[Path], env_extra: dict) -> dict[str, bool]:
    """{file name: all tests in it passed}"""
    import os
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as fh:
        xml = fh.name
    env = {**os.environ, **env_extra, "PROMISE_EVALS_ALL": "1"}   # the scoreboard collects every eval
    result: dict[str, bool] = {f.name: False for f in files}   # a file pytest never reported is not a pass
    try:
        subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}",
                        *[str(f) for f in files]], cwd=cwd, env=env, capture_output=True, text=True,
                       timeout=SUITE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        print(f"TIMEOUT after {SUITE_TIMEOUT_S}s in {cwd}: an eval hangs; every file in this batch counts red",
              file=sys.stderr)
        return result
    try:
        tree = ET.parse(xml)
    except (ET.ParseError, OSError):
        return result
    seen: dict[str, bool] = {}
    for case in tree.iter("testcase"):
        fname = (case.get("file") or case.get("classname", "").split(".")[-1] + ".py").split("/")[-1]
        bad = any(child.tag in ("failure", "error") for child in case)
        skipped = any(child.tag == "skipped" for child in case)
        seen[fname] = seen.get(fname, True) and not bad and not skipped
    for f in files:
        if f.name in seen:
            result[f.name] = seen[f.name]
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--list", action="store_true", help="list promises and eval files, run nothing")
    ap.add_argument("--write-baseline", action="store_true",
                    help="record the green promises in .datacore/registry/promise-baseline.json (the CI gate)")
    args = ap.parse_args()

    ps = promises()
    files = eval_files()
    only = {norm(x) for x in args.only.split(",") if x.strip()}
    wanted = {pid: text for pid, text in ps.items() if not only or norm(pid) in only}

    status: dict[str, str] = {}
    if args.list:
        for pid in wanted:
            status[pid] = f"{len(files.get(norm(pid), []))} eval file(s)"
    else:
        by_suite: dict[str, list[Path]] = defaultdict(list)
        for pid in wanted:
            for suite, f in files.get(norm(pid), []):
                by_suite[suite].append(f)
        passed: dict[str, bool] = {}
        for name, cwd, _tdir, env in SUITES:
            if by_suite.get(name):
                for fname, ok in run_suite(cwd, sorted(set(by_suite[name])), env).items():
                    passed[f"{name}:{fname}"] = ok
        for pid in wanted:
            fs = files.get(norm(pid), [])
            if not fs:
                status[pid] = "no-eval"
            else:
                status[pid] = "green" if all(passed.get(f"{s}:{f.name}", False) for s, f in fs) else "red"

    if args.write_baseline and not args.list:
        import promise_gate
        greens = sorted(norm(p) for p, st in status.items() if st == "green")
        prev = promise_gate.green()
        lost = sorted(prev - set(greens)) if not only else []
        promise_gate.BASELINE.write_text(json.dumps({"green": sorted(set(greens) | (prev if only else set()))},
                                                    indent=1) + "\n")
        if lost:
            print(f"REGRESSED since the last baseline: {', '.join(lost)}")

    counts = defaultdict(int)
    for v in status.values():
        counts[v.split()[0] if args.list else v] += 1
    if args.json:
        print(json.dumps({"counts": counts, "promises": status}, indent=1))
    else:
        for pid, st in status.items():
            print(f"{st:10} {pid:10} {wanted[pid][:90]}")
        print("\n" + "  ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    return 0 if not args.list and counts.get("red", 0) == 0 and counts.get("no-eval", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
