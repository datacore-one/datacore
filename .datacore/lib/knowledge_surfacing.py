#!/usr/bin/env python3
"""
Knowledge Surfacing System - Layer 1 Spaced Repetition

Surfaces recently extracted knowledge in daily briefings to ensure insights
don't languish unread. Uses state tracking to rotate through recent extractions.

State File: .datacore/state/knowledge-surfacing.yaml
Format:
  items:
    /path/to/file.md:
      last_surfaced: "2026-02-20T08:00:00"
      surface_count: 3
      first_surfaced: "2026-02-15T08:00:00"
  config:
    rotation_window_days: 30  # Only surface items from past N days
    min_days_between: 1        # Min days before re-surfacing same item
    max_surfaces_per_item: 5   # Max times to surface same item
"""

from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
import re
from state_store import YamlStateStore


class KnowledgeSurfacing:
    def __init__(self, data_root: Path):
        self.data_root = data_root
        # Discover knowledge roots from all spaces
        try:
            entries = sorted(data_root.iterdir())
        except (PermissionError, OSError):
            entries = []
        self.knowledge_roots = sorted(
            p / "3-knowledge" for p in entries
            if p.is_dir() and p.name[:1].isdigit() and (p / "3-knowledge").is_dir()
        )
        # Backwards-compatible single root (first space, usually 0-personal)
        self.knowledge_root = self.knowledge_roots[0] if self.knowledge_roots else data_root / "0-personal" / "3-knowledge"
        self._store = YamlStateStore(
            ".datacore/state/knowledge-surfacing.yaml",
            default={
                'items': {},
                'config': {
                    'rotation_window_days': 30,
                    'min_days_between': 1,
                    'max_surfaces_per_item': 5
                }
            },
            data_root=data_root,
        )
        self.state = self._store.load()

    def _get_recent_files(self) -> List[Tuple[Path, datetime]]:
        """Get knowledge files modified within rotation window"""
        window_days = self.state['config']['rotation_window_days']
        cutoff = datetime.now() - timedelta(days=window_days)

        recent = []
        for pattern in ['literature/**/*.md', 'clippings/**/*.md', 'zettel/**/*.md',
                       'topics/**/*.md', 'insights/**/*.md', 'pages/**/*.md']:
            for kr in self.knowledge_roots:
                for file in kr.glob(pattern):
                    # Skip index files, README, processing summaries
                    if file.name.startswith('INDEX') or file.name == 'README.md' or \
                       'PROCESSING_SUMMARY' in file.name or file.name.endswith('(highlights).md'):
                        continue

                    mtime = datetime.fromtimestamp(file.stat().st_mtime)
                    if mtime > cutoff:
                        recent.append((file, mtime))

        return sorted(recent, key=lambda x: x[1], reverse=True)

    def _is_eligible(self, file_path: Path) -> bool:
        """Check if file is eligible for surfacing"""
        file_str = str(file_path)

        if file_str not in self.state['items']:
            return True  # Never surfaced before

        item_state = self.state['items'][file_str]

        # Check max surface count
        if item_state.get('surface_count', 0) >= self.state['config']['max_surfaces_per_item']:
            return False

        # Check minimum days between surfaces
        last_surfaced = datetime.fromisoformat(item_state['last_surfaced'])
        days_since = (datetime.now() - last_surfaced).days

        return days_since >= self.state['config']['min_days_between']

    def _extract_excerpt(self, file_path: Path, max_lines: int = 10) -> Optional[str]:
        """Extract meaningful excerpt from file"""
        try:
            content = file_path.read_text()

            # Skip frontmatter if present
            lines = content.split('\n')
            start = 0
            if lines[0].strip() == '---':
                for i, line in enumerate(lines[1:], 1):
                    if line.strip() == '---':
                        start = i + 1
                        break

            # Extract first meaningful paragraph or list
            excerpt_lines = []
            in_excerpt = False

            for line in lines[start:]:
                line = line.strip()

                # Skip headers but capture them
                if line.startswith('#'):
                    if not in_excerpt:
                        excerpt_lines.append(line)
                        in_excerpt = True
                    continue

                # Capture content
                if line and not line.startswith('<!--'):
                    excerpt_lines.append(line)
                    if len(excerpt_lines) >= max_lines:
                        break

            return '\n'.join(excerpt_lines[:max_lines])

        except Exception as e:
            return None

    def _get_context_match(self, calendar_events: List[str] = None) -> Optional[Path]:
        """Get contextually relevant knowledge based on calendar/priorities"""
        # Future enhancement: match knowledge to today's meetings/tasks
        # For now, return None to use default rotation
        return None

    def select_daily_nugget(self, calendar_events: List[str] = None) -> Optional[Dict]:
        """
        Select one knowledge item to surface today

        Returns:
            Dict with:
                - file_path: Path to file
                - title: Extracted title
                - excerpt: Content excerpt
                - age_days: Days since file was created/modified
                - source_type: Type of knowledge (zettel, literature, clipping, etc.)
        """
        # Try context-based match first
        context_match = self._get_context_match(calendar_events)
        if context_match and self._is_eligible(context_match):
            selected = context_match
        else:
            # Default rotation: oldest unsurfaced or least recently surfaced
            recent_files = self._get_recent_files()
            eligible = [(f, mtime) for f, mtime in recent_files if self._is_eligible(f)]

            if not eligible:
                return None

            # Sort by last_surfaced (oldest first), then by mtime (newest first)
            def sort_key(item):
                file_path, mtime = item
                file_str = str(file_path)
                if file_str in self.state['items']:
                    last = datetime.fromisoformat(self.state['items'][file_str]['last_surfaced'])
                    return (last, -mtime.timestamp())
                else:
                    # Never surfaced - prioritize newest
                    return (datetime.min, -mtime.timestamp())

            selected, mtime = sorted(eligible, key=sort_key)[0]

        # Extract information
        excerpt = self._extract_excerpt(selected)
        if not excerpt:
            return None

        # Determine source type from path
        source_type = "knowledge"
        if "zettel" in str(selected):
            source_type = "zettel"
        elif "literature" in str(selected):
            source_type = "literature"
        elif "clippings" in str(selected):
            source_type = "clipping"
        elif "topics" in str(selected):
            source_type = "topic note"
        elif "insights" in str(selected):
            source_type = "insight"

        # Extract title
        title = selected.stem.replace('-', ' ').replace('_', ' ')

        # Calculate age
        file_stat = selected.stat()
        age_days = (datetime.now() - datetime.fromtimestamp(file_stat.st_mtime)).days

        # Update state
        file_str = str(selected)
        if file_str not in self.state['items']:
            self.state['items'][file_str] = {
                'first_surfaced': datetime.now().isoformat(),
                'surface_count': 0
            }

        self.state['items'][file_str]['last_surfaced'] = datetime.now().isoformat()
        self.state['items'][file_str]['surface_count'] = \
            self.state['items'][file_str].get('surface_count', 0) + 1

        self._store.save(self.state)

        # Make path relative for wiki-link
        try:
            # Try each knowledge root for relative path
            rel_path = None
            for kr in self.knowledge_roots:
                try:
                    rel_path = selected.relative_to(kr)
                    break
                except ValueError:
                    continue
            if rel_path:
                wiki_link = f"[[{rel_path.parent / rel_path.stem}]]"
            else:
                wiki_link = f"[[{selected.stem}]]"
        except (ValueError, TypeError, OSError):
            wiki_link = f"[[{selected.stem}]]"

        return {
            'file_path': selected,
            'title': title,
            'excerpt': excerpt,
            'age_days': age_days,
            'source_type': source_type,
            'wiki_link': wiki_link
        }

    def format_nugget(self, nugget: Dict) -> str:
        """Format nugget for daily briefing"""
        age_str = f"{nugget['age_days']} days ago" if nugget['age_days'] > 0 else "today"

        return f"""### Knowledge Nugget

📚 **From {nugget['source_type'].title()}** ({age_str})

**{nugget['title']}**
{nugget['excerpt']}

*Source: {nugget['wiki_link']}*
"""


def main():
    """CLI for testing"""
    import sys

    data_root = Path.home() / 'Data'
    surfacer = KnowledgeSurfacing(data_root)

    if len(sys.argv) > 1 and sys.argv[1] == 'reset':
        surfacer.state['items'] = {}
        surfacer._store.save(surfacer.state)
        print("State reset")
        return

    nugget = surfacer.select_daily_nugget()

    if nugget:
        print(surfacer.format_nugget(nugget))
    else:
        print("No knowledge items to surface")


if __name__ == '__main__':
    main()
