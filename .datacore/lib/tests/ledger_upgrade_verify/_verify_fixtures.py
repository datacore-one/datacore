"""Shared fixtures for the Phase 2 evals: a disposable installation root, spaces
with planted defects, and one verdict per consumer of the ledger.

Nothing here reads or writes a live space except `copy_live_space`, which only
READS one (and the caller checks its checksums before and after).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[2]
INSTALL = LIB.parents[1]
#: Where the live spaces are read from (a scratch worktree points this at the real install).
LIVE_ROOT = Path(os.environ.get("LEDGER_EVAL_LIVE_ROOT", str(INSTALL)))

PRINCIPALS = """\
principals:
  owner:
    kind: human
    writes_as: [owner]
  alice:
    kind: human
    writes_as: [alice]
  bot:
    kind: agent
    writes_as: [bot]
"""


@pytest.fixture
def sandbox_root(tmp_path, monkeypatch):
    """A throwaway installation root: principals, a key registry, and nothing else.

    In-process modules and every subprocess (through DATACORE_ROOT) resolve the
    same principals and the same verify keys, so the CLI and the library are
    judged on identical inputs.
    """
    import actor_identity
    from ledger import keys
    root = tmp_path / "root"
    (root / ".datacore" / "registry").mkdir(parents=True)
    (root / ".datacore" / "keys").mkdir(parents=True)
    principals = root / ".datacore" / "registry" / "principals.yaml"
    principals.write_text(PRINCIPALS)
    monkeypatch.setenv("DATACORE_ROOT", str(root))
    monkeypatch.setenv("DATACORE_ACTOR", "owner")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", principals)
    monkeypatch.setattr(keys, "DATACORE_ROOT", root)
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", root / ".datacore" / "keys" / "registry.yaml")
    monkeypatch.setattr(keys, "DEFAULT_KEYS_DIR", tmp_path / "private-keys")
    return root


def new_space(root: Path, name: str = "0-alpha") -> Path:
    space = root / name
    (space / ".datacore").mkdir(parents=True, exist_ok=True)
    (space / ".datacore" / "config.yaml").write_text(f"space:\n  name: {name}\n  type: team\n")
    return space


def append(space: Path, actor: str, n: int = 3, *, sign: bool = False, prefix: str = "t") -> None:
    from ledger.log import EventLog
    log = EventLog(space, actor, sign=sign)
    for i in range(n):
        log.append("item.create", {"id": f"{prefix}-{actor}-{i}", "title": f"item {i}", "state": "NEXT"})


def log_path(space: Path, actor: str, telemetry: bool = False) -> Path:
    return space / ".datacore" / ("telemetry" if telemetry else "events") / f"{actor}.jsonl"


def edit_line(path: Path, index: int, edit) -> dict:
    """Apply `edit(event_dict)` to the event on line `index` (0-based), keep everything else."""
    lines = path.read_text().splitlines()
    ev = json.loads(lines[index])
    edit(ev)
    lines[index] = json.dumps(ev, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")
    return ev


def tamper_payload(path: Path, index: int = 1) -> None:
    """Change a stored event's body without re-hashing it: a hash mismatch."""
    def edit(ev):
        ev["payload"]["title"] = "edited after the fact"
    edit_line(path, index, edit)


def forge_signature(path: Path, index: int = 1) -> None:
    """Keep the body and hash, break the signature (a known writer's key)."""
    def edit(ev):
        sig = ev["sig"]
        assert sig, "fixture expects a signed event"
        ev["sig"] = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    edit_line(path, index, edit)


def hand_append_bad_hash(path: Path, actor: str) -> tuple[int, str, str]:
    """Append one event by hand whose stored hash never matched its body.
    Returns (seq, stored hash, the hash its body produces) -- a void pins both."""
    from ledger.events import body_dict, compute_hash
    last = json.loads(path.read_text().splitlines()[-1])
    seq = last["seq"] + 1
    body = body_dict(seq, f"{int(last['hlc'].split('.')[0]) + 1000}.0000.{actor}", actor,
                     "item.create", {"id": "forged", "title": "forged", "state": "DONE"}, last["hash"])
    h = "0" * 63 + "1"
    with path.open("a") as f:
        f.write(json.dumps({**body, "hash": h, "sig": ""}, sort_keys=True, separators=(",", ":")) + "\n")
    return seq, h, compute_hash(body)


