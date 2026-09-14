"""Bounded, unambiguous YAML edits that retain authored comments and values."""
from datetime import date, datetime
from io import StringIO
import math

import yaml
from yaml_safety import UniqueStringKeyLoader


def validate_tree(value):
    nodes = size = 0
    seen = set()

    def visit(item, depth):
        nonlocal nodes, size
        nodes += 1
        if depth > 64 or nodes > 10000:
            raise ValueError('YAML document exceeds structural limits')
        if isinstance(item, str):
            size += len(item.encode())
            if size > 1024**2:
                raise ValueError('YAML document exceeds its size limit')
        elif isinstance(item, (dict, list)):
            if id(item) in seen:
                raise ValueError('mutable YAML aliases require reconciliation before mutation')
            seen.add(id(item))
            if isinstance(item, dict):
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise ValueError('YAML keys must be strings')
                    visit(key, depth + 1)
                    visit(child, depth + 1)
            else:
                for child in item:
                    visit(child, depth + 1)
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError('YAML numbers must be finite')
        elif item is not None and not isinstance(item, (int, bool, date, datetime)):
            raise ValueError('unsupported YAML value')
    visit(value, 0)


def _codec():
    from ruamel.yaml import YAML
    codec = YAML(typ='rt')
    codec.version = (1, 1)
    codec.allow_duplicate_keys = False
    codec.preserve_quotes = True
    return codec


def load_preserved(source):
    original = yaml.load(source, Loader=UniqueStringKeyLoader)
    validate_tree(original)
    preserved = _codec().load(source)
    if preserved != original:
        raise ValueError('YAML interpretations disagree; preserve and reconcile the source')
    return preserved


def dump_preserved(document):
    validate_tree(document)
    stream = StringIO()
    _codec().dump(document, stream)
    payload = stream.getvalue()
    if len(payload.encode()) > 1024**2:
        raise ValueError('YAML document exceeds its publication limit')
    if yaml.load(payload, Loader=UniqueStringKeyLoader) != document:
        raise ValueError('YAML serialization changed evidence')
    return payload
