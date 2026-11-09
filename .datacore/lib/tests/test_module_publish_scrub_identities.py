"""module_publish_scrub: whose identities to scrub is a local list (INS-3)."""
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import module_publish_scrub as m  # noqa: E402


def test_pairs_are_read_in_order_from_the_local_file(tmp_path):
    f = tmp_path / "publish-scrub.local.yaml"
    f.write_text("identities:\n  - [Ada Lovelace, Alice Example]\n  - [Ada, Alice]\n")
    assert m.load_identities(f) == [("Ada Lovelace", "Alice Example"), ("Ada", "Alice")]


def test_no_file_means_no_identities(tmp_path):
    assert m.load_identities(tmp_path / "absent.yaml") == []


def test_a_malformed_pair_is_refused_not_skipped(tmp_path):
    f = tmp_path / "publish-scrub.local.yaml"
    f.write_text("identities:\n  - [only-one]\n")
    with pytest.raises(SystemExit):
        m.load_identities(f)
