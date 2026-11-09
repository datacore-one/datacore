"""Shared contract for the AUD-1..7 promise evals (spec: .datacore/specs/cross-model-audit.md).

Nothing of the nightly cross-model audit is built yet (2026-09-26). These evals
are written first, from the spec's DONE_WHEN and boundaries, so they state the
interface the build must meet. Where the spec names a path, the eval uses it;
where it names only a behaviour, the eval names the smallest interface that
makes it checkable:

  .datacore/lib/cross_model_audit.py
    AGENTS                      {agent: model family}, four agents, four families
    capabilities()              the rotation set: unique capability keys in the
                                owner-reviewed promise files, read fresh
    rotation(caps, night, agents=None) -> {agent: capability}
    validate_findings(path, repo=ROOT) -> list[str]   schema errors, [] when valid
    aggregate(night_dir) -> {"confirmed": [...], "unconfirmed": [...]}
    calibrate(answer_key, findings_by_family) -> {family: {"recall", "false_positives"}}
    reviewer_for(author_family) -> family
    ready_for_owner(pr) -> bool      pr = {"author_family", "reviews": [{"family", "kind"}]}
    NIGHTLY_CAP_USD             {agent: positive USD cap}
    may_start(agent, spent_usd, cap_usd=None) -> bool
    night_alerts(night_dir, agents=None) -> list[str]   what The Firm is told next morning
  principal `auditor` in config/approvals_policy.yaml, enforced by tool_policy.decide

Findings file (one per agent per night), at
  2-datacore/1-tracks/dev/audits/nightly/<YYYY-MM-DD>/<agent>.yaml:

  agent: miles
  model: claude            # family
  capability: tasks
  commit: <40-hex sha that exists in the repo it read>
  findings:
    - promise: TSK-2
      claim: ...
      evidence: .datacore/lib/foo.py:123    # file:line at `commit`
      severity: high|medium|low
      seeded_failure: ...
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / ".datacore" / "lib" / "cross_model_audit.py"
NIGHTLY = ROOT / "2-datacore" / "1-tracks" / "dev" / "audits" / "nightly"
AGENTS = {"miles": "claude", "winston": "deepseek", "tris": "glm", "data": "gpt"}


def audit_module():
    """The built module, or a clear red naming what is missing."""
    if not MODULE.is_file():
        pytest.fail(f"not built: {MODULE.relative_to(ROOT)} does not exist "
                    f"(spec .datacore/specs/cross-model-audit.md)")
    spec = importlib.util.spec_from_file_location("cross_model_audit", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def head_sha(repo: Path = ROOT) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                          text=True, timeout=30, check=True).stdout.strip()


def findings_file(path: Path, *, agent, capability, commit, findings, model=None):
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"agent": agent, "model": model or AGENTS.get(agent, agent),
                                    "capability": capability, "commit": commit,
                                    "findings": findings}, sort_keys=False))
    return path


def finding(promise="TSK-2", evidence=".datacore/lib/promise_evals.py:10", claim="x", severity="high"):
    return {"promise": promise, "claim": claim, "evidence": evidence, "severity": severity,
            "seeded_failure": "duplicate :ID: in one org file"}
