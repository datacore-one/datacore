#!/usr/bin/env python3
"""Answer two questions nobody could answer before: is the suite green, and is it gated?

Running `pytest .datacore` does not work and never has. Module test packages are
all named `tests`, with no `__init__.py`, so the second one collected resolves its
modules into the first one's package and 52 files fail to import -- they are not
broken, they are merely un-collectable together. Each suite must be run in its own
pytest process, from its own rootdir.

So "the tests pass" was a claim about whichever subset someone happened to type.
This runs every suite the way that suite actually works and reports the union.

`--gate` answers the other half: which test files no automated gate ever runs.
CI names its test files one per line in validate-pr.yml, so a new test file is
run by nobody until a human edits that YAML -- and nothing tells them to.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

DATACORE = Path(__file__).resolve().parent.parent
WORKFLOW = DATACORE.parent / ".github" / "workflows" / "validate-pr.yml"
BASELINE = DATACORE / "config" / "suite-audit-baseline.yaml"

# Directories that hold build output, vendored code or a checked-out worktree.
# They contain files named test_*.py that are not this project's tests.
NOT_SOURCE = (
    "/venv/", "/.venv/", "/node_modules/", "/.worktrees/", "/site-packages/",
    "/target/", "/.git/", "/dist/", "/build/", "/.hypothesis/",
)


def is_source(path: Path) -> bool:
    s = f"/{path.as_posix()}/"
    return not any(x in s for x in NOT_SOURCE)


def discover_suites() -> list[Path]:
    """A suite is a directory that owns test files and is run from its own root.

    Keyed on the test directory rather than the module, because that is the unit
    pytest can actually collect in one process.
    """
    dirs: set[Path] = set()
    for p in DATACORE.rglob("test_*.py"):
        rel = p.relative_to(DATACORE)
        if not is_source(rel):
            continue
        dirs.add(p.parent)
    # Drop a directory pytest already reaches by recursing from an ancestor suite,
    # so each test file is run exactly once. Checking only the immediate parent
    # missed lib/sync/tests under lib, and counted it twice.
    roots = []
    for d in sorted(dirs):
        if any(d != a and a in d.parents for a in dirs):
            continue
        roots.append(d)
    return roots


@dataclass
class Result:
    suite: str
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    rc: int = 0
    summary: str = ""
    failures: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        """Files named test_*.py that pytest collects nothing from.

        Not a pass and not a failure. `chief-of-staff/server/lib` holds two
        standalone scripts with a `main()` and no test functions -- real tests,
        run as `python3 test_cos_route.py`, which pytest reports as "no tests
        ran". Calling that FAIL hides the actual state, which is that nothing
        automated runs them at all.
        """
        return self.passed == 0 and self.failed == 0 and self.errors == 0

    @property
    def ok(self) -> bool:
        if self.empty:
            return False
        return self.rc == 0 or (self.failed == 0 and self.errors == 0 and self.passed > 0)


_COUNT = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed)")


def run_suite(suite: Path, python: str, cov_dir: Path | None = None) -> Result:
    rel = suite.relative_to(DATACORE).as_posix()
    pre = [python, "-m"]
    if cov_dir is not None:
        # -p keeps one data file per process, which is what lets 19 separate
        # pytest runs be combined into a single picture afterwards.
        pre = [python, "-m", "coverage", "run", "-p", "--source", str(DATACORE / "lib"), "-m"]
    proc = subprocess.run(
        # --continue-on-collection-errors is the point of this runner. Without it a
        # single un-importable file aborts its whole root: `pytest .datacore/lib`
        # collected ZERO of 212 files because ws_chat_test.py reads a token at
        # import, and reported "1 error" -- which reads as one broken test, not as
        # "none of your tests ran". The suite that guards the ledger, identity and
        # delegation was silently not running for anyone who typed the directory.
        [*pre, "pytest", "-q", "-p", "no:cacheprovider", "--no-header", "-rf",
         "--continue-on-collection-errors", "."],
        cwd=suite,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
             **({"COVERAGE_FILE": str(cov_dir / ".coverage")} if cov_dir else {})},
        timeout=1800,
    )
    out = proc.stdout + proc.stderr
    res = Result(suite=rel, rc=proc.returncode)
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    res.summary = tail[:160]
    for n, what in _COUNT.findall(out):
        n = int(n)
        if what == "passed":
            res.passed = n
        elif what == "failed":
            res.failed = n
        elif what.startswith("error"):
            res.errors = n
        elif what == "skipped":
            res.skipped = n
    res.failures = [ln.strip() for ln in out.splitlines() if ln.startswith("FAILED") or ln.startswith("ERROR")][:40]
    return res


def ci_gated_paths() -> set[str]:
    """Paths pytest is pointed at in CI -- files or, since 2026-09-19, directories.

    A directory gates everything beneath it, which is the whole reason for naming
    directories: a test file added tomorrow is covered without anyone editing
    YAML. Match files first so the more specific pattern wins, then directories.
    """
    if not WORKFLOW.exists():
        return set()
    text = WORKFLOW.read_text()
    files = set(re.findall(r"\.datacore/[\w/\-]*tests?/[\w/\-]*test_\w+\.py", text))
    dirs = set(re.findall(r"\.datacore/[\w/\-]*\btests?\b(?![\w/\-]*\.py)", text))
    return files | dirs


def accepted_failures() -> list[dict]:
    """Failures the owner has seen and accepted, so a nightly run stays quiet
    about them and loud about everything else.

    Same shape and same rule as config/ledger-invariants-baseline.yaml: an entry
    matches ONE test id, so a second failure in the same file is still new and
    still fails. Adding to the list is a decision; removing from it is the fix.
    """
    if not BASELINE.exists():
        return []
    try:
        import yaml
    except ImportError:
        return []
    data = yaml.safe_load(BASELINE.read_text()) or {}
    return data.get("accepted") or []


def _is_accepted(failure_line: str, accepted: list[dict]) -> dict | None:
    for entry in accepted:
        nodeid = str(entry.get("test") or "")
        if nodeid and nodeid in failure_line:
            return entry
    return None


def is_gated(path: str, gated: set[str]) -> bool:
    if path in gated:
        return True
    return any(path.startswith(g.rstrip("/") + "/") for g in gated)


def gate_report() -> int:
    gated = ci_gated_paths()
    files = sorted(
        p.relative_to(DATACORE.parent).as_posix()
        for p in DATACORE.rglob("test_*.py")
        if is_source(p.relative_to(DATACORE))
    )
    ungated = [f for f in files if not is_gated(f, gated)]
    stale = sorted(g for g in gated if not (DATACORE.parent / g).exists())
    print(f"test files in the project : {len(files)}")
    print(f"run by CI                 : {len(files) - len(ungated)}")
    print(f"run by NO automated gate  : {len(ungated)}")
    if stale:
        print(f"\nCI names {len(stale)} file(s) that no longer exist:")
        for s in stale:
            print(f"  {s}")
    by_dir: dict[str, int] = {}
    for f in ungated:
        by_dir[str(Path(f).parent)] = by_dir.get(str(Path(f).parent), 0) + 1
    print("\nungated, by directory:")
    for d, n in sorted(by_dir.items(), key=lambda kv: -kv[1]):
        print(f"  {n:4d}  {d}")
    return 1 if stale else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gate", action="store_true", help="report which test files no automated gate runs")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--only", help="substring filter on suite path")
    ap.add_argument("--coverage", action="store_true",
                    help="also report lib/ modules that NO test executes at all")
    args = ap.parse_args()

    if args.gate:
        return gate_report()

    suites = discover_suites()
    if args.only:
        suites = [s for s in suites if args.only in s.as_posix()]
    if not args.json:
        print(f"{len(suites)} suite(s), each in its own pytest process\n", flush=True)

    cov_dir = None
    if args.coverage:
        cov_dir = Path(tempfile.mkdtemp(prefix="suite-audit-cov-"))

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda s: run_suite(s, args.python, cov_dir), suites))

    results.sort(key=lambda r: (r.ok, r.suite))
    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=2))
    else:
        accepted = accepted_failures()
        n_accepted = 0
        unexplained = []
        for r in results:
            live = [f for f in r.failures if not _is_accepted(f, accepted)]
            seen = [f for f in r.failures if _is_accepted(f, accepted)]
            n_accepted += len(seen)
            green = r.ok or (not r.empty and not live and r.passed > 0)
            mark = "ok  " if green else ("----" if r.empty else "FAIL")
            note = "  (no pytest tests collected)" if r.empty else ""
            print(f"  {mark} {r.suite:<52} {r.passed:5d} passed  {r.failed} failed  "
                  f"{r.errors} error  {r.skipped} skipped{note}")
            for f in live:
                print(f"         {f[:150]}")
            for f in seen:
                entry = _is_accepted(f, accepted)
                print(f"       x {f[:110]}   [accepted {entry.get('accepted_on', '?')}]")
            if live or (r.errors and not r.empty):
                unexplained.append(r)
        tot_p = sum(r.passed for r in results)
        tot_f = sum(r.failed for r in results)
        tot_e = sum(r.errors for r in results)
        empty = [r for r in results if r.empty]
        n_live = tot_f - n_accepted
        # The summary line a job contract asserts on.
        #
        # "unexplained" is the number that decides pass or fail, and it is named
        # as its own figure rather than left for a regex to derive from `failed`
        # minus `accepted` -- a contract that has to do arithmetic is a contract
        # that goes wrong quietly. Every other count stays on the line so one
        # regex can require ALL of them, because a regex on a single number
        # passes a run that went wrong in a different one: mac-seq-gap matched
        # "0 with unpublished events" for weeks while the error count climbed
        # beside it (2026-09-08).
        print(f"\n{tot_p} passed, {n_live} unexplained, {n_accepted} accepted, "
              f"{tot_e} error across {len(results)} suite(s); "
              f"{len(unexplained)} suite(s) not green, {len(empty)} collected nothing")

    if cov_dir is not None:
        report_untouched(cov_dir, args.python)
    return 1 if unexplained else 0


def report_untouched(cov_dir: Path, python: str) -> None:
    """lib/ modules that no test executed a single line of.

    A PERCENTAGE IS THE WRONG NUMBER HERE. A coverage target is a goal that can
    be met without meeting its intent -- write a test that imports a module and
    asserts nothing, and the figure moves. What is actionable is the binary: is
    there any test at all that runs this code. That is the question my first
    scan got wrong by grepping for imports, which missed `from sync.conflict
    import ...` and reported a well-covered module as untested.
    """
    subprocess.run([python, "-m", "coverage", "combine"], cwd=cov_dir,
                   capture_output=True, text=True)
    proc = subprocess.run(
        [python, "-m", "coverage", "json", "-o", "-", "--omit", "*/tests/*,*/_vendor/*"],
        cwd=cov_dir, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"\ncoverage: could not produce a report ({proc.stderr.strip()[:120]})")
        return
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print("\ncoverage: report was not JSON; skipped")
        return
    untouched = sorted(
        (f, m["summary"]["num_statements"])
        for f, m in data.get("files", {}).items()
        if m["summary"]["covered_lines"] == 0 and m["summary"]["num_statements"] > 0
    )
    total = len(data.get("files", {}))
    print(f"\ncoverage: {total} lib module(s) imported by the suite; "
          f"{len(untouched)} had no line executed by any test")
    for f, n in sorted(untouched, key=lambda x: -x[1])[:40]:
        print(f"  {n:5d} stmts  {f}")


if __name__ == "__main__":
    raise SystemExit(main())
