"""Reading the git hooks-path setting is diagnosis; only changing it unplugs the git guards.

2026-09-30: the guard refused a read-only `git config` of the setting six times
in one session (the owner: "some hooks and guards are maybe too restrictive").
Owner-approved: a read is allowed; every write form stays refused (MEM-09).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import config_protection as C  # noqa: E402

KEY = "core." + "hooksPath"


@pytest.mark.parametrize("cmd", [
    f"git config {KEY}",
    f"git config --global {KEY}",
    f"git config --get {KEY}",
    f"git config --global --get {KEY}",
    f"git config --show-origin --get {KEY} && git status",
    f"git -C ~/Data config --get {KEY}",
])
def test_reading_the_setting_is_allowed(cmd):
    assert C.switch_off("Bash", {"command": cmd}) is None, cmd


@pytest.mark.parametrize("cmd", [
    f"git config --global {KEY} /dev/null",
    f"git config {KEY} /tmp/nohooks",
    f"git config --unset {KEY}",
    f"git config --global --unset-all {KEY}",
    f"git -c {KEY}=/dev/null commit -m x",
    f"git config --get {KEY}; git config --global {KEY} /dev/null",
    f"GIT_CONFIG_PARAMETERS=\"'{KEY}'='/dev/null'\" git commit -m x",
    f"git config --global --replace-all {KEY} /dev/null",
    f"git config --add {KEY} /dev/null",
])
def test_changing_the_setting_is_still_refused(cmd):
    assert C.switch_off("Bash", {"command": cmd}), cmd
