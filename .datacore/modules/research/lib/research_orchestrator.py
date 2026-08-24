#!/usr/bin/env python3
"""
Canonical home: research module (moved from nightshift 2026-07-14 — nightshift keeps a shim).

Research Orchestrator — Python-driven research processing pipeline.

Processes TODO items from research_learning.org:
1. Python parses org file, picks top N items
2. Python fetches each URL
3. Claude analyzes content and creates literature notes + zettels
4. Python updates org file (marks DONE)
5. Python commits and pushes

Usage:
    python3 research_orchestrator.py [--limit N] [--dry-run] [--no-podcast]
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import claude_agent_sdk
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, RateLimitEvent, SystemMessage


DATA_DIR = Path(os.environ.get('DATA_DIR', Path.home() / 'Data'))
PERSONAL = DATA_DIR / '0-personal'
DATAFUND = DATA_DIR / '1-datafund'


def _module_settings() -> dict:
    """Load settings defaults from this module's module.yaml.

    Flattens {key: {default: X}} to {key: X}. Fail-safe: any error returns {}
    so the hardcoded fallbacks below keep the pipeline alive.
    """
    try:
        import yaml
        my = DATA_DIR / '.datacore' / 'modules' / 'research' / 'module.yaml'
        cfg = yaml.safe_load(my.read_text()) or {}
        return {k: (v.get('default') if isinstance(v, dict) else v)
                for k, v in (cfg.get('settings') or {}).items()}
    except Exception:
        return {}


_SETTINGS = _module_settings()


def _setting_path(key: str, fallback: Path) -> Path:
    val = _SETTINGS.get(key)
    return DATA_DIR / val if val else fallback


RESEARCH_ORG = _setting_path('research_org_file', PERSONAL / 'org' / 'research_learning.org')
DAILY_NEWS_ORG = PERSONAL / 'org' / 'daily_news.org'
LITERATURE_DIR = _setting_path('literature_output_dir', PERSONAL / 'notes' / '2-knowledge' / 'literature')
ZETTEL_DIR = _setting_path('zettel_output_dir', PERSONAL / 'notes' / '2-knowledge' / 'zettel')
COMPANIES_DIR = PERSONAL / '3-knowledge' / 'reference' / 'companies'
PEOPLE_DIR_DF = DATAFUND / '3-knowledge' / 'reference' / 'people'
PEOPLE_DIR_PERSONAL = PERSONAL / '3-knowledge' / 'reference' / 'people'
LANDSCAPE_FILE = _setting_path('industry_landscape_file', DATAFUND / '1-tracks' / 'research' / 'Industry landscape.md')
REPORTS_DIR = _setting_path('reports_output_dir', PERSONAL / 'content' / 'reports')
JOURNAL_DIR = PERSONAL / 'notes' / 'journals'
PODCAST_DIR = _setting_path('podcast_output_dir', PERSONAL / 'content' / 'podcasts')
TODAY = date.today().isoformat()


def log(msg: str):
    print(f"[research] {msg}")


# ---- Parse research queue ----

def parse_research_items(limit: int = 10) -> List[Dict[str, str]]:
    """Parse TODO items from research_learning.org."""
    if not RESEARCH_ORG.exists():
        log("research_learning.org not found")
        return []

    content = RESEARCH_ORG.read_text(encoding='utf-8')
    lines = content.split('\n')
    items = []

    i = 0
    # Match any heading level >= 2 (** TODO or *** TODO or **** TODO)
    # Strip optional priority cookie [#A]/[#B]/[#C] and trailing :tag1:tag2:.
    HEADING_RE = re.compile(r'^(\*{2,})\s+TODO\s+(?:\[#([ABC])\]\s+)?(.+?)(?:\s+:[\w:]+:)?\s*$')
    # Extract URL from org-mode title-link: [[https://url][title]] or [[https://url]]
    TITLE_LINK_RE = re.compile(r'\[\[(https?://[^\]]+?)(?:\]\[[^\]]*\])?\]')
    # Collect ALL url-bearing TODOs, then sort and cut — limiting during
    # collection made "priority A first" meaningless (file order won, so
    # [#A] items appended late in the file waited weeks behind [#B] reads).
    while i < len(lines):
        line = lines[i]
        match = HEADING_RE.match(line)
        if match:
            level = len(match.group(1))
            priority = match.group(2) or 'C'
            title = match.group(3).strip()
            tags = ''
            tag_match = re.search(r'(:\w[\w:]*:)\s*$', lines[i])
            if tag_match:
                tags = tag_match.group(1)

            # First: try to extract URL directly from the title-link syntax
            url = ''
            title_link = TITLE_LINK_RE.search(title)
            if title_link:
                url = title_link.group(1)

            purpose = ''
            effort = ''
            line_number = i + 1  # 1-indexed

            # Look ahead for Link: line and properties (incl. :SOURCE: / :EXTERNAL_URL:)
            j = i + 1
            while j < len(lines) and j < i + 30:
                l = lines[j].strip()
                if l.startswith('Link:'):
                    raw_url = l.split('Link:', 1)[1].strip()
                    link_match = re.match(r'\[\[([^\]]+?)(?:\]\[[^\]]*\])?\]', raw_url)
                    candidate = link_match.group(1) if link_match else raw_url
                    if candidate.startswith('http') and not url:
                        url = candidate
                elif l.startswith(':SOURCE:') and not url:
                    candidate = l.split(':SOURCE:', 1)[1].strip()
                    if candidate.startswith('http'):
                        url = candidate
                elif l.startswith(':EXTERNAL_URL:') and not url:
                    candidate = l.split(':EXTERNAL_URL:', 1)[1].strip()
                    # Org may wrap as [[url][label]]
                    em = re.match(r'\[\[([^\]]+?)(?:\]\[[^\]]*\])?\]', candidate)
                    candidate = em.group(1) if em else candidate
                    if candidate.startswith('http'):
                        url = candidate
                elif l.startswith('Purpose:'):
                    purpose = l.split('Purpose:', 1)[1].strip()
                elif ':EFFORT:' in l:
                    effort = l.split(':EFFORT:', 1)[1].strip()
                elif l.startswith('*'):
                    break
                j += 1

            # URL-less TODOs (e.g. Daily News reading digests) are unprocessable
            # here and must not consume limit slots — with limit=5 they
            # head-of-line blocked the whole queue (Processed: 0 since ~May).
            if url:
                items.append({
                    'title': title,
                    'priority': priority,
                    'url': url,
                    'purpose': purpose,
                    'effort': effort,
                    'tags': tags,
                    'line_number': line_number,
                    'heading_line': line,
                })

        i += 1

    # Sort by priority (A first), then cap at limit
    items.sort(key=lambda x: x['priority'])
    return items[:limit]


# ---- Fetch URL content ----

PAYWALL_DOMAINS = ('wsj.com', 'reuters.com', 'bloomberg.com', 'ft.com', 'nytimes.com',
                   'theverge.com', 'archive.ph', 'archive.org', 'wired.com',
                   'theinformation.com', 'economist.com')

# Map domain → env var name holding cookie string (full Cookie: header value).
# Populate the env vars from a browser session for paywalled sources you subscribe to.
# Example: WSJ_COOKIES="wsjregion=NA,US; ...; sso_user_id=..."
DOMAIN_COOKIE_ENV = {
    'wsj.com': 'WSJ_COOKIES',
    'nytimes.com': 'NYTIMES_COOKIES',
    'ft.com': 'FT_COOKIES',
    'bloomberg.com': 'BLOOMBERG_COOKIES',
    'economist.com': 'ECONOMIST_COOKIES',
    'theinformation.com': 'THEINFORMATION_COOKIES',
}


def _cookies_for(url: str) -> Optional[str]:
    """Return Cookie header value if env var is set for the URL's domain."""
    for domain, env_var in DOMAIN_COOKIE_ENV.items():
        if domain in url:
            val = os.environ.get(env_var, '').strip()
            if val:
                return val
    return None


