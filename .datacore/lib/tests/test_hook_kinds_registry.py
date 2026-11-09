"""Every kind of hook Datacore runs is in one registry (DIP-0024 amendment 2026-11-09).

WHY. In 2026-11 twelve modules declared `post_install` and one each declared
`nightshift_queue` and `nightshift_complete` in module.yaml, and nothing ran
any of them: the CLI installs from requirements.txt / package.json /
install.sh, and no code reads the nightshift kinds. A declaration nobody runs
reads like a promise. The registry now lists each module hook kind with the
file that runs it, and the manifest check reports any kind it does not list.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]
CORE = LIB.parent
REGISTRY = CORE / "registry" / "hooks.yaml"

_spec = importlib.util.spec_from_file_location("module_manifest_check", LIB / "module_manifest_check.py")
mmc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mmc)


def _registry() -> dict:
    return yaml.safe_load(REGISTRY.read_text()) or {}


def test_registry_lists_module_hook_kinds_with_what_runs_them():
    kinds = _registry().get("module_hook_kinds") or {}
    assert {"today", "claude_code"} <= set(kinds)
    for kind, spec in kinds.items():
        run_by = (spec or {}).get("run_by")
        assert run_by, f"module hook kind {kind!r} names nothing that runs it"
        assert (CORE.parent / run_by).exists(), f"{kind}: run_by {run_by} does not exist"


def test_registry_has_no_kind_that_nothing_runs():
    kinds = set(_registry().get("module_hook_kinds") or {})
    assert not kinds & {"post_install", "nightshift_queue", "nightshift_complete"}


def test_registry_records_lifecycle_and_ledger_event_families():
    reg = _registry()
    life = reg.get("lifecycle") or {}
    assert life.get("run_by") == ".datacore/lib/hooks.py"
    assert {"pre_execution", "post_execution", "validation", "error"} <= set(life.get("kinds") or [])
    subs = reg.get("ledger_events") or []
    assert subs, "ledger_events declares no subscriber"
    for s in subs:
        assert {"name", "types", "call", "declared_by"} <= set(s), s


def _fixture_module(tmp_path: Path, hooks: dict) -> Path:
    core = tmp_path / ".datacore"
    (core / "registry").mkdir(parents=True)
    (core / "registry" / "hooks.yaml").write_text(REGISTRY.read_text())
    mod = core / "modules" / "demo"
    mod.mkdir(parents=True)
    (mod / "module.yaml").write_text(yaml.safe_dump({"name": "demo", "hooks": hooks}))
    return mod


def test_manifest_check_reports_a_hook_kind_nothing_runs(tmp_path):
    mod = _fixture_module(tmp_path, {"post_install": "pip install -r requirements.txt"})
    findings = mmc.check(mod)
    assert any("post_install" in f and "nothing runs" in f for f in findings), findings


def test_manifest_check_accepts_a_listed_kind(tmp_path):
    mod = _fixture_module(tmp_path, {"today": {"agent": "x", "slot": "post"}})
    assert not [f for f in mmc.check(mod) if "nothing runs" in f]


def _present_modules():
    root = CORE / "modules"
    return sorted(p for p in root.glob("*/module.yaml")) if root.is_dir() else []


@pytest.mark.parametrize("manifest", _present_modules(), ids=lambda p: p.parent.name)
def test_every_module_here_declares_only_listed_hook_kinds(manifest):
    findings = [f for f in mmc.check(manifest.parent) if "nothing runs" in f]
    assert not findings, findings
