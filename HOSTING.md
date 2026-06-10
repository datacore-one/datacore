# Repository Hosting Policy

Where Datacore repos live and why. Decided 2026-06-10 after the repo-strategy
audit (80 repos, previously undocumented split). Machine-specific details
(hosts, addresses) live in private engram memory, not in this public file.

## The rule

| Repo class | Host | Visibility | Examples |
|---|---|---|---|
| **Public skeletons & specs** | GitHub `datacore-one` | PUBLIC | `datacore` (root), `datacore-dips`, `datacore-org` |
| **Team spaces** | GitHub, owned by the team's org | PRIVATE | `datafund/datafund-space`, `fairDataSociety/fds-space`, `plur-ai/plur-space`, `datacore-one/datacore-space` |
| **Personal space + ventures** | Self-hosted Gitea | PRIVATE | `0-personal`, `4-forge`, `6-meridian`, `7-megaphone`, venture project repos |
| **Modules** | GitHub `datacore-one` (`datacore-<name>` / `module-<name>`) | PRIVATE until released; PUBLIC when community-ready | `datacore-nightshift`, `module-grants` |
| **Project repos** (inside `*/2-projects/`) | Follow the owning space's host | PRIVATE by default | team project → team org; personal/venture project → Gitea |
| **Secrets** | Self-hosted Gitea only | PRIVATE | never on GitHub |

Rationale: team repos belong to the team's org (continuity beyond any one
person); personal and venture work stays sovereign on self-hosted infra;
public repos are limited to what the community is meant to fork.

## Invariants

1. **Every repo has a remote.** Local-only repos are a total-loss risk —
   the weekly registry/structure audit flags them.
2. **Public repos are guarded**, not just gitignored: they are listed in
   `.datacore/config/public-repo-denylist.yaml` (pre-push hook blocks
   forbidden paths: state, org files, journals, inboxes, env/secrets).
3. **One origin per repo.** Mirrors are deliberate and documented, not
   leftover `github-legacy` remotes.
4. **Self-hosted Gitea needs a backup channel** — decision pending
   (tracked in next_actions): GitHub private mirror vs server-side snapshots.

## Where do I put a new repo?

Ask: *who must be able to read it if I disappear for a month?*
- The team → that team's GitHub org, private.
- Only me / a venture → Gitea.
- The community → `datacore-one`, public, denylist-protected, only after a
  deliberate publication decision (see DIP-0001 for the contribution model).