def _fetch_jina(url: str) -> Optional[str]:
    """Try Jina Reader (clean markdown extraction)."""
    jina_key = os.environ.get('JINA_API_KEY', '')
    if not jina_key:
        return None
    try:
        jina_url = f"https://r.jina.ai/{url}"
        req = urllib.request.Request(jina_url, headers={
            'Authorization': f'Bearer {jina_key}',
            'Accept': 'text/markdown',
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode('utf-8', errors='replace')
            if len(content) > 200:
                return content[:15000]
    except Exception as e:
        log(f"  Jina failed for {url}: {e}")
    return None


def _fetch_direct(url: str, with_cookies: bool = False) -> Optional[str]:
    """Direct HTTP fetch with HTML-strip. Optionally include subscription cookies."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
        }
        if with_cookies:
            cookies = _cookies_for(url)
            if cookies:
                headers['Cookie'] = cookies
                log(f"  Using subscription cookies for {url[:60]}")
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            content = resp.read().decode('utf-8', errors='replace')
            content = re.sub(r'<[^>]+>', ' ', content)
            content = re.sub(r'\s+', ' ', content)
            if len(content) > 500:  # paywall stubs are typically <500 chars
                return content[:15000]
            log(f"  Direct fetch returned only {len(content)} chars — likely paywall stub")
    except Exception as e:
        log(f"  Direct fetch failed for {url}: {e}")
    return None


def _fetch_wayback(url: str) -> Optional[str]:
    """Wayback Machine fallback for paywalled / blocked URLs."""
    try:
        avail_url = f"https://archive.org/wayback/available?url={urllib.parse.quote(url)}"
        with urllib.request.urlopen(avail_url, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        snap = data.get('archived_snapshots', {}).get('closest', {})
        if not snap.get('available') or not snap.get('url'):
            log(f"  No Wayback snapshot for {url}")
            return None
        wb_url = snap['url']
        log(f"  Wayback snapshot: {wb_url}")
        # Prefer Jina on the Wayback URL (best chance of clean text)
        content = _fetch_jina(wb_url) or _fetch_direct(wb_url)
        if content and len(content) > 200:
            return content
    except Exception as e:
        log(f"  Wayback fallback failed for {url}: {e}")
    return None


def fetch_url(url: str) -> Optional[str]:
    """Fetch and extract text content from a URL.

    Fallback chain:
      1. If paywall domain AND a subscription cookie env var is set →
         direct fetch with Cookie header (freshest content)
      2. Jina Reader (works for most public URLs)
      3. Direct fetch without cookies
      4. Wayback Machine snapshot (last resort)
    """
    if not url:
        return None

    is_paywalled = any(d in url for d in PAYWALL_DOMAINS)
    has_subscription = is_paywalled and _cookies_for(url) is not None

    # 1. Subscription-authenticated fetch for paywalled domains we have cookies for
    if has_subscription:
        content = _fetch_direct(url, with_cookies=True)
        if content:
            return content
        log(f"  Subscription fetch returned no content — falling back")

    # 2. Jina Reader (skip for paywalled if we already tried cookies and failed)
    if not is_paywalled:
        content = _fetch_jina(url)
        if content:
            return content
        content = _fetch_direct(url)
        if content:
            return content

    # 3. Wayback as final fallback
    log(f"  Trying Wayback Machine for {url[:60]}")
    return _fetch_wayback(url)


# ---- Process single item with Claude ----

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / 'lib'))
from ops_markers import AUTH_FAILURE_MARKERS  # noqa: E402


_MAX_BUDGET_USD = 0.50


async def _claude_json_async(prompt: str, label: str) -> Optional[Dict[str, Any]]:
    """Async implementation: call claude_agent_sdk.query() and collect the reply.

    RUNS IN AN EMPTY DIRECTORY, DELIBERATELY. These calls used to run with
    cwd=DATA_DIR, which makes the subprocess load ~/Data/CLAUDE.md — and that
    file instructs the agent to call plur_session_start before anything else.
    PLUR is not connected in a headless subprocess, so instead of returning
    JSON the model spent its turn explaining that the MCP server was missing.
    Every item then failed to parse and was skipped, which is why a run could
    report "Processed: 0, Failed: N" while `claude` itself was perfectly
    healthy. These prompts are self-contained text-in/JSON-out; they need no
    workspace, so they get none.

    Reports what actually came back on a parse failure. The bare
    "Expecting value: line 1 column 1 (char 0)" that this replaces was true
    and useless — it described the symptom and hid the response that would
    have identified the cause in one read.
    """
    workdir = tempfile.mkdtemp()
    options = ClaudeAgentOptions(
        permission_mode='bypassPermissions',
        cwd=workdir,
        max_budget_usd=_MAX_BUDGET_USD,
    )

    output = ''
    async for message in claude_agent_sdk.query(prompt=prompt, options=options):
        if isinstance(message, (SystemMessage, RateLimitEvent)):
            continue
        if isinstance(message, ResultMessage):
            if message.is_error:
                log(f"  {label}: SDK returned is_error=True; result={message.result!r:.200}")
                return None
            cost = message.total_cost_usd or 0.0
            if cost > _MAX_BUDGET_USD:
                log(f"  {label}: cost ${cost:.4f} exceeded budget ${_MAX_BUDGET_USD:.2f} — aborting")
                return None
            output = (message.result or '').strip()

    # AUTH failures surface as plain text in the result with exit 0; check them
    # the same way the old subprocess path did.
    low = output.lower()
    for marker in AUTH_FAILURE_MARKERS:
        if marker in low:
            log(f"  {label}: AUTH FAILURE from SDK (is_error=False): {output[:200]!r}")
            return None

    output = re.sub(r'^```json\s*', '', output)
    output = re.sub(r'\s*```\s*$', '', output)
    if not output:
        log(f"  {label}: Claude returned NOTHING")
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError as first_error:
        pass

    # Repair invalid escape sequences, then try once more.
    #
    # JSON permits only \" \\ \/ \b \f \n \r \t \uXXXX. Models routinely emit
    # \' when prose contains an apostrophe ("Ben\'s Bites"), which is valid in
    # Python and JavaScript source but not in JSON, and json.loads rejects the
    # whole document over one character. Dropping the stray backslash is safe:
    # the negative lookahead leaves every legal escape untouched, so this
    # cannot corrupt \n, \t or \uXXXX.
    repaired = re.sub(r'\\(?!["\\/bfnrtu])', '', output)
    if repaired != output:
        try:
            data = json.loads(repaired)
            log(f"  {label}: repaired invalid escape sequence(s) in the reply")
            return data
        except json.JSONDecodeError:
            pass

    log(f"  {label}: reply was not JSON ({first_error})")
    log(f"  {label}: got instead -> {output[:200]!r}")
    return None


def _claude_json(prompt: str, timeout: int, label: str) -> Optional[Dict[str, Any]]:
    """Synchronous wrapper around _claude_json_async().

    The ``timeout`` parameter is retained for API compatibility with existing
    callers.  The SDK does not expose a simple per-call wall-clock timeout, so
    we enforce it here via asyncio.wait_for().  The budget guard inside the
    async function provides an additional cost-based safety net.
    """
    try:
        return asyncio.run(
            asyncio.wait_for(_claude_json_async(prompt, label), timeout=timeout)
        )
    except asyncio.TimeoutError:
        log(f"  {label}: Claude timed out after {timeout}s")
        return None


def process_item(item: Dict[str, str], content: str) -> Optional[Dict[str, Any]]:
    """Send fetched content to Claude for analysis. Returns structured output."""
    title = item['title']
    url = item['url']
    purpose = item.get('purpose', '')

    prompt = f"""Analyze this article and create knowledge artifacts.

Title: {title}
URL: {url}
Purpose: {purpose}

Content:
{content[:12000]}

Create FOUR outputs:

1. A LITERATURE NOTE (markdown) with:
   - Title, URL, date accessed
   - 3-5 sentence summary
   - Key takeaways (bullet points)
   - Relevance to the purpose above
   - Tags

2. 1-3 ATOMIC ZETTELS — each a single concept extracted from the article.
   Each zettel should have a descriptive title and 2-4 sentences explaining the concept.

3. NAMED ENTITIES — companies and people mentioned in the article.
   Only include entities that are CENTRAL to the article (not just passing mentions).
   For companies, include the website if mentioned; for people, include role/affiliation.

4. A short 1-2 sentence JOURNAL SUMMARY.

Return as JSON:
{{
  "literature_note": {{
    "filename": "slug-of-title.md",
    "content": "full markdown content"
  }},
  "zettels": [
    {{
      "filename": "concept-name.md",
      "content": "full markdown content"
    }}
  ],
  "entities": {{
    "companies": [
      {{"name": "Company Name", "website": "https://...", "category": "ai|crypto|health|...", "one_liner": "what they do"}}
    ],
    "people": [
      {{"name": "Person Name", "role": "CEO of X", "organization": "X", "context": "why mentioned"}}
    ]
  }},
  "summary": "1-2 sentence summary for the journal"
}}

Output ONLY valid JSON, nothing else.
"""

    try:
        return _claude_json(prompt, timeout=90, label="analysis")
    except Exception as e:
        log(f"  Processing error: {e}")
        return None


# ---- Write outputs ----

def write_literature_note(data: Dict[str, str]) -> Optional[Path]:
    """Write literature note to knowledge base."""
    articles_dir = LITERATURE_DIR / 'articles'
    articles_dir.mkdir(parents=True, exist_ok=True)

    filename = data.get('filename', 'untitled.md')
    # Sanitize filename
    filename = re.sub(r'[^\w\s\-.]', '', filename)
    if not filename.endswith('.md'):
        filename += '.md'

    path = articles_dir / filename
    path.write_text(data.get('content', ''), encoding='utf-8')
    return path


def _slug(s: str) -> str:
    """Conservative slug for filenames."""
    s = re.sub(r"['\"]", '', s)
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'\s+', '-', s).strip('-')
    return s[:80]


def write_companies(companies: List[Dict[str, str]], source_url: str) -> List[Path]:
    """Write CRM stubs for new companies; append note for existing ones.

    Skips entities already tracked in either:
      - 0-personal/3-knowledge/reference/companies/
      - 1-datafund/3-knowledge/reference/companies/
    """
    if not companies:
        return []

    df_companies = DATAFUND / '3-knowledge' / 'reference' / 'companies'
    existing = set()
    for d in (COMPANIES_DIR, df_companies):
        if d.exists():
            existing.update(p.stem.lower() for p in d.glob('*.md'))

    created = []
    for c in companies:
        name = (c.get('name') or '').strip()
        if not name:
            continue
        slug = _slug(name)
        if not slug or slug.lower() in existing:
            continue
        # Datafund-relevant verticals go to 1-datafund; everything else 0-personal
        cat = (c.get('category') or '').lower()
        target_dir = df_companies if cat in ('rwa', 'crypto', 'web3', 'fintech', 'data',
                                              'health', 'identity', 'tokenization') else COMPANIES_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{slug}.md"
        content = f"""---
type: contact
entity_type: company
name: "{name}"
status: draft
relationship_status: discovered
relevance: 2
industries: [{cat or 'unknown'}]
website: {c.get('website', '')}
discovered_in: "Daily research {TODAY}"
source: {source_url}
created: {TODAY}
updated: {TODAY}
---

# {name}

## Overview

{c.get('one_liner', 'Discovered via daily research; needs review.')}

## Notes

Auto-captured from research pipeline {TODAY}. Source: {source_url}
"""
        path.write_text(content, encoding='utf-8')
        created.append(path)
        existing.add(slug.lower())
    return created


def write_people(people: List[Dict[str, str]], source_url: str) -> List[Path]:
    """Write CRM stubs for new people. Skips existing."""
    if not people:
        return []

    existing = set()
    for d in (PEOPLE_DIR_DF, PEOPLE_DIR_PERSONAL):
        if d.exists():
            existing.update(p.stem.lower() for p in d.glob('*.md'))

    created = []
    for p in people:
        name = (p.get('name') or '').strip()
        if not name:
            continue
        slug = _slug(name)
        if not slug or slug.lower() in existing:
            continue
        target_dir = PEOPLE_DIR_DF if PEOPLE_DIR_DF.exists() else PEOPLE_DIR_PERSONAL
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{slug}.md"
        content = f"""---
type: contact
entity_type: person
name: "{name}"
status: draft
relationship_status: discovered
role: "{p.get('role', '')}"
organization: "{p.get('organization', '')}"
discovered_in: "Daily research {TODAY}"
source: {source_url}
created: {TODAY}
updated: {TODAY}
---

# {name}

## Overview

{p.get('context', 'Discovered via daily research; needs review.')}

**Role**: {p.get('role', 'unknown')}
**Organization**: {p.get('organization', 'unknown')}

## Notes

Auto-captured from research pipeline {TODAY}. Source: {source_url}
"""
        path.write_text(content, encoding='utf-8')
        created.append(path)
        existing.add(slug.lower())
    return created


def append_landscape_rows(companies: List[Dict[str, str]]) -> List[str]:
    """Append landscape rows for companies that aren't already in the table.

    Looks for `[[Name]]` markers in the existing landscape file.
    """
    if not companies or not LANDSCAPE_FILE.exists():
        return []

    text = LANDSCAPE_FILE.read_text(encoding='utf-8')
    appended_names = []
    new_rows = []
    for c in companies:
        name = (c.get('name') or '').strip()
        if not name:
            continue
        # Already in landscape?
        marker = f"[[{name}]]"
        if marker.lower() in text.lower() or f"|{name}|" in text:
            continue
        cat = c.get('category', 'unknown')
        url = c.get('website', '')
        url_md = f"[{url}]({url})" if url else ''
        comment = c.get('one_liner', '').replace('|', ' / ').strip()
        if comment and not comment.endswith('.'):
            comment += '.'
        comment += f" Captured {TODAY}."
        row = f"|[[{name}]]|Watching|{url_md}|Tracking|{cat.capitalize()}|{comment}|||"
        new_rows.append(row)
        appended_names.append(name)

    if not new_rows:
        return []

    header = f"\n## {TODAY} — Auto-captured peers from research pipeline\n\n"
    text = text.rstrip() + '\n' + header + '\n'.join(new_rows) + '\n'
    LANDSCAPE_FILE.write_text(text, encoding='utf-8')
    return appended_names


def write_zettels(zettels: List[Dict[str, str]]) -> List[Path]:
    """Write zettel notes."""
    ZETTEL_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for z in zettels:
        filename = z.get('filename', 'untitled.md')
        filename = re.sub(r'[^\w\s\-.]', '', filename)
        if not filename.endswith('.md'):
            filename += '.md'
        path = ZETTEL_DIR / filename
        path.write_text(z.get('content', ''), encoding='utf-8')
        paths.append(path)
    return paths


def mark_done(item: Dict[str, str], output_path: str, zettel_names: List[str]):
    """Mark a research item as DONE in the org file."""
    content = RESEARCH_ORG.read_text(encoding='utf-8')
    old_heading = item['heading_line']
    new_heading = old_heading.replace(' TODO ', ' DONE ')

    # Add CLOSED timestamp and properties
    closed_line = f"    CLOSED: [{date.today().strftime('%Y-%m-%d %a')}]"
    output_prop = f":OUTPUT: [[{output_path}]]" if output_path else ""
    zettel_prop = f":ZETTELS: {', '.join(f'[[{z}]]' for z in zettel_names)}" if zettel_names else ""

    # Replace heading
    content = content.replace(old_heading, new_heading, 1)

    # Insert CLOSED after the heading
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if line == new_heading:
            # Insert closed timestamp after heading
            insert_at = i + 1
            # Skip past existing CLOSED line if any
            if insert_at < len(lines) and 'CLOSED:' in lines[insert_at]:
                lines[insert_at] = closed_line
            else:
                lines.insert(insert_at, closed_line)

            # Find :END: in properties to add output/zettel props
            for j in range(insert_at, min(insert_at + 15, len(lines))):
                if ':END:' in lines[j]:
                    insert_props = []
                    if output_prop:
                        insert_props.append(f"    {output_prop}")
                    if zettel_prop:
                        insert_props.append(f"    {zettel_prop}")
                    for k, prop in enumerate(insert_props):
                        lines.insert(j + k, prop)
                    break
            break

    RESEARCH_ORG.write_text('\n'.join(lines), encoding='utf-8')


# ---- Main Pipeline ----

def auto_archive_stale_research(max_age_days: int = 60) -> int:
    """Move research_learning.org items older than max_age_days to a dated
    archive file. Prevents the queue from accumulating stale items that the
    main pipeline never gets to.

    Returns the count of items archived.
    """
    import re
    import sys
    from datetime import date
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / '2-datacore' / '2-projects' / 'org-workspace' / 'src'))
    try:
        from org_workspace import OrgWorkspace
        import org_workspace.workspace as wsmod
    except ImportError:
        log("org_workspace not importable; skipping auto-archive")
        return 0

    if not RESEARCH_ORG.exists():
        return 0

    today_d = date.today()
    archive_dir = RESEARCH_ORG.parent / '.archive'
    archive_dir.mkdir(exist_ok=True)
    archive_path = archive_dir / f'research_learning-auto-archived-{today_d.isoformat()}.org'

    # Bulk archive may shrink the source heavily — temporarily raise guard.
    original_threshold = wsmod.OrgWorkspace._MAX_SHRINK_FRACTION
    wsmod.OrgWorkspace._MAX_SHRINK_FRACTION = 0.85

    try:
        ws = OrgWorkspace()
        ws.load(RESEARCH_ORG)

        # Create archive file if first time today
        if not archive_path.exists():
            archive_path.write_text(
                f"#+TITLE: Research auto-archive {today_d.isoformat()}\n"
                f"#+CATEGORY: ResearchArchive\n"
                f"#+FILETAGS: :archive:research:auto:\n"
                f"#+STARTUP: overview\n\n"
                f"* Auto-archived stale research items (>{max_age_days}d)\n"
                f"  :PROPERTIES:\n"
                f"  :ID: org-research-auto-archive-{today_d.isoformat()}\n"
                f"  :END:\n"
            )
        ws.load(archive_path)

        opens = [n for n in ws.all_nodes()
                 if 'research_learning.org' in str(n.path)
                 and '.archive' not in str(n.path)
                 and n.todo and n.todo not in ('DONE', 'CANCELLED', 'CLOSED', 'FAILED')]

        stale_ids = []
        for n in opens:
            raw = n.get_property('CREATED') or n.get_property('RECEIVED') or ''
            m = re.search(r'(\d{4}-\d{2}-\d{2})', raw)
            if not m:
                # Undated items >max_age_days assumed stale (no provenance)
                # but only if the FILE itself is older than max_age_days — we
                # don't want to archive items added yesterday that lack a date.
                # Heuristic: undated items get a grace period of max_age_days
                # before they're considered stale. Since we have no created
                # date for them, we don't archive on this pass — they stay
                # until they accrue a date or get manually triaged.
                continue
            d = date(*[int(x) for x in m.group().split('-')])
            if (today_d - d).days > max_age_days:
                stale_ids.append(n.id())

        if not stale_ids:
            return 0

        log(f"Auto-archiving {len(stale_ids)} research items older than {max_age_days}d...")
        archive_target = archive_path.resolve()
        moved = 0
        for tid in stale_ids:
            node = ws.find_by_id(tid)
            if not node:
                continue
            try:
                ws.set_property(node, 'AUTO_ARCHIVED', today_d.isoformat())
                node = ws.find_by_id(tid)
                ws.refile(node, archive_target)
                moved += 1
            except Exception as e:
                log(f"  archive failed for {tid}: {e}")
        ws.save_all()
        return moved
    finally:
        wsmod.OrgWorkspace._MAX_SHRINK_FRACTION = original_threshold


# ---- Daily news section processor ----

def extract_today_daily_news_section() -> Optional[str]:
    """Extract today's section from daily_news.org as plain text + URLs.

    Returns the section body or None if no section for today exists.
    """
    if not DAILY_NEWS_ORG.exists():
        return None
    text = DAILY_NEWS_ORG.read_text(encoding='utf-8')
    lines = text.split('\n')
    today_re = re.compile(rf'^\*\*\s+{re.escape(TODAY)}\b')
    next_section_re = re.compile(r'^\*\*\s+\d{4}-\d{2}-\d{2}\b')
    start = None
    for i, line in enumerate(lines):
        if today_re.match(line):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if next_section_re.match(lines[j]):
            end = j
            break
    return '\n'.join(lines[start:end])


def process_daily_news() -> Optional[Path]:
    """Build today's daily-news brief from daily_news.org and write to reports.

    Calls Claude once with the section content; returns the brief path.
    """
    section = extract_today_daily_news_section()
    if not section:
        log("No daily_news.org section for today")
        return None

    prompt = f"""Build a daily-news brief for {TODAY} from this org-mode section.

For each item below, produce a 2-3 sentence summary capturing what happened and
why it matters. Group items into themes (AI Products, Markets & Funding, Policy,
Crypto/Macro, Other). Keep brief — max 1500 words total.

Also extract:
- Up to 8 CENTRAL companies mentioned (with website + 1-liner + category)
- Up to 6 CENTRAL people mentioned (with role + organization)
- 3-5 action items the reader should consider

Section content:
{section[:14000]}

Return as JSON:
{{
  "brief_markdown": "full markdown brief grouped by theme",
  "entities": {{
    "companies": [
      {{"name": "...", "website": "...", "category": "...", "one_liner": "..."}}
    ],
    "people": [
      {{"name": "...", "role": "...", "organization": "...", "context": "..."}}
    ]
  }},
  "action_items": [
    {{"title": "...", "context": "..."}}
  ]
}}

Output ONLY valid JSON, nothing else.
"""

    try:
        data = _claude_json(prompt, timeout=180, label="daily-news")
        if data is None:
            return None
    except Exception as e:
        log(f"  Daily-news Claude parse failed: {e}")
        return None

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    brief_path = REPORTS_DIR / f"{TODAY}-daily-news-brief.md"
    brief_md = data.get('brief_markdown') or ''
    if not brief_md:
        log("  Empty brief — skipping write")
        return None

    front = f"""---
type: brief
date: {TODAY}
source: daily_news.org
created: {TODAY}
---

# Daily News Brief {TODAY}

"""
    brief_path.write_text(front + brief_md.rstrip() + '\n', encoding='utf-8')
    log(f"  Daily news brief: {brief_path}")

    # Extract entities → CRM + landscape
    entities = data.get('entities') or {}
    companies = entities.get('companies') or []
    people = entities.get('people') or []
    company_paths = write_companies(companies, str(DAILY_NEWS_ORG))
    person_paths = write_people(people, str(DAILY_NEWS_ORG))
    landscape_added = append_landscape_rows(companies)
    log(f"  Daily-news entities: {len(company_paths)} companies, {len(person_paths)} people, {len(landscape_added)} landscape")

    # Append action items to inbox.org
    inbox = PERSONAL / 'org' / 'inbox.org'
    actions = data.get('action_items') or []
    if actions and inbox.exists():
        try:
            text = inbox.read_text(encoding='utf-8')
            blocks = []
            for a in actions[:5]:
                title = (a.get('title') or '').replace('\n', ' ').strip()
                ctx = (a.get('context') or '').replace('\n', ' ').strip()
                if not title:
                    continue
                blocks.append(f"** TODO {title} :daily-news:research:\n:PROPERTIES:\n:CREATED: [{TODAY}]\n:SOURCE: Daily news brief {TODAY}\n:RESEARCH_URL: {brief_path.relative_to(DATA_DIR)}\n:END:\n{ctx}\n")
            inbox.write_text(text.rstrip() + '\n\n' + '\n'.join(blocks), encoding='utf-8')
            log(f"  Daily-news action items added: {len(blocks)}")
        except Exception as e:
            log(f"  Inbox append failed: {e}")

    return brief_path


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Research Orchestrator')
    parser.add_argument('--limit', '-l', type=int, default=5, help='Max items to process')
    parser.add_argument('--dry-run', action='store_true', help='Parse queue but skip processing')
    parser.add_argument('--no-podcast', action='store_true', help='Skip podcast generation')
    parser.add_argument('--no-daily-news', action='store_true', help='Skip daily news brief')
    parser.add_argument('--auto-archive-days', type=int, default=60,
                        help='Auto-archive items older than this many days (0 to disable)')
    args = parser.parse_args()

    log(f"Starting research processing for {TODAY}")

    # Step 0: Auto-archive stale items so the queue can't accumulate indefinitely.
    # Without this, the queue grows whenever ingest > processing capacity and
    # never drains — exactly how 365 items accumulated by 2026-05-20.
    if args.auto_archive_days > 0 and not args.dry_run:
        archived = auto_archive_stale_research(max_age_days=args.auto_archive_days)
        if archived:
            log(f"  Auto-archived {archived} stale items (>{args.auto_archive_days}d)")

    # Step 1: Parse research queue
    items = parse_research_items(limit=args.limit)
    if not items:
        log("No TODO items found in research queue. Nothing to do.")
        return

    # Filter to items with URLs (can't process without a link)
    items_with_urls = [i for i in items if i.get('url')]
    items_without = [i for i in items if not i.get('url')]

    log(f"Found {len(items)} TODO items ({len(items_with_urls)} with URLs, {len(items_without)} without)")

    if args.dry_run:
        for i, item in enumerate(items, 1):
            log(f"  {i}. [{item['priority']}] {item['title']}")
            if item['url']:
                log(f"     URL: {item['url'][:80]}")
        return

    # Step 2: Process each item
    processed = []
    failed = []

    for i, item in enumerate(items_with_urls, 1):
        log(f"\n[{i}/{len(items_with_urls)}] {item['title']}")

        # Fetch content
        log(f"  Fetching: {item['url'][:60]}...")
        content = fetch_url(item['url'])
        if not content:
            log(f"  SKIP: Could not fetch URL")
            failed.append(item)
            continue

        log(f"  Fetched {len(content)} chars")

        # Process with Claude
        log(f"  Analyzing with Claude...")
        result = process_item(item, content)
        if not result:
            log(f"  SKIP: Claude analysis failed")
            failed.append(item)
            continue

        # Write outputs
        lit_path = None
        if result.get('literature_note'):
            lit_path = write_literature_note(result['literature_note'])
            if lit_path:
                log(f"  Literature note: {lit_path.name}")

        zettel_paths = []
        if result.get('zettels'):
            zettel_paths = write_zettels(result['zettels'])
            log(f"  Zettels: {len(zettel_paths)}")

        # Extract entities → CRM stubs + landscape rows
        entities = result.get('entities') or {}
        companies = entities.get('companies') or []
        people = entities.get('people') or []
        company_paths = write_companies(companies, item['url'])
        if company_paths:
            log(f"  Companies created: {len(company_paths)}")
        person_paths = write_people(people, item['url'])
        if person_paths:
            log(f"  People created: {len(person_paths)}")
        landscape_added = append_landscape_rows(companies)
        if landscape_added:
            log(f"  Landscape rows added: {len(landscape_added)} ({', '.join(landscape_added[:3])})")

        # Mark done in org file
        zettel_names = [z.stem for z in zettel_paths]
        output_rel = str(lit_path.relative_to(DATA_DIR)) if lit_path else ''
        mark_done(item, output_rel, zettel_names)

        processed.append({
            'title': item['title'],
            'summary': result.get('summary', ''),
            'literature_note': str(lit_path) if lit_path else None,
            'zettels': len(zettel_paths),
            'companies': len(company_paths),
            'people': len(person_paths),
            'landscape_added': landscape_added,
        })

        log(f"  Done!")

    # Step 3: Write journal entry
    log(f"\nProcessed: {len(processed)}, Failed: {len(failed)}")

    if processed:
        journal_path = JOURNAL_DIR / f'{TODAY}.md'
        section = f"\n\n## Research Processing\n\n"
        section += f"Processed {len(processed)} research items:\n\n"
        for p in processed:
            section += f"- **{p['title']}**: {p['summary']} ({p['zettels']} zettels)\n"
        if failed:
            section += f"\nFailed to process: {len(failed)} items (kept as TODO for retry)\n"

        if journal_path.exists():
            existing = journal_path.read_text()
            journal_path.write_text(existing + section, encoding='utf-8')
        else:
            journal_path.write_text(f"---\ndate: {TODAY}\ntype: daily\n---\n{section}", encoding='utf-8')

        log(f"Journal updated: {journal_path}")

    # Step 4: Daily news brief (best-effort)
    daily_brief_path = None
    if not args.no_daily_news:
        try:
            daily_brief_path = process_daily_news()
        except Exception as e:
            log(f"Daily-news step failed (non-fatal): {e}")

    # Step 5: NotebookLM podcast (best-effort; pipeline must not fail if NLM is down)
    notebook_id = None
    if (processed or daily_brief_path) and not args.no_podcast:
        try:
            notebook_id = create_notebook_with_podcast(processed, daily_brief_path)
            if notebook_id:
                log(f"NotebookLM notebook ready: {notebook_id}")
            else:
                # "best-effort" governs whether the RUN fails, not whether the
                # user is told. A podcast step that produced nothing and said
                # nothing is indistinguishable from one that was never asked
                # for — which is how this broke for six days unnoticed.
                log("PODCAST STEP PRODUCED NO NOTEBOOK — see the nlm errors above. "
                    "Likely causes: expired nlm auth (refresh on the Mac and "
                    "re-sync), or a stale nlm binary.")
        except Exception as e:
            log(f"NotebookLM step failed (non-fatal): {e}")

    # Step 5: Telegram push (notebook URL + summary)
    if processed:
        try:
            send_telegram_summary(processed, failed, notebook_id)
        except Exception as e:
            log(f"Telegram push failed (non-fatal): {e}")

    # Step 6: Git commit + push
    try:
        subprocess.run(['git', 'add', '-A'], cwd=PERSONAL, capture_output=True, timeout=10)
        subprocess.run(
            ['git', 'commit', '-m', f'nightshift: research processing {TODAY} ({len(processed)} items)'],
            cwd=PERSONAL, capture_output=True, timeout=10
        )
        subprocess.run(['git', 'push'], cwd=PERSONAL, capture_output=True, timeout=30)
        log("Committed and pushed")
    except Exception as e:
        log(f"Git error: {e}")

    # Summary
    log(f"\n{'='*50}")
    log(f"Research Complete!")
    log(f"{'='*50}")
    log(f"Processed: {len(processed)}")
    log(f"Failed: {len(failed)}")
    log(f"Literature notes: {len([p for p in processed if p.get('literature_note')])}")
    log(f"Total zettels: {sum(p.get('zettels', 0) for p in processed)}")
    if notebook_id:
        log(f"Notebook: https://notebooklm.google.com/notebook/{notebook_id}")


# ---- NotebookLM podcast (best-effort) ----

def create_notebook_with_podcast(processed: List[Dict[str, Any]],
                                  daily_brief_path: Optional[Path] = None) -> Optional[str]:
    """Create a NotebookLM notebook, add literature notes + daily-news brief as sources,
    queue audio overview.

    Returns notebook UUID on success, None on failure. The audio overview generation
    is queued asynchronously — user must manually download via browser (CLI is
    blocked by Google CDN cookie requirement).
    """
    nlm = os.environ.get('NLM_BIN') or _SETTINGS.get('nlm_path') or ''
    if not nlm or not Path(nlm).exists():
        # Fallback to PATH lookup
        nlm_path = subprocess.run(['which', 'nlm'], capture_output=True, text=True).stdout.strip()
        if not nlm_path:
            log("  nlm binary not found — skipping podcast")
            return None
        nlm = nlm_path

    # Create notebook
    title = f"Datacore Research {TODAY}"
    log(f"  Creating notebook: {title}")
    # Old-style syntax ('create', not 'notebook create') — the server binary
    # (Apr 2026) only knows old-style; the new binary keeps it as a
    # deprecated alias. 'notebook create' here meant this function NEVER
    # succeeded on the server (silent best-effort failure).
    res = subprocess.run(
        [nlm, 'create', title],
        capture_output=True, text=True, timeout=30
    )
    if res.returncode != 0:
        log(f"  nlm create failed: {res.stderr[:200]}")
        return None

    # Extract notebook ID from output
    nb_match = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', res.stdout)
    if not nb_match:
        log(f"  Could not extract notebook ID from: {res.stdout[:200]}")
        return None
    notebook_id = nb_match.group(1)
    log(f"  Notebook ID: {notebook_id}")

    # Build the source list: literature notes + daily-news brief
    sources: List[str] = []
    for p in processed:
        lit = p.get('literature_note')
        if lit:
            sources.append(lit)
    if daily_brief_path:
        sources.append(str(daily_brief_path))

    # Add sources
    sources_added = 0
    for src in sources:
        try:
            add_res = subprocess.run(
                [nlm, 'add', notebook_id, src],
                capture_output=True, text=True, timeout=60
            )
            if add_res.returncode == 0:
                sources_added += 1
            else:
                log(f"  add failed for {src}: {add_res.stderr[:120]}")
        except Exception as e:
            log(f"  add error for {src}: {e}")
    log(f"  Sources added: {sources_added}")

    if sources_added == 0:
        log("  No sources added — skipping audio generation")
        return notebook_id

    # Queue audio overview.
    #
    # INSTRUCTIONS MUST BE EMPTY. In notebooklm/client_audio.go,
    # CreateAudioOverviewWithOptions routes to the new CreateUniversalArtifact
    # RPC only when Instructions == "" (and DEEP_DIVE / DEFAULT / "en"). ANY
    # custom instruction falls through to the old CreateAudioOverview path,
    # which the server now rejects with "One or more arguments are invalid".
    #
    # This function used to pass a per-day instruction string, so every audio
    # overview failed — and because the failure was only logged, the run
    # reported success with no podcast. That is the silent-failure mode this
    # whole path keeps regressing into. Custom instructions must be set in the
    # web UI instead. See ENG-2026-08-09-028.
    audio_res = subprocess.run(
        [nlm, 'create-audio', notebook_id, ''],
        capture_output=True, text=True, timeout=60
    )
    if audio_res.returncode != 0:
        # Loud, and reflected in the return value: a notebook with no audio is
        # not a podcast, and a caller that cannot distinguish the two will keep
        # reporting success to the user while nothing is produced.
        log(f"  AUDIO QUEUE FAILED: {(audio_res.stderr or audio_res.stdout)[:300]}")
        log(f"  Notebook {notebook_id} exists with {sources_added} source(s) but has NO audio.")
        return None

    log("  Audio overview queued")
    return notebook_id


def send_telegram_summary(processed: List[Dict[str, Any]], failed: List[Dict[str, str]],
                          notebook_id: Optional[str]) -> bool:
    """Push a research-run summary to Telegram. Returns True on success."""
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    # Fall back to the host env file. Under cron, or when an agent shells out,
    # the process inherits almost nothing, so these were routinely absent and
    # the run finished having told nobody it was done — the notification path
    # failing exactly when it is the only thing that would report the run.
    if not token or not chat_id:
        env_file = Path.home() / ".datacore" / "datacore.env"
        try:
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                v = v.strip().strip('"').strip("'")
                if k.strip() == "TELEGRAM_BOT_TOKEN" and not token:
                    token = v
                elif k.strip() == "TELEGRAM_CHAT_ID" and not chat_id:
                    chat_id = v
        except OSError:
            pass
    if not token or not chat_id:
        missing = [n for n, v in (("TELEGRAM_BOT_TOKEN", token),
                                  ("TELEGRAM_CHAT_ID", chat_id)) if not v]
        log(f"  Telegram push SKIPPED — missing {', '.join(missing)} "
            f"(checked environment and ~/.datacore/datacore.env)")
        return False

    lines = [f"📚 Research processed for {TODAY}"]
    lines.append(f"✅ {len(processed)} items · ❌ {len(failed)} failed")
    if notebook_id:
        lines.append("")
        lines.append("🎧 Audio overview generating in NotebookLM:")
        lines.append(f"https://notebooklm.google.com/notebook/{notebook_id}")
        lines.append("(Tap Audio Overview → ⋯ → Download — CDN cookie blocks CLI download)")
    if processed:
        lines.append("")
        lines.append("Top items:")
        for p in processed[:5]:
            lines.append(f"• {p['title'][:80]}")

    msg = "\n".join(lines)

    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=urllib.parse.urlencode({
                'chat_id': chat_id,
                'text': msg,
                'disable_web_page_preview': 'false',
            }).encode('utf-8'),
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = resp.status == 200
        log(f"  Telegram push: {'ok' if ok else 'failed'}")
        return ok
    except Exception as e:
        log(f"  Telegram error: {e}")
        return False


if __name__ == '__main__':
    main()
