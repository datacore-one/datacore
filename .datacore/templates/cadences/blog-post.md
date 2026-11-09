---
cadence: blog-post
# DIP-0050: judged by today's publication record -- the live plur.ai URL and its
# dev.to mirror, committed during the run. A post that did not go live fails.
evidence:
  path: "1-tracks/comms/blog/blog-post-{date}.md"
  require: ["^# Blog post", "https://plur\\.ai/blog/[a-z0-9-]+/?", "https://dev\\.to/[A-Za-z0-9_-]+/[a-z0-9-]+"]
  min_bytes: 400
role: cmo
frequency: daily
duration: 45min
tools: [Read, Write, exec, web_fetch]
---

## Objective

One post a day on plur.ai/blog (canonical), mirrored to dev.to with the canonical URL
pointing home. Evergreen posts publish by default after the fact-check; a
comparison-class post (competitor claims, pricing, benchmarks) is never published by
this cadence -- it is listed for the owner's approval in the run record, and today's post
is an evergreen one instead. A comparison draft is never a reason to stop.

## Steps

1. **Sync:** `git pull` in this space and in `2-projects/website`.
2. **Choose:** the best-ranked ready draft in `1-tracks/geo/drafts/` that is not yet in
   `2-projects/website/blog-app/src/content/blog/`; else the top gap in the newest
   `1-tracks/geo/results/summary-*.md` backlog, written fresh. Skip comparison-class topics:
   note each skipped draft for step 7's `## Waiting for approval` section, and choose the next
   evergreen one. Needing no approval tool, this step cannot block.
3. **Fact-check before publishing:** every product claim against the PLUR repo or docs,
   every external claim against a source you fetched today. Remove what you cannot verify.
4. **Stage:** write `blog-app/src/content/blog/<slug>.md` with the collection's front matter
   (title, description, pubDate from `python3 ~/Data/.datacore/lib/date_utils.py today`,
   slug, `author: "Data"`, geoTargets, `draft: false`). Disclose agent authorship in the post.
5. **Publish:** commit and push the website repo; GitHub Actions deploys it. Wait until
   `https://plur.ai/blog/<slug>/` returns 200 (up to 10 minutes).
6. **Mirror:** `python3 1-tracks/geo/publish_devto.py <slug>`; it prints the dev.to URL.
7. **Record:** write `1-tracks/comms/blog/blog-post-YYYY-MM-DD.md`, headed
   `# Blog post — YYYY-MM-DD`, with the title, both URLs, what the fact-check removed, and a
   `## Waiting for approval` section naming the comparison drafts skipped (or "none").
   Commit it with `1-tracks/geo/publish-log.jsonl`; `git push`.
8. **If a step fails:** stop, write no record, and say which step and why. Do not log the
   run anywhere else: the runner records it.
