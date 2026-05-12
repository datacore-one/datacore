---
name: ingest
description: Process files from inbox folders or external sources into Datacore with deep knowledge extraction
user_invocable: true
recall:
  # DIP-0029 default — engrams scoped to this command + tag-matched.
  scopes:
    - command:ingest
  tags:
    - ingest
---

# /ingest Command

## Command Context

### When to Reference DIP-0015

**Always reference when:**
- Determining file destinations
- Creating companion files
- Routing to semantic folders
- Extracting knowledge (zettels, insights)

**Key decisions this DIP informs:**
- Semantic routing (active/knowledge/archive)
- Companion file format
- Tag application (DIP-0014)
- YAML frontmatter requirements

### Quick Reference

| Question | Answer |
|----------|--------|
| Default inbox? | `0-inbox/` in each space |
| Active work? | `1-tracks/` or `1-active/` |
| Reference? | `3-knowledge/` |
| Archive? | `4-archive/` |
| What DIPs govern this? | DIP-0015 (Semantic Org), DIP-0014 (Tags) |

### Agents This Command Invokes

| Agent | Purpose |
|-------|---------|
| `ingest-orchestrator` | Orchestration, planning (replaces ingest-coordinator, DIP-0021) |
| `knowledge-extractor` | Per-file knowledge extraction (replaces ingest-processor, DIP-0021) |
| `docx-reader` | DOCX conversion |

### Integration Points

- **DIP-0021** - Search & Research Architecture
- **DIP-0015** - Semantic organization
- **DIP-0014** - Tag taxonomy
- **Git LFS** - Large file handling

---

Process files from inbox folders or external sources into Datacore with **deep knowledge extraction** - not just file sorting, but reading, analyzing, extracting insights, and discovering actionable items.

## Usage

```
/ingest [optional: folder path]
```

- **Default**: Processes `0-inbox/` folders across all spaces
- **With path**: Processes specified folder (e.g., `~/Documents/Migration/`)

## Workflow

### Pre-Phase: Goal Clarification

**Before scanning**, ask user about extraction goals:

```
What's your primary goal for this ingest?
1. Contact extraction - Build CRM entries from documents
2. Knowledge capture - Extract zettels, insights, literature notes
3. File organization - Route files to semantic destinations
4. Archive migration - Move historical content with minimal processing
5. All of the above - Comprehensive processing
```

**If contact extraction selected:**
```
Contact extraction depth:
- Surface (faster) - File/folder names, document headers
- Deep (comprehensive) - Full document body scan, all file types

Recommended: DEEP for corporate/business folders
```

### Phase 1: Plan

Before processing, present a plan to the user:

1. **Inventory** - Scan source folder, count files by type
2. **File-Type Strategy** - Create extraction approach per file type:
   - DOCX: ZIP extract, body text, signatures, tables
   - PDF: Multimodal read, letterheads, legal parties
   - XLSX: Cell scan, contact columns
   - EML: Header parse (From, To, CC)
   - PPTX: Metadata, notes, credits
3. **Analyze** - Identify file categories:
   - Active/current → `1-tracks/` or `1-active/`
   - Reference → `3-knowledge/`
   - Historical → `4-archive/`
   - Sensitive → Flag for manual handling
4. **Propose destinations** - Show intended folder structure
5. **Estimate** - Count of files, zettels to extract, companions needed
6. **Request approval** - Wait for user confirmation before proceeding

### Phase 2: Process (Multi-Pass for Contact Extraction)

**If contact extraction was selected with DEEP mode:**

```
PASS 1: Surface Extraction
==========================
Scanning file/folder names, document headers...
Contacts found: 8 investors, 8 companies

PASS 2: Deep Content Extraction
===============================
Extracting DOCX body text...
Parsing signatures, CC lines, tables...
Additional contacts: +10 service providers, +9 companies
Total: 18 people, 17 companies

PASS 3: Archive Review (during Phase 4)
=======================================
Final entity scan during file movement...
```

**Key insight**: Surface extraction typically captures only 30-40% of entities. Deep extraction is essential for corporate/business folders.

#### 6-Phase DIP-0015 Methodology

Each document goes through systematic deep processing:

#### Phase 2.1: READ

Analyze content based on format:

| Format | Method | Output |
|--------|--------|--------|
| PDF | Read tool (multimodal) | Full text + visual analysis |
| DOCX | ZIP extraction (see below) | Markdown conversion |
| XLSX/CSV | Read tool | Data extraction, key metrics |
| Images | Read tool | OCR, visual description |
| Keynote/PPTX | Metadata only | Companion required |

**DOCX Extraction:**
```bash
unzip -p "file.docx" word/document.xml 2>/dev/null | \
  sed 's/<\/w:p>/\n\n/g' | \
  sed 's/<[^>]*>//g' | \
  tr -s '\n' | \
  sed 's/^[[:space:]]*//'
```

