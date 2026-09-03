# Research pipeline → evergreen podcasts

Status: proposal, 2026-09-02. Decisions marked **[G]** were made by Gregor.

## Where this came from

The research queue holds **203 open items, median age 50 days**, 17 of the 19
dated items older than 30 days. Two beliefs about it turned out to be wrong on
inspection, and both are worth recording because they were the reason it never
drained.

**Wrong belief 1: "the queue is full of bare titles with no links."**
193 of 203 carry a URL. They are just not where a naive read looks — the URL
lives in org link syntax `[[url][title]]` or in a `:SOURCE:` property, and an
extractor that reads only the heading returns the title with the link stripped.
The queue is fully processable.

**Wrong belief 2: "we can route items to themes at capture time."**
No. With title + URL + body, a keyword router themes only **36 of 203**.
"Ben's Bites: Caught cheating" and "Not Boring: Weekly Dose of Optimism #203"
carry no theme signal at all until the content is fetched. Theme assignment is
a post-extraction step, not a triage step. This is the single most important
ordering constraint in the design below.

## Pipeline order

```
capture (URL)
    │
    ▼
url-fetcher ──────────► content            existing agent
    │
    ▼
knowledge-extractor ──► literature note     existing agent
    │                    + summary
    ▼
research_router.py ───► theme + venture + dest      ← NEW, runs HERE
    │                                                  not at capture
    ├─► nlm source add <theme-notebook>   evergreen source accrual
    └─► action, if any:
            repo named AND change named ──► gh issue          [G]
            otherwise                    ──► inbox.org        [G]
    │
    ▼
weekly: nlm create-audio per theme        audio ready in advance
```

The router is deterministic keyword matching, not model inference — same
rationale as `strategic-prioritizer`. It runs unattended nightly, and a router
whose answer moves between runs cannot be debugged from its output. When an
item lands in the wrong theme you can see which token did it.

## Themes — evergreen, not dated **[G]**

Themes cross ventures on purpose; that is where the insight is. Notebooks are
long-lived and accumulate sources, so context compounds. The 2026-08-30 run
already demonstrated this: extending existing notebooks to 13 sources produced
better-grounded overviews than fresh ones.

Today there are **duplicate dated notebooks for the same theme** — the drift
that evergreen is meant to end:

| Theme | Keep | Merge in | Queue items |
|---|---|---|---|
| Agent auditability & provenance | `13c0e557` (6 src) | `57c04f66` (13 src) | 6 |
| Agent memory & competitive landscape | `4edfd074` (4 src) | `62090361` (5 src) | 10 |
| Context engineering for long-horizon agents | `52458c09` (6 src) | `3c9d1c28` (13 src) | 9 |
| Data sovereignty & regulation | — create | — | 8 |
| Agent payments & monetization rails | — create | — | 3 |

`55e14b98` (Agent verification & reward hacking, 9 src) folds into provenance.

Merging means adding the older notebook's sources to the keeper, then deleting
the duplicate. Deletion is destructive and needs explicit approval per notebook.

## Team model — and its real cost **[G]**

Verified 2026-09-02, both paths dead:

```
nlm audio download → prints the notebook URL, exits 3, writes nothing
nlm audio share    → ShareAudio: One or more arguments are invalid (exit 2)
```

So there is **no programmatic way to hand a teammate a podcast**. The decision
is to stay with NotebookLM and share notebooks by hand through the Workspace
UI. What that commits to, stated plainly so it is chosen and not discovered:

- every share is a manual step, per notebook, by Gregor
- teammates need Google accounts on the Workspace
- the pipeline can create and fill notebooks unattended, but the last hop to a
  human is always manual
- five evergreen notebooks means five one-time shares, not one per week — this
  is the main reason evergreen beats dated here

If `audio share` is ever fixed upstream, the manual hop disappears with no
other change to this design. A `nlm audio share` probe belongs in the weekly
run so the fix is noticed rather than waited for.

## Routing gate **[G]**

```
repo can be named  AND  change can be named   → gh issue in that repo
anything else                                 → inbox.org
```

Deliberately conservative. A wrong inbox item costs ten seconds; a wrong issue
is visible to the team. Ambiguity resolves to inbox.

Venture attribution (`plur`, `datafund`, `fds`, `datacore`, `meridian`) is
recorded separately from destination — an item can be attributed to PLUR and
still be reading rather than work.

## Draining the 203

The queue cannot be emptied by routing alone, because routing needs content.
Order of operations:

1. **Discard lane first.** A queue that cannot be emptied is not a queue. The
   router already flags obvious non-research (`google search`, a testosterone
   supplement). ~3 confirmed, likely more once titles are read.
2. **Batch-fetch the 193 URLs** through the existing url-fetcher →
   knowledge-extractor path. This is the expensive step and the only one that
   makes the rest possible.
3. **Route the extracted notes**, not the captures.
4. **Backfill notebooks** with the resulting sources, per theme.
5. **Generate audio once per theme**, then weekly thereafter.

Do not attempt 2 in one run. The 2026-09-01 and 09-02 runs failed 7 of 10 and
5 of 10 items respectively; a 193-item batch would fail large and tell you
nothing about why.

## Open

- Notebook merges need per-notebook approval before any delete.
- Weekly audio regeneration cadence is not yet scheduled — and per the
  `registry_gc` precedent, a script that exists but is not in a crontab is
  indistinguishable from one that was never written. Schedule it or do not
  build it.
