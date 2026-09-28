"""Where the github module keeps its data (MEM-63).

The repo list (repos.json) and the scan cache (scan_cache.json) are
user data. They live in the selected space's private folder
``<space>/.datacore/module-data/github/data``, resolved through the core
``module_context`` helper -- never in this installed module. The helper refuses
while legacy state still sits in the module's own ``data/``; move it with
``.datacore/lib/module_data_migrate.py`` (writers stopped first).

Environment: ``DATACORE_ROOT`` (default ``~/Data``), ``DATACORE_SPACE``
(default ``personal``), ``DATACORE_LIB`` (default: the core lib beside this
module).
"""
import os
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent.parent


def _core_lib() -> Path:
    return Path(os.environ.get("DATACORE_LIB") or MODULE_DIR.parent.parent / "lib")


def data_dir(create: bool = True) -> Path:
    lib = str(_core_lib())
    if lib not in sys.path:
        sys.path.insert(0, lib)
    from module_context import resolve
    root = os.environ.get("DATACORE_ROOT") or str(Path.home() / "Data")
    space = os.environ.get("DATACORE_SPACE") or "personal"
    return resolve("github", MODULE_DIR, root=root, space=space, create=create).data


if __name__ == "__main__":
    # Shell entry (triage_github.sh): creates the private folder and prints it;
    # a refusal (legacy data still in the module) exits non-zero.
    print(data_dir(create="--read-only" not in sys.argv))
