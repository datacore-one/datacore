"""Tests for the fleet kill switch (bin/fleetctl).

stop-all must remember each agent's replica count before scaling it to zero, so
resume-all brings back exactly what was running -- and a second stop-all must
not overwrite that memory with the zero it left behind.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

FLEETCTL = Path(__file__).resolve().parents[1] / "bin" / "fleetctl"

FAKE_KUBECTL = r'''#!/usr/bin/env python3
import json, os, sys
state_path = os.environ["FAKE_STATE"]
log_path = os.environ["FAKE_LOG"]
state = json.load(open(state_path))
args = sys.argv[1:]
open(log_path, "a").write(json.dumps(args) + "\n")
def find(ns, name):
    for d in state:
        if d["ns"] == ns and d["name"] == name:
            return d
    sys.exit(f"not found {ns}/{name}")
if args[:2] == ["get", "deploy"]:
    items = [{"metadata": {"namespace": d["ns"], "name": d["name"],
                           "annotations": d.get("ann", {}),
                           "labels": {"datacore.one/agent": d["name"]}},
              "spec": {"replicas": d["replicas"]},
              "status": {"readyReplicas": d["replicas"]}} for d in state]
    print(json.dumps({"items": items}))
elif args[0] == "annotate":
    d = find(args[args.index("-n") + 1], args[1].split("/")[1])
    for a in args[2:]:
        if "=" in a and not a.startswith("-"):
            k, v = a.split("=", 1); d.setdefault("ann", {})[k] = v
        elif a.endswith("-") and not a.startswith("-"):
            d.get("ann", {}).pop(a[:-1], None)
elif args[0] == "scale":
    d = find(args[args.index("-n") + 1], args[1].split("/")[1])
    d["replicas"] = int([a for a in args if a.startswith("--replicas=")][0].split("=")[1])
json.dump(state, open(state_path, "w"))
'''


def setup(tmp_path, deployments):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    k = bindir / "kubectl"
    k.write_text(FAKE_KUBECTL)
    k.chmod(0o755)
    state = tmp_path / "state.json"
    state.write_text(json.dumps(deployments))
    log = tmp_path / "log.jsonl"
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
               FAKE_STATE=str(state), FAKE_LOG=str(log))
    return env, state, log


def run(env, *args):
    return subprocess.run([sys.executable, str(FLEETCTL), *args], env=env,
                          capture_output=True, text=True)


def calls(log):
    return [json.loads(l) for l in log.read_text().splitlines()]


def test_stop_all_records_replicas_then_scales_to_zero(tmp_path):
    env, state, log = setup(tmp_path, [{"ns": "firm", "name": "tris", "replicas": 1}])
    r = run(env, "stop-all")
    assert r.returncode == 0, r.stderr
    d = json.loads(state.read_text())[0]
    assert d["replicas"] == 0
    assert d["ann"]["datacore.one/resume-replicas"] == "1"
    verbs = [c[0] for c in calls(log)]
    assert verbs.index("annotate") < verbs.index("scale"), "remember before stopping"


def test_second_stop_all_keeps_the_remembered_count(tmp_path):
    env, state, log = setup(tmp_path, [{"ns": "firm", "name": "tris", "replicas": 1}])
    run(env, "stop-all")
    run(env, "stop-all")
    d = json.loads(state.read_text())[0]
    assert d["ann"]["datacore.one/resume-replicas"] == "1"


def test_resume_all_restores_and_forgets(tmp_path):
    env, state, _ = setup(tmp_path, [
        {"ns": "firm", "name": "tris", "replicas": 1},
        {"ns": "firm", "name": "data", "replicas": 0},
    ])
    run(env, "stop-all")
    r = run(env, "resume-all")
    assert r.returncode == 0, r.stderr
    by = {d["name"]: d for d in json.loads(state.read_text())}
    assert by["tris"]["replicas"] == 1
    assert by["data"]["replicas"] == 0, "an agent that was off stays off"
    assert "datacore.one/resume-replicas" not in by["tris"].get("ann", {})


def test_status_lists_every_agent(tmp_path):
    env, _, _ = setup(tmp_path, [{"ns": "firm", "name": "tris", "replicas": 1}])
    r = run(env, "status")
    assert r.returncode == 0
    assert "firm/tris" in r.stdout and "running" in r.stdout


def test_namespaces_limit_the_query_for_a_namespaced_operator(tmp_path):
    # Winston's kill-switch credential may only touch the team namespaces, so
    # fleetctl must not ask the API for deployments across all namespaces.
    env, state, log = setup(tmp_path, [{"ns": "firm", "name": "tris", "replicas": 1}])
    env["FLEET_NAMESPACES"] = "firm"
    r = run(env, "stop-all")
    assert r.returncode == 0, r.stderr
    gets = [c for c in calls(log) if c[:2] == ["get", "deploy"]]
    assert gets and all("-A" not in c and c[c.index("-n") + 1] == "firm" for c in gets)
    assert json.loads(state.read_text())[0]["replicas"] == 0


def test_kubectl_failure_is_reported_not_swallowed(tmp_path):
    env, _, _ = setup(tmp_path, [{"ns": "firm", "name": "tris", "replicas": 1}])
    env["FAKE_STATE"] = str(tmp_path / "missing.json")
    r = run(env, "stop-all")
    assert r.returncode != 0
