"""Contract tests for the agent-hermes image files.

The agent's duties run on the image's system Python with the agent's own
user site from its volume. On the old host, part of that environment came
from Ubuntu's python3-* packages: pytest imports pygments from there, and
without it every promise eval failed (0 green, found on the first live pod).
"""
from __future__ import annotations

from pathlib import Path

IMAGE = Path(__file__).resolve().parents[1] / "images" / "agent-hermes"


def test_system_python_has_the_libraries_the_old_host_had():
    dockerfile = (IMAGE / "Dockerfile").read_text()
    for pkg in ("python3-pygments", "python3-packaging", "python3-jinja2",
                "python3-dateutil", "python3-tz", "python3-jsonschema",
                "python3-rich", "python3-cryptography", "python3-icalendar"):
        assert pkg in dockerfile, pkg


def test_role_lock_waits_for_a_pod_that_is_still_stopping():
    # A replacement pod (kill switch resume, pod deleted) starts while the old
    # one has up to 90 s to stop. Waiting for the lock starts it as soon as the
    # old one is gone; a real second replica still gives up with 75.
    run = (IMAGE / "agent-run").read_text()
    assert "--wait 120" in run and "--conflict-exit-code 75" in run
    assert "--nonblock" not in run
