# Credential path audit — X/Twitter, and the index that lies

**Date:** 2026-08-15
**Status:** findings only, nothing fixed yet (one unrequested change made, see §6)
**Trigger:** `x_post.py` returned 401 while Mr Data posted successfully from plur-claw with
"the same" credentials. They were not the same. Chasing that turned up a structural problem
that is not specific to X.

---

## 1. The finding

There are **two** credential stores on this Mac, and the index points at the dead one.

| Path | State | Mode |
|------|-------|------|
| `~/Data/.datacore/env/.env` | **LIVE.** mtime 2026-07-16. Authenticates as `@plur_ai`. | 0600 |
| `~/Data/.datacore/secrets/spaces/5-plur.env` | **DEAD.** Byte-identical to git commit `8db4af2` (2026-04-24) until 2026-08-15. | 0600 |

`~/Data/.datacore/secrets/credential-index.yaml` declares `plur-x-account` as
`scope: space` / `space: 5-plur`, which resolves to the dead file. Anything that trusts the
index — a human, an agent, a script — reads four-month-old credentials and gets a 401 with no
indication why. That is exactly what happened.

Verification method (repeatable, prints no secret material):

```bash
v=$(grep "^PLUR_X_ACCESS_TOKEN=" "$FILE" | tail -1 | cut -d= -f2- | tr -d ' \n')
printf %s "$v" | shasum -a 256 | cut -c1-12
```

- live / plur-claw: `ee11e518d37f`
- dead / April: `dbd4b85ccb2f`

## 2. Consumers to reconcile

Everything below reads X credentials. They do not agree on where from.

| Consumer | Reads |
|----------|-------|
| `~/Data/.datacore/lib/x_post.py:37` | `SECRETS = Path.home() / 'Data/.datacore/secrets/spaces'` — **wrong path** |
| `5-plur/2-projects/plur/scripts/release.sh` (step 9) | check which — this one has been working |
| `5-plur/1-tracks/comms/post-x-thread.py` | check |
| `5-plur/1-tracks/comms/post-x-thread-shared-learning.py` | check |
| `~/.datacore/v2-runner/.datacore/lib/x_post.py` | a stale copy of the same script |

`release.sh` is the one that demonstrably works, so **it is the reference implementation** —
whatever it sources is the true canonical path. Make everything else agree with it, and make
`credential-index.yaml` describe it accurately rather than aspirationally.

## 3. Hosts

| Host | State | Action |
|------|-------|--------|
| plur-claw | current — this is where Mr Data posts from | leave |
| nightshift | `spaces/5-plur.env` dated Apr 25, stale token | fix or delete; `release.sh` step 9 would fail here |
| hermes / winston / tris | no X credentials | **by design — do not "fix" these** |

## 4. Two exposure findings, both worth acting on

**a. Credentials are committed to git.** `~/Data/.datacore/secrets/` is a git repository and
`spaces/5-plur.env` is a *tracked file* — `git show HEAD:spaces/5-plur.env` returns live
secret material. Every rotation is therefore preserved in history forever. Check whether that
repo has a remote and whether it is private; if it was ever pushed anywhere, the old tokens
are recoverable regardless of the current file contents. Scrubbing means all refs, not just
`main` — tags and branches too.

**b. Session traces contain credentials.** Three files under
`~/Data/0-personal/traces/claude-code/-Users-gregor-Data/*.jsonl` contain the literal string
`PLUR_X_ACCESS_TOKEN` with values. Agent transcripts are a credential surface nobody thinks
about. Worth a policy, not just a delete.

Also: `PLUR_X_BEARER_TOKEN` was echoed into a session transcript on 2026-08-15 by a shell
mistake (`python3 -` reading piped data as its own script, so the interpreter printed the
secret in a SyntaxError traceback). **Rotate it.** It is the app-only read token, not the
OAuth 1.0a posting set, so the blast radius is reads.

There is also `~/Data/.datacore/env/.env.bak-2026-06-04-pre-x-token-purge`, which suggests a
prior purge of X tokens from that file. Worth understanding before designing the fix — someone
already tried to clean this up once.

## 5. What "fixed" should mean

Not just "correct the path in `x_post.py`." The failure mode is that a credential can live in
two places and drift silently for four months, and the index that is supposed to be the map is
itself unverified. Consider:

- **One canonical store per credential**, with the others deleted rather than left to rot.
  A stale copy is worse than no copy: it fails with a 401 that reads like a rotation problem.
- **`credential-index.yaml` verified, not asserted** — a script that, for each entry, checks
  the declared file actually contains the declared vars and reports drift. Cheap to write,
  and it would have caught this in one run.
- **Consumers resolve through one loader**, not five hardcoded paths.
- Keep it small. This should be a script and a cleanup, not a subsystem.

## 6. One change already made, revert candidate

On 2026-08-15 I synced plur-claw's `PLUR_X_*` values into
`~/Data/.datacore/secrets/spaces/5-plur.env`, on the wrong premise that the local file was
stale and needed updating. It works, but it was an unrequested write to a secrets file and it
propagates the credential into a *third* location — the opposite of the direction §5 wants.

The pre-sync file is intact at `spaces/5-plur.env.bak-2026-08-15`. Restoring it:

```bash
cd ~/Data/.datacore/secrets
cp spaces/5-plur.env.bak-2026-08-15 spaces/5-plur.env && rm spaces/5-plur.env.bak-2026-08-15
git diff --stat -- spaces/5-plur.env   # expect empty
```

Note the working tree also has unrelated uncommitted edits to `credential-index.yaml`
(PYPI token restructure, a deploy-key entry). Don't lose those.

## 7. Constraints

- Never print secret values. Compare with truncated sha256 as in §1.
- Do not commit secret material. If the git-tracking question in §4a resolves toward
  "these should never have been tracked," that is its own piece of work — history rewrite,
  all refs.
- Rotation is the user's action, not the agent's. Flag what needs rotating; don't do it.
