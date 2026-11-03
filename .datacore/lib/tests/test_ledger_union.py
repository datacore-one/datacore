"""One work view across spaces by a read-time union (plan 2026-10-31, phase G option (a),
owner's choice 2026-11-03).

Each space is folded exactly as today (`authoritative_state`); the results are
unioned keyed by (space, id), because 696 item ids are created in more than one
space and folding them as one stream would merge different tasks. Nothing is
written. A space that cannot be folded is named, and the installation root is
withheld rather than computed over fewer spaces. Written before the code.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_ledger_cli import run_cli  # noqa: E402

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))


def _space(root, name, items, tmp_path):
    space = root / name
    space.mkdir(parents=True)
    for iid, title in items:
        r = run_cli("append", "--space", str(space), "--type", "item.create",
                    "--payload", json.dumps({"id": iid, "title": title, "state": "TODO"}),
                    tmp_path=tmp_path)
        assert r.returncode == 0, r.stderr
    return space


def _snapshot(root):
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_same_id_in_two_spaces_stays_two_items(tmp_path):
    from ledger.union import union_state
    root = tmp_path / "Data"
    _space(root, "0-personal", [("t1", "Pay the bill"), ("t2", "Call back")], tmp_path)
    _space(root, "5-plur", [("t1", "Ship the release")], tmp_path)
    u = union_state(root)
    assert set(u.items) == {("0-personal", "t1"), ("0-personal", "t2"), ("5-plur", "t1")}
    assert u.items[("5-plur", "t1")].title == "Ship the release"
    assert u.items[("0-personal", "t1")].title == "Pay the bill"


def test_folders_without_a_ledger_are_not_spaces(tmp_path):
    from ledger.union import union_state
    root = tmp_path / "Data"
    _space(root, "2-datacore", [("a", "x")], tmp_path)
    (root / "3-empty").mkdir()
    (root / "notes").mkdir()
    assert list(union_state(root).roots) == ["2-datacore"]


def test_installation_root_is_one_hash_over_each_spaces_root(tmp_path):
    from ledger.hub import authoritative_state
    from ledger.union import union_state
    root = tmp_path / "Data"
    a = _space(root, "0-personal", [("t1", "a")], tmp_path)
    b = _space(root, "5-plur", [("t1", "b")], tmp_path)
    u = union_state(root)
    assert u.roots == {"0-personal": authoritative_state(a).state_root(),
                       "5-plur": authoritative_state(b).state_root()}
    r1 = u.installation_root()
    assert isinstance(r1, str) and len(r1) == 64
    run_cli("append", "--space", str(b), "--type", "item.create",
            "--payload", json.dumps({"id": "t9", "title": "new", "state": "TODO"}), tmp_path=tmp_path)
    assert union_state(root).installation_root() != r1


def test_a_space_that_fails_is_named_and_the_root_is_withheld(tmp_path):
    from ledger.union import union_state
    root = tmp_path / "Data"
    _space(root, "0-personal", [("t1", "a")], tmp_path)
    bad = _space(root, "6-meridian", [("m1", "b")], tmp_path)

    def fold(space_dir):
        if Path(space_dir).name == "6-meridian":
            raise ValueError("boom")
        from ledger.hub import authoritative_state
        return authoritative_state(space_dir)

    u = union_state(root, fold=fold)
    assert "6-meridian" in u.errors and "boom" in u.errors["6-meridian"]
    assert u.installation_root() is None
    assert ("0-personal", "t1") in u.items and not any(s == bad.name for s, _ in u.items)


def test_the_cli_reads_and_writes_nothing(tmp_path):
    root = tmp_path / "Data"
    _space(root, "0-personal", [("t1", "a")], tmp_path)
    _space(root, "5-plur", [("t1", "b")], tmp_path)
    before = _snapshot(root)
    r = subprocess.run([sys.executable, str(LIB / "ledger" / "union.py"), "--root", str(root), "--json"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["spaces"]["0-personal"]["items"] == 1 and out["spaces"]["5-plur"]["items"] == 1
    assert len(out["installation_root"]) == 64 and out["errors"] == {}
    assert _snapshot(root) == before, "the union must not write"
