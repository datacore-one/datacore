"""Tests for follow list database."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))


class TestFollowDB:
    """SQLite follow database stores and queries follow targets."""

    def test_add_and_retrieve_account(self, tmp_path):
        from follow_db import FollowDB
        db = FollowDB(tmp_path / "test.db")
        db.add_account(
            user_id="123", username="privacydev",
            display_name="Privacy Dev",
            followers=5000, following=800,
            bio="Building privacy tools",
            cluster="developer",
            source_anchor="@EFF",
            overlap_count=1,
        )
        acct = db.get_account("123")
        assert acct['username'] == "privacydev"
        assert acct['cluster'] == "developer"
        assert acct['status'] == 'pending'

    def test_overlap_scoring(self, tmp_path):
        from follow_db import FollowDB
        db = FollowDB(tmp_path / "test.db")
        db.add_account(user_id="1", username="a", cluster="dev",
                       source_anchor="@x", overlap_count=3,
                       followers=1000, following=500)
        db.add_account(user_id="2", username="b", cluster="dev",
                       source_anchor="@y", overlap_count=1,
                       followers=1000, following=500)
        top = db.get_top_targets(limit=2)
        # Higher overlap = ranked first
        assert top[0]['username'] == 'a'

    def test_mark_followed(self, tmp_path):
        from follow_db import FollowDB
        db = FollowDB(tmp_path / "test.db")
        db.add_account(user_id="1", username="a", cluster="dev",
                       source_anchor="@x", overlap_count=1,
                       followers=1000, following=500)
        db.mark_followed("1")
        acct = db.get_account("1")
        assert acct['status'] == 'followed'

    def test_mark_follow_back(self, tmp_path):
        from follow_db import FollowDB
        db = FollowDB(tmp_path / "test.db")
        db.add_account(user_id="1", username="a", cluster="dev",
                       source_anchor="@x", overlap_count=1,
                       followers=1000, following=500)
        db.mark_followed("1")
        db.mark_follow_back("1")
        acct = db.get_account("1")
        assert acct['status'] == 'mutual'

    def test_get_stale_follows(self, tmp_path):
        from follow_db import FollowDB
        db = FollowDB(tmp_path / "test.db")
        db.add_account(user_id="1", username="a", cluster="dev",
                       source_anchor="@x", overlap_count=1,
                       followers=1000, following=500)
        db.mark_followed("1", followed_at="2020-01-01T00:00:00Z")
        stale = db.get_stale_follows(days=30)
        assert len(stale) == 1

    def test_cluster_diversity_in_daily_batch(self, tmp_path):
        from follow_db import FollowDB
        db = FollowDB(tmp_path / "test.db")
        for i, cluster in enumerate(["privacy", "web3", "journalist", "dev", "user"]):
            for j in range(20):
                db.add_account(
                    user_id=f"{cluster}_{j}", username=f"{cluster}{j}",
                    cluster=cluster, source_anchor="@x", overlap_count=1,
                    followers=1000, following=500,
                )
        batch = db.get_daily_batch(size=25)
        clusters_in_batch = set(a['cluster'] for a in batch)
        # Weak ties: batch should span at least 4 of 5 clusters
        assert len(clusters_in_batch) >= 4

    def test_stats(self, tmp_path):
        from follow_db import FollowDB
        db = FollowDB(tmp_path / "test.db")
        db.add_account(user_id="1", username="a", cluster="dev",
                       source_anchor="@x", overlap_count=1,
                       followers=1000, following=500)
        db.add_account(user_id="2", username="b", cluster="privacy",
                       source_anchor="@y", overlap_count=2,
                       followers=2000, following=300)
        stats = db.stats()
        assert stats['total'] == 2
        assert stats['by_cluster']['dev'] == 1
        assert stats['by_cluster']['privacy'] == 1
