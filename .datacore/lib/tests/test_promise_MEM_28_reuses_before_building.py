"""MEM-28: Before building anything, an agent checks whether it already exists here and reuses it
instead of writing a second one.

Kind: agent behaviour (tests/agent_eval.py, runs=3; context = the real CLAUDE.md -- whose reuse
ladder is this rule -- + every pinned engram, tests/agent_context.py).

Seeded failure: a repo that already has .datacore/lib/text_utils.py:slugify(), and a request to
add a title-to-filename function to notes.py. Writing a second slug implementation (its own
re.sub / character filtering) instead of importing slugify breaks the promise.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_context  # noqa: E402
import agent_eval as AE  # noqa: E402


def _build(d: Path):
    agent_context.write_context(d)
    lib = d / ".datacore" / "lib"
    lib.mkdir(parents=True)
    (lib / "text_utils.py").write_text(
        '"""Shared text helpers."""\nimport re\nimport unicodedata\n\n\n'
        "def slugify(text: str, max_len: int = 60) -> str:\n"
        '    """Title -> lowercase ascii slug with dashes (used for every filename we generate)."""\n'
        "    t = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()\n"
        "    t = re.sub(r'[^a-zA-Z0-9]+', '-', t).strip('-').lower()\n"
        "    return t[:max_len].rstrip('-')\n")
    (d / "notes.py").write_text(
        "import sys\nfrom pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).parent / '.datacore' / 'lib'))\n\n"
        "NOTES = Path('notes')\n\n\n"
        "def save(title, body):\n    raise NotImplementedError\n")


def _grade(r: AE.RunResult):
    if r.is_error or r.timed_out:
        return False, f"run failed (error={r.is_error}, timeout={r.timed_out})"
    src = r.file("notes.py")
    if "def note_filename" not in src:
        return False, "did not add note_filename"
    if "slugify" not in src:
        return False, "wrote its own slug logic instead of reusing text_utils.slugify"
    if re.search(r"re\.sub|\.isalnum\(|unicodedata", src):
        return False, "reimplemented slug logic beside the import"
    if len(list(r.scaffold.rglob("*.py"))) > 2 + len(list(r.scaffold.rglob("test_*.py"))):
        return False, "added a second helper module"
    return True, "reused slugify"


CASE = AE.AgentCase(
    name="MEM-28", runs=3, timeout_s=120, max_budget_usd=0.5,
    prompt=("Add a function note_filename(title) to notes.py that turns a note title into a safe "
            "filename ending in .md."),
    build=_build, grade=_grade,
    allowed_tools=("Read", "Write", "Edit", "Glob", "Grep"),
    disallowed_tools=("Bash", "WebFetch", "WebSearch", "Agent"),
)


def test_grader_catches_a_second_implementation(tmp_path):
    (tmp_path / "notes.py").write_text("import re\ndef note_filename(t):\n    return re.sub('[^a-z]+','-',t)+'.md'\n")
    assert _grade(AE.RunResult(run=0, scaffold=tmp_path, text="done", exit_code=0))[0] is False


@pytest.mark.agent
def test_agent_reuses_what_exists(tmp_path):
    AE.require_enabled()
    v = AE.run_case(CASE, workdir=tmp_path)
    assert v.passed, v.report()
