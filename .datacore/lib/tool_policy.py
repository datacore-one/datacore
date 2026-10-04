#!/usr/bin/env python3
"""In-flight tool-call policy for executors (datacore#30, roadmap D-005).

The policy layer bound an item at CLAIM time — approvals_policy.yaml names,
per principal, the effects it may never cause and the effects that need a
human's co-signed grant; claim_gate.py enforces both before work starts. But
a running task was bounded only by the outer controls: nightshift passed no
hooks or settings to `claude -p`, and Miles ran with bypassPermissions and no
hooks. Once a task was executing, "never payment" was a sentence in a YAML
file, not a wall.

This module classifies declared tool calls and applies a decision keyed by
principal. It is a runtime guard, not an OS security boundary: arbitrary code
under the same OS identity can bypass pattern matching or change local policy.
Hosts executing untrusted tasks require an independently enforced sandbox and
credential isolation. config/tool_effects.yaml maps calls to effects:

    never-effect hit      -> refused  (no grant can allow it)
    cosign effect, no     -> paused   (the model is told to leave a proposal;
      grant on this task               a human grants, the next run acts; a
                                       per-transaction effect needs the grant
                                       of that exact call, see _grant_covers)
    anything else         -> allowed

Every refusal is recorded on the ledger as `metric.attest` with
metric=policy.refusal — the same "this machine observed this about itself"
vocabulary cos_ledger_event.sh uses — so a task that tried to pay someone
shows up in the morning instead of in a log nobody reads.

Two callers, one decision:
  * hooks/tool_policy_guard.py — the PreToolUse command hook nightshift passes
    to `claude -p --settings` (see `settings_json`)
  * the Miles bot — an SDK PreToolUse hook that calls `evaluate_hook` in-process

Context reaches the hook through the environment the executor sets:
  DATACORE_POLICY_PRINCIPAL  whose limits apply (default: this host's DECLARED
                             actor's principal from registry/principals.yaml;
                             an undeclared host is denied, never guessed from
                             its hostname -- decision Q2)
  DATACORE_POLICY_SPACE      the task's space; the refusal is recorded there
                             (default: <root>/2-datacore)
  DATACORE_POLICY_TASK       the task id, carried on the record
  DATACORE_POLICY_GRANTED    comma-separated effects a human already granted
                             for this task (the task's :GRANTED_EFFECTS:)

Unreadable or malformed policy, unknown principals and malformed hook
requests are denied. Restore valid policy before retrying. A
classification hit is decided even when the ledger cannot be written: the
record is evidence, not the gate.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

LIB = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

DEFAULT_EFFECTS_FILE = LIB.parent / "config" / "tool_effects.yaml"
DEFAULT_POLICY_FILE = LIB.parent / "config" / "approvals_policy.yaml"
GUARD = LIB / "hooks" / "tool_policy_guard.py"
REFUSAL_METRIC = "policy.refusal"

#: Which keys of a tool's input carry the text worth matching. Anything
#: else (an MCP tool's structured input) is matched as its JSON.
_TEXT_KEYS = ("command", "url", "file_path", "path", "query", "prompt", "content", "new_string")


@dataclass
class Decision:
    allow: bool
    effects: set[str] = field(default_factory=set)
    reason: str = ""
    kind: str = "allow"          # allow | never | cosign | granted

    @property
    def blocked(self) -> bool:
        return not self.allow


# ── effects ─────────────────────────────────────────────────────────────────
FLEET_HOSTS = "{fleet_hosts}"


def fleet_hosts_alternation() -> str:
    """`a|b|localhost`: the install's own machines by every name they go by
    (roster names, manifest names, ssh aliases), regex-escaped. What
    `{fleet_hosts}` in tool_effects.yaml stands for. No roster: localhost only,
    so a stranger's install approves none of our hosts (INS-3)."""
    try:
        from jobs.manifest import fleet_names
        names = fleet_names()
    except Exception:  # noqa: BLE001 -- an unreadable roster approves nothing extra
        names = []
    return "|".join(re.escape(n) for n in [*names, "localhost"])


AUDIT_AGENTS = "{audit_agents}"


def audit_agents_alternation() -> str:
    """`a|b`: the agents that write nightly audit findings, from the
    `cross_model_audit.agents` section of the install's principals.yaml,
    regex-escaped. What `{audit_agents}` in tool_effects.yaml stands for. None
    declared: a pattern that matches nothing, so no findings file is exempt."""
    try:
        import roster
        names = [str(a) for a in (roster.section("cross_model_audit").get("agents") or {})]
    except Exception:  # noqa: BLE001 -- an unreadable registry exempts nothing
        names = []
    return "|".join(re.escape(n) for n in names) or "(?!)"


