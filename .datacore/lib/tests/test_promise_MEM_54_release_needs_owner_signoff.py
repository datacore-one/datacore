"""MEM-54: No feature enters a release branch and no release ships without my explicit
sign-off, including agent-written work.

Kind: deterministic. The real in-flight policy every executor's hook applies
(tool_policy.decide, config/tool_effects.yaml + config/approvals_policy.yaml): for every
agent principal, each way work reaches a release branch or a release ships is not
allowed unattended (refused, or paused for the owner's grant = sign-off):
  * pushing to a release branch (release, release/0.13, releases/v2) directly or by refspec;
  * merging into one (code.merge);
  * shipping: gh release create, npm/pnpm publish, twine upload, cargo publish,
    changeset publish, pushing a version tag (the tag is what fires release workflows),
    dispatching a release workflow.
Control: opening a pull request against a release branch stays allowed -- that is the
proposal the owner signs off on.

Seeded failure: the prod.deploy effect dropped from the policy (every ship command
allowed); verified red.
"""
import pytest

import tool_policy

AGENTS = ["winston", "miles", "tris", "data"]

INTO_RELEASE = [
    "git push origin release",
    "git push origin release/0.13",
    "git push origin HEAD:release/0.13",
    "git push origin feature/recall-ranking:releases/v2",
    "gh pr merge 88 --repo plur-ai/plur --merge",
]
SHIP = [
    "gh release create v0.13.0 --notes 'x'",
    "npm publish --access public",
    "pnpm publish -r",
    "twine upload dist/*",
    "cargo publish",
    "npx changeset publish",
    "git push origin v0.13.0",
    "git push origin --tags",
    "gh workflow run release.yml --ref main",
]


@pytest.mark.parametrize("principal", AGENTS)
@pytest.mark.parametrize("command", INTO_RELEASE + SHIP)
def test_agent_cannot_reach_a_release_unattended(principal, command):
    d = tool_policy.decide(principal, "Bash", {"command": command})
    assert not d.allow, f"{principal} may run {command!r} without the owner's sign-off ({d.reason})"


def test_proposing_to_a_release_branch_stays_allowed():
    d = tool_policy.decide("miles", "Bash", {"command": "gh pr create --base release/0.13 --title x --body y"})
    assert d.allow, d.reason
