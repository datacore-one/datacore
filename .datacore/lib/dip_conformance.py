#!/usr/bin/env python3
"""dip_conformance.py — derive a DIP's status instead of reading the typed one.

WHY THIS EXISTS. On 2026-09-02 an audit of six production failures found that
three of the five recurring bug classes already had a DIP, and that DIP-0035
(Job Contracts) was marked `Implemented` while the class it covers was live in
production. Its own ratification note explains why:

    Status moved Draft -> Implemented on the owner's instruction. No human
    review was performed on this DIP. `Implemented` here means the work this
    DIP specifies has landed. It does not mean every follow-up named in the
    body is closed.

So the status field asserts a property that nothing tests, inside a system
whose defining defect is facts that nothing tests. That is not a governance
problem to solve with more governance; it is a missing derivation.

WHAT THIS DERIVES. Four mechanical signals, none of which requires reading
the DIP's prose:

  specs      every path named in `Specs:` / `Affects:` resolves on disk.
             A DIP cannot have landed if the files it claims to define are
             absent. Globs count as satisfied if they match anything.
  deps       a DIP is never more-implemented than its hard dependencies.
             DIP-0035 already says this about itself in prose ("DIP-0034 is
             itself unratified/Draft, so this dependency chain is Draft ->
             Draft") -- this makes prose into a check.
  test       some file under a tests/ directory names this DIP. This is the
             weakest signal and deliberately so: it asks "did anyone bind
             this to an executable assertion", not "is the test good".
  review     the DIP does not carry an explicit no-review ratification note.

DERIVED STATUS is the floor of what the signals support, never the claim.
`Implemented` requires all four. Anything less renders `Draft` with reasons.
A DIP that claims more than it can prove is the finding, not an error.

This tool does NOT edit DIPs. It reports. Rewriting 45 status fields is the
owner's call, and doing it automatically would replace one unreviewed claim
with another.

    dip_conformance.py                  # table, all DIPs
    dip_conformance.py --json           # machine-readable
    dip_conformance.py --only 0035      # one DIP, with reasons
    dip_conformance.py --gap            # only DIPs that overclaim
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
DIPS = ROOT / ".datacore" / "dips"

# Ranked so "not more-implemented than its dependency" is a comparison.
RANK = {"unknown": 0, "draft": 1, "proposed": 1, "accepted": 2,
        "implemented": 3, "superseded": 3, "deprecated": 3}

FIELD_RE = re.compile(r"^\|\s*\*\*(?P<k>[^*]+)\*\*\s*\|\s*(?P<v>.*?)\s*\|\s*$", re.M)
PATH_RE = re.compile(r"`([^`]+)`")
DIPREF_RE = re.compile(r"DIP-(\d{4})")
NO_REVIEW_RE = re.compile(r"no human review was performed", re.I)


def _norm_status(raw: str) -> str:
    """First word of the status cell, lowercased.

    Statuses carry qualifiers -- "Draft — gated on Evolver-spike lift signal".
    The qualifier is prose for humans; the rank comes from the first token.
    """
    m = re.match(r"[A-Za-z]+", raw.strip())
    return m.group(0).lower() if m else "unknown"


def _paths(cell: str) -> list[str]:
    """Backticked paths from an Affects/Specs cell, with PARENTHETICAL context.

    Directory context applies only inside parentheses:

        `.datacore/lib/jobs/` (`manifest.py`, `checks.py`)   -> members
        `.datacore/registry/`, `tags.yaml`                    -> siblings

    Applying it across a comma list too resolved DIP-0016's `tags.yaml` to
    `.datacore/registry/tags.yaml` and reported it absent, when the file is at
    `.datacore/tags.yaml`. The prose distinguishes the two cases with
    parentheses; the extractor now does as well.

    Paths introduced by "future" or "planned" are aspirational, not claims.
    DIP-0037 says "future `cos_generate.py`/`cos_reasoning.py` call sites" --
    reading those as current made a DIP fail for describing its own roadmap.
    """
    out: list[str] = []
    ctx = ""
    depth = 0
    i = 0
    aspirational = False
    while i < len(cell):
        ch = cell[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
            if depth == 0:
                ctx = ""
        elif ch == "`":
            j = cell.find("`", i + 1)
            if j == -1:
                break
            s = cell[i + 1:j].strip().rstrip(",")
            preceding = cell[max(0, i - 40):i].lower()
            aspirational = ("future" in preceding or "planned" in preceding
                            or "proposed" in preceding)
            if s and not aspirational:
                if "/" in s:
                    out.append(s)
                    if depth == 0:
                        ctx = s if s.endswith("/") else s.rsplit("/", 1)[0] + "/"
                elif s.endswith((".py", ".md", ".yaml", ".yml", ".sh", ".ts", ".json")):
                    out.append((ctx + s) if depth > 0 and ctx else s)
            i = j
        i += 1
    return out


def _classify(p: str) -> str:
    """exists | absent | unverifiable.

    Three states, because "could not tell" must never render as "absent" --
    the same rule the credential broker uses for `n-a`. A DIP is not failed
    for naming something this tool cannot resolve; it is failed for naming a
    real filesystem path that is not there.

    Unverifiable on purpose:
      /today, /wrap-up        slash commands, not filesystem paths
      <space>/, [space]/      placeholders standing in for a real name
      {a,b}.py                brace sets; the DIP means several files
    """
    p = p.strip().rstrip(",")
    if not p:
        return "unverifiable"
    if re.match(r"^/[a-z][a-z0-9-]*$", p):          # /today, /search
        return "unverifiable"
    if any(ch in p for ch in "<>[]{}"):              # placeholders, brace sets
        return "unverifiable"

    if p.startswith("~/"):
        base, rel = pathlib.Path.home(), p[2:]
    elif p.startswith("/"):
        base, rel = pathlib.Path("/"), p[1:]
    else:
        base, rel = ROOT, p

    if any(ch in rel for ch in "*?"):
        try:
            return "exists" if any(base.glob(rel)) else "absent"
        except (ValueError, OSError):
            return "unverifiable"

    if (base / rel).exists():
        return "exists"

    # A DIP about a project writes paths relative to THAT project:
    # `datacore-app/daemon/datacored/api/mail.py` is real, under
    # 2-datacore/2-projects/. Reading it root-relative reported a shipped file
    # as absent.
    if base is ROOT and "/" in rel:
        try:
            for proj in ROOT.glob("[0-9]-*/2-projects"):
                if (proj / rel).exists():
                    return "exists"
        except OSError:
            pass

    # A bare name with no directory context ("org/", "dips/") may be relative
    # to a space rather than the repo root. Look before calling it absent.
    if rel.count("/") <= 1:
        try:
            if any(ROOT.glob(f"*/{rel.rstrip('/')}")) or any((ROOT / ".datacore").rglob(rel.rstrip("/"))):
                return "exists"
        except OSError:
            pass
        return "unverifiable"
    return "absent"


_BASENAMES: set[str] | None = None


def _basename_index() -> set[str]:
    """Every basename under .datacore and the space repos, indexed once.

    Built lazily and cached: the naive version rglob'd the whole repo per
    absent path and did not finish inside two minutes. One walk, one set.
    """
    global _BASENAMES
    if _BASENAMES is not None:
        return _BASENAMES
    names: set[str] = set()
    roots = [ROOT / ".datacore"] + [d for d in ROOT.glob("[0-9]-*") if d.is_dir()]
    skip = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}
    for r in roots:
        for dirpath, dirnames, filenames in __import__("os").walk(r):
            dirnames[:] = [d for d in dirnames if d not in skip]
            names.update(filenames)
            names.update(dirnames)
    _BASENAMES = names
    return names


def _absence_kind(p: str) -> str:
    """For an absent path: did it move, or was it never built?

    These need opposite fixes and the distinction is cheap. DIP-0037 names
    `.datacore/lib/cos_reasoning.py`; that file exists, in the datacore-app
    daemon -- the code shipped and the DIP went stale (class 1). DIP-0034
    names `.datacore/keys/registry.yaml`; `.datacore/keys/` does not exist
    anywhere -- the spec did not ship. Reporting both as "absent" would send
    someone to write code that is already written.
    """
    name = p.rstrip("/").rsplit("/", 1)[-1]
    if not name:
        return "missing"
    # Basename equality is a HINT, not proof: two unrelated files can share a
    # name. It is enough to stop someone rewriting code that already exists,
    # and the caller's wording says "verify" rather than asserting the move.
    return "moved" if name in _basename_index() else "missing"


def parse(path: pathlib.Path) -> dict:
    text = path.read_text(errors="replace")
    fields = {m.group("k").strip(): m.group("v").strip()
              for m in FIELD_RE.finditer(text)}
    num = re.search(r"DIP-(\d{4})", path.name).group(1)

    claimed = _norm_status(fields.get("Status", ""))

    declared, missing, unverifiable = [], [], []
    moved, never_built = [], []
    for key in ("Specs", "Affects"):
        for p in _paths(fields.get(key, "")):
            declared.append(p)
            state = _classify(p)
            if state == "absent":
                missing.append(p)
                (moved if _absence_kind(p) == "moved" else never_built).append(p)
            elif state == "unverifiable":
                unverifiable.append(p)

    deps = set()
    for key in ("Depends", "Depends On"):
        deps |= {d for d in DIPREF_RE.findall(fields.get(key, "")) if d != num}

    return {
        "dip": num,
        "title": fields.get("Title", path.stem),
        "claimed": claimed,
        "declared_paths": declared,
        "missing_paths": missing,
        "unverifiable_paths": unverifiable,
        "moved_paths": moved,
        "never_built_paths": never_built,
        "deps": sorted(deps),
        "no_review": bool(NO_REVIEW_RE.search(text)),
        "path": str(path.relative_to(ROOT)),
    }


def find_tests(num: str) -> list[str]:
    """Any file under a tests/ dir that names this DIP.

    Deliberately broad. The question is whether anyone bound the DIP to an
    executable assertion at all -- a stricter check would report zero across
    the board and tell us nothing about where to start.
    """
    hits = []
    for t in ROOT.glob(".datacore/**/tests/*.py"):
        try:
            if f"DIP-{num}" in t.read_text(errors="replace"):
                hits.append(str(t.relative_to(ROOT)))
        except OSError:
            continue
    return hits


def derive(recs: dict[str, dict]) -> None:
    for r in recs.values():
        r["tests"] = find_tests(r["dip"])

    # Dependency rank needs a fixed point: A may depend on B which depends on C.
    # Iterate to convergence rather than assuming declaration order.
    for _ in range(len(recs) + 1):
        changed = False
        for r in recs.values():
            reasons = []
            if r["never_built_paths"]:
                reasons.append(
                    f"{len(r['never_built_paths'])} declared path(s) never built")
            if r["moved_paths"]:
                reasons.append(
                    f"{len(r['moved_paths'])} declared path(s) absent, but that "
                    f"basename exists elsewhere — likely moved, verify")
            if not r["tests"]:
                reasons.append("no test names this DIP")
            if r["no_review"]:
                reasons.append("ratified without review")

            ceiling = RANK["implemented"]
            for d in r["deps"]:
                dep = recs.get(d)
                if dep is None:
                    reasons.append(f"depends on DIP-{d}, which is missing")
                    ceiling = min(ceiling, RANK["draft"])
                else:
                    dr = RANK.get(dep.get("derived", dep["claimed"]), 0)
                    if dr < RANK["implemented"]:
                        reasons.append(
                            f"depends on DIP-{d} ({dep.get('derived', dep['claimed'])})")
                    ceiling = min(ceiling, dr)

            supported = RANK["implemented"] if not reasons else RANK["draft"]
            floor = min(supported, ceiling, RANK.get(r["claimed"], 0))
            new = "implemented" if floor >= RANK["implemented"] else "draft"

            if new != r.get("derived"):
                changed = True
            r["derived"] = new
            r["reasons"] = reasons
            r["overclaims"] = RANK.get(r["claimed"], 0) > RANK[new]
        if not changed:
            break


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--gap", action="store_true", help="only DIPs that overclaim")
    a = ap.parse_args()

    recs = {}
    for f in sorted(DIPS.glob("DIP-*.md")):
        r = parse(f)
        recs[r["dip"]] = r
    derive(recs)

    rows = sorted(recs.values(), key=lambda r: r["dip"])
    if a.only:
        want = a.only.zfill(4)
        rows = [r for r in rows if r["dip"] == want]
    if a.gap:
        rows = [r for r in rows if r["overclaims"]]

    if a.json:
        json.dump(rows, sys.stdout, indent=1)
        return 0

    over = sum(1 for r in recs.values() if r["overclaims"])
    print(f"{'DIP':<6}{'CLAIMED':<14}{'DERIVED':<14}{'':<3}TITLE")
    print("-" * 96)
    for r in rows:
        flag = "!!" if r["overclaims"] else "  "
        print(f"{r['dip']:<6}{r['claimed']:<14}{r['derived']:<14}{flag:<3}{r['title'][:46]}")
        if (a.only or a.gap) and r["reasons"]:
            for why in r["reasons"]:
                print(f"{'':<37}- {why}")
            for p in r["missing_paths"][:6]:
                print(f"{'':<37}  absent: {p}")
    print("-" * 96)
    print(f"{len(recs)} DIPs · {over} overclaim their status · "
          f"{sum(1 for r in recs.values() if r['derived']=='implemented')} derive as implemented")
    return 1 if over else 0


if __name__ == "__main__":
    raise SystemExit(main())
