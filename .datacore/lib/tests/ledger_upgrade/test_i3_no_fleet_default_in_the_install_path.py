"""I3 (deterministic): no fleet name is a default anywhere in the install path.

Ledger upgrade Phase 3, eval I3 (PLAN.md; audit C13). The install path of
profile A is what its commands run: `ledger_cli.py` (init, doctor, principals,
append, verify, items, void) and `ledger_transport.py`, plus every local module
they import, transitively. Names: winston, miles, nightshift, mac, box, tris,
gregor, plur-claw, hermes, and this fleet's home paths.

Complements the INS-3 promise eval (`test_promise_INS3_no_fleet_names_by_default.py`,
not edited here), which scans every shipped string value for most of these
names but, by owner decision 2026-09-27, lets "nightshift" and "hermes" stand
outside an ssh target and does not look for "mac" or "box" at all. I3 is
narrower in files and stricter in names:

  * STATIC, in default positions only: a parameter default, `default=`, the
    fallback of `.get()`/`getenv()`/`setdefault()`/`pop()`, an `x or "name"`
    fallback, and a module- or class-level constant. Comments and docstrings
    are the allow-list (they may tell history); so are other string literals,
    which are messages, not defaults.
  * BEHAVIOURAL, on a clean machine (`_install_kit`): every file that init,
    `principals add` and doctor write, and everything they print, names no
    fleet name and not this machine's own hostname -- the hostname is never a
    guess for a writer (on the owner's Mac the hostname IS a fleet name). And
    init without --actor is refused rather than defaulted.

Seeded failure: `DEFAULT_SEQUENCER = "winston"` in ledger/seal.py, an argparse
`default="mac"`, or an init that falls back to the hostname when --actor is
left out.
"""
from __future__ import annotations

import ast
import re
import socket
from pathlib import Path

import pytest

import _install_kit as K

LIB = K.LIB
ENTRY = ("ledger_cli.py", "ledger_transport.py")


def _module_file(name: str) -> Path | None:
    parts = name.split(".")
    for cand in (LIB.joinpath(*parts).with_suffix(".py"), LIB.joinpath(*parts) / "__init__.py"):
        if cand.is_file():
            return cand
    return None


def install_path() -> list[Path]:
    """The entry scripts and every local module they import, transitively."""
    seen: set[Path] = set()
    todo = [LIB / e for e in ENTRY]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        pkg = f.parent.relative_to(LIB).parts
        for n in ast.walk(ast.parse(f.read_text())):
            names: list[str] = []
            if isinstance(n, ast.Import):
                names = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                if n.level:
                    base = ".".join(pkg[:len(pkg) - n.level + 1]) if pkg else ""
                    mod = ".".join(x for x in (base, n.module or "") if x)
                else:
                    mod = n.module or ""
                names = [mod] + [f"{mod}.{a.name}" for a in n.names]
            todo += [m for m in (_module_file(x) for x in names if x) if m]
    return sorted(seen)


def _defaults(tree: ast.AST):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for d in n.args.defaults + [d for d in n.args.kw_defaults if d is not None]:
                yield "parameter default", d
        elif isinstance(n, ast.Call):
            for kw in n.keywords:
                if kw.arg == "default":
                    yield "default=", kw.value
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name in ("get", "getenv", "setdefault", "pop") and len(n.args) >= 2:
                yield f"{name}() fallback", n.args[1]
        elif isinstance(n, ast.BoolOp) and isinstance(n.op, ast.Or):
            for v in n.values[1:]:
                if isinstance(v, (ast.Constant, ast.Tuple, ast.List, ast.Set)):
                    yield "or-fallback", v
    bodies = [tree.body] + [c.body for c in ast.walk(tree) if isinstance(c, ast.ClassDef)]
    for body in bodies:
        for st in body:
            if isinstance(st, (ast.Assign, ast.AnnAssign)) and st.value is not None:
                yield "module/class constant", st.value


def fleet_defaults(source: str, rel: str = "<src>") -> list[str]:
    hits = []
    for where, node in _defaults(ast.parse(source)):
        for s in ast.walk(node):
            if isinstance(s, ast.Constant) and isinstance(s.value, str):
                m = K.FLEET.search(s.value)
                if m:
                    hits.append(f"{rel}:{s.lineno}: {where} names {m.group(0)!r}")
    return hits


def test_the_scanner_bites():
    assert len(install_path()) >= 20, "the install path closure is broken: too few modules"
    assert fleet_defaults('DEFAULT_SEQUENCER = "winston"\n')
    assert fleet_defaults('import argparse\np = argparse.ArgumentParser()\np.add_argument("--machine", default="mac")\n')
    assert fleet_defaults('import os\na = os.environ.get("DATACORE_ACTOR", "nightshift")\n')
    assert fleet_defaults('def f(host="box"):\n    pass\n')
    assert fleet_defaults('def f(a):\n    return a or "miles"\n')
    assert not fleet_defaults('def f():\n    """winston wrote this once."""\n    # mac, history\n    return 1\n')
    assert not fleet_defaults('def f(x):\n    raise ValueError(f"no such writer {x}")\n')


def test_no_fleet_name_is_a_default_in_the_install_path():
    hits = [h for f in install_path() for h in fleet_defaults(f.read_text(), str(f.relative_to(LIB)))]
    assert hits == [], "fleet names offered as defaults in the install path:\n" + "\n".join(hits)


def test_a_clean_install_writes_and_says_no_fleet_name_and_never_guesses_the_hostname(tmp_path):
    m = K.clean_machine(tmp_path)
    host = socket.gethostname().split(".")[0].lower()

    bare = m.cli("init --space team")
    assert bare.returncode != 0, "init without --actor was accepted: a writer was guessed"
    assert not (m.home / ".datacore" / "identity.env").exists(), "init without --actor declared an identity anyway"

    runs = [m.cli("init --space team --actor alice"), m.cli("principals add --actor bob --kind human"),
            m.cli("doctor --space team")]
    for p in runs[:2]:
        assert p.returncode == 0, f"{p.args[-1]} failed: {p.stderr[-600:]}"
    said = m.scrub("\n".join(p.stdout + p.stderr for p in [bare] + runs))
    written = {k: m.scrub(v) for k, v in K.files_written(m).items()}
    assert written, "the install wrote nothing to inspect"

    leaks = [f"output: {K.FLEET.search(said).group(0)!r}"] if K.FLEET.search(said) else []
    leaks += [f"{k}: {K.FLEET.search(v).group(0)!r}" for k, v in written.items() if K.FLEET.search(v)]
    assert not leaks, "a clean install names this fleet:\n" + "\n".join(leaks)

    own_words = {"alice", "bob", "team", "human", "ledger", "data", "home"}
    if len(host) > 2 and host not in own_words:
        word = re.compile(rf"(?<![\w.-]){re.escape(host)}(?![\w-])", re.I)
        guessed = [k for k, v in written.items() if word.search(v)]
        assert not guessed, f"the install wrote this machine's hostname {host!r}, a guess, into: {guessed}"


@pytest.mark.parametrize("mutant", ['DEFAULT_SEQUENCER = "winston"\n'])
def test_the_seeded_failure_is_caught(mutant):
    """The seeded failure, as text appended to a module of the install path."""
    seal = LIB / "ledger" / "seal.py"
    assert seal in install_path(), "ledger/seal.py is no longer in the install path; reseed I3"
    assert fleet_defaults(seal.read_text() + mutant, "ledger/seal.py"), "I3 does not catch its own seeded failure"
