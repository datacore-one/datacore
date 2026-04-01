"""Tests for campaign dashboard metrics."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))


class TestCampaignDashboard:
    """Dashboard aggregates metrics from engagement state + follow DB."""

    def test_renders_basic_metrics(self, tmp_path):
        from campaign_dashboard import CampaignDashboard
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({
            "pending": [{"id": "1"}, {"id": "2"}],
            "posted": [
                {"posted_at": "2026-03-04T10:00:00Z", "mode": "autonomous"},
                {"posted_at": "2026-03-04T11:00:00Z", "mode": "manual"},
            ],
            "daily_stats": {"2026-03-04": {"posted": 2, "drafted": 5}},
        }))

        from follow_db import FollowDB
        db = FollowDB(tmp_path / "follow.db")
        db.add_account(user_id="1", username="a", cluster="dev",
                       source_anchor="@x", overlap_count=1,
                       followers=1000, following=500)
        db.mark_followed("1")

        dash = CampaignDashboard(
            state_file=state_file, follow_db=db,
        )
        report = dash.generate()
        assert 'posted' in report.lower()
        assert 'autonomous' in report.lower()
        assert 'follow' in report.lower()
