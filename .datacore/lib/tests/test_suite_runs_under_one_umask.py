"""The suite creates its sandbox folders the same way on every host."""
import os


def test_a_folder_a_test_creates_is_not_group_writable(tmp_path):
    made = tmp_path / "home"
    made.mkdir()
    assert not made.stat().st_mode & 0o022, "the host's login umask leaked into the suite"
