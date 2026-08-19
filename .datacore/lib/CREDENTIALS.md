# Credentials — read this before looking for one

This file ships next to `creds.py` on **every** host, by `distribute.sh`, because
the hosts do not share a context file: winston and nightshift track `datacore.git`,
plur-claw runs `data-space.git`, hermes runs `tris-space.git`. A rule that lives
only in one repo's `CLAUDE.md` reaches only that repo's machines — and the agents
that most need this rule are the ones on the other machines.

## Never search for a credential

Do not grep `.env` files. Do not look on another host. Do not read `~/.hermes/.env`
or any other copy you find.

**Searching is what creates the problem.** A search finds *a* value, and nothing
about a found value tells you whether it is current or abandoned. Measured on
2026-08-18: one host's copy of an OpenRouter key was dead while two others were
live, so the search order — not the credential — decided the outcome.

## Ask instead

```bash
python3 ~/Data/.datacore/lib/creds.py get <id-or-VAR_NAME> --consumer <who-wants-it>
```

- resolves the **one** declared location, never guesses
- prefers this host's own value (`local.env`) over a fleet-wide one
- **verifies against the provider** before returning it
- prints the value on **stdout**, every diagnostic on **stderr**

So pipe it into whatever needs it. Do not echo it, do not paste it into a message,
do not put it in a commit. If you are an agent whose output is transcribed, the
value must never pass through your output at all — call the code that needs the
credential and let *it* resolve it.

If `get` refuses because the credential is not indexed, **that refusal is the
feature**. An unindexed credential cannot be located, verified, or audited. Add it
with `creds add`. Do not go looking for it.

## Everything else

| Need | Command |
|------|---------|
| Is it alive? | `creds doctor [--id X]` |
| Where does it live? | `creds show X` · `creds list` · `creds search X` |
| What is unindexed / duplicated? | `creds audit` |
| Reassemble this host's env | `creds sync` |

`doctor` reports three states. **`n-a` means "could not tell" and is never a pass** —
a check whose "healthy" and "unknown" look the same is not a check. `n-a` with a
stated reason ("disabled by decision", "no free probe endpoint") is deliberate;
`n-a` with "no verifier declared" is a gap nobody has closed yet.

## Changing a value

Edit the space or project file under `~/Data/.datacore/secrets/`, then `creds sync`.

**Never edit `~/Data/.datacore/env/.env`.** It is generated, its own header says so,
and an edit there is silently lost on the next sync. This file existing on your host
does not mean you may write to it.

Distribution to other hosts is push-based from the Mac
(`.datacore/secrets/scripts/distribute.sh`). Each host receives only its own scoped
set, because a host holding the whole store has the whole store regardless of what
its manifest says.

## When a credential returns 401

**Diff it across every host before concluding the provider revoked it.**

On 2026-07-08 the @plur_ai X keys were rotated. The new values were written into one
host's working tree and never committed, so no other host received them. The
canonical store served pre-rotation values from April to August, `doctor` reported
FAIL against the wrong variable, and a release published to npm, PyPI, a tag, a
GitHub release, the MCP registry and the website before failing to post.

Two sessions independently diagnosed that as "the provider regenerated the keys". It
was not. Sending someone to regenerate keys that are alive on another machine
destroys a working credential.

Compare with truncated `sha256`, never by printing values. Rotation is the user's
action: flag what needs rotating, do not rotate it.
