"""Untrusted documents need text inference, never a tool-capable agent."""
import subprocess
from unittest.mock import Mock

import pytest


def assert_text_only(call, prompt):
    command = call.args[0]
    assert command[command.index('--tools') + 1] == ''
    assert '--safe-mode' in command and '--strict-mcp-config' in command
    assert '--no-session-persistence' in command
    assert '--dangerously-skip-permissions' not in command
    assert prompt not in command and prompt in call.kwargs['input']


def test_pr_review_is_text_only_and_failure_is_not_a_review(monkeypatch):
    import pr_review
    run = Mock(return_value=subprocess.CompletedProcess([], 0, 'review', ''))
    import process_run
    monkeypatch.setattr(process_run, 'run', run)
    assert pr_review.run_review('untrusted diff') == 'review'
    assert_text_only(run.call_args, 'untrusted diff')
    assert run.call_args.kwargs['check'] is True
    run.side_effect = subprocess.CalledProcessError(1, ['claude'])
    with pytest.raises(subprocess.CalledProcessError):
        pr_review.run_review('untrusted diff')


def test_newsletter_analysis_is_text_only(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    spec = importlib.util.spec_from_file_location('newsletter_under_test', Path(__file__).parents[1] / 'process_newsletters.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run = Mock(return_value=subprocess.CompletedProcess([], 0, 'report', ''))
    monkeypatch.setattr(module, 'run_process', run)
    assert module.process_group({'projects': 'test', 'name': 'test'}, ['untrusted title']) == 'report'
    assert_text_only(run.call_args, 'untrusted title')
