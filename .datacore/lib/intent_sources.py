"""Read-only, source-bound inputs shared by intent planning and review."""
from pathlib import Path
import stat

import yaml

from file_utils import read_text_within
from space_catalog import catalog
from yaml_safety import UniqueStringKeyLoader


class IntentInputError(ValueError):
    """Incomplete or ambiguous evidence must not become an empty review."""


def spaces(root):
    return catalog(Path(root).resolve(strict=True))['spaces']


def files(root, directory, suffix):
    """Enumerate a required evidence directory without hiding traversal errors."""
    root, directory = Path(root).resolve(strict=True), Path(directory)
    current = root
    for part in directory.relative_to(root).parts:
        if part == '..':
            raise IntentInputError('invalid evidence directory')
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return []
        if not stat.S_ISDIR(info.st_mode):
            raise IntentInputError('evidence directory is aliased or invalid')
    before = directory.stat()
    result = []
    for entry in directory.iterdir():
        if entry.suffix == suffix:
            result.append(entry)
            if len(result) > 10000:
                raise IntentInputError('evidence directory exceeds review limit')
    after = directory.lstat()
    if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns):
        raise IntentInputError('evidence directory changed during enumeration')
    return sorted(result)


def text(root, path):
    try:
        return read_text_within(root, path)
    except (OSError, ValueError):
        # Source values and parser excerpts can contain private strategy data.
        raise IntentInputError('intent input is unreadable, unsafe or changed; preserve the source') from None


def org_nodes(root, path, *, required=False):
    source = text(root, path)
    if source is None:
        if required:
            raise IntentInputError('Org evidence disappeared during review')
        return []
    # The declared parser's vocabulary remains the baseline; per-file headers
    # extend it. Parse the bounded snapshot directly, without writing a copy or
    # invoking OrgWorkspace's implicit in-memory duplicate-ID repair.
    from org_workspace import StateConfig
    from org_workspace._vendor.orgparse import loads
    from org_workspace._vendor.orgparse.node import OrgEnv
    env = OrgEnv(filename=str(path))
    env.add_todo_keys(*StateConfig.default().env_keys())
    try:
        nodes = list(loads(source, filename=str(path), env=env)[1:])
    except (ValueError, TypeError, RecursionError):
        raise IntentInputError('invalid Org intent input; preserve the source') from None
    ids = [node.get_property('ID') for node in nodes if node.get_property('ID')]
    if len(ids) != len(set(ids)):
        raise IntentInputError('duplicate Org ids; preserve and reconcile the source')
    return nodes


def mapping(root, path):
    source = text(root, path)
    if source is None:
        return {}
    try:
        data = yaml.load(source, Loader=UniqueStringKeyLoader)
    except (yaml.YAMLError, ValueError, RecursionError):
        raise IntentInputError('invalid intent configuration; preserve the source') from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise IntentInputError('intent configuration must be a mapping')
    return data
