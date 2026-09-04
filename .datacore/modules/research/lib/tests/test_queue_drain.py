import importlib.util, pathlib, sys, types
LIB = pathlib.Path(__file__).resolve().parents[1]
# The orchestrator imports the agent SDK at module level; the queue logic under
# test does not need it. Stub it so the module loads in a plain test env.
from unittest.mock import MagicMock
for name in ("claude_agent_sdk", "claude_agent_sdk.types"):
    if name not in sys.modules:
        sys.modules[name] = MagicMock()
spec = importlib.util.spec_from_file_location("ro", LIB / "research_orchestrator.py")
R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)


def test_unfetchable_item_is_parked_after_three_attempts(tmp_path, monkeypatch):
    org = tmp_path / "research_learning.org"
    heading = "** TODO Read: paywalled thing (Bloomberg)"
    org.write_text("* Queue\n" + heading + "\n    :PROPERTIES:\n    :URL: https://x\n    :END:\n** TODO Read: another\n")
    monkeypatch.setattr(R, "RESEARCH_ORG", org)
    item = {"heading_line": heading, "title": "Read: paywalled thing", "url": "https://x"}
    assert R.note_fetch_failure(item) == 1
    assert R.note_fetch_failure(item) == 2
    assert "** TODO Read: paywalled thing" in org.read_text(), "two failures keep it TODO"
    assert R.note_fetch_failure(item) == 3
    text = org.read_text()
    assert "** WAITING Read: paywalled thing (Bloomberg)" in text
    assert ":RESULT: unfetchable after 3 attempts" in text
    assert ":FETCH_ATTEMPTS: 3" in text
    assert "** TODO Read: another" in text, "the neighbour is untouched"
