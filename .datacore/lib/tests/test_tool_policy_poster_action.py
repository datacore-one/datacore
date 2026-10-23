"""A posting script is a public post when it is RUN to post, not when it is named.

On 2026-10-22 the communications agent's read-only command

    git -C ~/Data/.datacore/modules/comms status && git pull && sed -n 1,40p lib/x_poster.py

was refused as public.post, because any mention of `x_poster.py` counted as a
post. The vocabulary now judges a shell command by what it does with the
script (task W5):

  * reading it (cat, head, sed -n, grep, git log/show ...) is not a post;
  * running it with only its read-only flags (`--whoami`, `--recent N`, with
    `--account`) is not a post;
  * running it any other way -- the posting flag, an abbreviation, an unknown
    flag, a positional argument, inline Python that imports it, a wrapper the
    classifier does not read, text fed into an interpreter -- IS a post (fail
    closed).
"""
from __future__ import annotations

import pytest

import tool_policy

POSTER = "/home/agent/Data/.datacore/modules/comms/lib/x_poster.py"
INCIDENT = ("git -C ~/Data/.datacore/modules/comms status && git pull && "
            "sed -n 1,40p lib/x_poster.py")


@pytest.fixture(scope="module")
def effects():
    return tool_policy.load_effects()


def posts(effects, command, tool="Bash"):
    return "public.post" in tool_policy.classify(tool, {"command": command}, effects)


def test_the_incident_command_is_not_a_post(effects):
    assert not posts(effects, INCIDENT)


@pytest.mark.parametrize("command", [
    f"cat {POSTER}",
    f"head -40 {POSTER}",
    f"sed -n 1,40p {POSTER}",
    f"sed -n '/def main/,$p' {POSTER}",
    f"grep -n add_argument {POSTER}",
    f"rg -n post_file {POSTER}",
    f"wc -l {POSTER} && ls -la /home/agent/Data/.datacore/modules/comms/lib",
    "git -C ~/Data/.datacore/modules/comms log --oneline -5 -- lib/x_poster.py",
    "git -C ~/Data/.datacore/modules/comms show HEAD:lib/x_poster.py | head -50",
    "git diff -- lib/engagement_post.py",
    "cat .datacore/modules/comms/lib/engagement_post.py",
    "find .datacore/modules/comms -name 'x_poster.py'",
])
def test_reading_a_posting_script_is_not_a_post(effects, command):
    assert not posts(effects, command), command


@pytest.mark.parametrize("command", [
    f"python3 {POSTER} --account plur --whoami",
    f"python3 {POSTER} --account plur --recent 5",
    f"python3 {POSTER} --account=plur --recent=5",
    f"python3 -u {POSTER} --account plur --recent 3 2>&1 | head -5",
    f"cd ~/Data/.datacore/modules/comms/lib && python3 x_poster.py --account plur --whoami",
    f"{POSTER} --whoami --account plur",
])
def test_the_read_only_flags_are_not_a_post(effects, command):
    assert not posts(effects, command), command


@pytest.mark.parametrize("command", [
    f"python3 {POSTER} --account plur --post-file /tmp/post.txt",
    f"python3 {POSTER} --account=plur --post-file=/tmp/post.txt",
    f"{POSTER} --account plur --post-file /tmp/post.txt",
    f"cd lib && python3 x_poster.py --account plur --post-file post.txt",
    f"python3 {POSTER} --account plur --whoami > /tmp/o --post-file /tmp/post.txt",
    # argparse accepts an unambiguous abbreviation: --post means --post-file
    f"python3 {POSTER} --account plur --post /tmp/post.txt",
    # an unknown flag, or a positional, is not known to be harmless
    f"python3 {POSTER} --account plur --dry-run",
    f"python3 {POSTER} --account plur hi",
    f"python3 {POSTER} --account plur --rec 5",
    # inline python that imports the poster
    "python3 -c 'from x_poster import post; post(\"hi\")'",
    "python3 - <<'EOF'\nimport x_poster\nx_poster.main()\nEOF",
    "echo 'import x_poster; x_poster.main()' | python3",
    "python3 -m x_poster --account plur --whoami",
    # wrappers and substitutions the classifier does not read
    f"bash -lc 'python3 {POSTER} --account plur --post-file p.txt'",
    f"echo $(python3 {POSTER} --account plur --post-file p.txt)",
    f"echo `python3 {POSTER} --account plur --post-file p.txt`",
    f"uv run python {POSTER} --account plur --post-file p.txt",
    f"echo p.txt | xargs python3 {POSTER} --account plur --post-file",
    f"find . -name x_poster.py -exec python3 {{}} --account plur --post-file p.txt \\;",
    # the other posting scripts have no read-only form
    "python3 .datacore/modules/comms/lib/engagement_post.py --text 'Big news today'",
    "python3 chrome_poster.py",
])
def test_an_actual_post_is_still_caught(effects, command):
    assert posts(effects, command), command


def test_a_non_shell_tool_that_names_the_poster_still_counts(effects):
    assert "public.post" in tool_policy.classify(
        "execute_code", {"code": "import x_poster\nx_poster.main(['--account', 'plur', '--post-file', 'p'])"},
        effects)


def test_the_poster_named_outside_the_command_field_still_counts(effects):
    call = {"command": f"cat {POSTER}", "cmd": ["bash", "-lc", f"python3 {POSTER} --account a --post-file p"]}
    assert "public.post" in tool_policy.classify("Bash", call, effects)


def test_the_policy_now_lets_the_incident_through_but_not_a_post(tmp_path):
    """End to end through decide(): the incident command is not refused as a
    post for the communications agent; a real post by an agent whose policy
    never allows posting is still refused as one (AGT-1). The principals'
    real entries live in the private overlay, so a minimal policy stands in."""
    policy = tmp_path / "approvals_policy.yaml"
    policy.write_text("version: 1\napprover: owner\ncosign_effects: [public.post]\nprincipals:\n"
                      "  owner: {}\n  data: {}\n  miles: {never_effects: [public.post]}\n")
    d = tool_policy.decide("data", "Bash", {"command": INCIDENT}, policy_path=policy)
    assert "public.post" not in d.effects, d.reason
    d = tool_policy.decide("data", "Bash", {"command": f"python3 {POSTER} --account plur --post-file p.txt"},
                           policy_path=policy)
    assert not d.allow and d.kind == "cosign"
    d = tool_policy.decide("miles", "Bash", {"command": f"python3 {POSTER} --account plur --post-file p.txt"},
                           policy_path=policy)
    assert not d.allow and d.kind == "never" and "public.post" in d.effects
