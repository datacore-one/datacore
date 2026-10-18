"""The satellite dispatcher tick honours the host's push policy.

`ledger-claim-pull.sh` (plur-claw, every 15 minutes) pushed its claims to
`ledger/<actor>` on every tick and converged with no actor, so it published
in scheduled mode too. Now:

  * its converge names the actor, so in scheduled mode an agent receives and
    does not publish (the same rule as ledger_claim_run.sh);
  * the push of its own ref runs only when `ledger_transport.py policy`
    does not answer `deferred`. Anything else -- `now`, an empty answer from
    an older runner, an error -- pushes as before: holding work back by
    mistake costs more than one extra push.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=check)


def _fleet(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    space = tmp_path / "5-plur"
    (space / ".datacore" / "events").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(space)], check=True)
    for k, v in (("user.email", "drill@datacore"), ("user.name", "drill"),
                 ("core.hooksPath", str(space / ".git" / "hooks"))):
        _git(space, "config", k, v)
    (space / "README").write_text("seed\n")
    _git(space, "add", "-A"); _git(space, "commit", "-qm", "seed")
    _git(space, "remote", "add", "origin", str(origin))
    _git(space, "push", "-qu", "origin", "main")
    return space, origin


def _runner(tmp_path, policy_answer):
    """Inert claim step; a transport stub that answers `policy` and logs argv."""
    runner = tmp_path / "runner"
    lib = runner / ".datacore" / "lib"
    lib.mkdir(parents=True)
    shutil.copyfile(LIB / "ledger-claim-pull.sh", lib / "ledger-claim-pull.sh")
    (lib / "ledger_claim.py").write_text("import sys\nsys.exit(0)\n")
    calls = tmp_path / "transport-calls.txt"
    (lib / "ledger_transport.py").write_text(
        "import sys\n"
        f"open({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:2] == ['policy']:\n"
        f"    print({policy_answer!r})\n"
        "sys.exit(0)\n")
    (lib / "actor_identity.py").write_text(
        "import os\nprint(os.environ.get('DATACORE_ACTOR', 'data') + ' (env)')\n")
    return runner, calls


def _run(space, runner, actor="data"):
    env = dict(os.environ,
               HOME=str(space.parent), DATACORE_RUNNER=str(runner),
               DISPATCH_SPACE=str(space), DISPATCH_LIMIT="1", DATACORE_ACTOR=actor)
    return subprocess.run(["bash", str(runner / ".datacore/lib/ledger-claim-pull.sh")],
                          capture_output=True, text=True, env=env, timeout=120)


def _remote_has_own_ref(origin, actor="data"):
    out = subprocess.run(["git", "--git-dir", str(origin), "for-each-ref", "--format=%(refname)"],
                         capture_output=True, text=True, check=True).stdout.split()
    return f"refs/heads/ledger/{actor}" in out


def _tick(tmp_path, answer):
    space, origin = _fleet(tmp_path)
    (space / ".datacore" / "events" / "data.jsonl").write_text('{"actor":"data","seq":1}\n')
    runner, calls = _runner(tmp_path, answer)
    result = _run(space, runner)
    assert result.returncode == 0, result.stdout + result.stderr
    return space, origin, calls.read_text().splitlines()


def test_scheduled_agent_commits_its_claims_but_does_not_push(tmp_path):
    space, origin, calls = _tick(tmp_path, "deferred")
    published = _git(space, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert ".datacore/events/data.jsonl" in published  # committed locally
    assert not _remote_has_own_ref(origin)              # but not pushed
    assert any(c.startswith("policy") and "--actor data" in c for c in calls), calls


@pytest.mark.parametrize("answer", ["now", ""])
def test_now_or_an_unknown_answer_pushes_as_before(tmp_path, answer):
    _, origin, _ = _tick(tmp_path, answer)
    assert _remote_has_own_ref(origin)


def test_the_converge_names_the_actor(tmp_path):
    _, _, calls = _tick(tmp_path, "now")
    converges = [c for c in calls if c.startswith("converge")]
    assert converges and all("--actor data" in c for c in converges), calls
