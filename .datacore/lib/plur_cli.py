"""Use the installed PLUR CLI without resolving packages during a request.

Managed services bind DATACORE_PLUR_CLI to their qualified release. Interactive
installations can use `plur` on PATH. This is dependency selection, not an OS
security boundary; the deployment must protect the executable and its runtime.
"""
import os
from pathlib import Path
import shutil
import stat

import process_run


def command(*args):
    selected = os.environ.get('DATACORE_PLUR_CLI')
    if selected is None:
        selected = shutil.which('plur')
        if selected is None:
            raise FileNotFoundError('PLUR CLI is not installed')
    path = Path(selected)
    if not selected or not path.is_absolute() or '..' in path.parts or '\x00' in selected:
        raise ValueError('PLUR CLI must be an absolute installed executable')
    # Package bin links are expected; resolve and validate their final target.
    resolved = path.resolve(strict=True)
    if not stat.S_ISREG(resolved.stat().st_mode) or not os.access(resolved, os.X_OK):
        raise ValueError('PLUR CLI is not an executable file')
    return [str(path), *args]


def run(*args, **kwargs):
    return process_run.run(command(*args), **kwargs)


def main(argv=None):
    import argparse
    import math
    import signal
    import subprocess
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, default=15)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.arguments or not math.isfinite(args.timeout) or not 0 < args.timeout <= 3600:
        parser.error('provide a command and a finite timeout between 0 and 3600 seconds')

    def terminate(signum, _frame):
        # Turn a harness termination into stack unwinding so process_run can
        # clean up the foreground descendants before the wrapper exits.
        raise SystemExit(128 + signum)

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        return run(*args.arguments, timeout=args.timeout).returncode
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f'PLUR CLI unavailable or failed: {type(error).__name__}', file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    raise SystemExit(main())