#### Phase 2.2: ASSESS

**Step 1: Space Routing**

```
Is this official organizational content?
├── YES → Route to org space (e.g., 1-teamspace/)
│   - Company contracts, agreements
│   - Official financial statements
│   - Brand assets, approved marketing
│   - Team decisions, meeting notes
│
└── NO → Route to personal space (0-personal/)
    - Personal writings/thoughts
    - Draft ideas not yet shared
    - Personal notes, research
```

**Key questions:**
1. "If I left, should this stay with the org?" → Yes = org
2. "Does this represent the company's position or mine?" → Company = org
3. "Would this be shared in a team handoff?" → Yes = org

**Step 2: Semantic Destination**

| Question | If Yes → Destination |
|----------|----------------------|
| Still active/binding? | `1-tracks/[track]/` |
| Reference value? | `3-knowledge/` |
| Historical only? | `4-archive/` |

#### Phase 2.3: EXTRACT (Knowledge Extraction)

**This is the critical phase.** Extract knowledge worth preserving:

##### Zettels (Atomic Concepts)

Create a zettel when you find:
- **Key decision made** with rationale
- **Novel framework or model** that could be reused
- **Core concept** defined with precision
- **Reusable pattern** applicable elsewhere
- **Principle or guideline** worth remembering

**Zettel criteria:**
- Is it atomic (one concept)?
- Is it evergreen (not time-bound)?
- Would it be useful in another context?
- Can it stand alone without the source document?

**Format:**
```markdown
---
title: [Concept Name]
type: zettel
created: [today]
source: [original document]
---

# [Concept Name]

[Definition or core idea in 1-2 sentences]

## [Elaboration sections as needed]

## Related

- [[related-concept-1]]
- [[related-concept-2]]

#Tag1 #Tag2
```

**Location:** `[space]/3-knowledge/zettel/` (personal) or `[space]/3-knowledge/zettel/` (org)

##### Literature Notes (Document Summaries)

Create a literature note for **significant documents** worth summarizing:
- Strategic plans, whitepapers
- Research reports, analyses
- Important contracts or agreements
- Foundational documents

**Format:**
```markdown
---
title: [Document Title] - Literature Note
type: literature
source: [filename]
author: [author]
date: [original date]
---

# [Document Title]

## Summary

[2-3 paragraph summary of key points]

## Key Takeaways

1. [Main point 1]
2. [Main point 2]
3. [Main point 3]

## Concepts Mentioned

- [[concept-1]] - brief context
- [[concept-2]] - brief context

## Quotes

> "[Notable quote from document]"

## Related

- [[related-doc]]

#Literature #[topic-tags]
```

**Location:** `[space]/3-knowledge/literature/` (personal) or `[space]/3-knowledge/literature/` (org)

##### Insights (Strategic Observations)

Add to `insights.md` when you discover:
- Cross-domain connections
- Strategic pivot observations
- Market positioning insights
- Pattern recognitions across documents

**Format for insights.md:**
```markdown
## [Date] - [Insight Title]

**Source:** [document name]

[1-2 paragraph insight]

**Implication:** [What this means for action/strategy]

---
```

**Location:** `[space]/3-knowledge/insights.md`

##### Market Timing Re-evaluation

**Critical for old documents (2018-2021):** Flag ideas where:
- Technology has matured since then
- Market understanding has improved
- Regulatory clarity now exists
- Infrastructure is now available
- Timing may now be right

Create task: `TODO Review [concept] - timing may be right now`

##### Tasks (Discovered Action Items)

Capture to `org/inbox.org`:
- Follow-ups needed
- Unfulfilled commitments discovered
- Promises to check if kept
- Opportunities worth investigating

#### Phase 2.4: CAPTURE

Log discovered items:

| Item Type | Destination | Format |
|-----------|-------------|--------|
| Tasks | `org/inbox.org` | Org-mode TODO |
| Someday items | `org/someday.org` | Org-mode entry |
| Research items | Knowledge base | Note or zettel |

#### Phase 2.5: FILE

Move to semantic destination:
- Route by **role/purpose**, not format
- Create companions for non-AI-readable files
- Add YAML frontmatter metadata
- Apply tags from registry
- Use kebab-case filenames

#### Phase 2.6: LINK

Connect to existing content:
- Add wiki-links to related content
- Update `_index.md` files
- Reference in track docs if significant
- Tag per DIP-0014

### Phase 3: Report

After processing, generate detailed report:

