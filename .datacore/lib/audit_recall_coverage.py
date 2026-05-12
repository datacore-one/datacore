#!/usr/bin/env python3
"""
audit_recall_coverage.py — DIP-0029 Phase 5.

Audits commands, module commands, module.yaml files, and agents for `recall:`
frontmatter coverage. Reports:

  - missing-recall:  files with no recall: block
  - empty-recall:    files with recall: but no ids/scopes/tags/query
  - failure-mode-uncovered:
        commands whose name appears in a known failure-mode engram's domain or
        tags, but whose recall: block doesn't include that engram's ID

Exit codes:
  0  all-clear or report-only
  1  drift detected (used when --strict is passed)

Usage:
  python3 .datacore/lib/audit_recall_coverage.py             # human report
  python3 .datacore/lib/audit_recall_coverage.py --json      # machine output
  python3 .datacore/lib/audit_recall_coverage.py --strict    # exit 1 on drift
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUR_STORE = Path.home() / ".plur" / "engrams.yaml"

try:
    import yaml  # type: ignore
    _HAS_YAML = True
except Exception:
    _HAS_YAML = False


FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    if _HAS_YAML:
        try:
            data = yaml.safe_load(m.group(1)) or {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def parse_yaml(path: Path) -> dict:
    if not _HAS_YAML or not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_engrams() -> list[dict]:
    if not _HAS_YAML or not PLUR_STORE.exists():
        return []
    try:
        data = yaml.safe_load(PLUR_STORE.read_text(encoding="utf-8")) or {}
        engrams = data.get("engrams", [])
        return [e for e in engrams if isinstance(e, dict)]
    except Exception:
        return []


def find_failure_mode_engrams(engrams: list[dict], name: str) -> list[str]:
    """Engrams that mention failure-mode patterns for this command name.

    Heuristic: scope == command:NAME, OR domain == datacore.NAME / command.NAME,
    OR tags contain NAME, AND type in {behavioral, corrective} (those denote
    failure-modes the agent should not repeat).
    """
    name_lower = name.lower()
    hits: list[str] = []
    for e in engrams:
        etype = str(e.get("type", "")).lower()
        if etype not in ("behavioral", "corrective", "operational"):
            continue
        scope = str(e.get("scope", "")).lower()
        domain = str(e.get("domain", "")).lower()
        tags = [str(t).lower() for t in (e.get("tags") or [])]
        if (scope == f"command:{name_lower}"
                or domain in (f"datacore.{name_lower}", f"command.{name_lower}", name_lower)
                or name_lower in tags):
            eid = e.get("id")
            if eid:
                hits.append(eid)
    return hits


def command_recall(fm: dict) -> dict:
    recall = fm.get("recall") if isinstance(fm, dict) else None
    if not isinstance(recall, dict):
        return {}
    return recall


def has_any_recall_content(recall: dict) -> bool:
    for k in ("ids", "scopes", "tags", "query"):
        v = recall.get(k)
        if isinstance(v, list) and v:
            return True
        if isinstance(v, str) and v.strip():
            return True
    return False


def audit_file(path: Path, engrams: list[dict], name_override: str | None = None,
               is_yaml_module: bool = False) -> dict:
    fm = parse_yaml(path) if is_yaml_module else parse_frontmatter(path)
    name = name_override or fm.get("name") or path.stem
    recall = command_recall(fm)
    result: dict = {
        "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "name": name,
        "has_recall": bool(recall),
        "has_content": has_any_recall_content(recall),
        "declared_ids": list(recall.get("ids") or []),
        "issues": [],
    }
    if not result["has_recall"]:
        result["issues"].append("missing-recall")
    elif not result["has_content"]:
        result["issues"].append("empty-recall")

    if engrams:
        fm_engrams = find_failure_mode_engrams(engrams, name)
        declared = set(result["declared_ids"])
        uncovered = [e for e in fm_engrams if e not in declared]
        if uncovered:
            result["failure_mode_uncovered"] = uncovered
            result["issues"].append("failure-mode-uncovered")
    return result


def iter_targets() -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {"commands": [], "module_commands": [], "module_yamls": [], "agents": [], "module_agents": []}

    cmd_dir = ROOT / ".datacore" / "commands"
    if cmd_dir.exists():
        for p in sorted(cmd_dir.glob("*.md")):
            if p.is_symlink():
                continue
            out["commands"].append(p)

    modules_dir = ROOT / ".datacore" / "modules"
    if modules_dir.exists():
        for mod_dir in sorted(modules_dir.iterdir()):
            if not mod_dir.is_dir() or mod_dir.name.startswith("_"):
                continue
            cmd_sub = mod_dir / "commands"
            if cmd_sub.exists():
                for p in sorted(cmd_sub.glob("*.md")):
                    if p.is_symlink() or "_deprecated" in str(p):
                        continue
                    out["module_commands"].append(p)
            yaml_path = mod_dir / "module.yaml"
            if yaml_path.exists():
                out["module_yamls"].append(yaml_path)
            ag_sub = mod_dir / "agents"
            if ag_sub.exists():
                for p in sorted(ag_sub.glob("*.md")):
                    if p.is_symlink():
                        continue
                    out["module_agents"].append(p)

    ag_dir = ROOT / ".datacore" / "agents"
    if ag_dir.exists():
        for p in sorted(ag_dir.glob("*.md")):
            if p.is_symlink():
                continue
            out["agents"].append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--skip-agents", action="store_true",
                    help="Skip agent files (recall: is currently advisory for agents)")
    args = ap.parse_args()

    engrams = load_engrams()
    targets = iter_targets()

    audit: dict[str, list[dict]] = {}
    for category, files in targets.items():
        if args.skip_agents and category in ("agents", "module_agents"):
            continue
        results = []
        for f in files:
            is_yaml = category == "module_yamls"
            results.append(audit_file(f, engrams, is_yaml_module=is_yaml))
        audit[category] = results

    total = sum(len(v) for v in audit.values())
    missing = sum(1 for v in audit.values() for r in v if "missing-recall" in r["issues"])
    empty = sum(1 for v in audit.values() for r in v if "empty-recall" in r["issues"])
    failure = sum(1 for v in audit.values() for r in v if "failure-mode-uncovered" in r["issues"])

    if args.json:
        print(json.dumps({
            "summary": {"total": total, "missing": missing, "empty": empty, "failure_mode_uncovered": failure},
            "audit": audit,
        }, indent=2))
    else:
        print("DIP-0029 RECALL COVERAGE AUDIT")
        print("=" * 60)
        print(f"  Total files scanned:        {total}")
        print(f"  Missing recall: block:      {missing}")
        print(f"  Empty recall: block:        {empty}")
        print(f"  Failure-mode uncovered:     {failure}")
        print()
        for category, results in audit.items():
            issues = [r for r in results if r["issues"]]
            if not issues:
                print(f"[{category}] OK ({len(results)} files)")
                continue
            print(f"[{category}] {len(issues)} issue(s):")
            for r in issues:
                tags = ",".join(r["issues"])
                print(f"  - {r['path']}  ({tags})")
                if "failure_mode_uncovered" in r["issues"]:
                    for eid in r.get("failure_mode_uncovered", [])[:3]:
                        print(f"      + uncovered: {eid}")
            print()

    if args.strict and (missing or failure):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
