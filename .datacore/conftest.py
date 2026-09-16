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
