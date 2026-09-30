"""The restricted-networks guard and directories only known at run time.

Owner decision 2026-09-30 ("Network guard: smarter"): 66 ordinary pushes and
pulls of GitHub repositories were refused in 48 hours because git ran in a
directory the guard could not name before the command ran (`for s in a b; do
git -C $s push; done`, `W=/x && cd $W && git fetch`). The guard must stop
refusing those when it can name every directory exactly, and keep refusing
whenever it cannot — it never guesses.

Every case runs the real hook script with the JSON Claude Code sends. HOME is
a throwaway directory whose private list restricts the fictional `acme`; the
public list's private ranges apply as shipped. Nothing here reaches a network:
the guard only reads git config of throwaway repositories.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
GUARD = Path(os.environ.get("GUARDS_UNDER_TEST", LIB / "hooks")) / "restricted_hosts_guard.py"
RANGE = "192.168.253"            # a range the shipped public list restricts
GITHUB = "https://github.com/ok/{}.git"


def _env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and k not in ("XDG_CONFIG_HOME", "CDPATH")}
    env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1")
    return env


@pytest.fixture
def machine(tmp_path):
    """A fake machine: HOME with a private list naming `acme`, and repositories
    ok1, ok2 (GitHub), acme_repo (on acme) and lan_repo (in a restricted range)."""
    home = tmp_path / "home"
    (home / ".datacore" / "private").mkdir(parents=True)
    (home / ".datacore" / "private" / "customer-denylist.yaml").write_text(
        "restricted_hosts:\n  hosts: [acme]\n")
    work = tmp_path / "work"
    work.mkdir()
    env = _env(home)

    def repo(name: str, url: str) -> Path:
        path = work / name
        subprocess.run(["git", "init", "-q", str(path)], check=True, env=env)
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", url], check=True, env=env)
        return path

    repos = {
        "ok1": repo("ok1", GITHUB.format("ok1")),
        "ok2": repo("ok2", GITHUB.format("ok2")),
        "acme_repo": repo("acme_repo", "https://git.acme.example/x/y.git"),
        "lan_repo": repo("lan_repo", f"ssh://git@{RANGE}.7/srv/y.git"),
    }
    return {"home": home, "work": work, "env": env, **repos}


def run(machine, command: str, cwd: Path | None = None):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command},
                          "cwd": str(cwd or machine["work"])})
    p = subprocess.run([sys.executable, str(GUARD)], input=payload, text=True,
                       capture_output=True, env=machine["env"], timeout=60)
    return p.returncode, p.stderr


def allowed(machine, command, cwd=None) -> bool:
    code, err = run(machine, command, cwd)
    assert code in (0, 2), err
    return code == 0


# ── the shapes that were refused: every directory is spelled out ─────────────

@pytest.mark.parametrize("command", [
    "for r in ok1 ok2; do git -C $r push; done",
    "for r in ok1 ok2; do git -C \"$r\" fetch -q origin 2>&1 | tail -1; done",
    "for r in ok1 ok2; do echo \"== $r\"; (cd $r && git pull --ff-only -q); done",
    "for s in ok1 ok2; do printf '%s: ' $s; git -C $s fetch -q 2>/dev/null; git -C $s status -sb | head -1; done",
    "W={work}/ok1 && cd $W && git fetch -q origin && git status",
    "W={work}/ok1; cd $W && git push -q origin HEAD:main",
    "S={work}; R=$S/ok2; git -C $R fetch -q",
    "cd {work} && for m in ok1 ok2; do git -C {work}/$m push -q 2>&1 | grep -v '^remote:'; done",
    "for r in {work}/ok1 {work}/ok2; do cd ${{r}} && git fetch -q origin && echo ok; done",
])
def test_every_directory_known_and_clean_is_allowed(machine, command):
    assert allowed(machine, command.format(work=machine["work"])), command


@pytest.mark.parametrize("command", [
    "for s in ok1 ok2; do git -C $s commit -qm \"Miles's change\"; git -C $s push; done",  # `'s` is not `read s`
    "for f in ok1 ok2; do n=${{f%%/*}}; git -C $f push; done",                  # a read, not a binding
    "S={work}; python3 -c \"print('$S')\"; git -C $S/ok1 push",                # ' inside \" is plain text
    "for s in ok1 acme_repo; do git -C $s remote -v; git -C $s remote get-url origin; done",  # local
    "cat > notes.md <<'EOF'\ngit -C $W push\nEOF\ngit -C {work}/ok1 push",       # a document, not commands
    "for r in ok1 ok2; do echo \"== $r $(git -C $r fetch -q && echo ok)\"; done",  # sees the loop variable
])
def test_what_the_parser_can_read_exactly_is_allowed(machine, command):
    assert allowed(machine, command.format(work=machine["work"])), command


@pytest.mark.parametrize("command", [
    "bash <<'EOF'\ncd $W && git push\nEOF",                                     # a script for bash
    "for r in ok1 acme_repo; do echo \"== $r $(git -C $r fetch -q)\"; done",
    "W={work}/ok1; cd '$W' && git push",                                        # the literal text $W
    "for s in ok1 acme_repo; do git -C $s remote update; done",                 # contacts the remote
])
def test_what_the_parser_reads_exactly_is_still_refused(machine, command):
    assert not allowed(machine, command.format(work=machine["work"])), command


# ── the same shapes reaching a restricted repository stay refused ────────────

@pytest.mark.parametrize("bad, named", [("acme_repo", "acme"), ("lan_repo", RANGE)])
@pytest.mark.parametrize("template", [
    "for r in ok1 {bad} ok2; do git -C $r push; done",
    "W={work}/{bad} && cd $W && git fetch -q origin",
    "for r in ok1 {bad}; do (cd $r && git pull --ff-only -q); done",
    "S={work}; R=$S/{bad}; git -C $R fetch -q",
])
def test_a_restricted_repository_among_them_is_refused_and_named(machine, bad, named, template):
    code, err = run(machine, template.format(work=machine["work"], bad=bad))
    assert code == 2, template
    assert named in err, err


def test_restricted_range_remote_in_the_start_directory_is_refused(machine):
    """A remote in a restricted range is refused like a restricted host name:
    until now resolved remotes were matched against host names only."""
    code, err = run(machine, "git push", cwd=machine["lan_repo"])
    assert code == 2 and RANGE in err, err


# ── no stale state: the remote is read when the command is judged ───────────

def test_a_remote_changed_after_an_allowed_run_is_refused(machine):
    command = "for r in ok1 ok2; do git -C $r push; done"
    assert allowed(machine, command)
    subprocess.run(["git", "-C", str(machine["ok2"]), "remote", "set-url", "origin",
                    "https://git.acme.example/z.git"], check=True, env=machine["env"])
    assert not allowed(machine, command)


def test_a_repository_added_after_an_allowed_run_is_refused(machine):
    command = "for r in ok1 new; do git -C $r push; done"
    assert allowed(machine, command)            # `new` does not exist: nothing to reach
    subprocess.run(["git", "init", "-q", str(machine["work"] / "new")], check=True, env=machine["env"])
    subprocess.run(["git", "-C", str(machine["work"] / "new"), "remote", "add", "origin",
                    f"ssh://{RANGE}.9/r.git"], check=True, env=machine["env"])
    assert not allowed(machine, command)


# ── insteadOf rewrites ─────────────────────────────────────────────────────

def _rewrite_github_to_acme(machine):
    (machine["home"] / ".gitconfig").write_text(
        '[url "https://git.acme.example/"]\n\tinsteadOf = https://github.com/\n')


def test_global_insteadof_into_a_restricted_host_is_refused(machine):
    _rewrite_github_to_acme(machine)
    assert not allowed(machine, "for r in ok1 ok2; do git -C $r push; done")
    assert not allowed(machine, "git push", cwd=machine["ok1"])


def test_insteadof_applies_to_a_url_given_in_the_command(machine):
    _rewrite_github_to_acme(machine)
    assert not allowed(machine, "git clone https://github.com/ok/fresh.git")
    assert not allowed(machine, f"git -C {machine['ok1']} push https://github.com/ok/other.git main")


def test_pushinsteadof_in_repository_config_is_refused(machine):
    subprocess.run(["git", "-C", str(machine["ok1"]), "config", "url.ssh://git.acme.example/.pushInsteadOf",
                    "https://github.com/"], check=True, env=machine["env"])
    assert not allowed(machine, "for r in ok1 ok2; do git -C $r push; done")


# ── remotes set in the same command ─────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "git -C {ok1} remote set-url origin acme:x/y.git && git -C {ok1} push",
    "git -C {ok1} config remote.origin.url acme:x/y.git && git -C {ok1} push",
    "git -C {ok1} push acme:x/y.git main",                     # scp form without user@
    "git -C {ok1} config remote.origin.url \"$U\" && git -C {ok1} push",   # value known only at run time
    "git -C {ok1} -c remote.origin.url=acme:x push",
    "git -C {ok1} -c include.path=/tmp/other.cfg push",
    "GIT_CONFIG_GLOBAL=/tmp/other.cfg git -C {ok1} push",
    "HOME=/tmp/elsewhere git -C {ok1} push",
    "GIT_SSH_COMMAND='ssh -J acme' git -C {ok1} push",
])
def test_remote_config_changed_or_overridden_in_the_command_is_refused(machine, command):
    assert not allowed(machine, command.format(ok1=machine["ok1"])), command


# ── still refused: directories that cannot be named ─────────────────────────

@pytest.mark.parametrize("command", [
    "cd $REPO && git push",                                   # set outside the command
    "cd $(mktemp -d) && git init -q && git push",             # made at run time
    "for d in */; do git -C $d push; done",                   # a glob, expanded at run time
    "for d in $(ls); do git -C $d push; done",
    "(W={work}/ok1); git -C $W push",                         # assignment inside a subshell
    "false && W={work}/ok1; git -C $W push",                  # assignment may not have run
    "W={work}/ok1 | true; git -C $W push",                    # assignment in a pipeline
    "for d in ok1; do read d; git -C $d push; done",          # rebound by read
    "W={work}/ok1; eval \"W=/somewhere/else\"; git -C $W push",
    "W={work}/ok1; f() {{ W=/x; }}; f; git -C $W push",       # rebound by a function
    "W={work}/ok1; export W=/x; git -C $W push",
    "W={work}/ok1; : ${{W:=/x}}; git -C ${{W%/}} push",       # modified by an expansion
    "W={work}/ok1; (( W=1 )); git -C $W push",
    "W={work}/ok1; source ./env.sh; git -C $W push",
    "W=\"{work}/ok1 {work}/ok2\"; git -C $W push",            # would be split into words
    "while read d; do git -C $d push; done < list.txt",
    "while true; do cd sub; git push; break; done",            # cd in a loop of unknown length
])
def test_a_directory_that_cannot_be_named_is_still_refused(machine, command):
    assert not allowed(machine, command.format(work=machine["work"])), command


def test_heredoc_text_is_not_an_assignment(machine):
    """Lines of a here-document are data. `W=...` in one assigns nothing."""
    command = ("cat > notes.txt <<'EOF'\nW={work}/ok1\nEOF\ngit -C $W push").format(work=machine["work"])
    assert not allowed(machine, command)


def test_relative_cd_repeated_by_a_loop_reaches_the_nested_repository(machine):
    """Each pass of a loop starts where the last one ended: the second
    `cd sub` lands in sub/sub. Until now a loop body was read once."""
    sub = machine["work"] / "sub"
    subprocess.run(["git", "init", "-q", str(sub)], check=True, env=machine["env"])
    subprocess.run(["git", "-C", str(sub), "remote", "add", "origin", GITHUB.format("sub")],
                   check=True, env=machine["env"])
    inner = sub / "sub"
    subprocess.run(["git", "init", "-q", str(inner)], check=True, env=machine["env"])
    subprocess.run(["git", "-C", str(inner), "remote", "add", "origin", "https://git.acme.example/i.git"],
                   check=True, env=machine["env"])
    assert not allowed(machine, "for x in 1 2; do cd sub && git push; done")
    assert allowed(machine, "for x in 1; do cd sub && git push; done")


# ── directories the command itself creates ─────────────────────────────────

def test_a_new_directory_inside_a_restricted_repository_is_refused(machine):
    """git in a directory made by the command uses the repository around it."""
    assert not allowed(machine, "mkdir -p acme_repo/tmp && git -C acme_repo/tmp push")
    assert not allowed(machine, "for d in acme_repo/tmp; do mkdir -p $d; git -C $d fetch; done")


def test_a_repository_copied_into_place_is_refused(machine):
    """A directory that is not a repository yet may become one by a copy."""
    assert not allowed(machine, "cp -R acme_repo copy && git -C copy push")
    assert not allowed(machine, "rsync -a acme_repo/ plain/ && cd plain && git push")


def test_a_worktree_of_a_restricted_repository_is_refused(machine):
    assert not allowed(machine, "git -C acme_repo worktree add ../wt && git -C wt push")


def test_submodule_urls_are_reached_by_a_fetch(machine):
    subprocess.run(["git", "-C", str(machine["ok1"]), "config", "submodule.lib.url",
                    "https://git.acme.example/lib.git"], check=True, env=machine["env"])
    assert not allowed(machine, "for r in ok1; do git -C $r pull; done")


def test_a_long_loop_stays_fast(machine):
    """Candidate directories must not multiply without bound."""
    import time
    words = " ".join(f"d{i}" for i in range(12))
    start = time.time()
    code, _ = run(machine, f"for s in {words}; do cd $s 2>/dev/null || continue; git pull -q; done")
    assert time.time() - start < 20
    assert code in (0, 2)


# ── local git is untouched ─────────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "cd $REPO && git status",
    "for d in $(ls); do git -C $d log --oneline -1; done",
])
def test_local_git_in_an_unknown_directory_still_passes(machine, command):
    assert allowed(machine, command)
