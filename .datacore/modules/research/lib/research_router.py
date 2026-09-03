#!/usr/bin/env python3
"""Route research items to evergreen podcast themes and to a work destination.

WHY DETERMINISTIC. Same reason strategic-prioritizer scores tasks by keyword
and not by asking a model: this runs unattended every night, and a router whose
answer moves between runs cannot be debugged from its output. Keywords are
auditable -- when an item lands in the wrong theme you can see which token did
it and fix that token.

TWO INDEPENDENT DECISIONS, deliberately not collapsed into one:

  theme  -- which evergreen NotebookLM notebook the SOURCE joins.
            Themes cross ventures on purpose; that is where the insight is.
  dest   -- where the ACTION goes, if the item implies one.
            `issue` only when a repo can be named AND a change can be named.
            Everything else is `inbox`. When in doubt it is `inbox`, because a
            wrong inbox item costs you ten seconds and a wrong issue is visible
            to your team.

An item can have a theme and no action (most reading), an action and no theme
(a repo chore that came up while reading), or neither -- `discard` exists
because a queue you cannot empty is not a queue. 203 open items with a median
age of 50 days is what happens when every capture must be processed.

WHERE THIS RUNS -- AFTER EXTRACTION, NOT AT CAPTURE. Measured 2026-09-02
against the live queue: given heading + URL + body, this router assigns a theme
to 36 of 203 items. The other 167 are titles like "Ben's Bites: Caught
cheating" that carry no theme signal until the article is fetched. Running this
at capture time therefore files five sixths of the queue as unthemed, which
looks like a router bug and is not one. Feed it the literature note that
knowledge-extractor produces, not the raw capture.

See docs/pipeline-podcasts.md for the pipeline this sits in.
"""
from __future__ import annotations

import json
import re
import sys

# Evergreen notebooks. `notebook` is filled in once the notebook exists; None
# means "create on first use". Keep titles free of dates -- the whole point of
# evergreen is that context compounds instead of forking a new notebook weekly.
THEMES = {
    "provenance": {
        "title": "Agent auditability & provenance",
        "notebook": "13c0e557-1916-4b5d-b533-e1860de38c96",
        "merge_from": ["57c04f66-cc18-49f0-a415-1c1b25f519e6"],
        "kw": ["provenance", "auditab", "audit trail", "attestation", "w3c prov",
               "hugging face", "huggingface", "rogue ai", "five eyes", "reward hack",
               "verifiab", "tamper", "chain of custody", "signed", "evidence"],
    },
    "payments": {
        "title": "Agent payments & monetization rails",
        "notebook": None,
        "merge_from": [],
        "kw": ["x402", "monetization", "monetisation", "agentic commerce", "stablecoin",
               "visa", "mastercard", "payment", "micropayment", "tokenized", "tokenised",
               "rwa", "aifi", "settlement", "payable"],
    },
    "sovereignty": {
        "title": "Data sovereignty & regulation",
        "notebook": None,
        "merge_from": [],
        "kw": ["sovereign", "gdpr", "ai act", "regulat", "palantir", "nhs",
               "addictive design", "surveill", "keystroke", "data protection",
               "consent", "pdp-connect", "privacy", "antitrust", "clarity act"],
    },
    "memory": {
        "title": "Agent memory & the competitive landscape",
        "notebook": "4edfd074-fd59-490a-9344-43b0e04b285c",
        "merge_from": ["62090361-0632-4f2d-9f01-0e905d545879"],
        "kw": ["memory", "engram", "recall", "claude-mem", "cmem", "mem0", "zep",
               "retrieval", "rag", "embedding", "vector", "knowledge graph",
               "consolidation", "sleep-time"],
    },
    "context": {
        "title": "Context engineering for long-horizon agents",
        "notebook": "52458c09-8f7e-4c5f-b804-94317dfdef33",
        "merge_from": ["3c9d1c28-9732-475b-82cc-4cc5507cd320"],
        "kw": ["context engineering", "long-horizon", "long horizon", "context window",
               "harness", "omnigent", "subagent", "sub-agent", "orchestrat",
               "skills.sh", "agent skills", "mcp", "tool use", "scaffold"],
    },
}

