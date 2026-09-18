"""The podcast failure names which of the two faults it actually hit."""
import importlib.util
import pathlib
import sys

import pytest

# Same stub as test_queue_drain: the orchestrator imports the agent SDK at module
# level, and the classification under test never calls a model.
from unittest.mock import MagicMock

LIB = pathlib.Path(__file__).resolve().parents[1]
for name in ("claude_agent_sdk", "claude_agent_sdk.types"):
    sys.modules.setdefault(name, MagicMock())
spec = importlib.util.spec_from_file_location("ro_audio", LIB / "research_orchestrator.py")
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)
audio_failure_reason = R.audio_failure_reason


def test_a_rejected_rpc_is_the_client_not_the_credential():
    """2026-09-18: NotebookLM rejected the audio call identically on winston and
    on the Mac with fresh native auth -- the session had to work to be told its
    arguments were invalid -- while three weeks of alerts said 'refresh auth'."""
    reason = audio_failure_reason(
        "nlm: create audio overview: CreateAudioOverview: execute rpc: One or more "
        "arguments are invalid.\nnlm: exit-class=bad-args (exit 2)")
    assert reason and 'refreshing auth cannot change' in reason
    # And it must not send them to upgrade either: checked 2026-09-18, the
    # installed binary is already past upstream's last push.
    assert 'upstream HEAD' in reason and 'Nor can upgrading' in reason
    assert 'sources are created and usable' in reason


def test_an_unusable_session_still_sends_the_owner_to_the_mac():
    reason = audio_failure_reason("nlm: cached browser session is no longer usable")
    assert reason and 'nlm_auth_sync.py sync' in reason


@pytest.mark.parametrize("detail", ["", "some transient network blip", "connection reset by peer"])
def test_an_unclassified_failure_claims_nothing(detail):
    assert audio_failure_reason(detail) is None
