"""Contract tests for the fleet Helm chart.

The chart is the boundary between an agent and everything else on its node, so
these tests pin the properties the migration plan depends on rather than the
exact YAML: no host paths, all egress through the allow-list proxy, a single
writer per agent, the signing key read-only, and a kill switch that is a plain
replica count.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[1] / "chart" / "fleet"
VALUES = Path(__file__).resolve().parent / "fixtures" / "values-test.yaml"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def render(*extra: str) -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "fleet", str(CHART), "-f", str(VALUES), *extra],
        check=True, capture_output=True, text=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def of_kind(docs, kind):
    return [d for d in docs if d["kind"] == kind]


def agent_deployments(docs):
    return [d for d in of_kind(docs, "Deployment")
            if "datacore.one/agent" in d["metadata"].get("labels", {})]


def test_no_host_paths_anywhere():
    for d in of_kind(render(), "Deployment"):
        for v in d["spec"]["template"]["spec"].get("volumes", []):
            assert "hostPath" not in v, (d["metadata"]["name"], v)


def test_every_agent_container_goes_through_the_proxy():
    deps = agent_deployments(render())
    assert deps, "no agent deployment rendered"
    for d in deps:
        for c in d["spec"]["template"]["spec"]["containers"]:
            env = {e["name"]: e.get("value") for e in c.get("env", [])}
            for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
                assert env.get(name) == "http://egress.shared.svc.cluster.local:3128", (c["name"], name)
            assert env.get("NODE_USE_ENV_PROXY") == "1", c["name"]
            assert "localhost" in env.get("NO_PROXY", ""), c["name"]


def test_python3_on_path_is_the_system_python():
    # The agent's scheduled jobs import packages installed for /usr/bin/python3
    # (and the user site in ~/.local); Hermes's own venv must not shadow it.
    for d in agent_deployments(render()):
        for c in d["spec"]["template"]["spec"]["containers"]:
            path = {e["name"]: e.get("value") for e in c["env"]}["PATH"].split(":")
            assert path.index("/usr/bin") < path.index("/opt/hermes-agent/venv/bin"), c["name"]


def test_agent_network_policy_allows_only_dns_and_proxy():
    docs = render()
    pols = [p for p in of_kind(docs, "NetworkPolicy")
            if p["metadata"]["namespace"] == "firm"]
    assert len(pols) == 1
    spec = pols[0]["spec"]
    assert spec["podSelector"] == {"matchLabels": {"datacore.one/role": "agent"}}
    assert set(spec["policyTypes"]) == {"Ingress", "Egress"}
    assert spec.get("ingress", []) == []
    targets = []
    for rule in spec["egress"]:
        for to in rule["to"]:
            assert "ipBlock" not in to, "agents must not get direct IP egress"
        targets.append((rule["to"], sorted((p["protocol"], p["port"]) for p in rule["ports"])))
    proxy = [t for t in targets if t[1] == [("TCP", 3128)]]
    dns = [t for t in targets if t[1] == [("TCP", 53), ("UDP", 53)]]
    assert len(proxy) == 1 and len(dns) == 1 and len(targets) == 2
    assert proxy[0][0] == [{
        "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "shared"}},
        "podSelector": {"matchLabels": {"app.kubernetes.io/name": "egress"}},
    }]


def test_proxy_accepts_only_the_team_namespace():
    pols = [p for p in of_kind(render(), "NetworkPolicy")
            if p["metadata"]["namespace"] == "shared"]
    assert len(pols) == 1
    ing = pols[0]["spec"]["ingress"]
    assert ing == [{
        "from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "firm"}}}],
        "ports": [{"protocol": "TCP", "port": 3128}],
    }]


def test_single_writer_and_least_privilege():
    for d in agent_deployments(render()):
        spec = d["spec"]
        assert spec["replicas"] == 1
        assert spec["strategy"] == {"type": "Recreate"}, "two pods of one agent must never overlap"
        pod = spec["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        assert pod["securityContext"]["runAsUser"] == 1000
        assert pod["securityContext"]["runAsNonRoot"] is True
        for c in pod["containers"]:
            assert c["securityContext"]["allowPrivilegeEscalation"] is False
            assert "ephemeral-storage" in c["resources"]["limits"], c["name"]
            assert "memory" in c["resources"]["limits"], c["name"]


def test_home_root_belongs_to_the_agent_and_is_not_world_writable():
    # The provisioner creates the volume root root-owned and 0777, and the
    # volume root IS the agent's home: the private-state check then refuses
    # to run (found on the first live pod). One root step fixes only that
    # directory, with nothing but CHOWN, before anything else starts.
    for d in agent_deployments(render()):
        pod = d["spec"]["template"]["spec"]
        first = pod["initContainers"][0]
        assert first["name"] == "home-owner"
        sc = first["securityContext"]
        assert sc["runAsUser"] == 0 and sc["runAsNonRoot"] is False
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["readOnlyRootFilesystem"] is True
        assert sc["capabilities"] == {"drop": ["ALL"], "add": ["CHOWN"]}
        assert [m["name"] for m in first["volumeMounts"]] == ["home", "tmp"]
        # The emptyDir /tmp is 0777 without the sticky bit; the same check
        # refuses that too (every promise eval using a temp dir went red).
        assert first["command"][-1].endswith("chmod 1777 /tmp")
        script = first["command"][-1]
        assert "chmod 00755 /var/lib/agent" in script and "chown 1000:1000 /var/lib/agent" in script
        assert "-R" not in script


def test_pod_hostname_is_the_agent_name():
    # Ledger guards map hostname -> actor through the roster; a pod's default
    # hostname changes on every restart, and the push guard then refused the
    # agent's own log as "another actor's" (first live pod, 2026-10-25).
    for d in agent_deployments(render()):
        assert d["spec"]["template"]["spec"]["hostname"] == d["metadata"]["name"]


def test_every_agent_container_holds_its_role_lock():
    # Even a hand-made second replica must not become a second writer: each
    # container runs under an exclusive lock for its role in the volume.
    for d in agent_deployments(render()):
        for c in d["spec"]["template"]["spec"]["containers"]:
            assert c["command"][:2] == ["/usr/local/bin/agent-run", c["name"]], c["name"]


def test_signing_key_is_a_read_only_secret_mount():
    # The Secret is read only by one root step that installs the key, 0400 and
    # owned by the agent, into memory; the agent's containers mount that copy
    # read-only. No fsGroup: with it the kubelet re-owned the whole home
    # (files 660, dirs 2770), which ssh and the private-state check refuse.
    d = agent_deployments(render())[0]
    pod = d["spec"]["template"]["spec"]
    assert "fsGroup" not in pod["securityContext"]
    vols = {v["name"]: v for v in pod["volumes"]}
    assert vols["signing-key"]["secret"]["secretName"] == "tris-signing-key"
    assert vols["signing-key"]["secret"]["defaultMode"] == 0o400
    assert vols["agent-key"]["emptyDir"]["medium"] == "Memory"
    users = [c["name"] for c in pod["initContainers"] + pod["containers"]
             if any(m["name"] == "signing-key" for m in c["volumeMounts"])]
    assert users == ["key-install"]
    ki = next(c for c in pod["initContainers"] if c["name"] == "key-install")
    assert ki["securityContext"]["capabilities"] == {"drop": ["ALL"], "add": ["CHOWN"]}
    script = ki["command"][-1]   # chown last: without FOWNER root cannot chmod the agent's file
    assert script.index("chmod 0400") < script.index("chown 1000:1000")
    mounts = [m for c in pod["containers"] for m in c["volumeMounts"] if m["name"] == "agent-key"]
    assert mounts and all(m["readOnly"] is True for m in mounts)
    assert all(m["mountPath"] == "/var/lib/agent/.datacore/keys/tris.key" for m in mounts)


def test_ssh_finds_the_agents_ssh_dir_when_home_is_moved():
    # ssh reads ~/.ssh from the passwd home (the image's /var/lib/agent), not
    # $HOME. With the volume at the old home path, every container must also
    # see the volume's .ssh there, or the proxy line and deploy keys are ignored.
    d = agent_deployments(render("--set", "agents.tris.home=/srv/oldhome"))[0]
    pod = d["spec"]["template"]["spec"]
    for c in pod["containers"]:
        ssh = [m for m in c["volumeMounts"] if m["mountPath"] == "/var/lib/agent/.ssh"]
        assert ssh == [{"name": "home", "mountPath": "/var/lib/agent/.ssh", "subPath": ".ssh"}], c["name"]
    d = agent_deployments(render())[0]
    mounts = [m["mountPath"] for c in d["spec"]["template"]["spec"]["containers"] for m in c["volumeMounts"]]
    assert "/var/lib/agent/.ssh" not in mounts


def test_kill_switch_is_a_replica_count():
    d = agent_deployments(render("--set", "agents.tris.running=false"))[0]
    assert d["spec"]["replicas"] == 0


def test_proxy_allow_list_is_rendered_and_ends_in_deny():
    cms = [c for c in of_kind(render(), "ConfigMap") if c["metadata"]["name"] == "egress-config"]
    conf = cms[0]["data"]["squid.conf"]
    assert "acl allowed_domains dstdomain .openrouter.ai api.telegram.org" in conf
    assert "acl allowed_ssh dst 192.0.2.10" in conf
    access = [l.strip() for l in conf.splitlines() if l.strip().startswith("http_access")]
    assert access[-1] == "http_access deny all"
    assert "access_log stdio:/dev/stdout" in conf


def test_crontab_comes_from_values():
    cms = [c for c in of_kind(render(), "ConfigMap") if c["metadata"]["name"] == "tris-crontab"]
    tab = cms[0]["data"]["crontab"]
    assert "*/30 * * * * /var/lib/agent/heartbeat.sh" in tab


RUNTIMES = Path(__file__).resolve().parent / "fixtures" / "values-runtimes.yaml"


def render_runtimes(*extra: str) -> dict[str, dict]:
    out = subprocess.run(
        ["helm", "template", "fleet", str(CHART), "-f", str(RUNTIMES), *extra],
        check=True, capture_output=True, text=True,
    ).stdout
    docs = [d for d in yaml.safe_load_all(out) if d]
    return {d["metadata"]["name"]: d for d in agent_deployments(docs)}


def _containers(dep):
    return {c["name"]: c for c in dep["spec"]["template"]["spec"]["containers"]}


def _env(c):
    return {e["name"]: e.get("value") for e in c.get("env", [])}


def test_each_agent_is_rendered_from_its_runtime_template():
    # A new agent names a runtime; image, gateway command and runtime paths
    # come from the template, so the fleet grows without copying chart code.
    deps = render_runtimes()
    tris, data = _containers(deps["tris"]), _containers(deps["data"])
    assert tris["gateway"]["image"].startswith("agent-hermes:")
    assert data["gateway"]["image"].startswith("agent-openclaw:")
    assert "hermes_cli.main" in tris["gateway"]["command"]
    assert data["gateway"]["command"][2:] == ["openclaw", "gateway", "run", "--bind", "loopback"]
    assert _env(tris["gateway"])["HERMES_HOME"] == "/srv/oldhome/.hermes"
    assert "HERMES_HOME" not in _env(data["gateway"])
    assert _env(data["gateway"])["OPENCLAW_STATE_DIR"] == "/var/lib/agent/.openclaw"
    assert "/opt/hermes-agent/venv/bin" not in _env(data["cron"])["PATH"]


def test_each_agent_has_its_own_home():
    # Tris keeps his old home path; a fresh agent lives at the image home.
    deps = render_runtimes()
    for name, home in (("tris", "/srv/oldhome"), ("data", "/var/lib/agent")):
        for c in _containers(deps[name]).values():
            mounts = {m["mountPath"] for m in c["volumeMounts"] if m["name"] == "home"}
            assert home in mounts, (name, c["name"], mounts)
            assert _env(c)["HOME"] == home, (name, c["name"])
        owner = deps[name]["spec"]["template"]["spec"]["initContainers"][0]["command"][-1]
        assert f"chown 1000:1000 {home}" in owner, name


def test_an_agent_may_pin_its_own_image():
    deps = render_runtimes("--set", "agents.data.image=agent-openclaw:pinned")
    assert {c["image"] for c in _containers(deps["data"]).values()} == {"agent-openclaw:pinned"}


def test_an_agent_without_a_runtime_is_refused():
    r = subprocess.run(["helm", "template", "fleet", str(CHART), "-f", str(RUNTIMES),
                        "--set", "agents.x.storage=1Gi"], capture_output=True, text=True)
    assert r.returncode != 0 and "runtime" in r.stderr


def test_node_scripts_learn_each_agents_runtime_home_and_backup_excludes():
    out = subprocess.run(["helm", "template", "fleet", str(CHART), "-f", str(RUNTIMES)],
                         check=True, capture_output=True, text=True).stdout
    cms = {d["metadata"]["name"]: d for d in yaml.safe_load_all(out) if d and d["kind"] == "ConfigMap"}
    tris, data = cms["tris-agent"]["data"], cms["data-agent"]["data"]
    assert (tris["runtime"], tris["home"]) == ("hermes", "/srv/oldhome")
    assert tris["backupExclude"] == ".hermes/hermes-agent,.hermes/node"
    assert (data["runtime"], data["home"], data["backupExclude"]) == ("openclaw", "/var/lib/agent", "")
    assert tris["image"].startswith("agent-hermes:")


def test_an_upgrade_never_restarts_an_agent_the_kill_switch_stopped():
    # fleetctl stop-all scales to 0 and remembers the count in an annotation;
    # a later `helm upgrade` must keep 0 while that annotation is there.
    tpl = (CHART / "templates" / "agents.yaml").read_text()
    assert 'lookup "apps/v1" "Deployment"' in tpl and "datacore.one/resume-replicas" in tpl


def test_standby_runs_nothing_of_the_agent():
    # A restored copy is checked in standby: volume, env and runtime links
    # as in production, but no gateway (Telegram), no schedule (pushes), no stream.
    deps = render_runtimes("--set", "agents.tris.mode=standby")
    pod = deps["tris"]["spec"]["template"]["spec"]
    assert [c["name"] for c in pod["containers"]] == ["standby"]
    c = pod["containers"][0]
    assert c["command"][:2] == ["/usr/local/bin/agent-run", "standby"] and "sleep" in c["command"]
    prepare = next(i for i in pod["initContainers"] if i["name"] == "prepare")
    assert {"name": "AGENT_STANDBY", "value": "1"} in prepare["env"]
    assert [x["name"] for x in _containers(deps["data"]).values()] == ["gateway", "cron"]
