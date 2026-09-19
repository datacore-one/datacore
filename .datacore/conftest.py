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
