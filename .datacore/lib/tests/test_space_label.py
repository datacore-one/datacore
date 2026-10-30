"""A space's ledger label is the space, not the folder its clone happens to sit in.

Tris's clone of the PLUR space (repository plur-space) sits in his pod at
~/Data/2-plur; everywhere else it is 5-plur. `genesis.scan` labelled every
item it admitted with `space_dir.name`, so his creates carried
"space": "2-plur", and the projector's foreign-space filter dropped them from
5-plur's task list on every other host: four GEO review tasks that existed in
the ledger and appeared nowhere (owner decision fix-a, fleet console 2026-10-30).

The label now comes from the repository registry: a folder that is itself a
registry key keeps its own name (no change on any host whose folders are
canonical); a clone in another folder takes the key of the registry entry for
its origin repository. And the projector reads two labels of the same space
-- the same name under different local number prefixes -- as one space, so
the creates already written as 2-plur are visible too.
"""
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

from ledger.fold import fold  # noqa: E402
from ledger.genesis import scan, space_label  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger.projector import projected_items  # noqa: E402

REGISTRY = ("repositories:\n"
            "  5-plur:\n"
            "    category: knowledge\n"
            "    host: github\n"
            "    transport: git\n"
            "    repo: plur-space\n")

TASK = ("* TODO Review GEO draft\n"
        "  :PROPERTIES:\n"
        "  :ID:       geo-draft-review\n"
        "  :END:\n")


def _root(tmp_path: Path) -> Path:
    (tmp_path / ".datacore" / "registry").mkdir(parents=True)
    (tmp_path / ".datacore" / "registry" / "repositories.yaml").write_text(REGISTRY)
    return tmp_path


def _clone(root: Path, folder: str, repo: str = "plur-space") -> Path:
    space = root / folder
    (space / "org").mkdir(parents=True)
    (space / ".datacore" / "events").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(space)], check=True)
    subprocess.run(["git", "-C", str(space), "remote", "add", "origin",
                    f"https://example.invalid/org/{repo}.git"], check=True)
    return space


def test_a_canonical_folder_keeps_its_own_name(tmp_path):
    assert space_label(_clone(_root(tmp_path), "5-plur")) == "5-plur"


def test_a_clone_in_another_folder_takes_the_registry_name_of_its_repo(tmp_path):
    assert space_label(_clone(_root(tmp_path), "2-plur")) == "5-plur"


def test_an_unregistered_repo_falls_back_to_the_folder(tmp_path):
    assert space_label(_clone(_root(tmp_path), "2-other", repo="other-space")) == "2-other"


def test_genesis_labels_a_misfiled_clone_with_the_canonical_space(tmp_path):
    space = _clone(_root(tmp_path), "2-plur")
    (space / "org" / "next_actions.org").write_text(TASK)
    payloads = scan(space).importable
    assert [p["space"] for p in payloads if p["id"] == "geo-draft-review"] == ["5-plur"]


def test_the_projector_reads_another_hosts_prefix_as_the_same_space(tmp_path):
    space = tmp_path / "5-plur"
    (space / ".datacore" / "events").mkdir(parents=True)
    log = EventLog(space, "tris")
    for iid, label in (("same-space", "2-plur"), ("this-space", "5-plur"), ("foreign", "3-other")):
        log.append("item.create", {"id": iid, "title": iid, "state": "TODO", "space": label, "level": 1,
                                   "tags": [], "org": {"body": "", "properties": {}, "priority": None}})
    ids = {i.id for i in projected_items(fold(read_events(space)), space="5-plur")}
    assert ids == {"same-space", "this-space"}
