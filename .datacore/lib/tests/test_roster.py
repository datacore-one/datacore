"""roster: this install's own people and agents come from its principals.yaml.

INS-3 batch 2: shipped code names no person or agent of ours; the names are
looked up here, from the gitignored registry, and a fresh install (no
registry) gets nothing rather than our names.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import roster  # noqa: E402

REG = """principals:
  boss:
    kind: human
    display: Boss
    decision: final
    writes_as: [laptop]
  ops:
    kind: agent
    display: Opsy
    role: Chief of Operations
    writes_as: [ops, runner]
  cos:
    kind: agent
    display: Cosy
    role: chief of staff
    writes_as: [cos]
delegation_exercise:
  ring: {cos: [ops], ops: [cos]}
  forbidden: [ops, boss]
"""


def _reg(tmp_path):
    p = tmp_path / "principals.yaml"
    p.write_text(REG)
    return p


def test_owner_role_display_and_agents_come_from_the_registry(tmp_path):
    p = _reg(tmp_path)
    assert roster.owner(p) == "boss"
    assert roster.by_role("chief of operations", p) == "ops"
    assert roster.by_role("CHIEF OF STAFF", p) == "cos"
    assert roster.by_role("nobody", p) is None
    assert roster.display("ops", p) == "Opsy"
    assert roster.display("stranger", p) == "stranger"
    assert roster.agents(p) == ["ops", "cos"]
    assert roster.entries(p)["ops"]["writes_as"] == ["ops", "runner"]


def test_a_section_is_read_from_the_same_file(tmp_path):
    p = _reg(tmp_path)
    assert roster.section("delegation_exercise", p)["forbidden"] == ["ops", "boss"]
    assert roster.section("absent", p) == {}


def test_a_fresh_install_has_no_names_at_all(tmp_path):
    missing = tmp_path / "none.yaml"
    assert roster.owner(missing) is None
    assert roster.by_role("chief of staff", missing) is None
    assert roster.agents(missing) == []
    assert roster.entries(missing) == {}
    assert roster.section("delegation_exercise", missing) == {}


def test_a_broken_registry_reads_as_empty_not_as_a_crash(tmp_path):
    p = tmp_path / "principals.yaml"
    p.write_text("principals: [not, a, mapping]\n")
    assert roster.owner(p) is None and roster.agents(p) == []
    assert roster.section("x", p) == {}
