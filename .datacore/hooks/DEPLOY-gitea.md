# Deploying the Gitea pre-receive hook (DIP-0046 D5)

Status: **written and rehearsed, NOT deployed.** The hook passes all eight
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

**2. Gitea owns `hooks/pre-receive`.**
Gitea generates that file itself and overwrites it on upgrade and on repo
re-sync. Custom hooks belong at `custom_hooks/pre-receive`, which Gitea's own
hook chains into. Installing to `hooks/pre-receive` works until the next
`gitea admin regenerate hooks`, then vanishes without a word.

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
