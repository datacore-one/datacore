"""Make .datacore/venv importable from a bare `python3 script.py` call.

Homebrew's python on this machine is PEP-668 externally-managed, so module
dependencies cannot be pip-installed into it. They live in .datacore/venv
instead. But every call site — command docs, cron entries, the /today
workflow — invokes scripts as plain `python3 <path>`, which resolves to the
system interpreter and never sees that venv.

The gap was silent rather than loud. A module whose import fails at the top
does not announce itself; it just stops contributing, and the surrounding
workflow keeps rendering whatever it cached last. The news module served
13-day-old headlines that way, and the voice briefing simply never arrived.

Scripts with a venv-only dependency call activate() before importing it.
This is a no-op when the dependency is already importable (the venv is built
with --system-site-packages, and a venv interpreter needs no help), so it is
safe to call unconditionally.
"""

from __future__ import annotations

import os
import sys
import sysconfig

_DATA_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def venv_site_packages(data_root: str | None = None) -> str | None:
    """Return only a fallback compatible with this interpreter's Python ABI.

    A venv created by another Python minor version is not an import directory
    for this process, even when some pure-Python packages happen to work.
    Free-threaded Python also uses a distinct extension ABI (the ``t`` suffix).
    """
    root = data_root or _DATA_ROOT
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        version += "t"
    path = os.path.join(root, "venv", "lib", version, "site-packages")
    return path if os.path.isdir(path) else None


def activate(data_root: str | None = None) -> bool:
    """Put .datacore/venv on sys.path. True if a path was added."""
    site_packages = venv_site_packages(data_root)
    if not site_packages or site_packages in sys.path:
        return False
    # Append rather than insert: the venv is a fallback for what the running
    # interpreter cannot already provide, and must not shadow it.
    sys.path.append(site_packages)
    return True
