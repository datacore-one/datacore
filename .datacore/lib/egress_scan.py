#!/usr/bin/env python3
"""Find every function that acts on the outside world, and check it is declared.

DIP-0047. The audit this replaces was done by grepping, and it was wrong in both
directions within an hour: it counted 73 "call sites" that were mostly reads, and
it reported one file as bypassing the X chokepoint when the calls in question
were search and inference. A gap list assembled by eye is a gap list nobody can
trust the second time.

Runs BOTH ways, because the two failure modes are different:

  UNDECLARED  code performs an outbound write and no `egress:` entry mentions it
              -- a new action nobody recorded. This is the one that matters:
              an unattested post is indistinguishable from no post at all.

  UNDECORATED an `egress:` entry names a function carrying no @attests -- the
              declaration outlived the wiring, usually via a rename.

WHAT IT CANNOT SEE, stated plainly because a coverage tool that overstates its
own coverage is worse than none. Detection is syntactic and matches HTTP-library
verbs. Egress through a vendor SDK is invisible to it: the Gmail client's
`.execute()`, `exchange.order()` on Hyperliquid, and `gh` via subprocess all
send without touching `requests`. Both of the highest-priority chokepoints --
email and trade orders -- are in that blind spot, and were decorated from a
hand-read of the code, not from this list.

So `undeclared` is a LOWER BOUND. The ratchet still holds for what it can see:
once a module declares, it cannot silently grow a new HTTP write. It cannot
promise a module has no egress at all.

Report-only by default. `--enforce` returns non-zero, for the checklist.

    egress_scan.py [--enforce] [--modules DIR]
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

# Attribute calls that SEND. Deliberately verb-based: `requests.get` fetches,
# `requests.post` acts. A read that happens to use POST (search APIs do) is
# exempted by name in the manifest, where the reason is visible, rather than
# being quietly excluded by a cleverer matcher here.
WRITE_ATTRS = {"post", "put", "patch", "delete"}
WRITE_ROOTS = {"requests", "httpx", "session", "_requests", "client"}
WRITE_FUNCS = {"urlopen", "sendmail", "send_message", "send_message_async"}


class _Visitor(ast.NodeVisitor):
    """Collect (function, lineno) for every outbound write in one file."""

    def __init__(self) -> None:
        self.stack: list[str] = []
        self.writes: list[tuple[str, int]] = []
        self.decorated: dict[str, str] = {}

    def _enter(self, node) -> None:
        self.stack.append(node.name)
        for dec in node.decorator_list:
            f = dec.func if isinstance(dec, ast.Call) else dec
            name = getattr(f, "id", None) or getattr(f, "attr", None)
            if name == "attests":
                kind = ""
                if isinstance(dec, ast.Call) and dec.args:
                    a = dec.args[0]
                    kind = a.value if isinstance(a, ast.Constant) else ""
                self.decorated[node.name] = str(kind)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _enter          # noqa: N815
    visit_AsyncFunctionDef = _enter     # noqa: N815

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        f = node.func
        hit = False
        if isinstance(f, ast.Attribute):
            root = getattr(f.value, "id", None) or getattr(f.value, "attr", None)
            if f.attr in WRITE_ATTRS and (root or "").lower() in WRITE_ROOTS:
                hit = True
            if f.attr in WRITE_FUNCS:
                hit = True
        elif isinstance(f, ast.Name) and f.id in WRITE_FUNCS:
            hit = True
        if hit:
            self.writes.append((self.stack[-1] if self.stack else "<module>",
                                node.lineno))
        self.generic_visit(node)


def _exempted(site: str, patterns: set) -> bool:
    """Exemptions may be globs, so a read-only module is one line not forty.

    `lib/reddit_scanner.py:*` exempts a whole file. The REASON is still
    required and still recorded next to it -- what is being bought here is
    brevity, not silence. A blanket exemption with a stated reason is
    reviewable; forty identical lines are skimmed.
    """
    import fnmatch
    return any(fnmatch.fnmatch(site, pat) for pat in patterns)


def scan_module(mod: Path) -> dict:
    """Declared egress, exemptions, and what the code actually does."""
    declared: dict[str, str] = {}
    exempt: set[str] = set()
    manifest = mod / "module.yaml"
    if manifest.is_file():
        try:
            import yaml
            man = yaml.safe_load(manifest.read_text()) or {}
            for e in (man.get("egress") or []):
                if isinstance(e, dict) and e.get("fn"):
                    declared[str(e["fn"])] = str(e.get("kind", ""))
            for e in (man.get("exempt") or []):
                if isinstance(e, dict) and e.get("fn"):
                    exempt.add(str(e["fn"]))
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}

    writes: dict[str, list[int]] = {}
    decorated: dict[str, str] = {}
    for py in sorted(mod.rglob("*.py")):
        # SKIP CODE THE MODULE DID NOT WRITE. The health module reported 131
        # sites, of which the overwhelming majority were build output and
        # vendored dependencies: `src-tauri/target/release`, a bundled
        # `Health.app/Contents/Resources`, a git worktree, and — literally —
        # the standard library's own `smtplib.py` and pip's vendored urllib3.
        #
        # Counting those is worse than useless. It buries the module's real
        # surface under noise nobody can act on, and no exemption written in
        # module.yaml would be true: the module does not own that code and
        # cannot decorate it. A coverage number dominated by vendored stdlib
        # measures the scanner, not the module.
        if any(p in {"tests", "test", "__pycache__", ".venv", ".worktrees",
                     "target", "node_modules", "site-packages", "_vendor",
                     "bundle", "dist", "build", "vendor"} for p in py.parts):
            continue
        # A BUNDLED INTERPRETER IS NOT MODULE CODE. The health app ships a whole
        # CPython under `resources/python/lib/python3.11/`, so the scan was
        # reporting `smtplib.send_message`, `distutils/upload.py` and
        # `urllib/request.py:urlretrieve` as health's egress. Matching the
        # `python3.N` directory catches every such vendored runtime regardless
        # of where it is staged.
        if any(p.startswith("python3.") for p in py.parts):
            continue
        try:
            tree = ast.parse(py.read_text(errors="replace"))
        except SyntaxError:
            continue
        v = _Visitor()
        v.visit(tree)
        rel = py.relative_to(mod).as_posix()
        for fn, line in v.writes:
            writes.setdefault(f"{rel}:{fn}", []).append(line)
        for fn, kind in v.decorated.items():
            decorated[f"{rel}:{fn}"] = kind
    return {"declared": declared, "exempt": exempt,
            "writes": writes, "decorated": decorated}


def main() -> int:
    from datacore.ledger import EGRESS_KINDS

    ap = argparse.ArgumentParser()
    ap.add_argument("--enforce", action="store_true")
    ap.add_argument("--modules", type=Path,
                    default=LIB.parent / "modules")
    ap.add_argument("--module", action="append",
                    help="limit to these module names (repeatable)")
    ap.add_argument("--limit", type=int, default=40,
                    help="rows printed per bucket; 0 for all")
    a = ap.parse_args()

    undeclared: list[str] = []
    unopted: list[str] = []
    undecorated: list[str] = []
    bad_kind: list[str] = []
    covered = 0

    wanted = set(a.module or [])
    for mod in sorted(p for p in a.modules.iterdir() if p.is_dir()):
        if wanted and mod.name not in wanted:
            continue
        r = scan_module(mod)
        if "error" in r:
            print(f"  {mod.name:22} manifest error: {r['error']}")
            continue
        # A module that has declared ANY egress is held to the full contract.
        # One that has not is reported and not failed -- otherwise turning this
        # on would fail every module at once and the check would be switched
        # off the same day. Declaring is the ratchet: once a module opts in, it
        # cannot silently grow a new action.
        opted_in = bool(r["declared"] or r["exempt"])
        for site in sorted(r["writes"]):
            if site in r["declared"] or _exempted(site, r["exempt"]):
                covered += 1
            elif opted_in:
                undeclared.append(f"{mod.name}/{site}")
            else:
                unopted.append(f"{mod.name}/{site}")
        for fn, kind in r["declared"].items():
            if fn not in r["decorated"]:
                undecorated.append(f"{mod.name}/{fn}")
            if kind and kind not in EGRESS_KINDS:
                bad_kind.append(f"{mod.name}/{fn} -> {kind!r}")

    print(f"\nEGRESS SCAN — {covered} declared/exempted, "
          f"{len(undeclared)} undeclared in opted-in modules, "
          f"{len(undecorated)} undecorated, "
          f"{len(unopted)} in modules not yet declaring")
    for label, rows in (("UNDECLARED (acts, nothing says so)", undeclared),
                        ("UNDECORATED (declared, not wired)", undecorated),
                        ("UNKNOWN KIND (not in vocabulary)", bad_kind),
                        ("NOT YET DECLARING (reported, not failed)", unopted)):
        if rows:
            print(f"\n  {label}: {len(rows)}")
            cap = len(rows) if a.limit == 0 else a.limit
            for row in rows[:cap]:
                print(f"    {row}")
            if len(rows) > cap:
                print(f"    ... and {len(rows)-cap} more")

    bad = len(undeclared) + len(undecorated) + len(bad_kind)
    if not a.enforce:
        print("\n  (report-only; --enforce to fail)")
        return 0
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