def void(space: Path, voider: str, target_log: str, seq: int, h: str, body_sha256: str | None = None) -> None:
    """What `ledger_cli.py void` writes: names the event, and pins its body."""
    from ledger.log import EventLog
    payload = {"log": target_log, "seq": seq, "hash": h, "reason": "hand-written, not produced by EventLog"}
    if body_sha256:
        payload["body_sha256"] = body_sha256
    EventLog(space, voider).append("ledger.void", payload)


def malform_line(path: Path, index: int) -> None:
    """Replace one middle line with something that is not an event."""
    lines = path.read_text().splitlines()
    lines[index] = '{"not": "an event"'
    path.write_text("\n".join(lines) + "\n")


# ── verdicts: one per consumer, each mapped to ok | broken | unknown ────────

def cli_verdict(space: Path, *extra: str) -> tuple[str, str]:
    proc = subprocess.run([sys.executable, str(LIB / "ledger_cli.py"), "verify", "--space", str(space), *extra],
                          capture_output=True, text=True, timeout=300, env=dict(os.environ))
    out = proc.stdout + proc.stderr
    return {0: "ok", 1: "broken", 3: "unknown"}.get(proc.returncode, f"error rc={proc.returncode}"), out


def health_verdict(root: Path) -> tuple[str, str]:
    import ledger_health
    result = ledger_health.check(root)
    return {True: "ok", False: "broken", None: "unknown"}[result["ok"]], json.dumps(result, sort_keys=True)


def relay_verdict(space: Path) -> tuple[str, str]:
    import git_relay
    bad = git_relay.ledger_forks(space)
    if not bad:
        return "ok", ""
    if all("could not be established" in b for b in bad):
        return "unknown", "; ".join(bad)
    return "broken", "; ".join(bad)


def checkpoint_verdict(space: Path) -> tuple[str, str]:
    import ledger_checkpoint
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            ledger_checkpoint.write(space)
        except ValueError as exc:
            return "broken", f"write refused: {exc}"
        ok, detail = ledger_checkpoint.verify(space)
    return ("ok" if ok else "broken"), detail


def seal_verdict(space: Path) -> tuple[str, str]:
    from ledger.log import read_events
    from ledger.seal import build_seal_payload
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            build_seal_payload(read_events(space))
        except ValueError as exc:
            return "broken", str(exc)
    return "ok", ""


def all_verdicts(root: Path, space: Path) -> dict[str, tuple[str, str]]:
    return {"cli": cli_verdict(space), "health": health_verdict(root), "relay": relay_verdict(space),
            "checkpoint": checkpoint_verdict(space), "seal": seal_verdict(space)}


# ── live spaces: read only, checked by checksum ──────────────────────────────

LEDGER_DIRS = ("events", "telemetry")


def count_events(space: Path) -> int:
    n = 0
    for d in LEDGER_DIRS:
        for f in sorted((space / ".datacore" / d).glob("*.jsonl")) if (space / ".datacore" / d).is_dir() else []:
            n += sum(1 for line in f.read_bytes().splitlines() if line.strip())
    return n


def ledger_digest(space: Path) -> dict[str, str]:
    out = {}
    for d in LEDGER_DIRS:
        folder = space / ".datacore" / d
        for f in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
            out[f"{d}/{f.name}"] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def live_spaces(min_events: int) -> list[Path]:
    """Real spaces of this installation holding at least `min_events` ledger events."""
    sys.path.insert(0, str(LIB))
    from spaces import discover_spaces
    found = []
    for s in discover_spaces(LIVE_ROOT):
        if (s.path / ".datacore" / "events").is_dir() and count_events(s.path) >= min_events:
            found.append(s.path)
    return found


def copy_live_space(live: Path, dest_root: Path) -> Path:
    """Copy one live space's ledger (events + telemetry) into the sandbox root."""
    copy = dest_root / live.name
    (copy / ".datacore").mkdir(parents=True)
    for d in LEDGER_DIRS:
        if (live / ".datacore" / d).is_dir():
            shutil.copytree(live / ".datacore" / d, copy / ".datacore" / d)
    return copy


def copy_live_registry(dest_root: Path) -> None:
    """The installation's principals (with its proven verify keys), read only."""
    src = LIVE_ROOT / ".datacore" / "registry" / "principals.yaml"
    if src.is_file():
        shutil.copyfile(src, dest_root / ".datacore" / "registry" / "principals.yaml")
    reg = LIVE_ROOT / ".datacore" / "keys" / "registry.yaml"
    if reg.is_file():
        shutil.copyfile(reg, dest_root / ".datacore" / "keys" / "registry.yaml")