# Venture attribution -- what this changes, if anything. Separate from theme:
# a sovereignty item can be a Datafund item, a PLUR item, or neither.
VENTURES = {
    "plur":     ["plur", "engram", "claude-mem", "cmem", "mcp", "clawhub", "openclaw",
                 "agent memory", "skills.sh", "omnigent", "hermes"],
    "datafund": ["datafund", "verity", "data as an asset", "data marketplace", "kraken",
                 "x402", "tokenized", "monetization", "monetisation", "santorio"],
    "fds":      ["fds", "fair data", "fairdrop", "swarm", "sovereign", "data sovereignty",
                 "fairdrive", "bee", "bzz"],
    "datacore": ["datacore", "second brain", "org-mode", "gtd", "obsidian", "zettel"],
    "meridian": ["meridian", "trading", "hmm", "wyckoff", "quant", "backtest"],
}

# A repo can only be named when the text names it. No inference.
REPO_HINTS = {
    "plur-ai/plur":        ["plur-ai/plur", "plur core", "@plur-ai/core", "@plur-ai/mcp"],
    "plur-ai/enterprise":  ["plur-ai/enterprise", "scim", "sso"],
    "plur-ai/plur-bench":  ["plur-bench", "benchmark"],
    "plur-ai/website":     ["plur-ai/website"],
    "datafund/verity":     ["datafund/verity", "verity"],
    "fairDataSociety/Fairdrop":        ["fairdrop"],
    "fairDataSociety/fairdrive-theapp": ["fairdrive"],
}

# Captures that are not research. A queue you cannot empty is not a queue.
DISCARD = ["google search", "supplement", "testosterone", "- google docs",
           "untitled", "localhost", "127.0.0.1"]

ACTION_VERBS = ["should", "need to", "must", "fix", "add ", "implement", "migrate",
                "upgrade", "bump", "port ", "wire ", "close the", "gate ", "publish"]


# ---------------------------------------------------------------------------
# Relevance is a vector, not a theme match.
#
# Theme answers "which notebook does this source join". It does NOT answer
# "is this worth your attention", and conflating the two is why a keyword
# router looks like a relevance engine and is not one. Four axes, scored
# independently, because they disagree in useful ways: a piece can be highly
# timely and strategically irrelevant (most news), or untimely and decisive
# (a paper from March that invalidates a roadmap assumption).
#
#   timeliness  does it decay? News does. A method does not. Scored from the
#               item's age against a per-kind half-life, not a flat cutoff.
#   roadmap     does it touch work that is OPEN right now -- a live milestone,
#               an open PR, a named issue? This is the axis that turns reading
#               into doing, and the only one that can promote to `issue`.
#   strategy    does it move a venture thesis, or merely confirm it?
#               Confirmation is worth less than challenge and is scored lower.
#   meta        does it change HOW we work rather than what we know? Rare,
#               and the highest-value class -- a better method compounds
#               across every future item, which no single fact does.
#
# Scores are 0-3 and deliberately coarse. A finer scale would imply a
# precision keyword matching does not have.
# ---------------------------------------------------------------------------

HALF_LIFE_DAYS = {"news": 10, "analysis": 60, "method": 365, "unknown": 45}

NEWS_MARKERS = ["bloomberg", "reuters", "techcrunch", "wsj", "nytimes", "cnbc",
                "coindesk", "cointelegraph", "theverge", "guardian", "ft.com"]
METHOD_MARKERS = ["arxiv", "paper", "benchmark", "spec", "rfc", "protocol",
                  "architecture", "postmortem", "post-mortem", "how we", "lessons"]

# Challenge outranks confirmation: a source that contradicts the thesis is
# worth more than one that agrees with it.
CHALLENGE = ["however", "contrary", "fails", "wrong", "myth", "overrated",
             "does not", "debunk", "instead of", "versus", "vs ", "critique",
             "limitation", "underperform", "regress"]

META = ["workflow", "how we work", "process", "practice", "convention",
        "postmortem", "post-mortem", "retro", "methodology", "operating",
        "checklist", "discipline", "habit", "tooling"]


def _kind(text: str) -> str:
    if any(m in text for m in METHOD_MARKERS):
        return "method"
    if any(m in text for m in NEWS_MARKERS):
        return "news"
    return "unknown"


def _timeliness(text: str, age_days: int | None) -> int:
    """3 = fresh for its kind, 0 = past its half-life twice over."""
    if age_days is None:
        return 1  # unknown age is not a reason to promote or bury
    hl = HALF_LIFE_DAYS.get(_kind(text), HALF_LIFE_DAYS["unknown"])
    ratio = age_days / hl
    return 3 if ratio <= .5 else 2 if ratio <= 1 else 1 if ratio <= 2 else 0


