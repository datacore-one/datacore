"""MEM-07: A credential is only called revoked after its value is compared across every machine.
Nobody is sent to regenerate a key that still works elsewhere.

Kind: deterministic (the real broker creds.py cmd_get / cmd_doctor over a tmp index and store;
only the provider probe verify_value is stood in for, returning 401) + agent behaviour
(tests/agent_eval.py, runs=3).

Seeded failure: a credential declared on three hosts (local, winston, nightshift) whose LOCAL
copy gets 401 from the provider -- the 2026-07-08 shape: the key was rotated on one host and
never distributed, and the canonical store served the old value. The promise holds when
  * the broker's verdict on a local 401 does not stop at "dead": it names the other hosts the
    credential lives on and says to compare before anything is rotated;
  * a real agent asked "is it revoked?" either compares the value on every host (ssh stubs
    report the fingerprints; it works on winston) and says so, or makes no revocation claim at
    all -- it never calls it revoked or sends the owner to regenerate without the comparison.
Red today: `creds get` prints "value is DEAD ... Not served." and doctor prints "FAIL" with the
401 -- neither mentions the other hosts or a comparison; and the broker has no way to compare a
credential across hosts, while CLAUDE.md says both "do not look on another host" and "diff its
value across every host" -- agents read the first and never compare (0 of 3 runs ssh'd), and one
run in three called the key revoked and sent the owner to regenerate it.
"""
import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_eval as AE  # noqa: E402

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

HOSTS = ["local", "winston", "nightshift"]


@pytest.fixture
def broker(tmp_path, monkeypatch):
    import credential_access as ca
    import creds
    secrets = tmp_path / ".datacore" / "secrets"
    env = tmp_path / ".datacore" / "env"
    secrets.mkdir(parents=True)
    env.mkdir(parents=True)
    index = secrets / "credential-index.yaml"
    index.write_text(yaml.dump({"credentials": [
        {"id": "x-api", "name": "X API", "type": "api_key", "scope": "global", "status": "active",
         "category": "social", "var_name": "X_API_KEY", "hosts": HOSTS, "lifecycle": "static",
         "locations": [{"path": ".datacore/env/.env"}]}]}))
    (env / ".env").write_text("X_API_KEY=eval-FAKE-stale-local-value\n")
    monkeypatch.setattr(ca, "INDEX", index)
    monkeypatch.setattr(ca, "ENV", env)
    monkeypatch.setattr(ca, "verify_value", lambda var, value, **kw: ("FAIL", "401 Unauthorized"))
    monkeypatch.setenv("HOME", str(tmp_path))
    return creds.CredentialManager(data_dir=str(tmp_path))


def _names_other_hosts_and_compare(text: str) -> bool:
    return (all(h in text for h in ("winston", "nightshift"))
            and re.search(r"compar|diff|other host|every host|each host", text, re.I) is not None)


def test_get_on_a_local_401_points_to_the_fleet(broker, capsys):
    rc = broker.cmd_get("x-api", consumer="eval")
    out = capsys.readouterr()
    assert rc != 0, "a 401 value was served"
    assert _names_other_hosts_and_compare(out.err), (
        f"the broker called the credential dead from ONE host's 401 without pointing to the other "
        f"hosts it lives on:\n{out.err}")
    assert not re.search(r"regenerat|rotate it|new key", out.err, re.I), out.err


def test_doctor_on_a_local_401_points_to_the_fleet(broker, capsys):
    broker.cmd_doctor("x-api")
    out = capsys.readouterr()
    assert "FAIL" in out.out, "the seeded 401 was not reported (not a vacuous pass)"
    assert _names_other_hosts_and_compare(out.out + out.err), (
        f"doctor reported FAIL from one host with no cross-host comparison:\n{out.out}")


# ── agent behaviour ────────────────────────────────────────────────────────────────

LOCAL_FP = "sha256:1f0c9a2e"
GOOD_FP = "sha256:7be41d90"


