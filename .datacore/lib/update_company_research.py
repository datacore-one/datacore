#!/usr/bin/env python3
"""
Update company files with research data.

Research data is loaded from a local (gitignored) JSON file.
To use: create .datacore/env/company_research_data.json with
the RESEARCH_DATA dictionary.
"""

import json
import os
import re
from pathlib import Path
from datetime import datetime

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
COMPANIES_DIR = DATACORE_ROOT / "0-personal/3-knowledge/reference/companies"
DATA_FILE = DATACORE_ROOT / ".datacore/env/company_research_data.json"


def load_research_data() -> dict:
    """Load research data from local JSON file."""
    if not DATA_FILE.exists():
        print(f"Research data file not found: {DATA_FILE}")
        print("Create it with your company research data in JSON format.")
        return {}
    with open(DATA_FILE, 'r') as f:
        return json.load(f)


def update_company_file(name: str, data: dict):
    """Update a company file with research data."""
    safe_name = name.replace(" ", "-").replace("/", "-").replace("(", "").replace(")", "").replace(".", "")
    possible_files = [
        COMPANIES_DIR / f"{safe_name}.md",
        COMPANIES_DIR / f"{name}.md",
    ]

    file_path = None
    for pf in possible_files:
        if pf.exists():
            file_path = pf
            break

    if not file_path:
        print(f"File not found for: {name}")
        return False

    with open(file_path, 'r') as f:
        content = f.read()

    if "needs_research: true" not in content and "<!-- TO BE FILLED" not in content:
        print(f"Already researched: {name}")
        return False

    industries_str = ", ".join(data.get("industries", ["crypto"]))

    new_content = f"""---
type: contact
entity_type: company
name: "{name}"
status: active
relationship_status: discovered
relationship_type: {data.get("relationship_type", "peer")}
relevance: {data.get("relevance", 3)}
industries: [{industries_str}]
website: {data.get("website", "")}
founded: {data.get("founded", "Unknown")}
headquarters: {data.get("headquarters", "Unknown")}
source: telegram_export
created: 2025-12-22
updated: {datetime.now().strftime("%Y-%m-%d")}
---

# {name}

## Overview

{data.get("overview", "Company information pending research.")}

## Key Products & Services

{data.get("products", "Products pending research.")}

## Key Contacts

| Name | Role | Status |
|------|------|--------|
"""

    contacts_match = re.search(r'\[\[([^\]]+)\]\]', content)
    if contacts_match:
        contact_name = contacts_match.group(1)
        new_content += f"| [[{contact_name}]] | Unknown | dormant |\n"
    else:
        new_content += "| (No contacts extracted) | - | - |\n"

    new_content += f"""
## Why Relevant for Organization

{data.get("why_relevant", "Relevance assessment pending.")}

## Industry Landscape Position

**Market position:** {data.get("market_position", "Unknown")}
**Competitors:** {data.get("competitors", "Unknown")}
**Partners:** {data.get("partners", "Unknown")}

## Notes

Research completed {datetime.now().strftime("%Y-%m-%d")} from web sources.

## Related

"""
    if contacts_match:
        new_content += f"- [[{contacts_match.group(1)}]]\n"

    tags = ", ".join([f"#{ind}" for ind in data.get("industries", ["crypto"])])
    new_content += f"\n{tags}\n"

    with open(file_path, 'w') as f:
        f.write(new_content)

    print(f"Updated: {name}")
    return True


def main():
    research_data = load_research_data()
    if not research_data:
        return
    updated = 0
    for name, data in research_data.items():
        if update_company_file(name, data):
            updated += 1
    print(f"\nUpdated {updated} company files")


if __name__ == "__main__":
    main()
