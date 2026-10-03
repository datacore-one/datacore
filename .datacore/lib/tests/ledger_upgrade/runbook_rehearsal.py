#!/usr/bin/env python3
"""Rehearse the profile A runbook on a clean machine (not a test; not collected).

Runs the commands of `.datacore/docs/ledger-team-install.md` as written --
doctor, init, principals add, append/claim/complete, items, verify, git init,
first converge into an empty remote, a hand-written line refused by the write
gate, the daily verify, the ledger/* branch check, and a void by a second person
from their own login -- in a throwaway HOME and data root (`_install_kit`), with
a bare git remote in tmp. Nothing outside tmp is touched.

It is the machine half of the I4 drill: it shows the commands still work. It
does not replace I4, which is a person who has never seen the code following the
page alone.

    python3 .datacore/lib/tests/ledger_upgrade/runbook_rehearsal.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _install_kit as K  # noqa: E402

ALICE = '''
set -e
DC={root}; LIB="$DC/.datacore/lib"; SPACE="$DC/team"; export DATACORE_ROOT="$DC"
echo "## 2.2 doctor on a new machine"; python3 "$LIB/ledger_cli.py" doctor --space "$SPACE" || true
echo "## 2.3 init"; python3 "$LIB/ledger_cli.py" init --space "$SPACE" --actor alice --email alice@example.org
echo "## 2.4 teammate"; python3 "$LIB/ledger_cli.py" principals add --actor bob --kind human --email bob@example.org
python3 "$LIB/ledger_cli.py" doctor --space "$SPACE"
echo "## 3 use"
python3 "$LIB/ledger_cli.py" append --space "$SPACE" --type item.create --payload '{{"id": "t-1", "title": "First task"}}'
python3 "$LIB/ledger_cli.py" append --space "$SPACE" --type item.claim --payload '{{"id": "t-1"}}'
python3 "$LIB/ledger_cli.py" append --space "$SPACE" --type item.complete --payload '{{"id": "t-1"}}'
python3 "$LIB/ledger_cli.py" items --space "$SPACE"
python3 "$LIB/ledger_cli.py" verify --space "$SPACE"
echo "## 4 share"
export DATACORE_ACTOR=alice
git -C "$SPACE" init -b main
git -C "$SPACE" remote add origin {remote}
python3 "$LIB/ledger_cli.py" init --space "$SPACE" --actor alice
git -C "$SPACE" add -A && git -C "$SPACE" commit -m "Start the ledger"
python3 "$LIB/ledger_transport.py" converge --space "$SPACE" --root "$DC" --line
echo "## 4 a hand-written line is refused"
echo '{{"actor":"alice","hlc":"x","payload":{{}},"prev":"0","seq":99,"type":"item.create","hash":"0"}}' >> "$SPACE/.datacore/events/alice.jsonl"
if git -C "$SPACE" add -A && git -C "$SPACE" commit -qm forged; then echo "FAIL: forged commit accepted"; exit 1; fi
git -C "$SPACE" reset -q && git -C "$SPACE" checkout HEAD -- .datacore/events/alice.jsonl
echo "## 5 verify"; python3 "$LIB/ledger_cli.py" verify --space "$SPACE"
echo "## 6 ledger branches"; git -C "$SPACE" fetch -q origin; git -C "$SPACE" branch -r --list 'origin/ledger/*' --no-merged origin/main
'''

BOB = '''
set -e
DC={root}; LIB="$DC/.datacore/lib"; SPACE="$DC/team"; export DATACORE_ROOT="$DC"
echo "## 8.3 bob voids alice's seq 0"
python3 "$LIB/ledger_cli.py" init --space "$SPACE" --actor bob
python3 "$LIB/ledger_cli.py" void --space "$SPACE" --log alice.jsonl --seq 0 --reason 'created by mistake (rehearsal)'
python3 "$LIB/ledger_cli.py" verify --space "$SPACE"
if python3 "$LIB/ledger_cli.py" void --space "$SPACE" --log bob.jsonl --seq 0 --reason self; then
  echo "FAIL: a self-void was accepted"; exit 1; fi
'''


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ledger-runbook-")).resolve()
    m = K.clean_machine(tmp)
    for d in ("githooks", "hooks"):
        shutil.copytree(K.REAL / ".datacore" / d, m.root / ".datacore" / d)
    remote = tmp / "team.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(remote)], check=True)
    git_id = {"GIT_AUTHOR_NAME": "alice", "GIT_AUTHOR_EMAIL": "alice@example.org",
              "GIT_COMMITTER_NAME": "alice", "GIT_COMMITTER_EMAIL": "alice@example.org"}
    rc = 0
    bob_home = tmp / "bob-home"
    bob_home.mkdir()
    for who, script, env in (
            ("alice", ALICE, dict(m.env, **git_id)),
            ("bob", BOB, dict(m.env, HOME=str(bob_home),
                              DATACORE_IDENTITY_FILE=str(bob_home / ".datacore" / "identity.env")))):
        p = subprocess.run(["bash", "-c", script.format(root=m.root, remote=remote)], env=env,
                           cwd=m.root, capture_output=True, text=True, timeout=600)
        print(m.scrub(p.stdout))
        if p.returncode:
            print(f"--- {who}: FAILED (rc {p.returncode})\n{m.scrub(p.stderr)[-2000:]}")
            rc = 1
    print("rehearsal:", "FAILED" if rc else "every runbook step worked", f"(in {tmp})")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