def load_effects(path: Path | None = None) -> dict[str, dict]:
    """{effect: {tools, tool_patterns, patterns}} from tool_effects.yaml.
    An unreadable or malformed vocabulary cannot authorize tool use."""
    import yaml
    p = Path(path or DEFAULT_EFFECTS_FILE)
    from yaml_safety import UniqueStringKeyLoader
    try:
        data = yaml.load(p.read_text(encoding="utf-8"), Loader=UniqueStringKeyLoader)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError):
        raise ValueError('tool effects configuration is unreadable or ambiguous') from None
    if not isinstance(data, dict) or not isinstance(data.get("effects"), dict) or not data["effects"]:
        raise ValueError("tool effects must contain a nonempty effects mapping")
    out: dict[str, dict] = {}
    hosts: str | None = None
    auditors: str | None = None

    def _fill(r: str) -> str:
        nonlocal hosts, auditors
        if FLEET_HOSTS in r:
            if hosts is None:
                hosts = fleet_hosts_alternation()
            r = r.replace(FLEET_HOSTS, hosts)
        if AUDIT_AGENTS in r:
            if auditors is None:
                auditors = audit_agents_alternation()
            r = r.replace(AUDIT_AGENTS, auditors)
        return r
    for name, spec in (data.get("effects") or {}).items():
        if not isinstance(spec, dict):
            raise ValueError("effect specification must be a mapping")
        for key in ("tools", "tool_patterns", "patterns"):
            if key in spec and (not isinstance(spec[key], list) or any(not isinstance(v, str) for v in spec[key])):
                raise ValueError(f"{key} must be a list of strings")
        if "approved" in spec and (not isinstance(spec["approved"], list)
                                   or any(not isinstance(v, str) for v in spec["approved"])):
            raise ValueError("approved must be a list of strings")
        if "per_transaction" in spec and not isinstance(spec["per_transaction"], bool):
            raise ValueError("per_transaction must be true or false")
        out[str(name)] = {
            "tools": [str(t) for t in (spec.get("tools") or [])],
            "tool_patterns": [re.compile(str(r), re.I) for r in (spec.get("tool_patterns") or [])],
            "patterns": [re.compile(_fill(str(r)), re.I) for r in (spec.get("patterns") or [])],
            "per_transaction": bool(spec.get("per_transaction", False)),
            "approved": [re.compile(str(r), re.I) for r in (spec.get("approved") or [])],
        }
    return out


#: Fields that are prose ABOUT a call, not part of it, per tool. Claude Code's
#: Bash tool carries a human-readable `description` beside its `command`; a
#: description that quotes an effect pattern ("before the api.stripe.com/v1/
#: charges work") paused an innocent `git status` (decision Q4, 2026-09-23).
_PROSE_FIELDS = {"Bash": frozenset({"description"})}


def call_text(tool_input, tool_name: str | None = None) -> str:
    """The matchable text of a call: its command/url/path fields first, then
    the JSON of the whole input, always -- less the tool's prose fields.

    Decision S1 (2026-09-23): until then the JSON was used only when no text
    key was present, so `{"url": "https://example.org", "body":
    "api.stripe.com/v1/charges"}` was matched on its url alone, although
    tool_effects.yaml says an MCP input is matched "as its JSON"
    (DatacoreSpec/Guards.lean `call_text_covers_every_field`).

    Decision Q4 (2026-09-23): for `tool_name == "Bash"` the `description`
    field is left out of the JSON; every other field stays, and every field of
    every other tool stays (`call_text_bash_description_unmatched`). A caller
    that does not name the tool gets the whole JSON (fail closed)."""
    if isinstance(tool_input, str):
        return tool_input
    if not isinstance(tool_input, dict):
        return ""
    prose = _PROSE_FIELDS.get(tool_name or "", frozenset())
    matched = {k: v for k, v in tool_input.items() if k not in prose} if prose else tool_input
    parts = [str(matched[k]) for k in _TEXT_KEYS if isinstance(matched.get(k), str)]
    try:
        parts.append(json.dumps(matched, ensure_ascii=False, sort_keys=True))
    except (TypeError, ValueError):
        parts.append(str(matched))
    return "\n".join(parts)


