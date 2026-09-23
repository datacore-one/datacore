"""Managed schedules are unique, real, and cannot erase unknown user state."""
import subprocess
from pathlib import Path

import pytest
import cron_install as C

LINE = '25 * * * * DATACORE_ROOT=/data /runner/.datacore/lib/ledger_phase1_cycle.sh >> /state/cycle.log 2>&1'
ENTRY = {'phase1-cycle': LINE}


def test_duplicate_legacy_paths_collapse_without_losing_unrelated_lines():
    old = LINE.replace('/runner/', '/old/') + '\n' + LINE + '\n'
    unrelated = '# explanation\nMAILTO=private@example.invalid\n1 * * * * echo ledger_phase1_cycle.sh\n2 * * * * /other/ledger_phase1_cycle.sh\n'
    result = C.reconcile(unrelated + old, ENTRY)
    assert result.startswith(unrelated)
    assert result.count('DATACORE_ROOT=') == 1
    assert C.reconcile(result, ENTRY) == result


def test_comments_and_stale_schedule_are_not_verification():
    old = '# ' + LINE + '\n' + LINE.replace('25 *', '30 *') + '\n'
    result = C.reconcile(old, ENTRY)
    assert result.startswith('# ' + LINE + '\n')
    assert result.endswith(LINE + ' # datacore-job:phase1-cycle\n')
    assert '30 *' not in result


def test_shared_script_discriminators_preserve_other_jobs():
    wanted = '*/15 * * * * /new/.datacore/lib/unit_alive.sh alpha.service /state/a'
    other = '*/15 * * * * /old/.datacore/lib/unit_alive.sh beta.service /state/b\n'
    result = C.reconcile(other, {'alpha-alive': wanted})
    assert result.startswith(other)
    assert C.invocation('0 8 * * * python3 /x/.datacore/lib/job_verify.py --machine a') != C.invocation('0 8 * * * python3 /x/.datacore/lib/job_verify.py --machine b')


def test_quoted_paths_and_no_final_newline_are_preserved():
    desired = "25 * * * * '/has space/.datacore/lib/ledger_phase1_cycle.sh'"
    assert C.invocation(desired) == C.invocation(LINE)
    result = C.reconcile('MAILTO=private@example.invalid', {'cycle': desired})
    assert result.startswith('MAILTO=private@example.invalid\n')


def test_failed_crontab_read_never_means_an_empty_crontab(monkeypatch):
    monkeypatch.setattr(C.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 1, '', 'permission denied\n'))
    with pytest.raises(RuntimeError, match='unknown contents'):
        C.read_crontab()


def test_initial_absence_is_distinguished_from_failure(monkeypatch):
    monkeypatch.setattr(C.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 1, '', 'no crontab for test\n'))
    assert C.read_crontab() == ''


def test_changed_crontab_is_not_overwritten(tmp_path, monkeypatch):
    reads = iter(['# initial\n', '# concurrent edit\n'])
    monkeypatch.setattr(C, 'read_crontab', lambda: next(reads))
    called = []
    monkeypatch.setattr(C.subprocess, 'run', lambda *a, **kw: called.append(a))
    with pytest.raises(RuntimeError, match='changed'):
        C.install(ENTRY, tmp_path / 'private')
    assert called == []
    assert next((tmp_path / 'private').glob('*.crontab')).read_text() == '# initial\n'


def test_install_is_verified_and_preserves_private_recovery_copy(tmp_path, monkeypatch):
    current = ['# private original\n']; writes = []
    monkeypatch.setattr(C, 'read_crontab', lambda: current[0])
    def run(*args, **kw):
        writes.append(kw['input']); current[0] = kw['input']
    monkeypatch.setattr(C.subprocess, 'run', run)
    state = tmp_path / 'private'
    assert C.install(ENTRY, state)
    assert C.install(ENTRY, state)
    assert C.install(ENTRY, state, verify=True)
    assert len(writes) == 1
    backup = next(state.glob('*.crontab'))
    assert backup.read_text() == '# private original\n'
    assert backup.stat().st_mode & 0o777 == 0o600


def test_verify_does_not_create_recovery_state(tmp_path, monkeypatch):
    monkeypatch.setattr(C, 'read_crontab', lambda: '# ' + LINE + '\n')
    state = tmp_path / 'absent'
    assert C.install(ENTRY, state, verify=True) is False
    assert not state.exists()


@pytest.mark.parametrize('suffix', ['; /other/job', ' && /other/job', ' | /other/job', ' & /other/job'])
def test_replacing_a_job_cannot_discard_a_compound_neighbors_command(suffix):
    with pytest.raises(ValueError, match='compound'):
        C.reconcile(LINE + suffix + '\n', ENTRY)


def test_unrelated_compound_commands_are_preserved():
    other = '0 * * * * /other/job && echo done\n'
    assert C.reconcile(other, ENTRY).startswith(other)


def test_wrong_post_install_contents_fail_verification(tmp_path, monkeypatch):
    reads = iter(['# initial\n', '# initial\n', '# unexpected\n'])
    monkeypatch.setattr(C, 'read_crontab', lambda: next(reads))
    monkeypatch.setattr(C.subprocess, 'run', lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match='does not match'):
        C.install(ENTRY, tmp_path / 'private')


