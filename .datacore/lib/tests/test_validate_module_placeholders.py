"""The placeholder scan flags markers, not the org keyword in prose."""
import importlib.util, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("vm", ROOT / ".datacore" / "lib" / "validate_module.py")
V = importlib.util.module_from_spec(spec); spec.loader.exec_module(V)


def _issues(tmp_path, text):
    mod = pathlib.Path(tempfile.mkdtemp(dir=tmp_path)); (mod / "commands").mkdir()
    (mod / "commands" / "x.md").write_text(text)
    v = V.ModuleValidator(mod); v.check_placeholders()
    return [i for i in v.issues if "Placeholder" in i]


def test_org_keyword_in_prose_is_not_a_placeholder(tmp_path):
    assert _issues(tmp_path, "- Counts pending TODO items\n- Scans for TODO/DONE counts per section\n") == []


def test_markers_are_placeholders(tmp_path):
    assert len(_issues(tmp_path, "TODO: fill this in\n")) == 1
    assert len(_issues(tmp_path, "See [TODO] and <FIXME> below\n")) == 2
    assert len(_issues(tmp_path, "FIXME: broken\n")) == 1


def test_markers_inside_code_blocks_are_ignored(tmp_path):
    assert _issues(tmp_path, "```\nTODO: example inside code\n```\nand `TODO: inline`\n") == []


def test_other_template_placeholders_still_count(tmp_path):
    assert len(_issues(tmp_path, "Author: <author>\n")) == 1
