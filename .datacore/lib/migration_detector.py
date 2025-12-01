"""
Migration Detector for Datacore.

Tracks checksums of structure-defining DIPs (DIP-0015, DIP-0017) and alerts
when they change, prompting users to verify compliance.

Usage:
    from migration_detector import MigrationDetector

    detector = MigrationDetector(Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data"))))
    alert = detector.check_for_updates()
    if alert:
        print(alert.message)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
import hashlib
import os
import yaml


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class MigrationAlert:
    """Alert when structure definitions have changed."""
    changed_dips: List[str]
    message: str
    severity: str = 'warning'  # warning, info


@dataclass
class StructureVersion:
    """Tracks structure definition versions and audit history."""
    version: str = "1.0"
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())

    definitions: Dict[str, Dict] = field(default_factory=dict)
    # {
    #   'DIP-0015': {'checksum': 'abc123', 'last_seen': '2025-12-30'},
    #   'DIP-0017': {'checksum': 'def456', 'last_seen': '2025-12-30'},
    # }

    spaces: Dict[str, Dict] = field(default_factory=dict)
    # {
    #   '0-personal': {
    #     'last_audit': '2025-12-30T08:00:00Z',
    #     'structure_version': '2025-12-30',
    #     'issues_at_audit': 0
    #   },
    # }

    migrations: List[Dict] = field(default_factory=list)
    # [
    #   {
    #     'date': '2025-12-30',
    #     'description': 'Personal space reorganization',
    #     'spaces_affected': ['0-personal'],
    #     'completed': True
    #   },
    # ]


# =============================================================================
# DETECTOR IMPLEMENTATION
# =============================================================================

# DIPs that define structure (changes require migration check)
STRUCTURE_DIPS = ['DIP-0015', 'DIP-0017']


class MigrationDetector:
    """
    Detect when structure definitions have changed.

    Tracks checksums of structure-defining DIPs and alerts when they change.
    """

    def __init__(self, datacore_root: Path):
        self.root = Path(datacore_root).resolve()
        self.state_file = self.root / '.datacore/state/structure_version.yaml'
        self.dips_dir = self.root / '.datacore/dips'

    def check_for_updates(self) -> Optional[MigrationAlert]:
        """
        Check if DIPs have changed since last check.

        Returns:
            MigrationAlert if changes detected, None otherwise.
        """
        current = self._compute_dip_checksums()
        stored = self._load_stored_checksums()

        changes = []
        for dip, checksum in current.items():
            stored_checksum = stored.get(dip, {}).get('checksum')
            if stored_checksum != checksum:
                changes.append(dip)

        if changes:
            # Update checksums after detection
            self._update_checksums(current)

            return MigrationAlert(
                changed_dips=changes,
                message=self._format_alert_message(changes),
            )

        return None

    def _compute_dip_checksums(self) -> Dict[str, str]:
        """Compute checksums for structure-related DIPs."""
        checksums = {}

        for dip in STRUCTURE_DIPS:
            files = list(self.dips_dir.glob(f'{dip}-*.md'))
            if files:
                checksums[dip] = self._file_checksum(files[0])

        return checksums

    def _file_checksum(self, path: Path) -> str:
        """Compute MD5 checksum of a file."""
        return hashlib.md5(path.read_bytes()).hexdigest()

    def _load_stored_checksums(self) -> Dict[str, Dict]:
        """Load stored checksums from state file."""
        if not self.state_file.exists():
            return {}

        try:
            data = yaml.safe_load(self.state_file.read_text())
            return data.get('definitions', {}) if data else {}
        except Exception:
            return {}

    def _update_checksums(self, checksums: Dict[str, str]):
        """Update stored checksums in state file."""
        # Load existing state or create new
        if self.state_file.exists():
            try:
                state = yaml.safe_load(self.state_file.read_text()) or {}
            except Exception:
                state = {}
        else:
            state = {}

        # Ensure structure
        state.setdefault('version', '1.0')
        state.setdefault('definitions', {})
        state.setdefault('spaces', {})
        state.setdefault('migrations', [])

        # Update definitions
        today = datetime.now().strftime('%Y-%m-%d')
        for dip, checksum in checksums.items():
            state['definitions'][dip] = {
                'checksum': checksum,
                'last_seen': today,
            }

        state['last_updated'] = datetime.now().isoformat()

        # Ensure directory exists
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        # Write state
        self.state_file.write_text(yaml.dump(state, default_flow_style=False, sort_keys=False))

    def _format_alert_message(self, changed_dips: List[str]) -> str:
        """Format alert message for changed DIPs."""
        if len(changed_dips) == 1:
            return f"Structure definition updated: {changed_dips[0]}. Run `/structural-integrity report` to verify compliance."
        else:
            dips = ', '.join(changed_dips)
            return f"Structure definitions updated: {dips}. Run `/structural-integrity report` to verify compliance."

    # -------------------------------------------------------------------------
    # Audit History Management
    # -------------------------------------------------------------------------

    def record_audit(self, space: str, errors: int, warnings: int, infos: int,
                     trigger: str = 'manual', duration_ms: int = 0):
        """
        Record an audit result for a space.

        Args:
            space: Space name (e.g., '0-personal')
            errors: Count of error-level issues
            warnings: Count of warning-level issues
            infos: Count of info-level issues
            trigger: What triggered the audit ('today', 'manual', 'nightshift', 'weekly-review')
            duration_ms: How long the audit took
        """
        state = self._load_state()

        # Update per-space summary
        state['spaces'][space] = {
            'last_audit': datetime.now().isoformat(),
            'structure_version': datetime.now().strftime('%Y-%m-%d'),
            'errors': errors,
            'warnings': warnings,
            'infos': infos,
        }

        # Append to rolling audit history
        state.setdefault('audit_history', [])
        state['audit_history'].append({
            'timestamp': datetime.now().isoformat(),
            'trigger': trigger,
            'space': space,
            'errors': errors,
            'warnings': warnings,
            'infos': infos,
            'duration_ms': duration_ms,
        })

        # Keep only last 100 entries
        if len(state['audit_history']) > 100:
            state['audit_history'] = state['audit_history'][-100:]

        self._save_state(state)

    def record_migration(self, description: str, spaces_affected: List[str]):
        """Record a completed migration."""
        state = self._load_state()

        state['migrations'].append({
            'date': datetime.now().strftime('%Y-%m-%d'),
            'description': description,
            'spaces_affected': spaces_affected,
            'completed': True,
        })

        self._save_state(state)

    def get_last_audit(self, space: str) -> Optional[Dict]:
        """Get last audit info for a space."""
        state = self._load_state()
        return state.get('spaces', {}).get(space)

    def get_audit_history(self, space: Optional[str] = None, days: int = 7) -> List[Dict]:
        """
        Get audit history for trend analysis.

        Args:
            space: Filter to specific space, or None for all
            days: Number of days to look back

        Returns:
            List of audit entries within the time window
        """
        state = self._load_state()
        history = state.get('audit_history', [])

        # Filter by time window
        cutoff = datetime.now() - timedelta(days=days)
        filtered = []
        for entry in history:
            try:
                timestamp = datetime.fromisoformat(entry['timestamp'])
                if timestamp >= cutoff:
                    if space is None or entry.get('space') == space:
                        filtered.append(entry)
            except (ValueError, KeyError):
                continue

        return filtered

    def get_trend_summary(self, space: Optional[str] = None) -> Dict:
        """
        Get trend summary comparing this week to last week.

        Returns:
            {
                'this_week': {'errors': N, 'warnings': N, 'infos': N, 'audits': N},
                'last_week': {'errors': N, 'warnings': N, 'infos': N, 'audits': N},
                'trend': 'improving' | 'stable' | 'declining',
                'change': {'errors': +/-N, 'warnings': +/-N}
            }
        """
        this_week = self.get_audit_history(space, days=7)
        last_week_all = self.get_audit_history(space, days=14)
        last_week = [e for e in last_week_all if e not in this_week]

        def summarize(entries):
            if not entries:
                return {'errors': 0, 'warnings': 0, 'infos': 0, 'audits': 0}
            return {
                'errors': sum(e.get('errors', 0) for e in entries),
                'warnings': sum(e.get('warnings', 0) for e in entries),
                'infos': sum(e.get('infos', 0) for e in entries),
                'audits': len(entries),
            }

        tw = summarize(this_week)
        lw = summarize(last_week)

        # Determine trend based on errors and warnings
        tw_total = tw['errors'] + tw['warnings']
        lw_total = lw['errors'] + lw['warnings']

        if lw_total == 0 and tw_total == 0:
            trend = 'stable'
        elif tw_total < lw_total:
            trend = 'improving'
        elif tw_total > lw_total:
            trend = 'declining'
        else:
            trend = 'stable'

        return {
            'this_week': tw,
            'last_week': lw,
            'trend': trend,
            'change': {
                'errors': tw['errors'] - lw['errors'],
                'warnings': tw['warnings'] - lw['warnings'],
            }
        }

    def _load_state(self) -> Dict:
        """Load full state from file."""
        if not self.state_file.exists():
            return {
                'version': '1.0',
                'last_updated': datetime.now().isoformat(),
                'definitions': {},
                'spaces': {},
                'migrations': [],
            }

        try:
            state = yaml.safe_load(self.state_file.read_text()) or {}
            # Ensure all keys exist
            state.setdefault('version', '1.0')
            state.setdefault('definitions', {})
            state.setdefault('spaces', {})
            state.setdefault('migrations', [])
            return state
        except Exception:
            return {
                'version': '1.0',
                'definitions': {},
                'spaces': {},
                'migrations': [],
            }

    def _save_state(self, state: Dict):
        """Save state to file."""
        state['last_updated'] = datetime.now().isoformat()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(yaml.dump(state, default_flow_style=False, sort_keys=False))


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def format_briefing_alert(alert: Optional[MigrationAlert]) -> str:
    """Format migration alert for daily briefing."""
    if not alert:
        return ""

    lines = [
        "⚠️ **Structure Definitions Updated**",
        f"{', '.join(alert.changed_dips)} modified since last check.",
        "→ Run `/structural-integrity report` to verify compliance",
    ]
    return '\n'.join(lines)


def format_trend_summary(trend: Dict) -> str:
    """
    Format trend summary for weekly review.

    Args:
        trend: Output from MigrationDetector.get_trend_summary()

    Returns:
        Formatted markdown string
    """
    tw = trend['this_week']
    lw = trend['last_week']
    change = trend['change']

    # Trend icon
    if trend['trend'] == 'improving':
        icon = '📈'
    elif trend['trend'] == 'declining':
        icon = '📉'
    else:
        icon = '➡️'

    lines = [
        f"## Structural Integrity Trends {icon}",
        "",
        "| Metric | Last Week | This Week | Change |",
        "|--------|-----------|-----------|--------|",
    ]

    def fmt_change(val):
        if val > 0:
            return f"+{val} ⬆"
        elif val < 0:
            return f"{val} ⬇"
        return "0"

    lines.append(f"| Errors | {lw['errors']} | {tw['errors']} | {fmt_change(change['errors'])} |")
    lines.append(f"| Warnings | {lw['warnings']} | {tw['warnings']} | {fmt_change(change['warnings'])} |")
    lines.append(f"| Audits | {lw['audits']} | {tw['audits']} | - |")
    lines.append("")

    if trend['trend'] == 'improving':
        lines.append("✅ **Trend: Improving** - Issues are decreasing")
    elif trend['trend'] == 'declining':
        lines.append("⚠️ **Trend: Declining** - Issues are increasing, review needed")
    else:
        lines.append("➡️ **Trend: Stable** - No significant change")

    return '\n'.join(lines)


# =============================================================================
# CLI INTERFACE
# =============================================================================

if __name__ == '__main__':
    import sys

    data_root = Path.home() / 'Data'

    detector = MigrationDetector(data_root)

    if '--record-migration' in sys.argv:
        # Record a migration
        idx = sys.argv.index('--record-migration')
        desc = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "Manual migration"
        detector.record_migration(desc, ['0-personal'])
        print(f"Recorded migration: {desc}")
    else:
        # Check for updates
        alert = detector.check_for_updates()
        if alert:
            print(f"ALERT: {alert.message}")
        else:
            print("No structure definition changes detected.")

        # Show state
        state = detector._load_state()
        print(f"\nState file: {detector.state_file}")
        print(f"Last updated: {state.get('last_updated', 'never')}")
        print(f"DIPs tracked: {list(state.get('definitions', {}).keys())}")