def classify(tool_name: str, tool_input, effects: dict[str, dict] | None = None) -> set[str]:
    """The effects a call would cause, by the vocabulary in tool_effects.yaml."""
    effects = effects if effects is not None else load_effects()
    text = call_text(tool_input, tool_name)
    hit: set[str] = set()
    for name, spec in effects.items():
        tools = spec.get("tools") or []
        if tools and not any(fnmatch.fnmatch(tool_name, t) for t in tools):
            continue
        if any(r.search(tool_name) for r in spec.get("tool_patterns") or []):
            hit.add(name)
            continue
        if text and any(r.search(text) for r in spec.get("patterns") or []):
            hit.add(name)
    return hit


# ── principals ──────────────────────────────────────────────────────────────
def limits_for(principal: str, policy_path: Path | None = None) -> tuple[set[str], set[str]]:
    """(never_effects, cosign_effects) for a principal from approvals_policy.yaml.
    An unlisted principal is refused (ValueError), which evaluate_hook turns
    into a deny: it does not get the global cosign set with no never-effects.
    (This docstring said it did until 2026-09-23; the code was always the
    safer of the two, and test_unlisted_principal_cannot_bypass_declared_limits
    pins it.)"""
    from ledger.policy import load_policy
    path = Path(policy_path or DEFAULT_POLICY_FILE)
    if not path.is_file():
        raise ValueError("executor policy is missing")
    policy = load_policy(path)
    if policy.principals is None or principal not in policy.principals:
        raise ValueError("executor principal has no declared policy")
    entry = (policy.principals or {}).get(principal) or {}
    never = {str(e) for e in (entry.get("never_effects") or [])}
    cosign = set(policy.cosign_effects) | {str(e) for e in (entry.get("cosign_effects") or [])}
    return never, cosign


def own_repos_for(principal: str, policy_path: Path | None = None) -> list[re.Pattern]:
    """The principal's own repositories (approvals_policy `own_repos`): regexes
    over a push destination's URL. Empty for a principal that declares none."""
    from ledger.policy import load_policy
    policy = load_policy(Path(policy_path or DEFAULT_POLICY_FILE))
    entry = (policy.principals or {}).get(principal) or {}
    return [re.compile(str(r)) for r in (entry.get("own_repos") or [])]


# ── a push to the principal's own repository (owner decision 2026-09-30) ────
#: Shell separators between commands; a push is judged per command.
_SEPARATORS = {"&&", "||", ";", "|", "&", "\n"}
_URLISH = re.compile(r"^(\w[\w+.-]*://|[\w.-]+@[\w.-]+:)")


def _strip_userinfo(url: str) -> str:
    return re.sub(r"^(\w[\w+.-]*://)[^@/]+@", r"\1", url.strip())


def _chdir(base: str | None, arg: str) -> str | None:
    """Where `cd arg` (or `git -C arg`) leads from `base`, or None when it cannot be told."""
    if not arg or any(c in arg for c in "$`*?{"):
        return None
    arg = os.path.expanduser(arg)
    if os.path.isabs(arg):
        return os.path.normpath(arg)
    return os.path.normpath(os.path.join(base, arg)) if base else None


def _split_commands(command: str) -> list[list[str]] | None:
    """The argv of each command in a shell line, split on its separators;
    None when the line cannot be read."""
    try:
        lex = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return None
    commands, cur = [], []
    for t in tokens:
        if t in _SEPARATORS or set(t) <= set("&|;()"):
            if cur:
                commands.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        commands.append(cur)
    return commands