def _roadmap(text: str, open_work: dict) -> tuple[int, list[str]]:
    """Score against work that is actually open, passed in by the caller.

    `open_work` maps a matchable token -> a human label, e.g.
    {"omnigent": "PR #3353", "provenance": "PR #1108"}. It is a parameter and
    not a constant on purpose: what is open changes daily, and a router with a
    hardcoded roadmap is stale the moment it is committed.
    """
    hit = [label for tok, label in open_work.items() if tok in text]
    return (3 if len(hit) > 1 else 2 if hit else 0), hit


def _strategy(text: str, ventures: list[str]) -> int:
    if not ventures:
        return 0
    return 3 if any(w in text for w in CHALLENGE) else 2


def _meta(text: str) -> int:
    n = len(_hits(text, META))
    return 3 if n >= 2 else 2 if n == 1 else 0


def _hits(text: str, words) -> list[str]:
    return [w for w in words if w in text]


def classify(item: dict, open_work: dict | None = None,
             age_days: int | None = None) -> dict:
    # The URL is signal, and it is signal the heading often lacks: a bare
    # newsletter title says nothing, its domain says a great deal. The URL
    # lives in org link syntax [[url][title]] or a :SOURCE: property, and an
    # extract that reads only `heading` silently drops it -- which is how a
    # 193-of-203-have-URLs queue gets misread as unprocessable.
    text = " ".join([item.get("heading", ""), item.get("body", "") or "",
                     item.get("url", "") or ""]).lower()
    tags = [t.lower() for t in (item.get("tags") or [])]

    if any(d in text for d in DISCARD):
        return {"theme": None, "ventures": [], "repo": None,
                "dest": "discard", "why": "not research",
                "relevance": {"timeliness": 0, "roadmap": 0,
                              "strategy": 0, "meta": 0},
                "score": 0, "roadmap_hits": []}

    scored = {k: len(_hits(text, v["kw"])) for k, v in THEMES.items()}
    theme = max(scored, key=scored.get) if max(scored.values()) > 0 else None

    ventures = [v for v, kws in VENTURES.items()
                if _hits(text, kws) or v in tags]

    repo = next((r for r, kws in REPO_HINTS.items() if _hits(text, kws)), None)
    names_change = any(v in text for v in ACTION_VERBS)

    open_work = open_work or {}
    if age_days is None:
        age_days = item.get("age_days")

    rm, rm_hits = _roadmap(text, open_work)
    rel = {
        "timeliness": _timeliness(text, age_days),
        "roadmap": rm,
        "strategy": _strategy(text, ventures),
        "meta": _meta(text),
    }
    # Roadmap and meta are weighted double. Roadmap because it is the axis
    # that converts reading into work; meta because a better method compounds
    # across every future item, which no single fact does. Timeliness is NOT
    # weighted up -- fresh is not the same as important, and weighting it
    # would rebuild a news feed.
    score = (rel["timeliness"] + rel["strategy"]
             + 2 * rel["roadmap"] + 2 * rel["meta"])

    # The gate the user set: name the repo AND name the change, or it is inbox.
    # Roadmap relevance is a necessary condition for an issue but never a
    # sufficient one -- it can stop a low-relevance item becoming an issue,
    # and can never promote one on its own.
    if repo and names_change and rm > 0:
        dest, why = "issue", f"names {repo} and a change; touches {', '.join(rm_hits)}"
    elif repo and names_change:
        dest, why = "inbox", f"names {repo} and a change, but no open work touches it"
    elif theme or ventures:
        dest, why = "inbox", "actionable but no repo+change named"
    else:
        dest, why = "inbox", "unclassified — needs a human glance"

    return {"theme": theme, "ventures": ventures, "repo": repo,
            "dest": dest, "why": why,
            "relevance": rel, "score": score, "roadmap_hits": rm_hits}


def main() -> int:
    args = sys.argv[1:]
    open_work = {}
    if "--open-work" in args:
        i = args.index("--open-work")
        open_work = json.load(open(args[i + 1]))
        del args[i:i + 2]
    items = json.load(open(args[0])) if args else json.load(sys.stdin)
    for it in items:
        it.update(classify(it, open_work=open_work))
    items.sort(key=lambda x: -x["score"])
    json.dump(items, sys.stdout, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