| Section | Contents |
|---------|----------|
| **Summary** | Total files processed, destinations used |
| **Files Moved** | List of source → destination mappings |
| **Knowledge Extracted** | Zettels, literature notes, insights created |
| **Tasks Captured** | Items added to inbox.org |
| **Market Timing Flags** | Old ideas worth re-evaluating |
| **Companions Created** | Non-AI-readable files with companions |
| **Sensitive Skipped** | Files flagged as sensitive |
| **Errors** | Any failures with reasons |

**Save report to:** `[space]/content/reports/[date]-ingest-report.md`

### Phase 4: Cleanup (with Mandatory Verification)

**CRITICAL: Never declare completion without explicit file count verification.**

After successful processing:

1. **COUNT SOURCE FILES** (mandatory) - `find source/ -type f | wc -l`
   - If count > 0: Report remaining, do NOT declare complete
   - Execute bulk archive for remaining files
2. **Final entity scan** - During archive movement, check file/folder names for missed entities
3. **Verify destinations** - Confirm all files exist in destination
4. **Delete source** - Only after count = 0 (except sensitive)
5. **Preserve sensitive** - Keep flagged sensitive files
6. **Clean empty dirs** - Remove empty directories
7. **Final report** - Show counts: archived, active, CRM contacts, zettels

**Progress reporting**: For operations >50 files, report batch progress:
```
Archiving batch 1/9 (files 1-100)... done
Archiving batch 2/9 (files 101-200)... done
...
```

### Phase 5: Quality Verification

Before marking complete, verify:

- [ ] All files moved to semantic destinations
- [ ] Non-AI-readable files have companions
- [ ] Zettels created for key concepts
- [ ] Literature notes for significant documents
- [ ] Tasks/obligations captured in inbox
- [ ] `_index.md` files updated
- [ ] Wiki-links created where relevant
- [ ] Tags applied per registry
- [ ] Journal updated with session summary
- [ ] No files left in source folder (except sensitive)

### Phase 6: Archive Review (Manual)

After initial processing, review for archiving. This is a **manual decision** - present candidates to user.

#### Archive Candidates

Flag items for archive review when:

| Condition | Example | Action |
|-----------|---------|--------|
| **Superseded** | Old website design, replaced pitch deck | Archive with note to replacement |
| **Project completed/abandoned** | Documentary never produced | Archive with status note |
| **Historical only** | Old working reports, past financials | Archive if no active reference |
| **Old versions** | v1, v2 when v3 exists | Archive versions, keep current |

#### Archive Process

1. **Present candidates** - List files flagged as potentially archivable
2. **Get user decision** - User confirms what to archive
3. **Move with companions** - If archiving, move source AND companion together
4. **Update indexes** - Update `_index.md` in both source and archive locations
5. **Add archive note** - Archive `_index.md` should explain why archived

#### Archive Destinations

| Space | Archive Location | Structure |
|-------|------------------|-----------|
| Personal | `4-outbox/archive/[topic]/` | Mirror active structure |
| Org | `4-archive/[category]/` | By document type |

#### Archive Index Format

```markdown
# [Topic] (Archived)

[Brief description of what's here and why archived]

## Documents

- **[[companion-file]]** - Description
- `source-file.pdf` - Original

## Why Archived

- [Reason 1: e.g., "Website redesigned in 2023"]
- [Reason 2: e.g., "Project never completed"]

## Active Content

See [[1-active/topic/]] for current materials.

#Archive #[topic-tag]
```

#### Key Principle

**Archive after processing, not during.** Complete the ingest workflow first (read, assess, extract, file, link), then review what should be archived. This ensures knowledge is extracted before archiving.

## Examples

```
/ingest                           # Process all 0-inbox/ folders
/ingest ~/Documents/Organization   # Import external folder
/ingest ~/Downloads               # Process downloads
```

## Sensitive File Handling

Files matching these patterns are flagged and NOT processed:
- `*wallet*`, `*backup*`, `*seed*`, `*credential*`, `*secret*`
- `.env`, `*.pem`, `*.key` (crypto keys)
- Files containing API keys or tokens

Sensitive files remain in the source folder for manual handling.

## Output Artifacts

| Artifact | Location | When Created |
|----------|----------|--------------|
| Converted documents | Semantic destination | All processed files |
| Zettels | `[space]/*/zettel/` | Atomic concepts discovered |
| Literature notes | `[space]/*/literature/` | Significant documents |
| Insights | `[space]/3-knowledge/insights.md` | Strategic observations |
| Tasks | `org/inbox.org` | Follow-ups discovered |
| Companions | Same folder as source | Non-AI-readable files |
| Index updates | `_index.md` files | All new content |
| Report | `content/reports/` | Every ingest session |
| Journal entry | `journal/` | Session summary |

## Agent

Spawns `ingest-orchestrator` which orchestrates `knowledge-extractor` subagents.

## Reference

See [DIP-0015: Semantic Organization](../dips/DIP-0015-semantic-organization.md) for full specification.
