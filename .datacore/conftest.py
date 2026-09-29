"""All bundled test suites use disposable state, including module tests."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_datacore_state(tmp_path_factory, monkeypatch):
    """A throwaway state directory, and NOT one inside the test's own tmp_path.

    `private_state_directory()` refuses to place runtime state anywhere under a
    Git repository, and plenty of tests `git init` their tmp_path -- so a state
    directory nested in it turned into "private runtime state cannot be stored
    in a Git repository" for reasons that had nothing to do with the test.
    """
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path_factory.mktemp("dc-state")))


@pytest.fixture(autouse=True)
def _hermetic_git_config(monkeypatch):
    """No test may inherit this machine's git configuration.

    `core.hooksPath` is set GLOBALLY here, to .datacore/githooks, so every
    repository on the machine runs Datacore's hooks -- fixture repositories
    included. Six tests that install a hook into a throwaway repo and assert it
    rejects a push were therefore asserting against a hooks directory they had
    never written to, and reported "DID NOT RAISE": the guarantee they exist to
    protect was not being tested at all, on this machine, silently.

    Nulling both config files is the standard hermetic-git idiom and makes the
    suite say the same thing wherever it runs. Fixtures already set user.name
    and user.email locally, which is the only ambient setting they needed.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")


@pytest.fixture(autouse=True)
def _isolated_ledger_keys(tmp_path_factory, monkeypatch):
    """No test may read or write this installation's signing keys or proven keys.

    `ledger.keys` resolves its key directory, its local registry and
    principals.yaml from DATACORE_ROOT at import -- the REAL ~/Data. So a test
    that signed with the default registry wrote its keys into the host's real
    `.datacore/keys/`: nightshift's registry carries `actor1`, `fixture`,
    `hosta`, `hostb`, `testactor`, `worker`, `writer` beside its genuine
    writers. And every signature check read the host's real principals.yaml,
    which is why a test signing as `miles` began failing the moment proven keys
    were given precedence (2026-09-17): the real `miles` key outranked the
    test's. A test that needs principals sets DATACORE_ROOT on the module
    itself, as the ones that exercise distribution already do.
    """
    try:
        from ledger import keys
    except Exception:  # noqa: BLE001 -- suites that never import the ledger are unaffected
        return
    root = tmp_path_factory.mktemp("dc-keys-root")
    monkeypatch.setattr(keys, "DATACORE_ROOT", root)
    monkeypatch.setattr(keys, "DEFAULT_KEYS_DIR", root / ".datacore" / "keys")
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", root / ".datacore" / "keys" / "registry.yaml")


@pytest.fixture(autouse=True)
def _isolated_attestations(tmp_path_factory, monkeypatch):
    """No test may attest into a real space's ledger.

    `attests()` records AFTER the wrapped call returns, and a test that mocks
    the transport makes it return. So an egress test writes a genuine
    `artifact.attest` event into whatever root `ledger_attest._roots()` finds --
    the real ~/Data -- claiming a post, a reply or a message that never left the
    machine. attest() never raises and returns None on failure, so nothing in
    the test fails and nothing in the output says it happened.

    Found on 2026-09-19 with 48 x.post/x.reply attestations sitting unpublished
    in 1-datafund. Attestations are the evidence record for DIP-0047 egress:
    false entries there are worse than missing ones, because the whole point is
    that the record can be trusted about what actually went out.

    A test that means to assert an attestation points _roots at its own tree.
    """
    try:
        import ledger_attest
    except Exception:  # noqa: BLE001 -- suites that never attest are unaffected
        return
    root = tmp_path_factory.mktemp("dc-attest-root")
    monkeypatch.setattr(ledger_attest, "_roots", lambda: [root])


# ── what this checkout can run (config/test-needs.yaml, lib/needs_gate.py) ────
# A test that checks the INSTALLATION -- its principal registry, its fleet, a
# module repository, the PLUR CLI, a live agent -- is left out only where that
# need is missing, and every run says so. On a full install nothing changes.
def _load_lib(name):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parent / "lib" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_NG = _load_lib("needs_gate")
_PG = _load_lib("promise_gate")
_UNMET = _NG.unmet_by_test()
_LEFT_OUT: dict[str, list[str]] = {}


def _repo_rel(path) -> str | None:
    from pathlib import Path
    try:
        return Path(str(path)).resolve().relative_to(_NG.ROOT).as_posix()
    except ValueError:
        return None


def pytest_ignore_collect(collection_path, config):
    # The promise gate (evals first) holds for every suite under .datacore, not
    # only lib/tests: a module's red-by-design eval must not break CI either.
    if _PG.should_ignore(collection_path):
        return True
    rel = _repo_rel(collection_path)
    if rel is None or rel not in _UNMET:
        return None
    _LEFT_OUT[rel] = _UNMET[rel]
    return True


def pytest_collection_modifyitems(session, config, items):
    named = {k: v for k, v in _UNMET.items() if "::" in k}
    if not named:
        return
    keep, drop = [], []
    for item in items:
        rel = _repo_rel(item.path)
        key = f"{rel}::{item.nodeid.split('::', 1)[1]}" if rel and "::" in item.nodeid else rel
        hit = next((d for d in named if key and _NG.matches(key, d)), None)
        if hit is None:
            keep.append(item)
        else:
            drop.append(item)
            _LEFT_OUT[hit] = named[hit]
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not _LEFT_OUT:
        return
    import os
    from collections import Counter
    by_need = Counter(n for needs in _LEFT_OUT.values() for n in needs)
    lines = [f"{len(_LEFT_OUT)} test file(s)/test(s) not run: they check an installation "
             f"and this machine lacks what they need (config/test-needs.yaml). "
             f"A full install runs them; PROMISE_EVALS_ALL=1 runs them anywhere."]
    lines += [f"  {n}: {c}" for n, c in sorted(by_need.items())]
    lines += [f"  - {t}  [needs {', '.join(n)}]" for t, n in sorted(_LEFT_OUT.items())]
    terminalreporter.write_sep("-", "not run here: needs this checkout does not have")
    for line in lines:
        terminalreporter.write_line(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as f:
                f.write("### Not run in CI: tests that need an installation\n\n")
                f.write(lines[0] + "\n\n| need | tests |\n|---|---|\n")
                f.writelines(f"| `{n}` | {c} |\n" for n, c in sorted(by_need.items()))
                f.write("\n<details><summary>Each test left out</summary>\n\n")
                f.writelines(f"- `{t}` needs {', '.join(f'`{x}`' for x in n)}\n"
                             for t, n in sorted(_LEFT_OUT.items()))
                f.write("\n</details>\n")
        except OSError:
            pass
