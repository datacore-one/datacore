# Deploying the Gitea pre-receive hook (DIP-0046 D5)

Status: **DEPLOYED 2026-08-12, report-only**, on 0-personal, 4-forge,
6-meridian, 7-megaphone. Three things below were WRONG in the first version of
this document and were only found by deploying and proving the hook fires. The hook passes all eight
cases in `.datacore/lib/tests/test_gitea_pre_receive.py`. Deployment is blocked
on SSH access — see "Blocked on" below.

## Two things that will make this look installed when it is not

**1. A global `core.hooksPath` disables per-repo server-side hooks.**
Discovered while rehearsing: the hook was present, executable and correct, and
never ran, because this Mac sets `core.hooksPath` globally and that setting wins
over the bare repo's own `hooks/` directory. If the `git` user on the Gitea box
has a global `core.hooksPath`, every repo's `pre-receive` is silently bypassed.

Check before installing:

    ssh <gitea-host> "sudo -u git git config --global --get core.hooksPath"

Expect **empty**. If it is set, the hook must go in that directory instead, or
the setting must be removed.

**2. The chained directory is `hooks/pre-receive.d/`, NOT `custom_hooks/`.**
This document originally said `custom_hooks/`. Gitea's generated
`hooks/pre-receive` reads:

    for hook in ${GIT_DIR}/hooks/${hookname}.d/*; do

so a hook in `custom_hooks/` is never executed. Installed there it looked
perfect and did nothing — caught only by pushing and observing no output.
Install as `hooks/pre-receive.d/50-datacore`.

**3. THE CONTAINER HAS NO PYTHON.** `gitea-pre-receive.py` cannot run on the
server at all; it is the readable reference. `gitea-pre-receive.sh` (POSIX sh,
using only sh/awk/sed/grep/git) is what deploys, and it is what the rehearsal
suite now targets. A hook needing an absent interpreter installs cleanly and is
inert — the same check-strength failure as hazard 1, from another direction.

**4. Gitea reports the ACCOUNT, not the actor.** `GITEA_PUSHER_NAME` is
`gregor` for every machine, while `members.yaml` lists machine actors. The
server therefore cannot attribute a push to a machine, so per-actor log
ownership is enforced CLIENT-side by
`.datacore/lib/hooks/log_ownership_guard.py`; the server checks only that the
pushing account is admitted. On Gitea the single-writer invariant rests on the
client hook plus config_drift watching `core.hooksPath`.

## Install (report-only)

Per repo, under Gitea's repository root (typically
`/var/lib/gitea/data/gitea-repositories/<owner>/<repo>.git`):

    scp .datacore/hooks/gitea-pre-receive.py <gitea-host>:/tmp/
    ssh <gitea-host> "sudo install -o git -g git -m 755 /tmp/gitea-pre-receive.py \
        /var/lib/gitea/data/gitea-repositories/gregor/<repo>.git/custom_hooks/pre-receive"

Gitea spaces: `0-personal`, `4-forge`, `6-meridian`, `7-megaphone` (verified
against `git remote get-url` on 2026-08-11, not assumed — the other five spaces
are GitHub and are covered by D6 rulesets instead).

**Do not set `DATACORE_ENFORCE` yet.** Report-only writes `datacore/warn:` and
`datacore/would reject:` to the pusher's terminal and always exits 0.

## Verify it actually fires

Installing the file proves nothing (see hazard 1). Prove it runs:

    # from a machine that is NOT a member of the target space
    DATACORE_ACTOR=nobody git push origin main

Expect `remote: datacore/would reject: ... pusher 'nobody' not in ...`.
No output means the hook did not run — go back to hazard 1.

## Promote to enforcing

Only after the report-only log has been **silent against real pushes for a
week**. A pre-receive rejection cannot be bypassed from the client, and
`0-personal` is the operator's own daily notes: a wrong rule locks them out of
their own space. Set `DATACORE_ENFORCE=1` in the hook's environment
(`custom_hooks/pre-receive` wrapper or Gitea's app.ini env passthrough), one
repo at a time, starting with a space that is not `0-personal`.

## Blocked on

`ssh blackpi` fails host-key verification from the Mac. Accepting a host key is
a trust decision for the operator to make, so this stops here rather than
auto-accepting. Once `ssh blackpi` works, the install above is a two-line
per-repo operation.
