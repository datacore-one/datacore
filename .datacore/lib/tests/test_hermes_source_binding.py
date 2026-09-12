"""Installed policy code must not be replaced by an older data checkout."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

LIB = Path(__file__).resolve().parents[1]
REQUIRED = ['actor_identity.py', 'tool_policy.py', 'yaml_safety.py',
            'file_utils.py', 'process_run.py', 'spaces.py', 'ledger/__init__.py']


def library(path, source):
    for name in REQUIRED:
        p = path / name;p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('SOURCE = ' + repr(source) + '\n')


def inspect(tmp_path, override=None, loaded=False, partial=False, selected_behind=False, alias=False):
    installed = tmp_path / 'installed/lib';library(installed, 'installed')
    stale = tmp_path / 'Data/.datacore/lib';library(stale, 'data')
    plugin = installed / 'hermes_plugin';plugin.mkdir()
    shutil.copy2(LIB / 'hermes_plugin/__init__.py', plugin / '__init__.py')
    if partial:(installed / (partial if isinstance(partial,str) else 'tool_policy.py')).unlink()
    if alias:
        (installed / 'tool_policy.py').unlink()
        (installed / 'tool_policy.py').symlink_to(stale / 'tool_policy.py')
    code = '''import importlib.util,json,sys
from pathlib import Path
installed,stale,loaded,behind=sys.argv[1:]
if loaded=='yes':
 sys.path.insert(0,stale)
 import actor_identity
if behind=='yes':sys.path[:0]=[stale,installed]
spec=importlib.util.spec_from_file_location('hermes_fixture',Path(installed)/'hermes_plugin/__init__.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
usable=m._lib();result={'usable':usable}
if usable:
 import actor_identity,tool_policy
 result.update(identity=actor_identity.SOURCE,policy=tool_policy.SOURCE)
print(json.dumps(result))
'''
    env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(tmp_path),
           'DATACORE_ROOT': str(tmp_path / 'Data')}
    if override is not None:env['DATACORE_LIB'] = override
    p = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(installed), str(stale),
                        'yes' if loaded else 'no', 'yes' if selected_behind else 'no'],
                       env=env, text=True, capture_output=True, timeout=10)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_installed_plugin_selects_its_own_policy_library(tmp_path):
    assert inspect(tmp_path) == {'usable': True, 'identity': 'installed', 'policy': 'installed'}


def test_existing_search_path_cannot_put_stale_code_ahead_of_selected_library(tmp_path):
    assert inspect(tmp_path, selected_behind=True) == {'usable': True, 'identity': 'installed', 'policy': 'installed'}


@pytest.mark.parametrize('override', ['', 'relative/lib', '/nonexistent/datacore-lib'])
def test_invalid_explicit_binding_refuses_instead_of_falling_back(tmp_path, override):
    assert inspect(tmp_path, override=override) == {'usable': False}


def test_preloaded_identity_from_another_installation_refuses(tmp_path):
    assert inspect(tmp_path, loaded=True) == {'usable': False}


@pytest.mark.parametrize("missing", REQUIRED)
def test_incomplete_installed_library_cannot_mix_with_data_code(tmp_path, missing):
    assert inspect(tmp_path, partial=missing) == {'usable': False}


def test_individual_policy_file_cannot_alias_another_installation(tmp_path):
    assert inspect(tmp_path, alias=True) == {'usable': False}
