#!/usr/bin/env python3
"""Import every declared egress function and prove it is really wrapped.

DIP-0047. `egress_scan.py` reads SOURCE: it proves a decorator is written above
a def. That is not the same as the decorator being in force at runtime, and the
difference is exactly where this system has failed before -- `from
datacore.ledger import attests` resolving on one machine and not another, a
module that raises on import, a decorator applied to the first of two
same-named methods while callers use the second.

So this imports the module for real and asks the object: do you carry
`__datacore_egress__`, and does it match what module.yaml claims?

WHAT IT DELIBERATELY DOES NOT DO: send anything. Verifying a WhatsApp sender by
sending a WhatsApp message means publishing to a third party to check the
bookkeeping, which is a poor trade and not reversible. The send path is proven
separately by `--functional`, which stubs the transport and asserts an event
lands in a scratch ledger.

A module that cannot be imported here reports `n-a` with the reason, never `ok`.
An unimportable module is an unknown, and the whole point of the three-state
convention is that unknowns do not read as fine.

    egress_runtime_check.py [--module NAME] [--functional]
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

MODULES = LIB.parent / "modules"


def _load(path: Path):
    """Import a module file under a unique name, without polluting sys.modules
    with a name another module might also claim."""
    # Module files import their SIBLINGS by bare name (`import waha_client`),
    # which works when the module's own lib dir is on the path and fails here
    # otherwise. Without this, three WhatsApp senders reported `n-a` — an
    # honest "could not tell", but an avoidable one caused by the harness, not
    # by the code under test.
    for extra in (path.parent, path.parent.parent):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    name = "egressprobe_" + path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"no loader for {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _resolve(mod, fname: str):
    """Find a function by name at module level or on any class it defines.

    Class methods are the common case here -- most senders are methods -- and
    module.yaml names them bare, as `file.py:send_message`, because that is what
    the AST scan sees.
    """
    if hasattr(mod, fname):
        return getattr(mod, fname)
    for obj in vars(mod).values():
        if isinstance(obj, type) and hasattr(obj, fname):
            return getattr(obj, fname)
    return None


def _candidates() -> list[str]:
    """Interpreters that might own a module's dependencies, best first."""
    import os
    import shutil
    seen, out = set(), []
    for c in (os.environ.get("DATACORE_PYTHON", ""), "python3.13", "python3.12",
              "python3.11", "python3.10", "/opt/homebrew/bin/python3",
              "/usr/local/bin/python3", "/usr/bin/python3", "python3"):
        if not c:
            continue
        p = shutil.which(c) or (c if Path(c).is_file() else None)
        if p and p not in seen and p != sys.executable:
            seen.add(p)
            out.append(p)
    return out


