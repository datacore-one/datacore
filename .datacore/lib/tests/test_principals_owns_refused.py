"""principals.yaml says who a principal is; venture.yaml says what it owns (DIP-0050)."""
import pathlib, sys
LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
import principals_check  # noqa: E402


def _root(tmp_path, body):
    reg = tmp_path / ".datacore" / "registry"; reg.mkdir(parents=True)
    (reg / "principals.yaml").write_text(body)
    return tmp_path


def test_an_owns_block_is_flagged(tmp_path):
    root = _root(tmp_path, "principals:\n  tris:\n    kind: agent\n    owns:\n      roles: [plur:cio]\n")
    [row] = principals_check.check(root)
    assert any(m.startswith("owns:") for m in row["missing"])


def test_a_principal_without_owns_is_not_flagged_for_it(tmp_path):
    root = _root(tmp_path, "principals:\n  tris:\n    kind: agent\n")
    [row] = principals_check.check(root)
    assert not any(m.startswith("owns:") for m in row["missing"])
