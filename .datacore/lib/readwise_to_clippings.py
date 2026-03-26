#!/usr/bin/env python3
"""
Parse research_learning.org Readwise items and create clipping notes.
Generates markdown files with frontmatter, summary, and project relevance.
"""

import re
import os
import unicodedata
from pathlib import Path

# Get absolute paths using DATACORE_ROOT
DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
ORG_FILE = DATACORE_ROOT / "0-personal" / "org" / "research_learning.org"
OUTPUT_DIR = DATACORE_ROOT / "0-personal" / "3-knowledge" / "clippings" / "readwise"

# Project relevance keywords (lowercase)
RELEVANCE_MAP = {
    "Project Alpha": [
        "tokeniz", "rwa", "real world asset", "real-world asset", "security token",
        "erc-3643", "erc3643", "erc-1400", "erc1400", "erc-4931", "erc-8004",
        "compliance", "vara", "dubai", "dfsa", "data marketplace", "data monetiz",
        "securitize", "blackrock", "buidl", "centrifuge", "ondo", "polytrade",
        "obligate", "brickken", "tokeny", "chainlink", "ixs dex", "ats ",
        "mifid", "fund tokeniz", "tokenized fund", "tokenized stock",
        "tokenized treasur", "onchain equit", "digital twin", "apollo",
        "data broker", "data as asset", "data valuation", "story protocol",
        "ip tokeniz", "ip rights", "tokenized ip", "regulated defi",
        "institutional defi", "converge blockchain", "world coin", "worldcoin",
    ],
    "Organization": [
        "fair data", "data sovereignty", "data ownership", "personal data",
        "data broker", "ai training data", "gdpr", "consent", "privacy",
        "data economy", "data rights", "data licensing", "data scraping",
        "user-owned", "user owned", "data provenance", "data monetiz",
        "synthetic data", "federated learning", "differential privacy",
        "homomorphic encrypt", "fhe ", "data vault", "data marketplace",
        "vana ", "sahara ai", "ocean protocol", "prifina", "itheum",
        "datavault", "datadance", "sitra", "data action plan",
        "ai training", "copyright", "data harvesting", "surveillance",
        "censorship", "data industry", "data platform", "data quality",
        "health data", "medical data", "clinical data", "nhs ",
        "23andme", "genetic data", "genomic data",
    ],
    "PartnerOrg": [
        "productx", "decentrali", "storage",
        "local-first", "local first", "depin", "ipfs", "codex storage",
        "walrus", "zero knowledge", "zero-knowledge", "zk proof", "zkp",
        "zktls", "circom", "peer-to-peer", "p2p", "crdt",
        "censorship-resistant", "durable record", "akash network",
        "0g documentation",
    ],
    "Datacore": [
        "claude", "mcp ", "model context protocol", "ai agent", "ai assistant",
        "rag ", "retrieval augmented", "second brain", "knowledge management",
        "workflow", "n8n", "autogen", "smolagent", "llamaindex", "langchain",
        "agentic", "multi-agent", "agent framework", "agent architecture",
        "coding agent", "browser automation", "eliza", "elizaos",
        "agent2agent", "a2a protocol", "retool", "personal ai",
        "obsidian", "memory system", "context management",
    ],
    "Trading": [
        "trading", "market", "defi", "bitcoin", "btc", "crypto market",
        "stablecoin", "hedge fund", "order flow", "quant", "dex ",
        "prediction market", "portfolio", "price prediction", "backtest",
        "bear market", "bull market", "macro ", "arthur hayes",
        "cvd", "delta", "footprint chart", "liquidit", "leverage",
        "short", "long position", "prop firm", "memecoin", "ai coin",
        "wintermute", "flashbot", "mev ", "uniswap", "hyperliquid",
        "narrative trading", "technical analysis",
    ],
    "Health": [
        "health", "longevity", "aging", "ageing", "medical", "drug",
        "supplement", "exercise", "sleep", "cancer", "brain", "heart",
        "muscle", "metaboli", "nutrition", "diet", "fasting",
        "autophagy", "alzheimer", "dementia", "mental health",
        "clinical trial", "pharma", "biotech", "epigenetic",
        "genomic", "dna ", "hrv", "hrr", "vo2", "cardio",
        "longevity", "lifespan", "anti-aging", "bryan johnson",
        "peter attia", "peter diamandis", "david sinclair",
        "sauna", "olive oil", "microbiome", "vitamin",
    ],
}


