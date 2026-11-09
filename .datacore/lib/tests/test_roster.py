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


# ── who answers for a machine (board D1, 2026-10-01) ─────────────────────────
HOSTS = """principals:
  boss: {kind: human, decision: final, hosts: [laptop]}
  helper: {kind: agent, role: health practice, hosts: [laptop]}
  cos: {kind: agent, role: chief of staff, hosts: [box]}
  ops: {kind: agent, role: chief of operations, hosts: [overnight]}
  intel: {kind: agent, hosts: [gateway]}
  migration: {kind: migration, hosts: [laptop, box]}
"""


def test_the_agent_of_a_machine_is_the_agent_principal_that_lives_there(tmp_path):
    p = tmp_path / "principals.yaml"; p.write_text(HOSTS)
    assert roster.resident_agent("box", p) == "cos"
    assert roster.resident_agent("overnight", p) == "ops"
    assert roster.resident_agent("gateway", p) == "intel"
    assert roster.resident_agent("nowhere", p) is None


def test_a_persons_machine_has_no_resident_agent_even_with_an_agent_listed(tmp_path):
    """The owner's machine is the owner's: an agent that also runs there (a
    practice agent on the laptop) does not make it an agent's machine."""
    p = tmp_path / "principals.yaml"; p.write_text(HOSTS)
    assert roster.person_on("laptop", p) == "boss"
    assert roster.resident_agent("laptop", p) is None
    assert roster.person_on("box", p) is None
