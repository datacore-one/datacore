"""Canonical, fail-closed layering for sync and conflict settings."""
from pathlib import Path
import yaml


def merge(base, override):
    for key, value in override.items():
        if isinstance(base.get(key), dict) and isinstance(value, dict):
            merge(base[key], value)
        else:
            base[key] = value


def load_settings(root):
    result = {}
    for name in ["settings.yaml", "settings.local.yaml"]:
        try:
            text = (Path(root) / ".datacore" / name).read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        settings = yaml.safe_load(text)
        if settings is None:
            settings = {}
        if not isinstance(settings, dict):
            raise ValueError("sync settings must be mappings")
        merge(result, settings)
    return result
