#!/usr/bin/env python3
"""The intent graph: why work matters, and what is missing.

What changed and why
--------------------
This module used to hold four bespoke readers over four schemas — `rank` in
one, `stage`+`autonomy` in another, `horizon`+`status` in a third, and a
markdown table parsed by regex in the fourth — plus a hardcoded band order
(1000 > 500 > 200 > 100) asserting that personal goals always outrank venture
goals. That was an invented ladder standing in for one the system already
specifies.

`2-datacore/1-tracks/ops/Intent-Graph.md` — adapted from the Swarm Foundation
Mission.md structure — has held the real graph since 2026-02-21: Vision ->
Intents (5) -> Goals (19, every one with a success criterion) -> Initiatives
(64) -> Projects. `modules/gtd/skills/intent-routing.md` kept only the five
level-1 rows as a keyword table, and that lossy projection is what every
scorer read. Four levels of measurable structure sat unused by any code.

So there are no bespoke readers now. `.datacore/intents.org` is generated from
the markdown by intent_graph_convert.py and parsed by org-workspace, which
already understands nesting, properties and tags. Heading depth carries
what-serves-what; `:SERVES:` carries the cross-branch edges that make it a DAG.

Ranking follows the graph's OWN stated rules rather than invented weights:

  * "multi-intent projects get worked on first" (source doc, Priority rule)
  * the weekly spotlight in .datacore/cos/priorities.yaml raises one branch
    without rewriting the graph — priorities change while goals stay put

And because the structure is explicit, absence is computable. gaps() answers
the Review Protocol questions the source doc already asks: which intents got
no work, which leaves have no tasks, which success criteria cannot be
measured, which high-leverage nodes are being ignored.

    from priority_score import IntentGraph
    g = IntentGraph.load(root)
    g.score_10("publish the GEO batch", container="5-plur")
    g.gaps()
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

#: Work matching nothing on the graph. `task_queue.calculate_priority` treats
#: 5 as neutral, so unmapped work keeps exactly today's behaviour — absence of
#: alignment is not evidence of unimportance, it is evidence of a missing edge.
NEUTRAL = 5.0
ON_GRAPH = 6.0
HIGH_LEVERAGE = 8.0        # the source doc's own priority rule
SPOTLIT = 10.0             # named in this week's spotlight

INTENTS_ORG = ".datacore/intents.org"
SPOTLIGHT_YAML = ".datacore/cos/priorities.yaml"


@dataclass
class Node:
    id: str
    title: str
    level: int
    success: str = ""
    serves: tuple[str, ...] = ()      # extra parents (cross-branch)
    parent: str | None = None         # tree parent, from heading nesting
    children: list[str] = field(default_factory=list)
    keywords: tuple[str, ...] = ()
    #: Extra properties surfaced in review outlines. Kept as plain fields
    #: rather than a dict so a typo fails loudly at construction.
    gate: str = ""
    target: str = ""
    metric: str = ""
    owner: str = ""
    benchmark: str = ""
    window: str = ""
    banned: str = ""
    why: str = ""
    status: str = ""
    note: str = ""
    source: str = ""


def _keywords(title: str) -> tuple[str, ...]:
    """Distinctive words from a node title, for matching work against it.

    Stopwords stripped and short tokens dropped, because a graph node called
    "Costs stay minimal" must not match every task mentioning "stay"."""
    stop = {"the", "a", "an", "is", "are", "as", "of", "for", "to", "and", "in",
            "on", "with", "without", "that", "by", "it", "its", "into", "from",
            "be", "can", "will", "has", "have", "not", "no", "at", "or", "but",
            "this", "their", "them", "they", "we", "our", "you", "your", "all",
            "more", "most", "own", "via", "per", "up", "out", "first", "new",
            "work", "works", "make", "makes", "use", "uses", "using", "get",
            "gets", "stay", "stays", "enough", "good", "great", "well"}
    return tuple(w for w in re.findall(r"[a-z0-9]{4,}", title.lower())
                 if w not in stop)


class IntentGraph:
    def __init__(self, nodes: dict[str, Node], spotlight: list[dict],
                 tag_map: dict[str, str]):
        self.nodes = nodes
        self.spotlight = spotlight
        self.tag_map = tag_map

    # ── loading ──────────────────────────────────────────────────────────

    @classmethod
    def load(cls, root: Path) -> "IntentGraph":
        """Every space's graph, composed.

        The personal view IS the union: the principal works across ventures, so
        seeing them together is the point rather than a reporting extra. One
        graph could only ever describe one venture, which is why 154 tasks in
        six other spaces had no branch to hang from.

        Space ids are namespaced (`5-plur:north-star`) so two ventures may both
        have a "growth" node, while `:SERVES:` can still cross spaces when one
        venture's work genuinely serves another's goal.
        """
        root = Path(root)
        nodes = cls._read_org(root / INTENTS_ORG)
        for f in sorted(root.glob("[0-9]-*/org/intents.org")):
            nodes.update(cls._read_org(f, prefix=f.parent.parent.name))
        cls._resolve_serves(nodes)
        return cls(nodes, cls._read_spotlight(root / SPOTLIGHT_YAML),
                   cls._read_tag_map(root))

    @staticmethod
    def _resolve_serves(nodes: dict[str, Node]) -> None:
        """Rewrite `:SERVES:` targets to fully-qualified ids.

        A bare id resolves within its own space first, then globally — so a
        space's file stays readable without repeating its own prefix, and a
        deliberate cross-space link (`5-plur:knowledge-exchange`) still works.
        """
        for nid, n in list(nodes.items()):
            space = nid.split(":", 1)[0] if ":" in nid else ""
            fixed = []
            for target in n.serves:
                if target in nodes:
                    fixed.append(target)
                elif space and f"{space}:{target}" in nodes:
                    fixed.append(f"{space}:{target}")
                else:
                    # Keep it unresolved rather than dropping it: gaps() reports
                    # a broken link, which is a finding, not noise to swallow.
                    fixed.append(target)
            nodes[nid] = replace(n, serves=tuple(fixed))

    @staticmethod
    def _read_org(f: Path, prefix: str = "") -> dict[str, Node]:
        """No bespoke parser: org-workspace already models this file."""
        if not f.is_file():
            return {}
        try:
            from org_workspace import OrgWorkspace
        except ImportError:
            return {}
        ws = OrgWorkspace()
        try:
            ws.load(str(f))
        except Exception:
            return {}

        nodes: dict[str, Node] = {}
        stack: list[tuple[int, str]] = []   # (heading depth, id)
        for n in ws.all_nodes():
            nid = n.get_property("INTENT_ID")
            if not nid:
                continue
            # Namespace by space so two ventures may share a node name.
            nid = f"{prefix}:{nid}" if prefix else nid
            depth = getattr(n, "level", None) or len(stack) + 1
            while stack and stack[-1][0] >= depth:
                stack.pop()
            parent = stack[-1][1] if stack else None
            title = (n.heading or "").strip()
            serves = tuple((n.get_property("SERVES") or "").split())
            space_tag = n.get_property("SPACE") or prefix
            nodes[nid] = Node(id=nid, title=title,
                              level=int(n.get_property("LEVEL") or 0),
                              success=(n.get_property("SUCCESS") or "").strip(),
                              serves=serves, parent=parent,
                              keywords=_keywords(title),
                              **{k: (n.get_property(k.upper()) or "").strip()
                                 for k in ("gate", "target", "metric", "owner",
                                           "benchmark", "window", "banned",
                                           "why", "status", "note", "source")})
            if parent and parent in nodes:
                nodes[parent].children.append(nid)
            stack.append((depth, nid))
        return nodes

    @staticmethod
    def _read_spotlight(f: Path) -> list[dict]:
        """This week's spotlight. Deliberately NOT part of the graph: the graph
        is where you're heading, the spotlight is what you're doing about it
        now. Re-ranking on Monday must not look like a change of direction."""
        if not (yaml and f.is_file()):
            return []
        try:
            data = yaml.safe_load(f.read_text()) or {}
        except Exception:
            return []
        out = []
        for i, p in enumerate(data.get("spotlight") or data.get("priorities") or [], 1):
            if isinstance(p, str):
                out.append({"id": p, "rank": i, "keywords": ()})
            elif isinstance(p, dict):
                out.append({
                    "id": str(p.get("id") or p.get("intent") or ""),
                    "rank": int(p.get("rank") or i),
                    "statement": str(p.get("statement") or p.get("priority") or ""),
                    "keywords": tuple(str(k).lower() for k in (p.get("keywords") or [])),
                })
        return out

    @staticmethod
    def _read_tag_map(root: Path) -> dict[str, str]:
        """Focus-area tag -> intent id, read from the DIP-0014 tag registries.

        NOT a new registry. DIP-0014 already declares tags "a coding language"
        whose purpose is cross-system integration and "planning and
        prioritization across contexts" — linking a tag to the intent it serves
        is exactly that, so it is an `intent:` field on existing entries rather
        than a parallel file that would drift from them.

        Root registry first, then space registries, which may override for
        their own space (`:comms:` means something different in each venture).

        Tasks already carry `:plur:` (311), `:gtd:` (320), `:enterprise:` (46).
        Mapping tags places hundreds of tasks without editing any of them.
        """
        out: dict[str, str] = {}
        files = [root / ".datacore" / "tags.yaml"]
        files += sorted(root.glob("[0-9]-*/.datacore/tags.yaml"))
        for f in files:
            if not (yaml and f.is_file()):
                continue
            try:
                data = yaml.safe_load(f.read_text()) or {}
            except Exception:
                continue
            for section in data.values():
                if not isinstance(section, dict):
                    continue
                for tag, spec in section.items():
                    if isinstance(spec, dict) and spec.get("intent"):
                        out[str(tag).lower()] = str(spec["intent"])
                        org = str(spec.get("org") or "").strip(":").lower()
                        if org:
                            out[org] = str(spec["intent"])
        return out

    # ── graph ────────────────────────────────────────────────────────────

    def parents(self, nid: str) -> list[str]:
        """Tree parent plus cross-branch :SERVES: targets."""
        n = self.nodes.get(nid)
        if not n:
            return []
        return [p for p in ([n.parent] if n.parent else []) + list(n.serves)
                if p in self.nodes]

    def ancestors(self, nid: str) -> set[str]:
        seen, stack = set(), [nid]
        while stack:
            cur = stack.pop()
            for p in self.parents(cur):
                if p not in seen:
                    seen.add(p)
                    stack.append(p)
        return seen

    def is_high_leverage(self, nid: str) -> bool:
        """Serves more than one intent — the source doc's definition, and its
        stated reason to work on something first."""
        n = self.nodes.get(nid)
        return bool(n and n.serves)

    def leaves(self) -> list[Node]:
        """Structural leaves — nodes with no children declared beneath them."""
        return [n for n in self.nodes.values() if not n.children]

    def frontier(self, done: set[str] | None = None) -> list[Node]:
        """Where work actually happens right now.

        A node is not permanently an "initiative" or a "project" — its kind is
        emergent. While anything beneath it is open it is an aggregator; once
        everything beneath it is done it becomes the actionable thing itself
        and advances to the frontier. So the frontier MOVES UPWARD as work
        completes, and `:LEVEL:` only records which depth a node was authored
        at, never what it currently is.

        This is the same rule org-mode already applies to a heading with TODO
        children, which is why the intent graph and next_actions.org are the
        same shape at different granularities.
        """
        done = done or set()
        out = []
        for n in self.nodes.values():
            live = [c for c in n.children if c not in done]
            if not live and n.id not in done:
                out.append(n)
        return out

    # ── matching and scoring ─────────────────────────────────────────────

    @staticmethod
    def _hits(hay: str, kws) -> int:
        """Token-boundary, never substring: `plur` must not fire on "plural"."""
        return sum(1 for k in kws
                   if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", hay))

    def match(self, text: str, container: str = "", tags=()) -> Node | None:
        """Which graph node is this work serving?

        Tag mapping wins over keyword guessing — a task tagged `:plur:` is
        placed by declaration, not inference. Keywords are the fallback for
        work that carries no mapped tag.
        """
        for t in tags or ():
            nid = self.tag_map.get(str(t).lower())
            if nid and nid in self.nodes:
                return self.nodes[nid]
        # Container is deliberately NOT part of the haystack. Blending it in
        # let a space directory named after a node ("9-nightshift") match that
        # node for every item inside it, scoring the whole space identically —
        # the same saturation bug in a new place. Scoping now comes from the
        # tag registry, so the container is context, never evidence.
        hay = (text or "").lower()
        best, best_hits = None, 0
        for n in self.nodes.values():
            if not n.keywords:
                continue
            h = self._hits(hay, n.keywords)
            # Deeper nodes are more specific, so break ties downward.
            if h > best_hits or (h and h == best_hits and best and n.level > best.level):
                best, best_hits = n, h
        return best

    def spotlight_rank(self, nid: str | None) -> int | None:
        """Is this node, or anything it serves, named in this week's spotlight?"""
        if not nid:
            return None
        family = {nid} | self.ancestors(nid)
        best = None
        for s in self.spotlight:
            if s.get("id") and s["id"] in family:
                best = min(best or 99, s["rank"])
        return best

    def score_10(self, text: str, container: str = "", tags=()) -> float:
        """0-10 for `task_queue.calculate_priority`, which treats 5 as neutral.

        Follows the graph's own rules: spotlight first, then the doc's
        multi-intent priority rule, then merely being on the graph at all.
        """
        node = self.match(text, container, tags)
        if node is None:
            # Unmapped is neutral, never penalised — it means a missing edge,
            # which gaps() reports rather than this silently downranking.
            #
            # Spotlight keywords are matched against CONTENT ONLY. Including
            # the container would let the keyword "plur" match the directory
            # "5-plur" and score every item in that space at 10.0 — the same
            # saturation that made all 209 tasks in a space rank identically.
            for s in self.spotlight:
                if s.get("keywords") and self._hits(text.lower(), s["keywords"]):
                    return SPOTLIT - (s["rank"] - 1) * 0.5
            return NEUTRAL
        rank = self.spotlight_rank(node.id)
        if rank is not None:
            return max(ON_GRAPH, SPOTLIT - (rank - 1) * 0.5)
        if self.is_high_leverage(node.id) or any(
                self.is_high_leverage(a) for a in self.ancestors(node.id)):
            return HIGH_LEVERAGE
        return ON_GRAPH

    # ── gaps ─────────────────────────────────────────────────────────────

    def gaps(self, task_index: dict[str, int] | None = None) -> list[dict]:
        """What is missing. This is the point of having a structure at all.

        Implements the Review Protocol the source doc already prescribes —
        "which intents got zero work?", "any stale nodes?" — plus the checks
        that only became possible once leaves could be compared against real
        tasks. Each gap is a named hole, which is proposable work: it turns
        agents from producers of more output into fillers of specific
        absences.

        `task_index` maps intent id -> number of open tasks. Omit it and the
        work-coverage checks are skipped rather than guessed at.
        """
        out: list[dict] = []
        for n in self.nodes.values():
            if n.level == 2 and not n.success:
                out.append({"kind": "unmeasurable", "id": n.id,
                            "detail": "goal has no success criterion — aspiration, not a goal"})
            if n.success and re.search(r"not yet instrumented|TBD|\?\?", n.success, re.I):
                out.append({"kind": "uninstrumented", "id": n.id,
                            "detail": f"success criterion cannot be measured: {n.success[:60]}"})
            for s in n.serves:
                if s not in self.nodes:
                    out.append({"kind": "broken_link", "id": n.id,
                                "detail": f"serves '{s}', which is not in the graph"})
        for s in self.spotlight:
            # An entry with no resolvable id is the same failure as one naming
            # a missing node: this week's stated priority cannot be connected
            # to any stated goal. Skipping the id-less case would hide exactly
            # the priorities that most need the link.
            sid = s.get("id")
            if sid and sid in self.nodes:
                continue
            label = sid or (s.get("statement") or "?")[:44]
            out.append({"kind": "spotlight_off_graph", "id": label,
                        "detail": "this week's priority serves no node in the graph"})
        if task_index is not None:
            # The frontier is where work either exists or is missing. Nodes
            # deeper in the tree are covered by their descendants; nodes above
            # the frontier are aggregates and are not supposed to carry tasks
            # of their own. Checking every node instead of the frontier is what
            # would turn one real gap into a hundred noisy ones.
            done = {nid for nid in self.nodes if task_index.get(nid, 0) == 0
                    and self._descendants(nid) and all(
                        task_index.get(d, 0) == 0 for d in self._descendants(nid))}
            for n in self.frontier():
                if not task_index.get(n.id, 0):
                    out.append({"kind": "frontier_no_work", "id": n.id,
                                "detail": "nothing open beneath it and no tasks — "
                                          "this is where the next action must be defined"})
            for n in self.nodes.values():
                covered = task_index.get(n.id, 0)
                if n.level == 1 and not covered:
                    out.append({"kind": "no_work", "id": n.id,
                                "detail": "intent has no open tasks anywhere beneath it"})
                if self.is_high_leverage(n.id) and not covered:
                    out.append({"kind": "ignored_high_leverage", "id": n.id,
                                "detail": "multi-intent node with no open tasks — "
                                          "the graph's own rule says work these first"})
        return out

    def _descendants(self, nid: str) -> set[str]:
        seen, stack = set(), [nid]
        while stack:
            cur = stack.pop()
            for c in self.nodes.get(cur, Node("", "", 0)).children:
                if c not in seen:
                    seen.add(c)
                    stack.append(c)
        return seen


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Intent graph: score work, find gaps.")
    ap.add_argument("text", nargs="*")
    ap.add_argument("--root", default=str(Path.home() / "Data"))
    ap.add_argument("--container", default="")
    ap.add_argument("--tags", default="")
    ap.add_argument("--graph", action="store_true")
    ap.add_argument("--gaps", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).expanduser()
    g = IntentGraph.load(root)

    if a.graph:
        for n in sorted(g.nodes.values(), key=lambda n: (n.level, n.id)):
            flag = "HL" if g.is_high_leverage(n.id) else "  "
            print(f"  L{n.level} {flag} {n.id:38} {n.title[:52]}")
        print(f"\n  {len(g.nodes)} nodes, {len(g.leaves())} leaves, "
              f"{sum(1 for n in g.nodes if g.is_high_leverage(n))} high-leverage")
        return 0
    if a.gaps:
        from intent_tasks import task_index          # noqa: E402
        gaps = g.gaps(task_index(root, g))
        by_kind: dict[str, int] = {}
        for x in gaps:
            by_kind[x["kind"]] = by_kind.get(x["kind"], 0) + 1
        for x in gaps[:24]:
            print(f"  {x['kind']:22} {x['id'][:34]:36} {x['detail'][:52]}")
        print("\n  " + "  ".join(f"{k}={v}" for k, v in sorted(by_kind.items())))
        return 0

    tags = [t for t in a.tags.split(",") if t]
    node = g.match(" ".join(a.text), a.container, tags)
    print(json.dumps({
        "score": g.score_10(" ".join(a.text), a.container, tags),
        "matched": node.id if node else None,
        "title": node.title if node else None,
        "high_leverage": g.is_high_leverage(node.id) if node else False,
        "spotlight_rank": g.spotlight_rank(node.id if node else None),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(_cli())
