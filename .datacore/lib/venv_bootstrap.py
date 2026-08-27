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

import glob
import os
import sys

_DATA_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def venv_site_packages(data_root: str | None = None) -> str | None:
    """Return the venv's site-packages path, or None if there is no venv."""
    root = data_root or _DATA_ROOT
    matches = sorted(
        glob.glob(os.path.join(root, "venv", "lib", "python*", "site-packages"))
    )
    return matches[-1] if matches else None


def activate(data_root: str | None = None) -> bool:
    """Put .datacore/venv on sys.path. True if a path was added."""
    site_packages = venv_site_packages(data_root)
    if not site_packages or site_packages in sys.path:
        return False
    # Append rather than insert: the venv is a fallback for what the running
    # interpreter cannot already provide, and must not shadow it.
    sys.path.append(site_packages)
    return True
