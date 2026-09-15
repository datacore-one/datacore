import pytest

from behavior_options import OPTIONS, enabled


@pytest.mark.parametrize('name,variable', OPTIONS.items())
def test_new_policies_are_opt_in_and_do_not_cache_configuration(name, variable, monkeypatch):
    monkeypatch.delenv(variable, raising=False)
    assert enabled(name) is False
    monkeypatch.setenv(variable, '1')
    assert enabled(name) is True
    monkeypatch.setenv(variable, '0')
    assert enabled(name) is False
    monkeypatch.setenv(variable, 'true')
    with pytest.raises(ValueError, match=variable):
        enabled(name)
