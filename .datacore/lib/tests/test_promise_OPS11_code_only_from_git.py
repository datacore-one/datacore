"""OPS-11: Every machine's code comes only from git: nothing is copied onto a machine by
hand or left staged or edited in place.

Kind: production (needs: fleet). Read-only ssh, one probe per machine, timeouts.

Which machines: every machine the job list (tracked + local) runs a job on, looked up in
the roster (registry/infrastructure.yaml) -- no machine is named here.

Which repositories, on each machine: every Datacore code checkout -- the data root when its
origin is the same repository as this one, and the runner checkout -- and every module
repository inside such a checkout (`.datacore/modules/*/.git`). Space repositories are data,
not code, and are left to the sync promises.

What each must show:
  * nothing staged and no tracked file modified (a hand-copied file over a tracked one shows
    up here; that is what stopped the overnight run twice);
  * no untracked file that is not gitignored. Decision: a gitignored file is install-local by
    declaration (the roster, the principal registry, *.local.yaml, backups the ignore file
    names) and is fine; an untracked file nobody declared is a file that did not come from
    git, which is what the promise forbids -- so it counts;
  * no merge, rebase or cherry-pick left half done;
  * HEAD is an ancestor of the machine's own origin/<default> tracking ref: a commit made on
    the machine that origin never had is code that did not come from git. (Being BEHIND
    origin is SYN-4's promise, not this one.)
  Submodules are skipped (`--ignore-submodules=all`): a submodule is its own repository.

Decision (for the owner): a machine the roster marks `kind: workstation` is where code is
written, so its authoring copy is dirty by design while work is in progress. Only its
runner checkout, if it has one, is judged; the authoring copy is listed as "not judged" in
the output. Jobs that run from the authoring copy are therefore not covered by this check.

"Could not tell" (unreachable machine, unreadable repository, no tracking ref) is red.

Seeded failure: files copied by hand onto the overnight host left staged changes in its
checkout; the overnight run refused to start on two nights.
"""
from __future__ import annotations

import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / ".datacore" / "lib"
sys.path.insert(0, str(LIB))

PROBE = r'''slug="__SLUG__"
check() {  # $1 repo dir, $2 kind
  d=$1
  st=$(git -C "$d" status --porcelain --ignore-submodules=all 2>/dev/null) || { echo "R|$2|$d|ERR"; return; }
  staged=$(printf '%s\n' "$st" | grep -c '^[MADRCT]')
  mod=$(printf '%s\n' "$st" | grep -c '^.[MDT]')
  untr=$(printf '%s\n' "$st" | grep -c '^??')
  names=$(printf '%s\n' "$st" | grep -v '^$' | head -4 | cut -c4- | tr '\n' ',')
  ref=$(git -C "$d" symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null)
  if [ -z "$ref" ]; then
    for r in refs/remotes/origin/main refs/remotes/origin/master; do
      git -C "$d" rev-parse -q --verify "$r" >/dev/null 2>&1 && { ref=$r; break; }
    done
  fi
  anc=unknown
  if [ -n "$ref" ]; then
    if git -C "$d" merge-base --is-ancestor HEAD "$ref" 2>/dev/null; then anc=yes; else anc=no; fi
  fi
  gd=$(git -C "$d" rev-parse --absolute-git-dir 2>/dev/null); op=none
  for f in MERGE_HEAD rebase-merge rebase-apply CHERRY_PICK_HEAD; do [ -e "$gd/$f" ] && op=$f; done
  echo "R|$2|$d|$staged|$mod|$untr|$anc|${ref#refs/remotes/}|$op|$names"
}
for spec in __DIRS__; do
  kind=${spec%%=*}; root=$(eval echo "${spec#*=}"); [ -d "$root/.git" ] || continue
  url=$(git -C "$root" remote get-url origin 2>/dev/null)
  case "$url" in *"$slug"|*"$slug".git) ;; *) continue ;; esac
  if [ "$kind" = skip ]; then echo "SKIP|$root"; continue; fi
  check "$root" core
  for m in "$root"/.datacore/modules/*/; do m=${m%/}; [ -e "$m/.git" ] && check "$m" module; done
done
echo PROBE-OK'''


