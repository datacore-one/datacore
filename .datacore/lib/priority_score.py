#!/usr/bin/env python3
"""One scorer for "what matters most", shared by every agent that picks work.

Why this exists
---------------
Two priority sources existed and never met:

  .datacore/cos/priorities.yaml        the principal's ranked priorities,
                                       rewritten at weekly planning
  modules/gtd/skills/intent-routing.md a static 5-intent graph marked
                                       "(extracted)", with no regeneration path

`strategic-prioritizer` — the thing `queue-optimizer` and `gtd-inbox-processor`
call to order autonomous work — read only the second. So the file the principal
actually edits each week steered the briefing's prose and nothing else. Stating
"GEO is this week's priority" would not have changed one task nightshift chose,
and the GEO drafts sat 22 days while GEO was described as struggling.

Both layers are legitimate; they answer different questions:

  intents     WHY the work matters. Stable, mission-level, rarely edited.
  priorities  WHAT matters NOW. Volatile, re-stated weekly, in the user's voice.

So they are layered rather than merged. A stated priority outranks every
mission intent — that is what stating one means — but an item matching no
priority still sorts by intent rather than falling to zero, so nothing outside
this week's focus becomes invisible.

Scoring is deterministic keyword/tag overlap, never an LLM call: the ordering
must be explainable ("this is first because you said GEO") and reproducible
across machines that cannot reach the same model.

    from priority_score import Scorer
    s = Scorer.load(root)
    score, why, layer = s.score("5-plur/1-tracks/comms/geo-queue.md")
"""
from __future__ import annotations

import re
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - present in every datacore venv
    yaml = None

#: Four bands, ordered by how deliberately and how recently each was stated.
#: A weekly priority outranks a quarterly goal outranks a venture's standing
#: stage outranks the mission graph. Bands never overlap, so the ordering is a
#: property of WHERE something was stated, not of how many words happened to
#: match — which keeps it explainable.
PRIORITY_BASE = 1000   # priorities.yaml — rewritten at weekly planning
GOAL_BASE = 500        # goals.yaml — open, quarter-horizon commitments
VENTURE_BASE = 200     # venture.yaml — standing stage/autonomy of the venture
INTENT_BASE = 100      # intent-routing.md — mission, rarely edited

#: Stage says how much a venture should be pulling attention right now.
_STAGE_WEIGHT = {"growth": 100, "scaling": 100, "validation": 50,
                 "discovery": 0, "paused": -150, "archived": -200}

_ROW = re.compile(r"^\|\s*\d+\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")


