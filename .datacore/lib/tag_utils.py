#!/usr/bin/env python3
"""
Tag utilities for Datacore.

Core functions for tag normalization, validation, registry loading,
and cross-system conversion. Used by all agents per DIP-0014.

Usage:
    from tag_utils import normalize_tag, load_registry, org_to_inline
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set
import yaml


def normalize_tag(tag: str) -> str:
    """
    Normalize any tag to canonical kebab-case form.

    Per DIP-0014: all tags use lowercase, hyphen-separated format.

    Examples:
        >>> normalize_tag("Privacy Tech")
        'privacy-tech'
        >>> normalize_tag("privacy_tech")
        'privacy-tech'
        >>> normalize_tag("PrivacyTech")
        'privacy-tech'
        >>> normalize_tag("zk-STARKs")
        'zk-starks'
    """
    if not tag:
        return ""

    # Handle camelCase/PascalCase by inserting hyphens before capitals
    normalized = re.sub(r'([a-z])([A-Z])', r'\1-\2', tag)

    # Convert to lowercase
    normalized = normalized.lower().strip()

    # Replace spaces and underscores with hyphens
    normalized = re.sub(r'[\s_]+', '-', normalized)

    # Collapse multiple hyphens
    normalized = re.sub(r'-+', '-', normalized)

    # Strip leading/trailing hyphens
    return normalized.strip('-')


def load_registry(data_root: Path) -> Dict:
    """
    Load and merge all tag registries from discovery locations.

    Priority order (highest first):
    1. .datacore/tags.yaml - System-wide reserved tags
    2. [space]/.datacore/tags.yaml - Space-specific tags
    3. .datacore/modules/[module]/tags.yaml - Module-contributed tags

    Returns merged registry with all tags.
    """
    registry = {
        'namespaces': {},
        'tags': {},
        'synonyms': {}
    }

    # 1. System registry (highest priority)
    system_registry = data_root / '.datacore' / 'tags.yaml'
    if system_registry.exists():
        _merge_registry(registry, system_registry)

    # 2. Space registries
    for space_dir in data_root.glob('[0-9]-*'):
        space_registry = space_dir / '.datacore' / 'tags.yaml'
        if space_registry.exists():
            _merge_registry(registry, space_registry)

    # 3. Module registries
    modules_dir = data_root / '.datacore' / 'modules'
    if modules_dir.exists():
        for module_dir in modules_dir.iterdir():
            if module_dir.is_dir():
                module_registry = module_dir / 'tags.yaml'
                if module_registry.exists():
                    _merge_registry(registry, module_registry)

    return registry


def _merge_registry(registry: Dict, registry_path: Path) -> None:
    """Merge a registry file into the main registry."""
    try:
        with open(registry_path) as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, IOError):
        return

    # Merge namespaces
    for ns_name, ns_data in data.get('namespaces', {}).items():
        if ns_name not in registry['namespaces']:
            registry['namespaces'][ns_name] = ns_data
        else:
            # Merge tags within namespace
            existing_tags = registry['namespaces'][ns_name].get('tags', [])
            new_tags = ns_data.get('tags', [])
            for tag in new_tags:
                tag_id = tag.get('id') if isinstance(tag, dict) else tag
                if not any((t.get('id') if isinstance(t, dict) else t) == tag_id for t in existing_tags):
                    existing_tags.append(tag)
            registry['namespaces'][ns_name]['tags'] = existing_tags

    # Merge flat tags
    for category, tags in data.get('tags', {}).items():
        if category not in registry['tags']:
            registry['tags'][category] = []

        for tag in tags:
            tag_id = tag.get('id') if isinstance(tag, dict) else tag
            normalized = normalize_tag(tag_id)

            # Add to tags list if not exists
            if normalized not in [normalize_tag(t.get('id') if isinstance(t, dict) else t)
                                   for t in registry['tags'][category]]:
                registry['tags'][category].append(tag)

            # Build synonym mapping
            if isinstance(tag, dict) and 'synonyms' in tag:
                for syn in tag['synonyms']:
                    registry['synonyms'][normalize_tag(syn)] = normalized


def get_all_tags(registry: Dict) -> Set[str]:
    """Get all canonical tag IDs from registry."""
    tags = set()

    # From namespaces
    for ns_data in registry.get('namespaces', {}).values():
        for tag in ns_data.get('tags', []):
            tag_id = tag.get('id') if isinstance(tag, dict) else tag
            tags.add(normalize_tag(tag_id))

    # From flat tags
    for category_tags in registry.get('tags', {}).values():
        for tag in category_tags:
            tag_id = tag.get('id') if isinstance(tag, dict) else tag
            tags.add(normalize_tag(tag_id))

    return tags


def validate_tag(tag: str, registry: Dict) -> Tuple[bool, Optional[str]]:
    """
    Validate tag exists in registry.

    Returns (is_valid, canonical_form_or_suggestion).
    - If valid: (True, canonical_form)
    - If synonym: (True, canonical_form)
    - If similar: (False, suggestion)
    - If unknown: (False, None)
    """
    normalized = normalize_tag(tag)
    all_tags = get_all_tags(registry)
    synonyms = registry.get('synonyms', {})

    # Direct match
    if normalized in all_tags:
        return True, normalized

    # Synonym match
    if normalized in synonyms:
        return True, synonyms[normalized]

    # Fuzzy match (simple Levenshtein)
    for known_tag in all_tags:
        if _levenshtein_distance(normalized, known_tag) <= 2:
            return False, known_tag

    return False, None


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def org_to_inline(org_tags: str) -> str:
    """
    Convert org-mode tags to inline hashtag format.

    Examples:
        >>> org_to_inline(':project-alpha:ops:legal:')
        '#project-alpha, #ops, #legal'
        >>> org_to_inline(':AI:research:')
        '#ai, #research'
    """
    if not org_tags:
        return ""

    # Extract tags between colons
    tags = [t for t in org_tags.split(':') if t and t != 'AI']

    # Normalize and format
    normalized = [normalize_tag(t) for t in tags if t]
    return format_inline_tags(normalized)


def inline_to_org(inline_tags: str) -> str:
    """
    Convert inline hashtags to org-mode format.

    Examples:
        >>> inline_to_org('#project-alpha, #ops, #legal')
        ':project-alpha:ops:legal:'
    """
    tags = extract_inline_tags(inline_tags)
    if not tags:
        return ""

    normalized = [normalize_tag(t) for t in tags]
    return ':' + ':'.join(normalized) + ':'


def extract_inline_tags(content: str) -> List[str]:
    """
    Extract #tags from markdown content.

    Examples:
        >>> extract_inline_tags('#privacy-tech, #project-alpha, #fhe')
        ['privacy-tech', 'project-alpha', 'fhe']
        >>> extract_inline_tags('Some text #tag1 more text #tag2')
        ['tag1', 'tag2']
    """
    # Match #tag patterns (alphanumeric with hyphens)
    pattern = r'#([a-zA-Z][a-zA-Z0-9-]*)'
    matches = re.findall(pattern, content)
    return [normalize_tag(m) for m in matches]


def format_inline_tags(tags: List[str]) -> str:
    """
    Format tags as inline hashtag string.

    Examples:
        >>> format_inline_tags(['privacy-tech', 'project-alpha', 'fhe'])
        '#privacy-tech, #project-alpha, #fhe'
    """
    if not tags:
        return ""

    normalized = [normalize_tag(t) for t in tags if t]
    unique = list(dict.fromkeys(normalized))  # Preserve order, remove duplicates
    return ', '.join(f'#{t}' for t in unique)


def suggest_tags(content: str, registry: Dict, limit: int = 5) -> List[str]:
    """
    Suggest relevant tags from registry based on content.

    Simple keyword matching - for AI-powered suggestions, use tag-suggester agent.
    """
    all_tags = get_all_tags(registry)
    content_lower = content.lower()

    matches = []
    for tag in all_tags:
        # Check if tag or variations appear in content
        if tag in content_lower:
            matches.append((tag, 1.0))
        elif tag.replace('-', ' ') in content_lower:
            matches.append((tag, 0.9))
        elif tag.replace('-', '') in content_lower:
            matches.append((tag, 0.8))

    # Sort by score and return top matches
    matches.sort(key=lambda x: x[1], reverse=True)
    return [m[0] for m in matches[:limit]]


def merge_tags(existing: List[str], suggested: List[str]) -> List[str]:
    """
    Merge existing and suggested tags, removing duplicates.

    Normalizes all tags and preserves order (existing first).
    """
    normalized_existing = [normalize_tag(t) for t in existing if t]
    normalized_suggested = [normalize_tag(t) for t in suggested if t]

    # Combine, preserving order
    merged = list(dict.fromkeys(normalized_existing + normalized_suggested))
    return merged


if __name__ == '__main__':
    # Test/demo
    import sys

    if len(sys.argv) > 1:
        data_root = Path(sys.argv[1])
    else:
        data_root = Path.home() / 'Data'

    print(f"Loading registries from {data_root}...")
    registry = load_registry(data_root)

    all_tags = get_all_tags(registry)
    print(f"Found {len(all_tags)} unique tags")

    # Test normalization
    test_tags = ['Privacy Tech', 'privacy_tech', 'PrivacyTech', 'zk-STARKs']
    print("\nNormalization tests:")
    for tag in test_tags:
        print(f"  {tag} → {normalize_tag(tag)}")

    # Test org conversion
    print("\nOrg conversion:")
    print(f"  :project-alpha:ops:legal: → {org_to_inline(':project-alpha:ops:legal:')}")
    print(f"  #project-alpha, #ops → {inline_to_org('#project-alpha, #ops')}")
