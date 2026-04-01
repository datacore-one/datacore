"""Campaign metrics dashboard — aggregates engagement + follow data.

Outputs a human-readable summary for /today briefings and Telegram.
"""
import json
from pathlib import Path
from datetime import datetime, timezone

import engagement_state as state_mod


class CampaignDashboard:
    def __init__(self, state_file: Path, follow_db=None):
        self.state_file = state_file
        self.follow_db = follow_db

    def generate(self, days: int = 7) -> str:
        """Generate campaign metrics summary."""
        lines = ["## Campaign Dashboard\n"]

        # Engagement metrics
        st, _ = state_mod.load(self.state_file)
        posted = st.get('posted', [])
        pending = st.get('pending', [])

        total_posted = len(posted)
        auto_posted = sum(1 for p in posted if p.get('mode') == 'autonomous')
        manual_posted = total_posted - auto_posted

        lines.append("### Engagement")
        lines.append(f"- Total posted: {total_posted} "
                     f"({auto_posted} autonomous, {manual_posted} manual)")
        lines.append(f"- Pending approval: {len(pending)}")

        # Daily stats
        daily = st.get('daily_stats', {})
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        today_stats = daily.get(today, {})
        if today_stats:
            lines.append(f"- Today: {today_stats.get('posted', 0)} posted, "
                        f"{today_stats.get('drafted', 0)} drafted")

        # Follow metrics
        if self.follow_db:
            stats = self.follow_db.stats()
            lines.append("\n### Follow List")
            lines.append(f"- Total: {stats['total']}")
            by_status = stats.get('by_status', {})
            lines.append(
                f"- Pending: {by_status.get('pending', 0)} | "
                f"Followed: {by_status.get('followed', 0)} | "
                f"Mutual: {by_status.get('mutual', 0)} | "
                f"Unfollowed: {by_status.get('unfollowed', 0)}"
            )
            by_cluster = stats.get('by_cluster', {})
            if by_cluster:
                lines.append(f"- Clusters: {', '.join(f'{k}: {v}' for k, v in sorted(by_cluster.items()))}")

        # Kill switch status
        kill_switch = self.state_file.parent / "campaign-kill-switch"
        if kill_switch.exists():
            lines.append(f"\n### !! KILL SWITCH ACTIVE !!")
            lines.append(f"Reason: {kill_switch.read_text().strip()}")

        return "\n".join(lines)