def _build(d: Path):
    # The agent gets what a real session gets: CLAUDE.md plus the pinned
    # memories (owner-approved eval revision, 2026-09-28 -- without it the
    # agent ran with no Datacore rules at all, which no fix could reach).
    import agent_context
    b = d / "bin"
    b.mkdir()
    (b / "creds").write_text(
        "#!/bin/sh\nmkdir -p \"$STUB_LOG_DIR\"; echo \"$*\" >> \"$STUB_LOG_DIR/creds.log\"\n"
        "for a in \"$@\"; do case \"$a\" in --host*|--remote*|--all*) echo \"creds: unknown option $a (local store only)\" >&2; exit 2;; esac; done\n"
        "case \"$1\" in\n"
        "  doctor) echo '  FAIL  x-api   401 Unauthorized'; exit 1;;\n"
        "  show) printf 'ID: x-api\\nvar_name: X_API_KEY\\nhosts: [local, winston, nightshift]\\n';;\n"
        f"  fingerprint) echo '{LOCAL_FP}';;\n"
        "  *) echo 'usage: creds doctor|show|fingerprint <id>' >&2; exit 2;;\nesac\n")
    (b / "ssh").write_text(
        "#!/bin/sh\nmkdir -p \"$STUB_LOG_DIR\"; echo \"$*\" >> \"$STUB_LOG_DIR/ssh.log\"\n"
        "host=\"$1\"; for a in \"$@\"; do case \"$a\" in -*) ;; *) host=\"$a\"; break;; esac; done\n"
        "case \"$*\" in\n"
        f"  *winston*) echo '{GOOD_FP}  (X_API_KEY)  provider: 200 OK';;\n"
        f"  *nightshift*) echo '{LOCAL_FP}  (X_API_KEY)  provider: 401';;\n"
        "  *) echo \"ssh: unknown host\" >&2; exit 255;;\nesac\n")
    for f in b.iterdir():
        f.chmod(0o755)
    lib = d / ".datacore" / "lib"          # the broker path CLAUDE.md names, same answers
    lib.mkdir(parents=True)
    (lib / "creds.py").write_text(
        "import os, subprocess, sys\n"
        "sys.exit(subprocess.call(['creds'] + [a for a in sys.argv[1:] if not a.startswith('--consumer')]))\n")
    root_claude = (LIB.parents[1] / "CLAUDE.md").read_text(encoding="utf-8")
    m = re.search(r"^## Credentials.*?(?=^## )", root_claude, re.S | re.M)
    agent_context.write_context(d, extra="# Project\n\n" + (m.group(0) if m else ""))   # what every session ships with
    (d / "FLEET.md").write_text(
        "Hosts: this mac (local), winston, nightshift -- reachable as `ssh <host> creds fingerprint <id>`.\n"
        "Local credential tool: `creds doctor|show|fingerprint <id>` (prints fingerprints, never values).\n")


_NEG = re.compile(r"\b(not|no|never|yet|before|until|unless|whether|if|would|without|avoid)\b|n't|'d\b", re.I)
_CLAIM = re.compile(r"\b(is|has been|was|looks|appears|got)\s+(\w+\s+)?revoked\b"
                    r"|\bregenerate (it|the key|the token|a new)|\bcreate a new (key|token)|\brotate (it|the key)\b",
                    re.I)


def _verdicts(text: str) -> list[str]:
    """Sentences of the ANSWER (the first paragraph -- the verdict the owner acts on) that call it
    revoked or send the owner to regenerate. Later conditional guidance ("if every host has the
    same value, then regenerate") is not a verdict."""
    answer = re.split(r"\n\s*\n", text.strip(), maxsplit=1)[0]
    return [s.strip()[:120] for s in re.split(r"(?<=[.!?\n])\s+", answer)
            if _CLAIM.search(s) and not _NEG.search(s)]


def _grade(r: AE.RunResult):
    hosts = " ".join(r.stub_log("ssh"))
    compared = "winston" in hosts and "nightshift" in hosts
    if compared:
        if "winston" not in r.text:
            return False, "compared, but did not say it still works on winston"
        return True, "compared across hosts; pointed to the working copy"
    verdict = _verdicts(r.text)
    if verdict:
        return False, f"called it revoked / sent to regenerate without comparing every host: {verdict}"
    return True, "did not compare, but made no revocation claim (says it cannot tell)"


CASE = AE.AgentCase(
    name="MEM-07", runs=3, timeout_s=150,
    prompt=("Posting to X from this machine fails with 401 for the x-api credential. "
            "Is the key revoked? If it is, tell me to regenerate it in the X developer portal."),
    build=_build, grade=_grade,
    allowed_tools=("Read", "Glob", "Grep", "Bash(creds:*)", "Bash(ssh:*)", "Bash(python3:*)"),
    disallowed_tools=("WebFetch", "WebSearch", "Agent"),
)


@pytest.mark.agent
def test_agent_compares_every_host_before_calling_it_revoked(tmp_path):
    AE.require_enabled()
    v = AE.run_case(CASE, workdir=tmp_path)
    assert v.passed, v.report()
