"""Run a foreground job with timeout cleanup of its POSIX process group.

This controls ordinary descendants that inherit the job's process group.
It does not replace an OS sandbox against a child that deliberately detaches.

An optional idle watchdog (Phase 5A, 2026-10-04) tells a hung job from a slow
one: `idle_probe()` returns the wall-clock time of the job's last sign of life
(for a model runtime, its session transcript's mtime) or None; when there has
been none for `idle_seconds`, the job is killed and `Hung` is raised instead
of waiting out the whole timeout and calling it too slow.
"""
import os
import signal
import subprocess
import time


class Hung(subprocess.TimeoutExpired):
    """No sign of life for `timeout` seconds: stopped as hung, not as too slow."""

    def __str__(self):
        return f"Command '{self.cmd}' showed no activity for {self.timeout} seconds"


def _watch(process, input, timeout, idle_seconds, idle_probe, idle_poll, args):
    began_wall = time.time()
    deadline = time.monotonic() + timeout if timeout else None
    first = True
    while True:
        step = idle_poll
        if deadline is not None:
            step = min(step, max(0.01, deadline - time.monotonic()))
        try:
            return process.communicate(input if first else None, timeout=step)
        except subprocess.TimeoutExpired:
            first = False
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(args, timeout) from None
            try:
                last = idle_probe() if idle_probe else None
            except Exception:  # noqa: BLE001 -- an unreadable probe is no sign of life
                last = None
            if time.time() - max(began_wall, last or 0) > idle_seconds:
                raise Hung(args, idle_seconds) from None


def run(args, *, input=None, capture_output=False, timeout=None, check=False,
        idle_seconds=None, idle_probe=None, idle_poll=30, **kwargs):
    if os.name != 'posix':
        raise RuntimeError('foreground process-group cleanup requires a POSIX runtime')
    if kwargs.get('start_new_session') is False:
        raise ValueError('foreground jobs require a separate process group')
    kwargs['start_new_session'] = True
    if input is not None:
        if kwargs.get('stdin') is not None:
            raise ValueError('stdin and input cannot both be provided')
        kwargs['stdin'] = subprocess.PIPE
    if capture_output:
        if kwargs.get('stdout') is not None or kwargs.get('stderr') is not None:
            raise ValueError('capture_output conflicts with stdout/stderr')
        kwargs['stdout'] = kwargs['stderr'] = subprocess.PIPE
    with subprocess.Popen(args, **kwargs) as process:
        try:
            if idle_seconds:
                stdout, stderr = _watch(process, input, timeout, idle_seconds, idle_probe,
                                        idle_poll, args)
            else:
                stdout, stderr = process.communicate(input, timeout=timeout)
        except BaseException:
            # The leader has not been reaped yet, so its PID/process-group ID
            # cannot have been recycled for an unrelated job.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                # A deliberately detached process may retain pipe handles.
                # Do not let it hang cleanup of the foreground job.
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
                process.wait(timeout=5)
            raise
        if check and process.returncode:
            raise subprocess.CalledProcessError(process.returncode, args, output=stdout, stderr=stderr)
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
