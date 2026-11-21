---
cadence: coverage-graph
role: cio
frequency: weekly
duration: 90min
tools: [Read, Write, Bash, WebSearch, WebFetch]
# DIP-0050: judged by the week's coverage-graph summary written during the run.
evidence:
  path: "1-tracks/comms/coverage-graph/*/summary.md"
  require: ["^# ", "(?i)origin"]
  min_bytes: 1500
---

## Objective

Each week, show which sources start the stories on AI agent memory, self-learning (continual-learning) agents
and recursive self-improvement, and which only amplify them. New origin hubs are pitch targets; amplifiers are
placement targets. Owner decision, 21 Nov 2026: weekly on Sunday, so the result is in Monday's briefing.

## Steps

1. **Sync:** `git -C <plur-space> pull`.
2. **Collect (agent step):** for each topic in `1-tracks/comms/coverage-graph/topics.json`, run its search
   queries with WebSearch (12–20 results each) and keep results dated within the last 60 days. Write
   `1-tracks/comms/coverage-graph/<YYYY-MM-DD>/collect/web-<topic>.jsonl`, one object per line:
   `{"url", "title", "published", "author", "topic", "source", "query"}`. Also run
   `python3 ~/Data/.datacore/modules/comms/lib/coverage_graph.py collect-hn --run <run dir> --topic <t> --query "<q>"`
   for each topic's Hacker News query.
3. **Build:** `python3 ~/Data/.datacore/modules/comms/lib/coverage_graph.py run --run <run dir>`
   (fetch, build, expand, rank, report). The raw page cache stays out of git (`cache/` is ignored).
4. **Compare:** read last week's `summary.md` and note new origin hubs, outlets that moved from amplifier to
   origin, and stories that started this week. Add a `## Week on week` section to this week's summary.md.
5. **Push:** commit the run folder (not `cache/`); `git push`. The runner records the run.

## DELIVERY FORMAT (IMPORTANT)
Your final response goes to Gregor on Telegram (and is read into Monday's briefing):

- Title: "Who starts the stories — week of {date}"
- 3 bullets: the top origin this week, any new hub worth pitching, one story PLUR could have joined
- One line: the week-on-week change

No file paths, scores or method notes. Under 120 words.