def _verify_elsewhere(target: Path, fname: str, kind: str):
    """Re-run the single-function probe under other interpreters.

    Returns (ok, detail) from the first interpreter that can import the module,
    or None if none can -- in which case the caller keeps the original `n-a`,
    because "nobody here can import it" really is an unknown.
    """
    import json
    import subprocess
    probe = (
        "import importlib.util,sys,json;"
        f"p=r'{target}';"
        "sys.path.insert(0,str(__import__('pathlib').Path(p).parent));"
        "sys.path.insert(0,str(__import__('pathlib').Path(p).parent.parent));"
        "s=importlib.util.spec_from_file_location('probe_m',p);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        f"f=getattr(m,'{fname}',None) or next((getattr(o,'{fname}') "
        f"for o in vars(m).values() if isinstance(o,type) and hasattr(o,'{fname}')),None);"
        "print(json.dumps({'k':getattr(f,'__datacore_egress__',None)}))"
    )
    for py in _candidates():
        try:
            r = subprocess.run([py, "-c", probe], capture_output=True,
                               text=True, timeout=90)
        except Exception:  # noqa: BLE001
            continue
        if r.returncode != 0:
            continue
        try:
            got = json.loads(r.stdout.strip().splitlines()[-1])["k"]
        except Exception:  # noqa: BLE001
            continue
        short = Path(py).name
        if got is None:
            return (False, f"imported under {short} but NOT wrapped")
        if got != kind:
            return (False, f"wrapped as {got!r} under {short}, manifest says {kind!r}")
        return (True, f"{kind} (verified under {short})")
    return None


def check(module_names: set[str]) -> list[tuple]:
    import yaml

    rows: list[tuple] = []
    for mod_dir in sorted(p for p in MODULES.iterdir() if p.is_dir()):
        if module_names and mod_dir.name not in module_names:
            continue
        manifest = mod_dir / "module.yaml"
        if not manifest.is_file():
            continue
        try:
            declared = (yaml.safe_load(manifest.read_text()) or {}).get("egress") or []
        except Exception as exc:  # noqa: BLE001
            rows.append((mod_dir.name, "-", None, f"manifest unreadable: {exc}"))
            continue
        for entry in declared:
            if not isinstance(entry, dict) or not entry.get("fn"):
                continue
            rel, _, fname = str(entry["fn"]).partition(":")
            kind = str(entry.get("kind", ""))
            target = mod_dir / rel
            label = f"{rel}:{fname}"
            if not target.is_file():
                rows.append((mod_dir.name, label, False, "declared file does not exist"))
                continue
            try:
                mod = _load(target)
            except Exception as exc:  # noqa: BLE001
                # A MODULE'S DEPENDENCIES BELONG TO ITS OWN INTERPRETER, not to
                # whichever one happens to run the checklist. Under python3.14
                # six modules reported `n-a` on aiohttp / PIL /
                # google.generativeai, while the same modules verified fine
                # under the 3.11 that actually runs them. That is a fact about
                # this process, not about the module -- and reporting it as
                # unverifiable made the checklist look permanently incomplete
                # for a reason that has nothing to do with attestation.
                #
                # So ask the other interpreters. If any of them can import the
                # module, the honest answer is whatever that one says.
                verdict = _verify_elsewhere(target, fname, kind)
                if verdict is not None:
                    rows.append((mod_dir.name, label) + verdict)
                    continue
                rows.append((mod_dir.name, label, None,
                             f"import failed: {type(exc).__name__}: {exc}"[:90]))
                continue
            fn = _resolve(mod, fname)
            if fn is None:
                rows.append((mod_dir.name, label, False, "function not found after import"))
                continue
            got = getattr(fn, "__datacore_egress__", None)
            if got is None:
                rows.append((mod_dir.name, label, False,
                             "imported but NOT wrapped — decorator not in force"))
            elif got != kind:
                rows.append((mod_dir.name, label, False,
                             f"wrapped as {got!r}, manifest says {kind!r}"))
            else:
                rows.append((mod_dir.name, label, True, kind))
    return rows


def functional() -> tuple[bool, str]:
    """Call a decorated function with the transport stubbed and prove an event
    lands. Uses a scratch space, so nothing touches a real ledger."""
    import os
    import tempfile

    from datacore.ledger import attests

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "1-datafund" / ".datacore" / "events").mkdir(parents=True)
        os.environ["DATACORE_ROOT"] = str(root)
        os.environ["DATACORE_ACTOR"] = "mac"
        for m in [k for k in sys.modules if k.startswith("ledger")]:
            del sys.modules[m]

        sent = {}

        @attests("whatsapp.sent", ref=lambda r: r["id"])
        def fake_sender(to, text):
            sent["to"] = to                      # stands in for the transport
            return {"id": "runtime-probe-1"}

        fake_sender("+000", "probe")

        sys.path.insert(0, str(LIB))
        from ledger.log import read_events
        events = [e for e in read_events(root / "1-datafund")
                  if e.type == "artifact.attest"]
        if not events:
            return False, "decorator ran but no artifact.attest reached the ledger"
        p = events[-1].payload
        if p.get("ref") != "runtime-probe-1":
            return False, f"event landed with wrong ref: {p.get('ref')!r}"
        return True, f"send -> {p['kind']} ref={p['ref']} in {len(events)} event(s)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", action="append")
    ap.add_argument("--functional", action="store_true")
    a = ap.parse_args()

    rows = check(set(a.module or []))
    ok = sum(1 for r in rows if r[2] is True)
    bad = [r for r in rows if r[2] is False]
    unk = [r for r in rows if r[2] is None]

    for name, label, state, detail in rows:
        mark = "ok  " if state else ("FAIL" if state is False else "n-a ")
        print(f"  {mark} {name}/{label:<52} {detail}")
    print(f"\n  runtime wiring: {ok} in force, {len(bad)} broken, {len(unk)} unverifiable")

    if a.functional:
        good, detail = functional()
        print(f"  {'ok  ' if good else 'FAIL'} functional: {detail}")
        if not good:
            return 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
