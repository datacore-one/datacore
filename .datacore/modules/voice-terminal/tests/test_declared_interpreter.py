"""speak_brief.py runs on the interpreter the host declared, whatever `python3` means today."""
import importlib.util
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parent / "lib"


def _load():
    # Import the module without triggering its re-exec: mark this process as already re-exec'd.
    os.environ["SPEAK_BRIEF_REEXEC"] = "1"
    sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location("speak_brief", LIB / "speak_brief.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_declared_interpreter_that_differs_is_chosen():
    sb = _load()
    assert sb._declared_interpreter({"DATACORE_PYTHON": "/opt/declared/bin/python3"}, "/opt/brew/bin/python3") == "/opt/declared/bin/python3"


def test_nothing_declared_or_already_there_means_stay():
    sb = _load()
    assert sb._declared_interpreter({}, "/opt/brew/bin/python3") is None
    assert sb._declared_interpreter({"DATACORE_PYTHON": "python3.11"}, "/x") is None, "a relative name is never trusted to PATH"
    assert sb._declared_interpreter({"DATACORE_PYTHON": "/opt/declared/bin/python3"}, "/opt/declared/bin/python3") is None
    assert sb._declared_interpreter({"DATACORE_PYTHON": "/opt/declared/bin/python3", "SPEAK_BRIEF_REEXEC": "1"}, "/x") is None, "no exec loop"
