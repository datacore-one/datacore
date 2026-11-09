"""INS-1: "A new team installs Datacore on one machine by following the guide,
without editing any of Datacore's own files."

Kind: deterministic. A fresh install is built from `git archive HEAD` in tmp and
INSTALL.md's local steps are run exactly as written (see _fresh_install.py).
  - every step the guide gives succeeds as written;
  - no file Datacore tracks was modified to get there;
  - the host installers a new team would run next take their root and user
    from the environment, not from constants naming this fleet's layout
    (audit C3: the only installer hardcoded /root/Data), so running them needs
    no edit either.

Seeded failure: the guide copies templates into a directory a fresh checkout
does not have (0-personal/ is gitignored), or an installer must be edited to
point at the new team's home.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fresh_install as F  # noqa: E402

DATA = F.REAL
INSTALLERS = [
    ".datacore/lib/v2_box_setup.sh",
    ".datacore/lib/agent_host_setup.sh",
]


@pytest.fixture(scope="module")
def install(tmp_path_factory):
    return F.follow_guide(tmp_path_factory.mktemp("ins1"))


def test_the_harness_follows_the_current_guide():
    text = F.guide_text()
    missing = [c for _, c in F.GUIDE_STEPS if c not in text]
    assert missing == [], f"INSTALL.md no longer says: {missing} -- update _fresh_install.GUIDE_STEPS"


def test_every_guide_step_succeeds_as_written(install):
    assert install.failed_steps() == [], "\n".join(install.failed_steps())


def test_no_datacore_file_was_edited(install):
    assert install.modified_tracked() == [], install.modified_tracked()


_HARD = re.compile(r"/root/Data\b|/root/\.datacore\b|/home/[a-z][\w-]*/")


@pytest.mark.parametrize("rel", INSTALLERS)
def test_host_installers_need_no_edit(rel):
    text = (DATA / rel).read_text()
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    hard = [l.strip()[:120] for l in code.splitlines() if _HARD.search(l)]
    assert hard == [], f"{rel} hardcodes an install location a new team would have to edit: {hard}"