def _slug() -> str:
    """owner/name of this installation's own code repository, from its origin URL."""
    url = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"],
                         capture_output=True, text=True, timeout=20).stdout.strip()
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url)
    assert m, "could not read this installation's origin (could not check)"
    return m.group(1)


def _job_machines() -> dict:
    """Roster entries of every machine the job list runs something on."""
    from jobs.manifest import load_manifest
    jobs = load_manifest(LIB / "jobs" / "manifest.yaml")
    used = {j.machine for j in jobs}
    reg = yaml.safe_load((ROOT / ".datacore" / "registry" / "infrastructure.yaml").read_text())
    servers = {k: v for k, v in (reg.get("servers") or {}).items() if isinstance(v, dict)}
    return {k: v for k, v in servers.items() if k in used or v.get("manifest_machine") in used}


def _probe(name: str, cfg: dict, slug: str) -> tuple[str, str]:
    access = cfg.get("access") or {}
    workstation = cfg.get("kind") == "workstation"
    specs = [f"{'skip' if workstation else 'core'}={access.get('data_root') or '~/Data'}",
             f"core={access.get('runner') or '~/.datacore/v2-runner'}"]
    script = PROBE.replace("__SLUG__", slug).replace(
        "__DIRS__", " ".join(f"'{s}'" for s in dict.fromkeys(specs)))
    alias = cfg.get("ssh_alias")
    local = alias in (None, "", "-")
    if local and not workstation:
        return name, "UNREACHABLE the roster gives no ssh alias"
    cmd = ["bash", "-c", script] if local else \
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", str(alias), script]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return name, "UNREACHABLE timeout"
    if r.returncode != 0 or "PROBE-OK" not in r.stdout:
        return name, f"UNREACHABLE rc={r.returncode}"
    return name, r.stdout


@pytest.mark.production
def test_every_machine_runs_only_code_that_came_from_git():
    slug = _slug()
    machines = _job_machines()
    assert machines, "no machine in the roster runs a job (could not check)"
    with ThreadPoolExecutor(len(machines)) as pool:
        outs = dict(pool.map(lambda kv: _probe(kv[0], kv[1], slug), machines.items()))
    problems, notes, repos = [], [], 0
    for name, out in sorted(outs.items()):
        if out.startswith("UNREACHABLE"):
            problems.append(f"{name}: could not check ({out[12:]})")
            continue
        for line in out.splitlines():
            if line.startswith("SKIP|"):
                notes.append(f"{name}:{line[5:]} (workstation authoring copy, not judged)")
                continue
            if not line.startswith("R|"):
                continue
            f = line.split("|")
            repos += 1
            where = f"{name}:{f[2]}"
            if f[3] == "ERR":
                problems.append(f"{where}: could not read its status (could not check)")
                continue
            staged, mod, untr, anc, ref, op, names = int(f[3]), int(f[4]), int(f[5]), f[6], f[7], f[8], f[9]
            what = []
            if staged:
                what.append(f"{staged} staged")
            if mod:
                what.append(f"{mod} modified in place")
            if untr:
                what.append(f"{untr} untracked and not gitignored")
            if op != "none":
                what.append(f"a {op} left half done")
            if anc == "unknown":
                what.append("no origin tracking ref, so it cannot tell where HEAD came from")
            elif anc == "no":
                what.append(f"HEAD holds commits {ref or 'origin'} never had")
            if what:
                problems.append(f"{where}: " + ", ".join(what) + (f" [{names.rstrip(',')}]" if names else ""))
    assert repos, "no Datacore code checkout found on any machine (could not check)"
    assert not problems, (
        "expected every machine's code checkouts to hold exactly what came from git -- nothing "
        "staged, edited in place or copied in, and no local commits; found: "
        + "; ".join(problems) + (f"  (also: {'; '.join(notes)})" if notes else ""))
