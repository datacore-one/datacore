#!/usr/bin/env python3
"""
DIP-0019 Learning Consolidation Script
Processes existing learnings into engram architecture
"""

import re
import yaml
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Set
from collections import defaultdict

class LearningConsolidator:
    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.patterns = []
        self.corrections = []
        self.preferences = []
        self.engram_candidates = []
        self.duplicates = []
        self.obsolete = []
        self.operational = []

    def load_learning_files(self):
        """Load all learning files"""
        patterns_file = self.base_path / "patterns.md"
        corrections_file = self.base_path / "corrections.md"
        preferences_file = self.base_path / "preferences.md"

        print(f"Loading {patterns_file}")
        with open(patterns_file, 'r') as f:
            self.patterns = self._parse_patterns(f.read())

        print(f"Loading {corrections_file}")
        with open(corrections_file, 'r') as f:
            self.corrections = self._parse_corrections(f.read())

        print(f"Loading {preferences_file}")
        with open(preferences_file, 'r') as f:
            self.preferences = self._parse_preferences(f.read())

    def _parse_patterns(self, content: str) -> List[Dict]:
        """Parse patterns.md into structured entries"""
        patterns = []

        # Split by heading markers (## or ###)
        sections = re.split(r'\n##+ ', content)

        for section in sections[1:]:  # Skip frontmatter
            lines = section.split('\n')
            title = lines[0].strip()

            # Skip placeholder sections
            if 'Placeholder' in title or '(To be learned' in section:
                continue

            # Extract key information
            pattern = {
                'title': title,
                'content': '\n'.join(lines[1:]).strip(),
                'type': 'pattern'
            }

            # Extract source date if available
            source_match = re.search(r'Source.*?(\d{4}-\d{2}-\d{2})', section)
            if source_match:
                pattern['source_date'] = source_match.group(1)

            # Detect type from structure
            if 'Context:' in section and 'Pattern:' in section:
                pattern['structured'] = True

            patterns.append(pattern)

        return patterns

    def _parse_corrections(self, content: str) -> List[Dict]:
        """Parse corrections.md into structured entries"""
        corrections = []

        sections = re.split(r'\n## ', content)

        for section in sections[1:]:
            lines = section.split('\n')
            title = lines[0].strip()

            correction = {
                'title': title,
                'content': '\n'.join(lines[1:]).strip(),
                'type': 'correction'
            }

            # Extract date
            date_match = re.search(r'\*\*Date\*\*:\s*(\d{4}-\d{2}-\d{2})', section)
            if date_match:
                correction['date'] = date_match.group(1)

            # Check for recurrence
            if 'Recurrence' in section:
                correction['recurring'] = True

            corrections.append(correction)

        return corrections

    def _parse_preferences(self, content: str) -> List[Dict]:
        """Parse preferences.md into structured entries"""
        preferences = []

        sections = re.split(r'\n## ', content)

        for section in sections[1:]:
            lines = section.split('\n')
            title = lines[0].strip()

            pref = {
                'title': title,
                'content': '\n'.join(lines[1:]).strip(),
                'type': 'preference'
            }

            preferences.append(pref)

        return preferences

    def classify_learnings(self):
        """Classify learnings according to DIP-0019 taxonomy"""
        print("\nClassifying learnings...")

        # Process patterns
        for pattern in self.patterns:
            classification = self._classify_learning(pattern)

            if classification == 'ENGRAM':
                self.engram_candidates.append(self._to_engram(pattern))
            elif classification == 'OPERATIONAL':
                self.operational.append(pattern)
            elif classification == 'DUPLICATE':
                self.duplicates.append(pattern)
            elif classification == 'OBSOLETE':
                self.obsolete.append(pattern)

        # Process corrections (mostly ENGRAM - behavioral rules)
        for correction in self.corrections:
            self.engram_candidates.append(self._to_engram(correction, engram_type='behavioral'))

        # Process preferences (mostly ENGRAM - user preferences)
        for pref in self.preferences:
            self.engram_candidates.append(self._to_engram(pref, engram_type='procedural'))

    def _classify_learning(self, learning: Dict) -> str:
        """Classify a single learning"""
        content = learning['content'].lower()
        title = learning['title'].lower()

        # OPERATIONAL: Specific procedural steps for immediate application
        operational_keywords = ['workflow', 'checklist', 'steps:', 'command', 'script']
        if any(kw in title or kw in content for kw in operational_keywords):
            if '```' in learning['content']:  # Has code blocks
                return 'OPERATIONAL'

        # OBSOLETE: Old technology or deprecated patterns
        obsolete_keywords = ['deprecated', 'old approach', 'no longer', 'replaced by']
        if any(kw in content for kw in obsolete_keywords):
            return 'OBSOLETE'

        # Check for duplicates (similar titles)
        for existing in self.engram_candidates:
            if self._is_similar(title, existing.get('title', '')):
                return 'DUPLICATE'

        # Default: ENGRAM candidate
        return 'ENGRAM'

    def _is_similar(self, title1: str, title2: str) -> bool:
        """Check if two titles are similar (potential duplicates)"""
        # Simple similarity: >80% word overlap
        words1 = set(title1.lower().split())
        words2 = set(title2.lower().split())

        if len(words1) == 0 or len(words2) == 0:
            return False

        overlap = len(words1 & words2) / max(len(words1), len(words2))
        return overlap > 0.8

    def _to_engram(self, learning: Dict, engram_type: str = None) -> Dict:
        """Convert learning to engram format"""
        # Generate engram ID
        date_str = learning.get('source_date') or learning.get('date') or datetime.now().strftime('%Y-%m-%d')
        year, month, day = date_str.split('-')

        # Determine engram type
        if not engram_type:
            if learning['type'] == 'correction':
                engram_type = 'behavioral'
            elif learning['type'] == 'preference':
                engram_type = 'procedural'
            elif 'Pattern' in learning['title']:
                engram_type = 'procedural'
            else:
                engram_type = 'behavioral'

        # Extract statement from content
        statement = self._extract_statement(learning)

        # Determine scope
        scope = self._determine_scope(learning)

        engram = {
            'id': f"ENG-{year}-{month}{day}-XXX",  # Will renumber later
            'version': 1,
            'status': 'candidate',
            'consolidated': False,
            'type': engram_type,
            'scope': scope,
            'statement': statement,
            'rationale': self._extract_rationale(learning),
            'contraindications': [],
            'source_patterns': [learning['title']],
            'derivation_count': 1,
            'activation': {
                'retrieval_strength': 0.0,
                'storage_strength': 0.5,
                'frequency': 0,
                'last_accessed': datetime.now().strftime('%Y-%m-%d')
            },
            'feedback_signals': {
                'positive': 0,
                'negative': 0,
                'neutral': 0
            },
            'provenance': {
                'origin': 'user/personal',
                'chain': [],
                'license': 'cc-by-sa-4.0'
            },
            'tags': self._extract_tags(learning),
            'abstract': None,
            'derived_from': None,
            'visibility': 'private',
            'title': learning['title'],  # Keep for reference
            'original_content': learning['content'][:200] + '...' if len(learning['content']) > 200 else learning['content']
        }

        return engram

    def _extract_statement(self, learning: Dict) -> str:
        """Extract single actionable sentence"""
        title = learning['title']
        content = learning['content']

        # Look for Pattern: line
        pattern_match = re.search(r'\*\*Pattern\*\*:\s*(.+?)(?:\n|$)', content)
        if pattern_match:
            return pattern_match.group(1).strip()

        # Look for Correction: line
        correction_match = re.search(r'\*\*Correction\*\*:\s*(.+?)(?:\n|$)', content)
        if correction_match:
            return correction_match.group(1).strip()

        # Look for Preference: line
        pref_match = re.search(r'\*\*Preference\*\*:\s*(.+?)(?:\n|$)', content)
        if pref_match:
            return pref_match.group(1).strip()

        # Fallback: use title as statement
        return title

    def _extract_rationale(self, learning: Dict) -> str:
        """Extract rationale/why this matters"""
        content = learning['content']

        # Look for Rationale: line
        rationale_match = re.search(r'(?:Rationale|Why this matters):\s*(.+?)(?:\n\n|$)', content, re.DOTALL)
        if rationale_match:
            return rationale_match.group(1).strip()

        # Look for first paragraph after pattern
        paragraphs = content.split('\n\n')
        if len(paragraphs) > 1:
            return paragraphs[1][:200]

        return "Extracted from learning patterns"

    def _determine_scope(self, learning: Dict) -> str:
        """Determine scope of engram"""
        title = learning['title'].lower()
        content = learning['content'].lower()

        # Check for specific agent mentions
        agents = ['nightshift', 'gtd', 'session-learning', 'research', 'content-writer']
        for agent in agents:
            if agent in title or agent in content:
                return f"agent:{agent}"

        # Check for specific command mentions
        commands = ['wrap-up', 'today', 'mails', 'ingest']
        for cmd in commands:
            if f"/{cmd}" in content or f"{cmd} command" in content:
                return f"command:{cmd}"

        # Check for space mentions
        if '0-personal' in content:
            return 'space:0-personal'

        # Default: global
        return 'global'

    def _extract_tags(self, learning: Dict) -> List[str]:
        """Extract relevant tags"""
        tags = []

        # Extract from title
        title_words = learning['title'].lower().split()
        tag_candidates = ['git', 'systemd', 'telegram', 'crm', 'trading', 'health',
                         'mail', 'research', 'ingest', 'dip', 'module']

        for word in title_words:
            if word in tag_candidates:
                tags.append(word)

        return tags

    def generate_report(self) -> str:
        """Generate consolidation report"""
        report = f"""# DIP-0019 Learning Consolidation Report
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Summary

Total learnings processed: {len(self.patterns) + len(self.corrections) + len(self.preferences)}
- Patterns: {len(self.patterns)}
- Corrections: {len(self.corrections)}
- Preferences: {len(self.preferences)}

## Classification Results

- **Engram Candidates**: {len(self.engram_candidates)}
- **Operational Items**: {len(self.operational)}
- **Duplicates**: {len(self.duplicates)}
- **Obsolete**: {len(self.obsolete)}

## Engram Breakdown by Type

"""
        # Count by type
        type_counts = defaultdict(int)
        scope_counts = defaultdict(int)

        for engram in self.engram_candidates:
            type_counts[engram['type']] += 1
            scope_counts[engram['scope']] += 1

        report += "### By Type\n"
        for etype, count in sorted(type_counts.items()):
            report += f"- {etype}: {count}\n"

        report += "\n### By Scope\n"
        for scope, count in sorted(scope_counts.items()):
            report += f"- {scope}: {count}\n"

        report += f"""
## Sample Engrams (Top 10)

"""
        for i, engram in enumerate(self.engram_candidates[:10]):
            report += f"""
### {i+1}. {engram['title']}

**Statement:** {engram['statement']}
**Type:** {engram['type']}
**Scope:** {engram['scope']}
**Rationale:** {engram['rationale'][:150]}...

"""

        report += f"""
## Next Steps

1. Review engram candidates for accuracy
2. Renumber engram IDs sequentially
3. Write engrams to engrams.yaml
4. Archive source patterns to absorbed.md
5. Clear patterns.md for new learnings

## Files to Update

- `engrams.yaml` - Write {len(self.engram_candidates)} candidate engrams
- `absorbed.md` - Archive source patterns
- `patterns.md` - Clear processed patterns, keep templates
- `corrections.md` - Clear processed corrections
- `preferences.md` - Clear processed preferences

"""
        return report

    def write_engrams_yaml(self, output_path: Path):
        """Write engrams to YAML file"""
        # Renumber IDs
        engrams_by_date = defaultdict(list)

        for engram in self.engram_candidates:
            date = engram['id'].split('-')[1:3]
            engrams_by_date[tuple(date)].append(engram)

        # Sort by date and renumber
        renumbered = []
        for date in sorted(engrams_by_date.keys()):
            for idx, engram in enumerate(engrams_by_date[date], 1):
                year, monthday = date
                engram['id'] = f"ENG-{year}-{monthday}-{idx:03d}"

                # Remove temp fields
                if 'title' in engram:
                    del engram['title']
                if 'original_content' in engram:
                    del engram['original_content']

                renumbered.append(engram)

        # Write YAML
        output = {
            'engrams': renumbered
        }

        with open(output_path, 'w') as f:
            f.write(f"""# Engrams - Active Memory Store (DIP-0019)
# Generated and managed by learning-reviewer and learning-absorber agents
# DO NOT EDIT MANUALLY - use /daily-review or learning agents
# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

""")
            yaml.dump(output, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        print(f"\nWrote {len(renumbered)} engrams to {output_path}")

def main():
    import os

    # Use DATACORE_ROOT or default to ~/Data
    datacore_root = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
    base_path = datacore_root / "0-personal" / ".datacore" / "learning"

    consolidator = LearningConsolidator(base_path)

    print("DIP-0019 Learning Consolidation")
    print("=" * 50)

    # Load files
    consolidator.load_learning_files()
    print(f"\nLoaded {len(consolidator.patterns)} patterns, {len(consolidator.corrections)} corrections, {len(consolidator.preferences)} preferences")

    # Classify
    consolidator.classify_learnings()

    # Generate report
    report = consolidator.generate_report()

    # Write report
    report_path = datacore_root / "0-personal" / "0-inbox" / "DIP-0019-learning-consolidation-report.md"
    with open(report_path, 'w') as f:
        f.write(report)
    print(f"\nReport written to {report_path}")

    # Write engrams
    engrams_path = base_path / "engrams.yaml"
    consolidator.write_engrams_yaml(engrams_path)

    print("\nConsolidation complete!")
    print(f"- {len(consolidator.engram_candidates)} engrams created")
    print(f"- Review report at {report_path}")

if __name__ == "__main__":
    main()