def test_real_host_installer_reconciles_and_verifies_fake_crontab(tmp_path):
    import os
    import shutil
    import sys
    home = tmp_path / 'home'
    runner = home / '.datacore/v2-runner'
    lib = runner / '.datacore/lib'
    registry = runner / '.datacore/registry'
    lib.mkdir(parents=True); registry.mkdir()
    registry.joinpath('infrastructure.yaml').write_text('servers:\n  hermes:\n    access: {actor: fixture}\n')
    home.joinpath('.datacore/identity.env').write_text('DATACORE_ACTOR=fixture\nDATACORE_LEDGER_SIGN=1\n')
    # Like the real module: importable (the installer reads REGISTRY_DIR, which
    # resolves where the private registry lives) and a resolver when run.
    lib.joinpath('actor_identity.py').write_text(
        'from pathlib import Path\n'
        f'REGISTRY_DIR = Path({str(registry)!r})\n'
        'if __name__ == "__main__":\n    print("fixture identity.env")\n')
    lib.joinpath('ledger_phase1_cycle.sh').write_text('#!/bin/sh\nexit 0\n')
    lib.joinpath('ledger_phase1_cycle.sh').chmod(0o755)
    shutil.copyfile(Path(C.__file__), lib / 'cron_install.py')
    bindir = tmp_path / 'bin'; bindir.mkdir()
    bindir.joinpath('python3').symlink_to(sys.executable)
    bindir.joinpath('systemctl').write_text('#!/bin/sh\nexit 0\n')
    bindir.joinpath('systemctl').chmod(0o755)
    store = tmp_path / 'crontab'
    old = '25 * * * * DATACORE_ROOT=/data /old/.datacore/lib/ledger_phase1_cycle.sh\n'
    store.write_text('# keep me\n' + old + old)
    bindir.joinpath('crontab').write_text('#!/bin/sh\ncase "$1" in\n-l) cat "$TEST_CRONTAB" ;;\n-) cat > "$TEST_CRONTAB" ;;\n*) exit 2 ;;\nesac\n')
    bindir.joinpath('crontab').chmod(0o755)
    env = {**os.environ, 'HOME': str(home), 'DATACORE_RUNNER': str(runner), 'DATACORE_STATE': str(home / '.datacore/state'), 'TEST_CRONTAB': str(store), 'PATH': str(bindir) + os.pathsep + os.environ['PATH']}
    command = ['bash', str(Path(C.__file__).with_name('agent_host_setup.sh')), '--host', 'hermes']
    before = subprocess.run([*command, '--verify'], env=env, capture_output=True, text=True)
    assert before.returncode == 1 and 'cron installation FAILED' in before.stdout
    applied = subprocess.run(command, env=env, capture_output=True, text=True)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    contents = store.read_text()
    assert contents.startswith('# keep me\n')
    assert contents.count('ledger_phase1_cycle.sh') == 1
    assert contents.count('job_verify.py') == 1
    checked = subprocess.run([*command, '--verify'], env=env, capture_output=True, text=True)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    again = subprocess.run(command, env=env, capture_output=True, text=True)
    assert again.returncode == 0 and store.read_text() == contents


def test_jobs_sharing_a_wrapper_are_told_apart_by_what_they_write():
    """atomic_out.sh wraps many producers and audit_trio_run.sh has many modes.

    The installer identifies a job by its executable, so five detectors each
    writing their own artifact through one wrapper were a single "ambiguous
    invocation" and all five were refused. The discriminator is the first
    argument: the artifact path, or the mode.
    """
    import cron_install as ci
    a = ci.invocation("25 * * * * /h/.datacore/lib/atomic_out.sh /s/a.log -- python3 /h/x.py")
    b = ci.invocation("40 * * * * /h/.datacore/lib/atomic_out.sh /s/b.log -- python3 /h/y.py")
    assert a != b and a[1] == "/s/a.log"
    c = ci.invocation("30 3 * * * /h/.datacore/lib/audit_trio_run.sh drill")
    d = ci.invocation("45 2 * * * /h/.datacore/lib/audit_trio_run.sh drills")
    assert c != d

    planned = ci.reconcile("", {"job-a": "25 * * * * /h/.datacore/lib/atomic_out.sh /s/a.log -- python3 /h/x.py",
                                "job-b": "40 * * * * /h/.datacore/lib/atomic_out.sh /s/b.log -- python3 /h/y.py"})
    assert planned.count("# datacore-job:") == 2


def test_the_same_artifact_twice_is_still_ambiguous():
    """The discriminator must not make genuinely duplicate jobs installable."""
    import cron_install as ci
    import pytest
    with pytest.raises(ValueError):
        ci.reconcile("", {"job-a": "25 * * * * /h/.datacore/lib/atomic_out.sh /s/a.log -- python3 /h/x.py",
                          "job-b": "40 * * * * /h/.datacore/lib/atomic_out.sh /s/a.log -- python3 /h/y.py"})
