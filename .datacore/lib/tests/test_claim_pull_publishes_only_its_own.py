"""The satellite dispatcher's tick must publish this writer's log and no other.

`ledger-claim-pull.sh` is plur-claw's cron tick. It reimplemented the transport
with raw git: `git pull --no-rebase` to receive, and `git add
.datacore/events/` to publish -- which stages EVERY writer's file. A log this
host merely received (Tris's, Winston's) was therefore committed and pushed
under this actor's ref.

That is the exact failure DIP-0044 authorship exists to prevent, and it
happened twice on hermes in September: a cadence appended to `winston.jsonl`
and an autosave committed it under Tris's name -- an approval-capable log
carrying events its principal never made. The transport grew
`foreign_writer_logs` for it; this path bypassed the guard by never going
through the transport at all.
"""
import os
import shutil
import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=check)


def _fleet(tmp_path):
    """A space with an origin, this actor's log, and a foreign one beside it."""
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


def _run(space, runner, actor="data"):
    env = dict(os.environ,
               HOME=str(space.parent), DATACORE_RUNNER=str(runner),
               DISPATCH_SPACE=str(space), DISPATCH_LIMIT="1", DATACORE_ACTOR=actor)
    return subprocess.run(["bash", str(runner / ".datacore/lib/ledger-claim-pull.sh")],
                          capture_output=True, text=True, env=env, timeout=120)


def _runner(tmp_path):
    """A runner tree whose claim and transport steps are inert stubs."""
    runner = tmp_path / "runner"
    lib = runner / ".datacore" / "lib"
    lib.mkdir(parents=True)
    shutil.copyfile(LIB / "ledger-claim-pull.sh", lib / "ledger-claim-pull.sh")
    for name in ("ledger_claim.py", "ledger_transport.py"):
        (lib / name).write_text("import sys\nsys.exit(0)\n")
    (lib / "actor_identity.py").write_text(
        "import os\nprint(os.environ.get('DATACORE_ACTOR', 'data') + ' (env)')\n")
    return runner


def test_a_log_this_host_only_received_is_never_published_as_its_own(tmp_path):
    space, origin = _fleet(tmp_path)
    events = space / ".datacore" / "events"
    (events / "data.jsonl").write_text('{"actor":"data","seq":1}\n')
    (events / "tris.jsonl").write_text('{"actor":"tris","seq":1}\n')

    result = _run(space, _runner(tmp_path))
    assert result.returncode == 0, result.stderr

    published = _git(space, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert ".datacore/events/data.jsonl" in published
    assert ".datacore/events/tris.jsonl" not in published, published
    # And it is still on disk, visible, for whoever owns the process that wrote it.
    assert (events / "tris.jsonl").exists()


def test_this_writers_run_scoped_logs_go_with_it(tmp_path):
    # `data-run-2026-09-18.jsonl` is the same writer on a run branch, not a
    # different principal; leaving it behind would strand the run's events.
    space, _ = _fleet(tmp_path)
    events = space / ".datacore" / "events"
    (events / "data.jsonl").write_text('{"actor":"data","seq":1}\n')
    (events / "data-run-2026-09-18.jsonl").write_text('{"actor":"data","seq":1}\n')

    assert _run(space, _runner(tmp_path)).returncode == 0

    published = _git(space, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert ".datacore/events/data-run-2026-09-18.jsonl" in published, published


def test_nothing_is_committed_when_this_writer_wrote_nothing(tmp_path):
    space, _ = _fleet(tmp_path)
    (space / ".datacore" / "events" / "tris.jsonl").write_text('{"actor":"tris","seq":1}\n')
    before = _git(space, "rev-parse", "HEAD").stdout.strip()

    assert _run(space, _runner(tmp_path)).returncode == 0

    assert _git(space, "rev-parse", "HEAD").stdout.strip() == before
