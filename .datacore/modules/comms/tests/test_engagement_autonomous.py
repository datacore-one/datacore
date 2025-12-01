# .datacore/modules/comms/tests/test_engagement_autonomous.py
"""Tests for autonomous mode integration in engagement engine."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))


class TestAutonomousIntegration:
    """Engagement engine in autonomous mode wires guardrails + poster + state."""

    def test_autonomous_mode_posts_clean_draft(self, tmp_path):
        """When guardrails pass, draft is auto-posted and recorded in state."""
        from autonomous_poster import AutonomousPoster
        from guardrails import ContentGuardrails, GuardrailResult
        import engagement_state as state_mod

        state_file = tmp_path / "state.json"
        poster = MagicMock()
        poster.reply.return_value = {'data': {'id': 'posted_123'}}
        notifier = MagicMock()
        guardrails = MagicMock()
        guardrails.check.return_value = GuardrailResult(passed=True, violations=[])

        ap = AutonomousPoster(
            poster=poster, notifier=notifier, voice_yaml_path=None,
            guardrails=guardrails, state_mod=state_mod, state_file=state_file,
            state_dir=str(tmp_path),
        )
        result = ap.process_draft("Clean reply.", "tweet_999", "@author")

        assert result['action'] == 'posted'
        poster.reply.assert_called_once_with("Clean reply.", "tweet_999")
        notifier.notify_posted.assert_called_once()

        # Verify state was updated
        st, _ = state_mod.load(state_file)
        assert len(st.get('posted', [])) == 1
        assert st['posted'][0]['mode'] == 'autonomous'

    def test_autonomous_mode_escalates_dirty_draft(self, tmp_path):
        """When guardrails fail, draft goes to Telegram approval."""
        from autonomous_poster import AutonomousPoster
        from guardrails import GuardrailResult

        poster = MagicMock()
        notifier = MagicMock()
        guardrails = MagicMock()
        guardrails.check.return_value = GuardrailResult(
            passed=False, violations=["Anti-pattern: WAGMI"]
        )

        ap = AutonomousPoster(
            poster=poster, notifier=notifier, voice_yaml_path=None,
            guardrails=guardrails,
            state_dir=str(tmp_path),
        )
        result = ap.process_draft("WAGMI friends!", "tweet_999", "@author")

        assert result['action'] == 'escalated'
        poster.reply.assert_not_called()
        notifier.escalate_for_approval.assert_called_once()

    def test_kill_switch_halts_autonomous(self, tmp_path):
        """Kill switch stops autonomous posting immediately."""
        from x_poster import XPoster, KillSwitchActive, KILL_SWITCH_PATH
        import os

        # Create kill switch file
        kill_file = tmp_path / "campaign-kill-switch"
        kill_file.write_text("Emergency stop - suspicious activity detected")

        env = {
            'FDS_X_API_KEY': 'k', 'FDS_X_API_SECRET': 's',
            'FDS_X_ACCESS_TOKEN': 't', 'FDS_X_ACCESS_TOKEN_SECRET': 'ts',
        }
        with patch.dict(os.environ, env, clear=False):
            with patch('x_poster.KILL_SWITCH_PATH', kill_file):
                poster = XPoster(account='fds', state_dir=str(tmp_path))
                import pytest
                with pytest.raises(KillSwitchActive, match="Emergency stop"):
                    poster.post("test")
