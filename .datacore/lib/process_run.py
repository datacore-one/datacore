"""Run a foreground job with timeout cleanup of its POSIX process group.

This controls ordinary descendants that inherit the job's process group.
It does not replace an OS sandbox against a child that deliberately detaches.
"""
import os
import signal
import subprocess


def run(args, *, input=None, capture_output=False, timeout=None, check=False, **kwargs):
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