class Scorer:
    """Layers of (weight, label, keywords), highest-weight match wins."""

    def __init__(self, layers: list[tuple[int, str, list[str], str]]):
        self.layers = layers

    # ── loading ──────────────────────────────────────────────────────────

    @classmethod
    def load(cls, root: Path) -> "Scorer":
        return cls(cls._priorities(root) + cls._goals(root)
                   + cls._ventures(root) + cls._intents(root))

    @staticmethod
    def _goals(root: Path) -> list[tuple[int, str, list[str], str]]:
        """Open goals. Only those carrying explicit `keywords` participate.

        Deliberately NOT inferred from the prose statement: a goal about
        "sleep of 7.5 hours" would otherwise match anything mentioning hours,
        and a scorer that silently guesses is worse than one that visibly does
        nothing. `goals_without_keywords()` reports the gap instead.
        """
        f = root / "0-personal" / "goals.yaml"
        if not (yaml and f.is_file()):
            return []
        try:
            data = yaml.safe_load(f.read_text()) or {}
        except Exception:
            return []
        out = []
        for g in data.get("goals") or []:
            if g.get("status") != "open":
                continue
            kws = [str(k).lower() for k in (g.get("keywords") or []) if k]
            if kws:
                out.append((GOAL_BASE, str(g.get("statement") or g.get("id"))[:60],
                            kws, "goal"))
        return out

    @staticmethod
    def _ventures(root: Path) -> list[tuple[int, str, list[str], str]]:
        """Standing weight per venture, matched by SPACE DIRECTORY.

        Space is used rather than keywords because it is unambiguous: anything
        under `5-plur/` is PLUR's work whatever words it happens to contain.
        A paused venture scores negative, so its backlog sinks below neutral
        work instead of competing with a venture that is actually running.
        """
        out = []
        for f in sorted(root.glob("[0-9]-*/venture.yaml")):
            try:
                d = yaml.safe_load(f.read_text()) or {}
            except Exception:
                continue
            stage = str(d.get("stage") or "").lower()
            if d.get("paused") is True:
                stage = "paused"
            weight = (VENTURE_BASE + _STAGE_WEIGHT.get(stage, 0)
                      + int(d.get("autonomy") or 0) * 10)
            label = f"{d.get('display_name') or d.get('name') or f.parent.name} ({stage or '?'})"
            # The space directory IS the keyword. No trailing slash: boundary
            # matching already delimits it, and "5-plur/" would demand a
            # non-alphanumeric AFTER the slash, which never holds in a path.
            out.append((weight, label, [f.parent.name.lower()], "venture"))
        return out

    def goals_without_keywords(self, root: Path) -> list[str]:
        """Open goals that cannot steer anything yet. Surfaced so the gap is
        visible rather than silently inert."""
        f = root / "0-personal" / "goals.yaml"
        if not (yaml and f.is_file()):
            return []
        try:
            data = yaml.safe_load(f.read_text()) or {}
        except Exception:
            return []
        return [str(g.get("statement") or g.get("id"))[:70]
                for g in (data.get("goals") or [])
                if g.get("status") == "open" and not g.get("keywords")]

    @staticmethod
    def _priorities(root: Path) -> list[tuple[int, str, list[str], str]]:
        f = root / ".datacore" / "cos" / "priorities.yaml"
        if not (yaml and f.is_file()):
            return []
        try:
            data = yaml.safe_load(f.read_text()) or {}
        except Exception:
            return []
        out = []
        for p in data.get("priorities") or []:
            kws = [str(k).lower() for k in (p.get("keywords") or []) if k]
            if not kws:
                continue
            # rank 1 must beat rank 2 outright, so rank dominates hit count.
            rank = int(p.get("rank") or 9)
            out.append((PRIORITY_BASE + (10 - min(rank, 9)) * 10,
                        str(p.get("priority") or "priority"), kws, "priority"))
        return out

    @staticmethod
    def _intents(root: Path) -> list[tuple[int, str, list[str], str]]:
        """The mission-level graph. Parsed from the skill's markdown table
        because that table IS the definition — duplicating it into yaml would
        create a third source of truth, which is the bug being fixed."""
        f = root / ".datacore" / "modules" / "gtd" / "skills" / "intent-routing.md"
        if not f.is_file():
            return []
        out = []
        try:
            lines = f.read_text(errors="ignore").splitlines()
        except OSError:
            return []
        for line in lines:
            m = _ROW.match(line.strip())
            if not m:
                continue
            label, kw_cell = m.group(1).strip(), m.group(2)
            kws = [k.strip().lower() for k in kw_cell.split(",") if k.strip()]
            if kws:
                out.append((INTENT_BASE, label, kws, "intent"))
        return out

    # ── scoring ──────────────────────────────────────────────────────────

    @staticmethod
    def _hits(hay: str, kws: list[str]) -> int:
        """Token-boundary match, not raw substring.

        `plur` must not match "plural" and `pack` must not match "package" —
        the paths being scored are full of English words, and a scorer that
        fires on accidental substrings produces confident nonsense. Boundaries
        are alphanumeric, so `-` and `/` in "5-plur/1-tracks" still delimit.
        """
        return sum(1 for k in kws
                   if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", hay))

    def score(self, text: str, container: str | None = None
              ) -> tuple[int, str | None, str | None]:
        """Return (score, matched label, layer). 0/None/None when nothing
        matches — a neutral item, not a penalised one.

        `container` (the space directory) is scored ONLY against venture
        layers, and `text` only against the rest. Merging them saturated
        everything: priority 1's keywords include "plur", which matches the
        path "5-plur/...", so all 209 tasks in that space scored identically
        and alignment discriminated nothing. Splitting them is what lets one
        GEO draft outrank its neighbours in the same space — which is the
        whole point of naming GEO a priority.
        """
        if container is None:
            # Called with a bare path: derive the container from it, so the
            # space segment never also feeds the priority keywords.
            text, container = self.split(text)
        hay = (text or "").lower()
        cont = (container or "").lower()
        best = (0, None, None)
        for weight, label, kws, layer in self.layers:
            subject = cont if layer == "venture" else hay
            if not subject:
                continue
            hits = self._hits(subject, kws)
            if hits and weight + hits > best[0]:
                best = (weight + hits, label, layer)
        return best

    @staticmethod
    def split(path: str) -> tuple[str, str]:
        """(content, container) for a repo-relative path.

        The leading `N-name/` segment is the container; everything after it is
        content, because the filename is where the topic actually lives
        ("plur-geo-distribution-approval-queue.md")."""
        p = str(path or "").lstrip("./")
        head, sep, rest = p.partition("/")
        if sep and re.match(r"^\d+-", head):
            return rest, head
        return p, ""

    def score_item(self, item: dict) -> tuple[int, str | None, str | None]:
        content, container = self.split(item.get("path") or "")
        container = container or str(item.get("space") or "")
        rest = " ".join(str(item.get(f, "")) for f in
                        ("agent", "queue", "heading", "tags"))
        return self.score(f"{content} {rest}", container)

    #: Layer -> the 0-10 band nightshift's priority formula expects. 5 is
    #: neutral there, so unmatched work keeps its existing behaviour exactly
    #: and only aligned/misaligned work moves.
    _BAND = {"priority": 10.0, "goal": 8.0, "venture": 6.0, "intent": 5.5}

    def score_10(self, text: str, container: str | None = None) -> float:
        """Normalise to the 0-10 scale `task_queue._calculate_priority` uses.

        Computed live rather than read from an `:INTENT_SCORE:` property:
        that property was never written to a single one of 877 tasks, so the
        term sat at its neutral default forever and priorities could not move
        anything. A value derived at queue-build time cannot go unpopulated
        or stale.

        A paused venture scores BELOW neutral, which is the only way stated
        de-prioritisation actually de-prioritises.
        """
        raw, _, layer = self.score(text, container)
        if layer is None:
            return 5.0
        if layer == "venture":
            # Recover the stage signal: bands run 50 (paused) .. 310 (growth).
            return max(0.0, min(10.0, 3.0 + (raw - VENTURE_BASE) / 30.0))
        return self._BAND.get(layer, 5.0)


def _cli() -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Score text against stated priorities.")
    ap.add_argument("text", nargs="*")
    ap.add_argument("--root", default=str(Path.home() / "Data"))
    ap.add_argument("--layers", action="store_true", help="show what is loaded")
    a = ap.parse_args()
    s = Scorer.load(Path(a.root).expanduser())
    if a.layers:
        for w, label, kws, layer in s.layers:
            print(f"  {w:>5} {layer:8} {label[:44]:46} {', '.join(kws[:6])}")
        return 0
    score, label, layer = s.score(" ".join(a.text))
    print(json.dumps({"score": score, "matched": label, "layer": layer}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