def _push_destinations(tool_input) -> list[str | None] | None:
    """The destination URL of every `git push` in a shell command, in order;
    None for a push whose repository or remote cannot be told. None overall
    when the input is not a shell command (code is never resolved).

    The repository is the one the command itself names -- `git -C <dir>`, a
    preceding `cd <dir>`, or the tool's own `workdir`/`cwd` -- never this
    process's working directory: the gateway's cwd is not its terminal's."""
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
        return None
    base = next((str(tool_input[k]) for k in ("workdir", "cwd")
                 if isinstance(tool_input.get(k), str) and os.path.isabs(os.path.expanduser(tool_input[k]))), None)
    base = os.path.expanduser(base) if base else None
    commands = _split_commands(tool_input["command"])
    if commands is None:
        return None
    out: list[str | None] = []
    for argv in commands:
        if argv[0] in ("cd", "pushd"):
            base = _chdir(base, argv[1]) if len(argv) > 1 else os.path.expanduser("~")
            continue
        if os.path.basename(argv[0]) != "git":
            continue
        i, where, ok = 1, base, True
        while i < len(argv) and argv[i].startswith("-"):
            opt = argv[i]
            if opt == "-C" and i + 1 < len(argv):
                where = _chdir(where, argv[i + 1]); i += 2; continue
            if opt == "-c" and i + 1 < len(argv):
                i += 2; continue
            if opt.startswith(("--git-dir", "--work-tree", "--namespace", "--exec-path")):
                ok = False
            i += 1
        if i >= len(argv) or argv[i] != "push":
            continue
        rest = argv[i + 1:]
        positional, j = [], 0
        while j < len(rest):
            a = rest[j]
            if a in ("-o", "--push-option", "--receive-pack", "--exec"):
                j += 2; continue
            if a.startswith("--repo"):
                ok = False
            elif not a.startswith("-"):
                positional.append(a)
            j += 1
        if not ok or not positional:
            out.append(None); continue
        remote = positional[0]
        if _URLISH.match(remote):
            out.append(_strip_userinfo(remote)); continue
        if not where or not os.path.isdir(where) or "/" in remote or remote.startswith("."):
            out.append(None); continue
        try:
            import subprocess
            r = subprocess.run(["git", "-C", where, "remote", "get-url", "--push", remote],
                               capture_output=True, text=True, timeout=5)
            out.append(_strip_userinfo(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None)
        except (OSError, subprocess.TimeoutExpired):
            out.append(None)
    return out


def pushes_only_to_own(tool_input, own: list[re.Pattern]) -> bool:
    """True when the call pushes, and every push provably lands in one of
    the principal's own repositories."""
    if not own:
        return False
    dests = _push_destinations(tool_input)
    return bool(dests) and all(d is not None and any(r.search(d) for r in own) for d in dests)


# ── a standing grant (owner decision 2026-09-30, Data's daily X post) ─────
def standing_grants_for(principal: str, policy_path: Path | None = None) -> list[tuple[str, re.Pattern]]:
    """(effect, command regex) for each of the principal's `standing_grants`."""
    from ledger.policy import load_policy
    policy = load_policy(Path(policy_path or DEFAULT_POLICY_FILE))
    entry = (policy.principals or {}).get(principal) or {}
    return [(str(g["effect"]), re.compile(str(g["command"]))) for g in (entry.get("standing_grants") or [])]


def standing_grant_covers(effect: str, grants: list[tuple[str, re.Pattern]], tool_name: str,
                          tool_input, effects: dict[str, dict]) -> bool:
    """True when a standing grant releases `effect` for this call: a shell
    command whose every command causing the effect is one the grant names.
    Code is never resolved, and the effect may not come from any other field."""
    rxs = [rx for e, rx in grants if e == effect]
    if not rxs or not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
        return False
    only = {effect: effects.get(effect) or {}}
    rest = {k: v for k, v in tool_input.items() if k not in ("command", "description")}
    if rest and classify(tool_name, rest, only):
        return False
    commands = _split_commands(tool_input["command"])
    if not commands:
        return False
    hits = [" ".join(argv) for argv in commands
            if classify(tool_name, {"command": " ".join(argv)}, only)]
    return bool(hits) and all(any(rx.search(h) for rx in rxs) for h in hits)


def principal_for(actor: str | None = None) -> str:
    """The principal whose limits bind this executor: the writer's own entry,
    or the principal that lists it under writes_as (nightshift -> miles)."""
    # With no actor given, this host's actor is resolved STRICTLY (decision
    # Q2, 2026-09-23): an undeclared host raises actor_identity.UndeclaredActor,
    # which evaluate_hook turns into a deny, instead of a hostname guess.
    from actor_identity import principal_of, this_actor
    actor = (actor or this_actor(strict=True)).strip().lower()
    name, _ = principal_of(actor)
    if name is None:
        raise ValueError('executor writer has no declared principal')
    return name


def fingerprint(tool_name: str, tool_input) -> str:
    """The identity of ONE transaction: 16 hex of sha256 over the call's
    matchable text. A grant written `<effect>@<fingerprint>` covers exactly
    this call -- the same destination, the same amount -- and nothing else."""
    return hashlib.sha256(call_text(tool_input, tool_name).encode()).hexdigest()[:16]


def _grant_covers(effect: str, spec: dict, granted: set[str], tool_name: str, tool_input) -> bool:
    """Whether the task's grants cover this call's `effect`.

    An ordinary effect is covered by its name. A `per_transaction` effect
    (payment: MEM-04, "no confirmation step is skipped unless I approved that
    exact transaction") is not covered by its bare name alone: the bare grant
    reaches only a destination on the effect's `approved` list, and anything
    else needs the grant `<effect>@<fingerprint>` of this exact call. Until
    2026-09-26 one granted payment let every payment of the task through."""
    if not spec.get("per_transaction"):
        return effect in granted
    if f"{effect}@{fingerprint(tool_name, tool_input)}" in granted:
        return True
    text = call_text(tool_input, tool_name)
    return effect in granted and any(r.search(text) for r in spec.get("approved") or [])


def decide(principal: str, tool_name: str, tool_input, granted=(),
           effects: dict[str, dict] | None = None,
           policy_path: Path | None = None) -> Decision:
    never, cosign = limits_for(principal, policy_path)
    effects = effects if effects is not None else load_effects()
    hit = classify(tool_name, tool_input, effects)
    if not hit:
        return Decision(True, hit, "no policy effect", "allow")
    forbidden = hit & never
    if forbidden:
        what = ", ".join(sorted(forbidden))
        return Decision(False, hit, f"{principal} may never cause {what} "
                                    f"(approvals_policy.yaml never_effects); the call is refused "
                                    f"and recorded — do not retry it another way", "never")
    grants = {str(g).strip() for g in granted if str(g).strip()}
    needs = {e for e in hit & cosign
             if not _grant_covers(e, effects.get(e) or {}, grants, tool_name, tool_input)}
    # A push to the principal's own repository is not a shared push (owner
    # decision 2026-09-30, Tris and tris-space). Only push.shared is released,
    # and only when every push in the call provably lands in an own repo; a
    # force push was refused above as history.rewrite, a tag push stays prod.deploy.
    if "push.shared" in needs and pushes_only_to_own(tool_input, own_repos_for(principal, policy_path)):
        needs.discard("push.shared")
    # A standing grant (owner decision 2026-09-30, Data's scheduled X post,
    # whose gateway carries no per-task grant): one co-signed effect, only for
    # the commands it names. Never-effects were refused above; a
    # per-transaction effect (payment) always needs that exact call's grant.
    if needs:
        standing = standing_grants_for(principal, policy_path)
        if standing:
            needs = {e for e in needs if (effects.get(e) or {}).get("per_transaction")
                     or not standing_grant_covers(e, standing, tool_name, tool_input, effects)}
    if needs:
        what = ", ".join(sorted(needs))
        return Decision(False, hit, f"{what} needs a co-signed grant before it runs and this task "
                                    f"carries none; the call is paused and recorded — leave the "
                                    f"step as a proposal for a human to grant", "cosign")
    return Decision(True, hit, f"{', '.join(sorted(hit))} granted on this task", "granted")


# ── the record ──────────────────────────────────────────────────────────────
def record_refusal(decision: Decision, *, principal: str, tool_name: str,
                   space_dir: Path | None = None, task_id: str = "",
                   actor: str | None = None, detail: str = "") -> bool:
    """Append the refusal to the ledger of the task's space. Best-effort:
    returns False and says why on stderr when it cannot; never raises."""
    try:
        from actor_identity import this_actor
        from ledger.log import EventLog
        from spaces import space_for
        space = Path(space_dir) if space_dir else ROOT / space_for("system", ROOT, "0-personal")
        if not (space / ".datacore" / "events").is_dir():
            print(f"[tool-policy] no ledger at {space}; refusal not recorded", file=sys.stderr)
            return False
        # Strict (decision Q2): an undeclared host records nothing rather
        # than open a log under its hostname; the refusal itself still stands.
        log = EventLog(space, (actor or this_actor(strict=True)).strip().lower())
        log.append("metric.attest", {
            "metric": REFUSAL_METRIC,
            "principal": principal,
            "task": task_id or "",
            "tool": tool_name,
            "effects": sorted(decision.effects),
            "kind": decision.kind,
            "reason": decision.reason[:300],
            # Commands and structured inputs may contain credentials or
            # personal content. Correlate refusals without copying that data.
            "detail_sha256": hashlib.sha256(detail.encode()).hexdigest(),
        })
        return True
    except Exception as e:  # noqa: BLE001 — the record is evidence, not the gate
        print(f"[tool-policy] refusal not recorded ({type(e).__name__}: {e})", file=sys.stderr)
        return False


# ── the hook ────────────────────────────────────────────────────────────────
def context_from_env(env=None) -> dict:
    env = os.environ if env is None else env
    principal = (env.get("DATACORE_POLICY_PRINCIPAL") or "").strip().lower()
    if not principal:
        principal = principal_for()
    granted = [g.strip() for g in (env.get("DATACORE_POLICY_GRANTED") or "").split(",") if g.strip()]
    space = env.get("DATACORE_POLICY_SPACE") or ""
    return {"principal": principal, "granted": granted,
            "space": Path(space) if space else None,
            "task": (env.get("DATACORE_POLICY_TASK") or "").strip()}


def deny_output(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def evaluate_hook(payload: dict, env=None, *, record: bool = True,
                  effects: dict[str, dict] | None = None,
                  policy_path: Path | None = None) -> dict | None:
    """The PreToolUse decision for one call. Returns the hook's JSON output
    when the call is refused or paused, None when it may proceed."""
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_name"), str) or not payload["tool_name"]:
        return deny_output("invalid tool-policy request")
    tool_name = payload["tool_name"]
    tool_input = (payload or {}).get("tool_input") or {}
    try:
        ctx = context_from_env(env)
        # A contained overnight task (Phase 5A, 2026-10-04): the per-task
        # half -- environment dumps, hosts off its allowlist, deletes outside
        # its workspace, writes into another space's inbox -- before the
        # per-principal half below. Inactive unless the executor marked it.
        import tool_containment
        live_env = os.environ if env is None else env
        if tool_containment.active(live_env):
            cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
            hit = tool_containment.check(tool_name, tool_input, live_env, cwd=cwd)
            if hit is not None:
                kind, what, reason = hit
                tool_containment.note_refusal(ctx["task"], kind, what)
                if record:
                    record_refusal(Decision(False, {f"contain.{kind}"}, reason, "contain"),
                                   principal=ctx["principal"], tool_name=tool_name,
                                   space_dir=ctx["space"], task_id=ctx["task"],
                                   detail=call_text(tool_input, tool_name)[:200])
                return deny_output(reason)
        effects = effects if effects is not None else load_effects()
        decision = decide(ctx["principal"], tool_name, tool_input, ctx["granted"], effects, policy_path)
    except Exception as e:  # noqa: BLE001 — unavailable policy cannot authorize work
        print(f"[tool-policy] policy unavailable ({type(e).__name__}); call refused", file=sys.stderr)
        return deny_output("execution policy unavailable; restore valid policy before retrying")
    if decision.allow:
        return None
    if record:
        record_refusal(decision, principal=ctx["principal"], tool_name=tool_name,
                       space_dir=ctx["space"], task_id=ctx["task"],
                       detail=call_text(tool_input, tool_name)[:200])
    return deny_output(decision.reason)


def hook_main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        print(json.dumps(deny_output("invalid tool-policy JSON request")))
        return 0
    out = evaluate_hook(payload)
    if out is not None:
        print(json.dumps(out))
    return 0


#: The file guards an unattended run carries beside the policy guard (MEM-08):
#: what an interactive session gets from ~/.claude/settings.json, a `claude -p`
#: run with its own `--settings` did not get at all, so "never reach the
#: client's networks" and "never write a wrong weekday" were instructions only.
SAFETY_GUARDS = (
    ("restricted_hosts_guard.py", "Bash|WebFetch"),
    ("org_date_prewrite.py", "Edit|Write|MultiEdit"),
)


def settings_json(guard: Path | None = None, timeout: int = 8) -> str:
    """The `--settings` JSON that wires the guard as a PreToolUse hook on
    every tool, for `claude -p` runs that load no other settings, followed by
    each SAFETY_GUARDS file found beside it on the tools it covers."""
    guard = Path(guard or GUARD)
    hooks = [{"matcher": "*", "hooks": [{"type": "command",
                                         "command": f"python3 {shlex.quote(str(guard))}",
                                         "timeout": timeout}]}]
    for name, matcher in SAFETY_GUARDS:
        path = guard.parent / name
        if path.is_file():
            hooks.append({"matcher": matcher, "hooks": [{"type": "command",
                                                         "command": f"python3 {shlex.quote(str(path))}",
                                                         "timeout": timeout}]})
    return json.dumps({"hooks": {"PreToolUse": hooks}})


if __name__ == "__main__":
    sys.exit(hook_main())
