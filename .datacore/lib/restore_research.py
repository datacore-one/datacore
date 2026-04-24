#!/usr/bin/env python3
"""Restore non-newsletter TODO entries from archive to research_learning.org."""
import re
from pathlib import Path

PERSONAL = Path.home() / "Data" / "0-personal"
archive = PERSONAL / "org" / "research_learning_archive_2026-04.org"
org_path = PERSONAL / "org" / "research_learning.org"

content = archive.read_text()
lines = content.split("\n")

entries_by_section = {}
current_section = "Unsorted"
current_entry = None
entry_lines = []

for line in lines:
    # Track sections (level 2)
    if re.match(r"^\*\*\s+(?!TODO|DONE|NEXT|WAITING)", line):
        sec_m = re.match(r"^\*\*\s+(.+?)(?:\s+:[\w:]+:)?\s*$", line)
        if sec_m:
            if current_entry and current_section != "Newsletter Reading Queue":
                entries_by_section.setdefault(current_section, []).append("\n".join(entry_lines))
            current_section = sec_m.group(1).strip()
            current_entry = None
            entry_lines = []
        continue

    if re.match(r"^\*{2,3}\s+TODO\s+", line):
        if current_entry and current_section != "Newsletter Reading Queue":
            entries_by_section.setdefault(current_section, []).append("\n".join(entry_lines))
        current_entry = line
        entry_lines = [line]
        continue

    if re.match(r"^\*{2,3}\s+DONE\s+", line):
        if current_entry and current_section != "Newsletter Reading Queue":
            entries_by_section.setdefault(current_section, []).append("\n".join(entry_lines))
        current_entry = None
        entry_lines = []
        continue

    if current_entry:
        entry_lines.append(line)

if current_entry and current_section != "Newsletter Reading Queue":
    entries_by_section.setdefault(current_section, []).append("\n".join(entry_lines))

total = sum(len(v) for v in entries_by_section.values())
print(f"Non-newsletter TODO entries: {total}")
for sec in sorted(entries_by_section.keys()):
    print(f"  {sec}: {len(entries_by_section[sec])}")

# Build new org file
out = [
    "#+TITLE: Research & Learning",
    "#+CATEGORY: Research",
    "#+FILETAGS: :gtd:research:",
    "#+STARTUP: overview",
    "#+CREATED: [2025-12-08 Sun]",
    "",
    "* RESEARCH & LEARNING",
]

section_order = [
    "Verity", "Daily News", "Datacore", "Trading", "Datafund",
    "Datafund / Verity", "Business & Strategy", "Technology & Innovation",
    "Personal", "Health & Longevity", "Personal Development",
    "Family", "Science", "GTD & Productivity", "Communication",
    "General Reading",
]

used = set()
for sec in section_order:
    out.append(f"** {sec}")
    if sec in entries_by_section:
        for entry in entries_by_section[sec]:
            out.append(entry.rstrip())
        used.add(sec)

for sec in sorted(entries_by_section.keys()):
    if sec not in used:
        out.append(f"** {sec}")
        for entry in entries_by_section[sec]:
            out.append(entry.rstrip())

org_path.write_text("\n".join(out) + "\n")
print(f"\nWritten {total} entries to {org_path}")
