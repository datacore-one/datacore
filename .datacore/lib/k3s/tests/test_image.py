"""Contract tests for the agent images.

Three layers: agent-base (OS, system Python, supercronic, the agent user, the
role lock and the start guard) and one image per runtime on top of it
(agent-hermes, agent-openclaw), each bringing its own Node.

The agent's duties run on the image's system Python with the agent's own user
site from its volume. On the old host part of that environment came from
Ubuntu's python3-* packages: pytest imports pygments from there, and without it
every promise eval failed (0 green, found on the first live pod).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

IMAGES = Path(__file__).resolve().parents[1] / "images"
BASE, HERMES, OPENCLAW = IMAGES / "agent-base", IMAGES / "agent-hermes", IMAGES / "agent-openclaw"


def test_system_python_has_the_libraries_the_old_host_had():
    dockerfile = (BASE / "Dockerfile").read_text()
    for pkg in ("python3-pygments", "python3-packaging", "python3-jinja2",
                "python3-dateutil", "python3-tz", "python3-jsonschema",
                "python3-rich", "python3-cryptography", "python3-icalendar"):
        assert pkg in dockerfile, pkg


def test_runtime_images_build_on_the_base_and_bring_their_own_node():
    for img, node in ((HERMES, "22.22.2"), (OPENCLAW, "24.21.0")):
        df = (img / "Dockerfile").read_text()
        assert "ARG BASE" in df and "FROM ${BASE}" in df, img.name
        assert f"NODE_VERSION={node}" in df, img.name
        assert "sha256sum -c" in df, img.name
    assert "ARG OPENCLAW_VERSION=2026.9.3" in (OPENCLAW / "Dockerfile").read_text()
    assert "openclaw@${OPENCLAW_VERSION}" in (OPENCLAW / "Dockerfile").read_text()
    assert "requirements.lock" in (HERMES / "Dockerfile").read_text()
    assert "nodejs.org" not in (BASE / "Dockerfile").read_text()


def test_role_lock_waits_for_a_pod_that_is_still_stopping():
    # A replacement pod (kill switch resume, pod deleted) starts while the old
    # one has up to 90 s to stop. Waiting for the lock starts it as soon as the
    # old one is gone; a real second replica still gives up with 75.
    run = (BASE / "agent-run").read_text()
    assert "--wait 120" in run and "--conflict-exit-code 75" in run
    assert "--nonblock" not in run


def _prepare(tmp_path, frozen: bool):
    home = tmp_path / "home"
    (home / ".datacore/state").mkdir(parents=True)
    if frozen:
        (home / ".datacore/state/k3s-cutover-frozen").write_text("fresh agent, no other host\n")
    hooks = tmp_path / "prepare.d"
    hooks.mkdir()
    (hooks / "10-mark").write_text('#!/bin/sh\ntouch "$HOME/hook-ran"\n')
    (hooks / "10-mark").chmod(0o755)
    env = dict(os.environ, HOME=str(home), AGENT_PREPARE_D=str(hooks))
    r = subprocess.run(["sh", str(BASE / "agent-prepare")], env=env, capture_output=True, text=True)
    return r, home


def test_prepare_refuses_until_the_agent_runs_nowhere_else(tmp_path):
    r, home = _prepare(tmp_path, frozen=False)
    assert r.returncode != 0 and "refusing to start" in r.stderr
    assert not (home / "hook-ran").exists()


def test_prepare_then_runs_the_runtime_hooks(tmp_path):
    r, home = _prepare(tmp_path, frozen=True)
    assert r.returncode == 0, r.stderr
    assert (home / "hook-ran").exists()


def test_hermes_hook_links_the_runtime_into_the_image():
    hook = (HERMES / "prepare.d" / "50-hermes").read_text()
    assert '"$HOME/.hermes/hermes-agent" /opt/hermes-agent' in hook
    assert '"$HOME/.hermes/node" /opt/node' in hook
