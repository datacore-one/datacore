"""process_inventory names no machine or space of this install's (INS-3).

Which machines carry schedules comes from the roster (registry/infrastructure.yaml);
where the inventory is written comes from the space holding the `system` role
(install.yaml). A fresh install ships neither.
"""
import process_inventory as P


ROSTER = """roles:
  always_on: hub
servers:
  laptop: {kind: workstation}
  hub: {kind: server, ssh_alias: hub-alias}
  worker: {kind: server}
  gone: {kind: server, retired: true}
"""


def test_the_schedules_read_are_the_rosters_servers(tmp_path):
    infra = tmp_path / "infrastructure.yaml"
    infra.write_text(ROSTER)
    assert P.schedule_places(infra) == [("hub", False), ("hub", True), ("worker", False)]


def test_the_inventory_is_written_into_the_system_space(tmp_path):
    (tmp_path / "7-sys" / ".datacore").mkdir(parents=True)
    (tmp_path / "install.yaml").write_text("roles:\n  system: sys\n")
    assert P.default_out(tmp_path) == tmp_path / "7-sys" / "1-tracks" / "dev" / "architecture" / "processes.yaml"


def test_without_a_system_space_it_goes_to_the_personal_space(tmp_path):
    assert P.default_out(tmp_path).relative_to(tmp_path).parts[0] == "0-personal"