def slugify(text, max_len=80):
    """Convert text to a filename-safe slug."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[-\s]+", "-", text).strip("-")
    return text[:max_len]


def determine_relevance(title, summary, tags):
    """Determine project relevance from title, summary, and tags."""
    searchable = f"{title} {summary} {' '.join(tags)}".lower()
    relevant = {}
    for project, keywords in RELEVANCE_MAP.items():
        matches = [kw for kw in keywords if kw in searchable]
        if matches:
            relevant[project] = matches[:3]  # top 3 matching keywords
    return relevant


def parse_readwise_items(filepath):
    """Parse all items from the Readwise Import sections."""
    with open(str(filepath), "r", encoding="utf-8") as f:
        content = f.read()

    items = []

    # Pattern 1: Readwise Import items (with PROPERTIES drawer)
    # Match ** TODO or ** DONE headings with properties
    pattern = re.compile(
        r"^\*\*\s+(TODO|DONE)\s+(?:\[#[ABC]\]\s+)?"  # state + optional priority
        r"(.*?)\s*$\n"  # title (rest of heading line)
        r"(?:.*?\n)*?"  # optional lines before properties
        r":PROPERTIES:\n"
        r"(.*?)"  # properties content
        r":END:\n"
        r"\n?"
        r"(.*?)(?=\n\*\*\s|\n\*\s[^*]|\Z)",  # summary until next heading
        re.MULTILINE | re.DOTALL,
    )

    for m in pattern.finditer(content):
        state = m.group(1)
        raw_title = m.group(2).strip()
        props_block = m.group(3)
        summary = m.group(4).strip()

        # Extract tags from title
        tag_match = re.search(r":(\S+):$", raw_title)
        tags = tag_match.group(1).split(":") if tag_match else []
        clean_title = re.sub(r"\s*:\S+:\s*$", "", raw_title).strip()
        # Remove duplicate title lines (some items have title repeated)
        clean_title = clean_title.split("\n")[0].strip()

        # Parse properties
        props = {}
        for line in props_block.strip().split("\n"):
            pm = re.match(r":(\w+):\s*(.*)", line.strip())
            if pm:
                props[pm.group(1)] = pm.group(2).strip()

        readwise_id = props.get("READWISE_ID", "")
        link = props.get("Link", "")
        author = props.get("AUTHOR", "")
        category = props.get("CATEGORY", "")
        created = props.get("CREATED", "")

        if not readwise_id:
            continue  # Skip non-Readwise items

        items.append({
            "title": clean_title,
            "url": link,
            "author": author,
            "category": category,
            "created": created,
            "readwise_id": readwise_id,
            "tags": [t for t in tags if t and t != "readwise"],
            "summary": summary.strip(),
            "state": state,
        })

    # Pattern 2: Final Import items (with org links)
    final_section = content.split("* Readwise Final Import")
    if len(final_section) > 1:
        final_content = final_section[1]
        link_pattern = re.compile(
            r"^\*{2,3}\s+(?:TODO|DONE)\s+"
            r"\[\[(https?://\S+?)\]\[(.+?)\]\]"  # org link
            r"\s*(:\S+:)?\s*$\n"  # optional tags
            r"(?:\s+Captured On:.*\n)?"  # optional captured date
            r"(.*?)(?=\n\*{2,3}\s|\n\*\s[^*]|\Z)",  # description
            re.MULTILINE | re.DOTALL,
        )
        for m in link_pattern.finditer(final_content):
            url = m.group(1)
            title = m.group(2).strip()
            tag_str = m.group(3) or ""
            description = m.group(4).strip()

            tags = [t for t in tag_str.strip(":").split(":") if t]

            items.append({
                "title": title,
                "url": url,
                "author": "",
                "category": "link",
                "created": "",
                "readwise_id": "",
                "tags": tags,
                "summary": description,
                "state": "TODO",
            })

    # Pattern 3: Final Import items with indented content
    if len(final_section) > 1:
        final_content = final_section[1]
        # More permissive pattern for indented org-link items
        link_pattern2 = re.compile(
            r"^\*{2,3}\s+(?:TODO|DONE)\s+"
            r"\[\[(https?://\S+?)\]\[(.+?)\]\]"  # org link
            r"\s*(:\S+:)?\s*$",  # optional tags
            re.MULTILINE,
        )
        for m in link_pattern2.finditer(final_content):
            url = m.group(1)
            title = m.group(2).strip()
            tag_str = m.group(3) or ""

            # Check if already added
            if any(i["url"] == url for i in items):
                continue

            # Get description from following indented lines
            start = m.end()
            desc_lines = []
            remaining = final_content[start:]
            for line in remaining.split("\n"):
                stripped = line.strip()
                if stripped.startswith("*"):
                    break
                if stripped and not stripped.startswith("Captured On:"):
                    desc_lines.append(stripped)
                elif not stripped:
                    if desc_lines:
                        break

            tags = [t for t in tag_str.strip(":").split(":") if t]

            items.append({
                "title": title,
                "url": url,
                "author": "",
                "category": "link",
                "created": "",
                "readwise_id": "",
                "tags": tags,
                "summary": " ".join(desc_lines),
                "state": "TODO",
            })

    return items


def create_clipping(item, output_dir):
    """Create a markdown clipping file for an item."""
    title = item["title"]
    if not title:
        title = "Untitled"

    # Determine relevance
    relevance = determine_relevance(title, item["summary"], item["tags"])

    # Build frontmatter
    fm_lines = [
        "---",
        f'title: "{title.replace(chr(34), chr(39))}"',
        f'url: "{item["url"]}"',
    ]
    if item["author"]:
        fm_lines.append(f'author: "{item["author"].replace(chr(34), chr(39))}"')
    if item["category"]:
        fm_lines.append(f'type: {item["category"]}')
    if item["readwise_id"]:
        fm_lines.append(f'readwise_id: {item["readwise_id"]}')
    if item["created"]:
        # Extract date from [2025-12-22 Mon] format
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", item["created"])
        if date_match:
            fm_lines.append(f"date_saved: {date_match.group()}")

    # Tags as inline list
    all_tags = list(set(item["tags"]))
    # Add project tags
    for project in relevance:
        ptag = project.lower()
        if ptag not in all_tags:
            all_tags.append(ptag)
    if all_tags:
        fm_lines.append(f'tags: [{", ".join(all_tags)}]')

    # Projects
    if relevance:
        fm_lines.append(f'projects: [{", ".join(relevance.keys())}]')

    fm_lines.append("---")

    # Build body
    body_lines = []
    if item["summary"]:
        body_lines.append("")
        body_lines.append(item["summary"])

    if relevance:
        body_lines.append("")
        body_lines.append("## Relevance")
        body_lines.append("")
        for project, keywords in relevance.items():
            kw_str = ", ".join(keywords[:3])
            body_lines.append(f"- **{project}**: matched on: {kw_str}")
    else:
        body_lines.append("")
        body_lines.append("## Relevance")
        body_lines.append("")
        body_lines.append("- General reading / cross-cutting interest")

    content = "\n".join(fm_lines) + "\n" + "\n".join(body_lines) + "\n"

    # Generate filename
    slug = slugify(title)
    if not slug:
        slug = item.get("readwise_id", "untitled") or "untitled"

    filename = f"{slug}.md"
    filepath = os.path.join(output_dir, filename)

    # Handle duplicates
    counter = 1
    while os.path.exists(filepath):
        filename = f"{slug}-{counter}.md"
        filepath = os.path.join(output_dir, filename)
        counter += 1

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath, bool(relevance)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Parsing {ORG_FILE}...")
    items = parse_readwise_items(ORG_FILE)
    print(f"Found {len(items)} items")

    created = 0
    with_relevance = 0
    project_counts = {}

    for item in items:
        filepath, has_relevance = create_clipping(item, OUTPUT_DIR)
        created += 1
        if has_relevance:
            with_relevance += 1

        # Count by project
        relevance = determine_relevance(item["title"], item["summary"], item["tags"])
        for project in relevance:
            project_counts[project] = project_counts.get(project, 0) + 1

        if created % 100 == 0:
            print(f"  Created {created}/{len(items)} clippings...")

    print(f"\nDone! Created {created} clipping notes in {OUTPUT_DIR}")
    print(f"  With project relevance: {with_relevance}")
    print(f"  General reading: {created - with_relevance}")
    print(f"\nProject distribution:")
    for project, count in sorted(project_counts.items(), key=lambda x: -x[1]):
        print(f"  {project}: {count}")


if __name__ == "__main__":
    main()
