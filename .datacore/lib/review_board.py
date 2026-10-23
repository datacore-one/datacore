#!/usr/bin/env python3
"""
review_board.py — the morning review board (org task cedc45c3).

Owner: "for reviews, there should be a decision board generated in the morning."

Finds every task in REVIEW across the spaces' org files (agent output and other
finished work waiting for the owner, nightshift results included) and renders
it as one decision board through gtd_decision_board.build, so the page, its
build identity and the apply path are the existing ones:

  * each row offers Accept / Rework it / Drop (plus "Already done");
  * each row carries its evidence: PR or issue links, the report path, the test
    result and any second-model review;
  * the board is written to $DATACORE_STATE/decision-boards/ (owner-only) and
    is never published.

Review rules (.datacore/config/review_rules.local.yaml, owner-edited and
gitignored; the shipped review_rules.local.yaml.example passes nothing):
`auto_pass` lists review types that may pass without the owner when the tests
are green AND a reviewer from a different model family passed the work. Such
rows go to their own section, pre-set to Accept, and the same choices are
saved as an answers file that `gtd_decision_board.py apply` accepts. Nothing
is applied here.

Never passed without the owner, whatever the rules say: release, security,
money, credentials and public posts. A rules entry naming one of them is
refused; a task whose type, tags or title touches one stays with the owner.

Usage:
  python3 .datacore/lib/review_board.py [--root ~/Data] [--today YYYY-MM-DD]
                                        [--rules PATH] [--slug morning-review]
The last stdout line is the job's summary:
  review-board: N items (A passed by rules) from F files, S skipped -> <path>
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import gtd_decision_board as B  # noqa: E402
from org_workspace_adapter import _load_ws, _node_to_dict  # noqa: E402
from file_utils import atomic_write_json, private_state_directory  # noqa: E402
from org_transaction import serialized  # noqa: E402

DEFAULT_RULES = HERE.parent / "config" / "review_rules.local.yaml"
AI_TYPES = ("research", "content", "data", "pm", "technical")

# Kinds the owner always reviews. Rules cannot name them; detection errs wide.
PROTECTED_TYPES = {
    "release", "releases", "security", "money", "payment", "payments", "finance",
    "credentials", "credential", "secret", "secrets", "public-post", "public-posts",
    "post", "posts", "publish", "social",
}
PROTECTED_WORDS = re.compile(
    r"\b(release[sd]?|version bump|publish\w*|post(s|ed|ing)?|tweet\w*|announce\w*|blog|"
    r"newsletter|press|social|security|vulnerab\w*|cve|exploit\w*|auth\w*|permission\w*|"
    r"token(s)?|credential(s)?|secret(s)?|password(s)?|api[ -]?key(s)?|rotat\w*|ssh|"
    r"pay|paid|payment(s)?|invoice(s)?|money|fund(s|ing)?|transfer\w*|pric\w*|billing|"
    r"trad(e|es|ing)|wallet(s)?|refund\w*|tax|bank\w*)\b", re.I)

FAMILIES = [
    ("anthropic", r"claude|opus|sonnet|haiku|anthropic|fable"),
    ("openai", r"gpt|openai|codex|\bo[134]\b"),
    ("google", r"gemini|google|palm"),
    ("meta", r"llama"),
    ("mistral", r"mistral|mixtral"),
    ("deepseek", r"deepseek"),
    ("qwen", r"qwen"),
    ("xai", r"grok"),
]
LINK_PROPS = (("PR", "PR"), ("PR_URL", "PR"), ("PULL_REQUEST", "PR"), ("ISSUE", "issue"),
              ("REF", "link"), ("URL", "link"))
PATH_PROPS = ("NIGHTSHIFT_OUTPUT", "REPORT", "DRAFT_PATH", "OUTPUT")


def _norm(t: str) -> str:
    return re.sub(r"[\s_]+", "-", str(t).strip().lower())


def is_protected_type(t: str) -> bool:
    return _norm(t) in PROTECTED_TYPES


def load_rules(path) -> dict:
    """{'auto_pass': [types], 'refused': [entries], 'error': str|None}. A missing
    file is the default: the owner reviews everything. An unreadable one passes
    nothing either."""
    rules = {"auto_pass": [], "refused": [], "error": None}
    if path is None or not Path(path).exists():
        return rules
    import yaml
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        raw = doc.get("auto_pass") or [] if isinstance(doc, dict) else None
        if not isinstance(raw, list):
            raise ValueError("auto_pass must be a list of review types")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        rules["error"] = f"{type(exc).__name__}: {exc}"
        return rules
    for t in raw:
        if is_protected_type(str(t)):
            rules["refused"].append(str(t))
        elif _norm(t) and _norm(t) not in rules["auto_pass"]:
            rules["auto_pass"].append(_norm(t))
    return rules


def family(model: str | None) -> str | None:
    for name, pat in FAMILIES:
        if model and re.search(pat, model, re.I):
            return name
    return None


def review_type(props: dict, tags: list[str]) -> str:
    for key in ("REVIEW_TYPE", "AI_TYPE"):
        if (props.get(key) or "").strip():
            return _norm(props[key])
    low = [t.lower() for t in tags]
    for t in AI_TYPES:
        if "ai" in low and t in low:
            return t
    return "general"


def touches_protected(typ: str, tags: list[str], heading: str) -> bool:
    if is_protected_type(typ) or any(is_protected_type(t) for t in tags):
        return True
    return bool(PROTECTED_WORDS.search(heading) or any(PROTECTED_WORDS.search(t.replace("_", " ")) for t in tags))


def tests_green(v: str) -> bool:
    v = v or ""
    return bool(re.match(r"\s*(green|pass(ed|es)?)\b", v, re.I)) and not re.search(r"\b(red|fail\w*|error\w*)\b", v, re.I)


def second_family_passed(props: dict) -> bool:
    verdict = props.get("SECOND_REVIEW") or ""
    if not re.match(r"\s*(pass(ed)?|approved?|green|ok|lgtm)\b", verdict, re.I):
        return False
    made = family(props.get("MODEL") or props.get("NIGHTSHIFT_MODEL"))
    checked = family(props.get("SECOND_REVIEW_MODEL"))
    return made is not None and checked is not None and made != checked


def evidence(props: dict) -> tuple[list[dict], list[str]]:
    links, meta, seen = [], [], set()
    for key, label in LINK_PROPS:
        v = (props.get(key) or "").strip()
        if re.match(r"https?://\S+$", v) and v not in seen:
            seen.add(v)
            links.append({"text": label, "url": v})
    for key in PATH_PROPS:
        v = (props.get(key) or "").strip()
        if v and not v.startswith("http"):
            meta.append(f"report: {v[:160]}")
            break
    if (props.get("TESTS") or props.get("TEST_RESULT") or "").strip():
        meta.append(f"tests: {(props.get('TESTS') or props.get('TEST_RESULT')).strip()[:80]}")
    if (props.get("SECOND_REVIEW") or "").strip():
        who = (props.get("SECOND_REVIEW_MODEL") or "").strip()
        meta.append(f"second review: {props['SECOND_REVIEW'].strip()[:40]}" + (f" ({who[:40]})" if who else ""))
    return links, meta


@serialized
def discover(root: Path) -> tuple[list[tuple[Path, list]], list[str]]:
    """Org files holding REVIEW tasks, each with its REVIEW nodes, and the files
    that could not be put on a board (with why)."""
    from org_space import validate_org_write_path
    from org_transaction import digest, watch_file
    found, skipped = [], []
    for f in sorted(root.glob("[0-9]-*/org/*.org")):
        if "archive" in f.name.lower():
            continue
        try:
            ws = _load_ws(str(f))
            nodes = [n for n in ws.all_nodes() if n.todo == "REVIEW"]
            if not nodes:
                continue
            path = validate_org_write_path(f)
            B._authority({str(path): digest(watch_file(path)["before"])}, require_projection=True)
        except Exception as exc:  # one unreadable file must not hide every other review
            skipped.append(f"{f.parent.parent.name}/{f.name} ({str(exc)[:120]})")
            continue
        found.append((path, nodes))
    return found, skipped


def _suggest(props: dict, age: int | None, passed: bool) -> str:
    if passed:
        return "accept"
    score = B._float(props.get("NIGHTSHIFT_SCORE"))
    has_out = any((props.get(k) or "").strip() for k in PATH_PROPS)
    if tests_green(props.get("TESTS") or props.get("TEST_RESULT") or "") or (score is not None and score >= 0.75 and has_out):
        return "accept"
    if age is not None and age > 60:
        return "drop"
    return "accept" if has_out else "next"


def run(*, root: Path, rules_path=DEFAULT_RULES, today: str | None = None,
        slug: str = "morning-review", as_of: str | None = None, out: str | None = None) -> dict:
    today = today or date.today().isoformat()
    day = date.fromisoformat(today)
    rules = load_rules(rules_path)
    found, skipped = discover(Path(root))
    overrides, passed_ids = {}, []
    for path, nodes in found:
        for node in nodes:
            d = _node_to_dict(node)
            props, tags, oid = d["properties"] or {}, d["tags"] or [], d["id"]
            if not oid:
                continue
            created = B._created(props, oid)
            age = (day - created).days if created else None
            typ = review_type(props, tags)
            passed = (typ in rules["auto_pass"] and not touches_protected(typ, tags, d["heading"])
                      and tests_green(props.get("TESTS") or props.get("TEST_RESULT") or "")
                      and second_family_passed(props))
            links, meta = evidence(props)
            ov = {"options": ["accept", "next", "drop"], "rec": _suggest(props, age, passed),
                  "meta": [f"type: {typ}"] + meta}
            if links:
                ov["links"] = links
            if passed:
                ov["section"] = "autopass"
                passed_ids.append(oid)
            overrides[oid] = ov

    noted = []
    if skipped:
        noted.append({"text": f"{len(skipped)} org file(s) with reviews could not be read this morning, "
                              f"so their items are not on this board: " + "; ".join(skipped)})
    if rules["refused"]:
        noted.append({"text": "Your review rules name kinds that always come to you, so they were ignored: "
                              + ", ".join(rules["refused"]) + "."})
    if rules["error"]:
        noted.append({"text": f"The review rules file could not be read ({rules['error']}); nothing passes without you."})
    work = private_state_directory("review-board")
    ov_file, week_file = work / f"{today}-{slug}.overrides.json", work / f"{today}-{slug}.week.json"
    atomic_write_json(ov_file, overrides)
    atomic_write_json(week_file, {"sections": [{"key": "board-notes", "prefix": "S", "label": "About this board",
                                                "hint": "", "rows": [], "noted": noted}]} if noted else {})

    args = argparse.Namespace(
        files=[str(p) for p, _ in found], projects=None, today=today, slug=slug,
        title="Morning review", week=str(week_file), overrides=str(ov_file), redact=None, prefill=None,
        states=["REVIEW"], min_age=None, h1="Waiting for your review",
        eyebrow=f"Morning review · {today}",
        lede=("Finished work across every space that waits for your sign-off, with its evidence. "
              "Accept, send back for rework, or drop; nothing changes until you say “apply the decisions”."),
        as_of=as_of or datetime.now().strftime("%Y-%m-%d %H:%M"), out=out)
    with contextlib.redirect_stdout(io.StringIO()):
        B.build(args)
    board_path = Path(out) if out else private_state_directory("decision-boards") / f"{today}-{slug}.html"
    data, _ = B._board_rows(board_path)
    rows = [r for s in data["sections"] for r in s["rows"] if r.get("apply")]
    autopass = None
    picked = {r["id"]: {"choice": "accept", "note": "passed by your review rules"}
              for r in rows if r["apply"].get("orgId") in passed_ids}
    if picked:
        answers = private_state_directory("decision-boards/answers")
        autopass = answers / f"{today}-{slug}.autopass.decisions.json"
        atomic_write_json(autopass, {"board": slug, "build": data["meta"]["build"],
                                     "savedAt": datetime.now().isoformat(), "decisions": picked})
        args.prefill = str(autopass)
        with contextlib.redirect_stdout(io.StringIO()):
            B.build(args)
    return {"out": str(board_path), "items": len(rows), "autopass_count": len(picked),
            "autopass": str(autopass) if autopass else None,
            "files": len(found), "skipped": skipped, "rules": rules}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write the morning review decision board (local only)")
    ap.add_argument("--root", default=os.environ.get("DATACORE_ROOT") or str(Path.home() / "Data"))
    ap.add_argument("--today", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--rules", default=str(DEFAULT_RULES), help="review rules YAML (default: the gitignored local file)")
    ap.add_argument("--slug", default="morning-review")
    ap.add_argument("--out", help="default: $DATACORE_STATE/decision-boards/<today>-<slug>.html")
    a = ap.parse_args(argv)
    r = run(root=Path(a.root), rules_path=a.rules, today=a.today, slug=a.slug, out=a.out)
    print(json.dumps({k: r[k] for k in ("out", "autopass", "skipped")}, ensure_ascii=False))
    print(f"review-board: {r['items']} items ({r['autopass_count']} passed by rules) from {r['files']} files, "
          f"{len(r['skipped'])} skipped -> {r['out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
