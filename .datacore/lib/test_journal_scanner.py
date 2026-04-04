# .datacore/lib/test_journal_scanner.py
"""Tests for journal_scanner.py"""
import pytest
from pathlib import Path
from journal_scanner import extract_sections, score_section, scan_journal

SAMPLE_JOURNAL = """# 2026-04-01

## Session: Fairdrop MCP Debugging

**Goal:** Fix download failures in Fairdrop MCP

### Findings

Download failure diagnosed:
- Symptom: /bytes endpoint returns 404 on download
- Root cause: upload used /bzz (creates manifest reference); download attempted /bytes (expects raw chunk reference)
- Fix: normalize all references through /bzz endpoint

### Code Changes

Updated `src/handlers/download.ts` to use /bzz for all downloads.

## Standup Notes

Quick sync with team. Nothing actionable.

## Research: Swarm Chunk Encryption

Read the Swarm whitepaper section on chunk encryption. Key insight: chunks are content-addressed via BMT hash, encryption uses the chunk address as the key. This means knowing the reference = having the decryption key. Privacy requires additional access control layer (ACT).

References:
- Swarm whitepaper v3, Section 4.2
- [[Swarm Architecture]]
"""


def test_extract_sections():
    sections = extract_sections(SAMPLE_JOURNAL)
    assert len(sections) >= 3
    assert any("Fairdrop" in s.title for s in sections)
    assert any("Swarm Chunk Encryption" in s.title for s in sections)


def test_score_section_high():
    """Sections with findings, key insights, references score high."""
    sections = extract_sections(SAMPLE_JOURNAL)
    research = [s for s in sections if "Encryption" in s.title][0]
    score = score_section(research)
    assert score >= 0.6, f"Research section should score >= 0.6, got {score}"


def test_score_section_low():
    """Standup notes with no substance score low."""
    sections = extract_sections(SAMPLE_JOURNAL)
    standup = [s for s in sections if "Standup" in s.title][0]
    score = score_section(standup)
    assert score < 0.3, f"Standup section should score < 0.3, got {score}"


def test_scan_journal_filters_by_threshold():
    results = scan_journal(SAMPLE_JOURNAL, threshold=0.5)
    titles = [r.title for r in results]
    assert "Standup Notes" not in titles
    assert any("Encryption" in t or "Fairdrop" in t for t in titles)
