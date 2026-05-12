#!/usr/bin/env python3
"""
command_recall_inject.py

PreToolUse hook for Skill / SlashCommand / Agent invocations.

Per DIP-0029 Phase 1 + Phase 2:
  - Phase 1 (fallback): name-based recall via BM25 against the local PLUR store.
  - Phase 2 (declarative): parse the command file's `recall:` frontmatter and
    resolve explicit ids/scopes/tags/queries. Module-level `recall:` from
    `module.yaml` is composed in for module-scoped commands.

The union of all resolved engrams is deduplicated by ID, ranked by score, and
emitted as an `additionalContext` block titled `## Relevant memory (engrams)`.

Fail-open: any error returns empty context and exit 0. Never block tool execution.

Registration in ~/.claude/settings.json (auto-installed via configure-hooks.py):

    {
      "hooks": {
        "PreToolUse": [
          {
            "matcher": "Skill|SlashCommand|Agent",
            "hooks": [
              {
                "type": "command",
                "command": "python3 $DATACORE_PATH/.datacore/lib/hooks/command_recall_inject.py",
                "timeout": 3
              }
            ]
          }
        ]
      }
    }
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Hard timeout for PLUR query (seconds) — must be well under harness 3s budget
PLUR_QUERY_TIMEOUT = 2.0

# Max engrams to inject; token budget cap is approximate
MAX_RESULTS = 8
TOKEN_BUDGET = 2000

# Datacore root — resolved from env, defaults to ~/Data
DATACORE_ROOT = Path(os.environ.get("DATACORE_PATH", str(Path.home() / "Data")))
PLUR_STORE = Path.home() / ".plur" / "engrams.yaml"

# Optional PyYAML — fall back to regex frontmatter parsing if unavailable
try:
    import yaml  # type: ignore
    _HAS_YAML = True
except Exception:
    _HAS_YAML = False


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

def _emit(context: str = "") -> None:
    """Emit the hook response and exit 0 (fail-open)."""
    if context:
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": context}}
        print(json.dumps(out))
    sys.exit(0)


def _extract_name(payload: dict) -> str | None:
    """Pull the skill/command/agent name from the hook input payload."""
    tool_name = payload.get("tool_name") or payload.get("toolName")
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}

    if tool_name == "Skill":
        return tool_input.get("skill")
    if tool_name == "SlashCommand":
        cmd = tool_input.get("command", "")
        return cmd.lstrip("/").split()[0] if cmd else None
    if tool_name == "Agent":
        return tool_input.get("subagent_type") or tool_input.get("description")
    return None


# ---------------------------------------------------------------------------
# Command / module file discovery
# ---------------------------------------------------------------------------

def _resolve_command_file(name: str) -> tuple[Path | None, Path | None]:
    """Return (command_file, module_yaml) for a given command/skill name.

    Skills are resolved as `plugin:skill` or bare `skill-name`. For module
    skills (e.g. `meetings:standup`), the colon-prefixed name maps to
    `.datacore/modules/<module>/commands/<skill>.md`.

    Returns (None, None) if not found.
    """
    if not name:
        return None, None

    # Strip plugin/module prefix if present
    module = None
    plain = name
    if ":" in name:
        module, plain = name.split(":", 1)

    # 1. Top-level command
    top = DATACORE_ROOT / ".datacore" / "commands" / f"{plain}.md"
    if top.exists():
        return top, None

    # 2. Module-scoped command (explicit module prefix)
    if module:
        mod_cmd = DATACORE_ROOT / ".datacore" / "modules" / module / "commands" / f"{plain}.md"
        if mod_cmd.exists():
            mod_yaml = DATACORE_ROOT / ".datacore" / "modules" / module / "module.yaml"
            return mod_cmd, (mod_yaml if mod_yaml.exists() else None)

    # 3. Search any module for the command
    modules_dir = DATACORE_ROOT / ".datacore" / "modules"
    if modules_dir.exists():
        for mod_dir in modules_dir.iterdir():
            if not mod_dir.is_dir():
                continue
            candidate = mod_dir / "commands" / f"{plain}.md"
            if candidate.exists():
                mod_yaml = mod_dir / "module.yaml"
                return candidate, (mod_yaml if mod_yaml.exists() else None)

    # 4. Agent file (subagent_type)
    agent_top = DATACORE_ROOT / ".datacore" / "agents" / f"{plain}.md"
    if agent_top.exists():
        return agent_top, None
    if modules_dir.exists():
        for mod_dir in modules_dir.iterdir():
            if not mod_dir.is_dir():
                continue
            candidate = mod_dir / "agents" / f"{plain}.md"
            if candidate.exists():
                mod_yaml = mod_dir / "module.yaml"
                return candidate, (mod_yaml if mod_yaml.exists() else None)

    return None, None


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(path: Path) -> dict:
    """Read the YAML frontmatter block of a markdown file. Empty dict on failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    block = m.group(1)
    if _HAS_YAML:
        try:
            data = yaml.safe_load(block) or {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    # Fallback: minimal hand-parse just enough to surface recall: keys
    return _parse_recall_minimal(block)


def _parse_recall_minimal(block: str) -> dict:
    """Tiny YAML subset parser for the recall: block when PyYAML is unavailable."""
    out: dict = {}
    recall: dict[str, list[str] | int] = {}
    in_recall = False
    current_key: str | None = None
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.startswith("recall:"):
            in_recall = True
            continue
        if in_recall:
            if not (line.startswith("  ") or line.startswith("\t")):
                in_recall = False
                continue
            stripped = line.strip()
            if stripped.endswith(":") and not stripped.startswith("-"):
                current_key = stripped.rstrip(":")
                recall.setdefault(current_key, [])
                continue
            if stripped.startswith("- ") and current_key:
                value = stripped[2:].split("#", 1)[0].strip().strip('"').strip("'")
                if value:
                    recall[current_key].append(value)  # type: ignore[arg-type]
                continue
            if ":" in stripped and current_key is None:
                k, v = stripped.split(":", 1)
                v = v.strip()
                if v.isdigit():
                    recall[k.strip()] = int(v)
    if recall:
        out["recall"] = recall
    return out


def _compose_recall(cmd_fm: dict, module_fm: dict) -> dict:
    """Union of command-level + module-level recall: blocks."""
    base: dict = {"ids": [], "scopes": [], "tags": [], "query": [], "k": MAX_RESULTS}
    for source in (module_fm.get("recall"), cmd_fm.get("recall")):
        if not isinstance(source, dict):
            continue
        for key in ("ids", "scopes", "tags", "query"):
            val = source.get(key) or []
            if isinstance(val, str):
                val = [val]
            for v in val:
                if v and v not in base[key]:
                    base[key].append(v)
        if isinstance(source.get("k"), int):
            base["k"] = max(base["k"], int(source["k"]))
    return base


# ---------------------------------------------------------------------------
# Engram resolution
# ---------------------------------------------------------------------------

_ENGRAM_CACHE: list[dict] | None = None


def _load_engrams() -> list[dict]:
    """Parse ~/.plur/engrams.yaml once per invocation. Returns list of engram dicts.

    Falls back to a regex-based extractor if PyYAML is missing or the file is
    too large to safely parse in 2s.
    """
    global _ENGRAM_CACHE
    if _ENGRAM_CACHE is not None:
        return _ENGRAM_CACHE
    if not PLUR_STORE.exists():
        _ENGRAM_CACHE = []
        return _ENGRAM_CACHE
    try:
        text = PLUR_STORE.read_text(encoding="utf-8")
    except OSError:
        _ENGRAM_CACHE = []
        return _ENGRAM_CACHE

    if _HAS_YAML:
        try:
            data = yaml.safe_load(text) or {}
            engrams = data.get("engrams", [])
            if isinstance(engrams, list):
                _ENGRAM_CACHE = [e for e in engrams if isinstance(e, dict)]
                return _ENGRAM_CACHE
        except Exception:
            pass

    # Regex fallback (slower, lossy)
    _ENGRAM_CACHE = _extract_engrams_regex(text)
    return _ENGRAM_CACHE


def _extract_engrams_regex(text: str) -> list[dict]:
    """Best-effort extraction when YAML parsing isn't available."""
    out: list[dict] = []
    MARKER = "\n  - id:"
    for block in text.split(MARKER)[1:]:
        eid = block.splitlines()[0].rstrip(":").strip()
        # statement (folded or plain)
        stmt = ""
        stmt_lines: list[str] = []
        in_stmt = False
        scope = ""
        tags: list[str] = []
        in_tags = False
        for line in block.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("scope:") and not scope:
                scope = stripped.split(":", 1)[1].strip()
            if stripped.startswith("statement:"):
                rest = stripped[len("statement:"):].strip()
                if rest in (">", ">-", "|", "|-"):
                    in_stmt = True
                    in_tags = False
                    continue
                if rest:
                    stmt = rest
                    break
            elif in_stmt:
                key_match = stripped.split(":", 1)[0] if ":" in stripped else ""
                if key_match in (
                    "derivation_count", "pack", "abstract", "derived_from",
                    "domain", "tags", "activation", "type", "visibility", "scope",
                    "associations",
                ):
                    in_stmt = False
                else:
                    if stripped:
                        stmt_lines.append(stripped)
            if stripped.startswith("tags:"):
                in_tags = True
                continue
            if in_tags:
                if stripped.startswith("- "):
                    tags.append(stripped[2:].strip())
                elif stripped and ":" in stripped:
                    in_tags = False
        if not stmt and stmt_lines:
            stmt = " ".join(stmt_lines).strip()
        if stmt:
            out.append({"id": eid, "statement": stmt, "scope": scope, "tags": tags})
    return out


def _resolve_declared(recall: dict, name: str) -> list[dict]:
    """Resolve ids / scopes / tags / queries against the engram store.

    Each result is scored:
      explicit id   : 100
      scope match   : 50
      tag match     : 30
      query keyword : 10 × hits
    """
    engrams = _load_engrams()
    if not engrams:
        return []

    by_id = {e.get("id"): e for e in engrams}
    results: dict[str, dict] = {}

    # Explicit IDs
    for eid in recall.get("ids", []):
        eng = by_id.get(eid)
        if eng:
            results[eid] = {**_normalize(eng), "score": results.get(eid, {}).get("score", 0) + 100}

    # Scopes
    wanted_scopes = {s.lower() for s in recall.get("scopes", [])}
    if wanted_scopes:
        for eng in engrams:
            scope = str(eng.get("scope", "")).lower()
            if scope in wanted_scopes:
                eid = eng.get("id")
                if not eid:
                    continue
                cur = results.get(eid, _normalize(eng))
                cur["score"] = cur.get("score", 0) + 50
                results[eid] = cur

    # Tags
    wanted_tags = {t.lower() for t in recall.get("tags", [])}
    if wanted_tags:
        for eng in engrams:
            tags = {str(t).lower() for t in (eng.get("tags") or [])}
            if tags & wanted_tags:
                eid = eng.get("id")
                if not eid:
                    continue
                cur = results.get(eid, _normalize(eng))
                cur["score"] = cur.get("score", 0) + 30
                results[eid] = cur

    # Free-text queries (BM25-lite: substring hits across statement + tags + domain)
    queries = recall.get("query") or []
    if queries:
        for eng in engrams:
            haystack = " ".join([
                str(eng.get("statement", "")).lower(),
                " ".join(str(t).lower() for t in (eng.get("tags") or [])),
                str(eng.get("domain", "")).lower(),
            ])
            hits = 0
            for q in queries:
                for word in re.findall(r"\w{4,}", q.lower()):
                    if word in haystack:
                        hits += 1
            if hits > 0:
                eid = eng.get("id")
                if not eid:
                    continue
                cur = results.get(eid, _normalize(eng))
                cur["score"] = cur.get("score", 0) + 10 * hits
                results[eid] = cur

    # Also include a soft name match (treats command name as implicit query)
    name_lower = name.lower()
    for eng in engrams:
        scope = str(eng.get("scope", "")).lower()
        tags = {str(t).lower() for t in (eng.get("tags") or [])}
        domain = str(eng.get("domain", "")).lower()
        soft = 0
        if scope == f"command:{name_lower}" or scope == f"skill:{name_lower}":
            soft += 25
        if name_lower in tags:
            soft += 10
        if name_lower in domain:
            soft += 5
        if soft:
            eid = eng.get("id")
            if not eid:
                continue
            cur = results.get(eid, _normalize(eng))
            cur["score"] = cur.get("score", 0) + soft
            results[eid] = cur

    ranked = sorted(results.values(), key=lambda r: r["score"], reverse=True)
    cap = int(recall.get("k", MAX_RESULTS) or MAX_RESULTS)
    return ranked[:cap]


def _normalize(eng: dict) -> dict:
    """Extract the fields we render."""
    stmt = eng.get("statement") or eng.get("text") or ""
    if isinstance(stmt, list):
        stmt = " ".join(str(s) for s in stmt)
    return {
        "id": eng.get("id", "UNKNOWN"),
        "statement": str(stmt).strip(),
        "scope": str(eng.get("scope", "")),
    }


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------

def _format_block(name: str, engrams: list[dict], source: str) -> str:
    if not engrams:
        return ""
    lines = [
        f"## Relevant memory for `/{name}` (DIP-0029 {source})",
        "",
        "_Per DIP-0029, these engrams are loaded before the command body executes._",
        "",
    ]
    chars = sum(len(l) for l in lines)
    char_budget = TOKEN_BUDGET * 4  # ~4 chars/token

    for e in engrams:
        eid = e.get("id", "UNKNOWN")
        stmt = e.get("statement", "").strip()
        if not stmt:
            continue
        entry = f"- **{eid}** — {stmt}"
        if chars + len(entry) + 1 > char_budget:
            break
        lines.append(entry)
        chars += len(entry) + 1
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw:
            _emit()
        payload = json.loads(raw)
    except Exception:
        _emit()

    name = _extract_name(payload)
    if not name:
        _emit()

    cmd_file, module_yaml = _resolve_command_file(name)
    cmd_fm = _parse_frontmatter(cmd_file) if cmd_file else {}
    module_fm = _parse_frontmatter(module_yaml) if module_yaml else {}

    # Module.yaml is YAML, not markdown — re-parse as plain YAML if frontmatter parse missed it
    if module_yaml and not module_fm and _HAS_YAML:
        try:
            module_fm = yaml.safe_load(module_yaml.read_text(encoding="utf-8")) or {}
        except Exception:
            module_fm = {}

    recall = _compose_recall(cmd_fm, module_fm)

    declared = _resolve_declared(recall, name)

    if declared:
        block = _format_block(name, declared, "frontmatter + fallback")
        _emit(block)

    # Phase 1 fallback: pure name-based recall when no frontmatter resolved anything
    recall_fb = {"ids": [], "scopes": [], "tags": [], "query": [name], "k": MAX_RESULTS}
    fb = _resolve_declared(recall_fb, name)
    block = _format_block(name, fb, "name-based fallback")
    _emit(block)


if __name__ == "__main__":
    main()
