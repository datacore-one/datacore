"""Read exact Git conflict stages and refuse to replace a manually edited file."""
from pathlib import Path
import re
import subprocess
import tempfile


def conflict_stages(repo, name, working):
    result = subprocess.run(['git', '-C', str(repo), 'ls-files', '-u', '-z', '--', name], capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError('cannot read conflict index')
    objects = {}
    for entry in result.stdout.split(b'\0'):
        if not entry:
            continue
        info, path = entry.split(b'\t', 1)
        mode, oid, stage = info.split()
        if path.decode() != name or mode not in {b'100644', b'100755'}:
            raise ValueError('unsupported conflict entry')
        objects[int(stage)] = oid.decode()
    if 2 not in objects or 3 not in objects:
        raise ValueError('both conflict sides are required')
    data = {1: b''}
    for number, oid in objects.items():
        result = subprocess.run(['git', '-C', str(repo), 'cat-file', 'blob', oid], capture_output=True, timeout=30)
        if result.returncode:
            raise ValueError('conflict object unavailable')
        data[number] = result.stdout
    def normalize(text):
        return re.sub(rb'(?m)^(<<<<<<<|\|\|\|\|\|\|\||>>>>>>>) [^\r\n]*', rb'\1', text)
    with tempfile.TemporaryDirectory(prefix='datacore-conflict-') as temporary:
        paths = {number: Path(temporary) / str(number) for number in (1, 2, 3)}
        for number, path in paths.items():
            path.write_bytes(data[number])
        for style in ([], ['--diff3'], ['--zdiff3']):
            merged = subprocess.run(['git', 'merge-file', '-p', *style, str(paths[2]), str(paths[1]), str(paths[3])], capture_output=True, timeout=30)
            if 0 <= merged.returncode <= 127 and normalize(merged.stdout) == normalize(working):
                return data
    raise ValueError('working copy was edited after the conflict; preserve it for review')
