"""All bundled test suites use disposable state, including module tests."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_datacore_state(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "state"))
