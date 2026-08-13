"""Tests for hooks.py - Agent Lifecycle Hooks (DIP-0016 §16-18)."""

import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from hooks import HookExecutor, HookResult


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    """Set up a minimal hook environment."""
    # Registry
    reg_dir = tmp_path / ".datacore" / "registry"
    reg_dir.mkdir(parents=True)
    # State dir
    state_dir = tmp_path / ".datacore" / "state"
    state_dir.mkdir(parents=True)
    # DIPs dir
    dips_dir = tmp_path / ".datacore" / "dips"
    dips_dir.mkdir(parents=True)

    monkeypatch.setenv("DATACORE_ROOT", str(tmp_path))
    monkeypatch.setattr("hooks.DATACORE_ROOT", tmp_path)
    monkeypatch.setattr("hooks.REGISTRY_PATH", reg_dir / "agents.yaml")
    monkeypatch.setattr("hooks.HOOK_STATE_PATH", state_dir / "hook_state.yaml")
    monkeypatch.setattr("hooks.DIPS_PATH", dips_dir)

    return tmp_path


def write_registry(hook_env, registry_data):
    """Write a registry file."""
    path = hook_env / ".datacore" / "registry" / "agents.yaml"
    with open(path, "w") as f:
        yaml.dump(registry_data, f)


class TestHookResult:
    """Test HookResult dataclass."""

    def test_default_values(self):
        """HookResult has sensible defaults."""
        r = HookResult()
        assert r.success is True
        assert r.abort is False
        assert r.context == ""
        assert r.data == {}

    def test_custom_values(self):
        """HookResult accepts custom values."""
        r = HookResult(success=False, abort=True, message="fail", data={"key": "val"})
        assert r.success is False
        assert r.abort is True
        assert r.message == "fail"
        assert r.data["key"] == "val"


class TestHookExecutorInit:
    """Test HookExecutor initialization."""

    def test_missing_registry_loads_empty(self, hook_env):
        """Missing registry file results in empty registry."""
        executor = HookExecutor()
        assert executor.registry == {}

    def test_missing_state_creates_default(self, hook_env):
        """Missing state file creates default state structure."""
        executor = HookExecutor()
        assert "retry_counts" in executor.state
        assert "space_cache" in executor.state

    def test_loads_existing_registry(self, hook_env):
        """Existing registry is loaded correctly."""
        write_registry(hook_env, {
            "agents": {"test-agent": {"description": "test"}},
        })
        executor = HookExecutor()
        assert "test-agent" in executor.registry.get("agents", {})


class TestAgentLookup:
    """Test _get_agent method."""

    def test_finds_core_agent(self, hook_env):
        """Core agents are found by ID."""
        write_registry(hook_env, {
            "agents": {"inbox-proc": {"description": "Inbox processor"}},
        })
        executor = HookExecutor()
        agent = executor._get_agent("inbox-proc")
        assert agent is not None
        assert agent["description"] == "Inbox processor"

    def test_finds_module_agent(self, hook_env):
        """Module agents are found by ID."""
        write_registry(hook_env, {
            "module_agents": {"crm-scorer": {"description": "CRM scorer", "module": "crm"}},
        })
        executor = HookExecutor()
        agent = executor._get_agent("crm-scorer")
        assert agent is not None

    def test_missing_agent_returns_none(self, hook_env):
        """Non-existent agent returns None."""
        write_registry(hook_env, {"agents": {}})
        executor = HookExecutor()
        assert executor._get_agent("nonexistent") is None


class TestHookResolution:
    """Test _resolve_hooks inheritance logic."""

    def test_defaults_used_when_no_agent_hooks(self, hook_env):
        """Default hooks are used when agent has no hooks."""
        write_registry(hook_env, {
            "defaults": {
                "hooks": {
                    "pre": [{"type": "context-inject", "config": {}}],
                },
            },
            "agents": {
                "test-agent": {"description": "test"},
            },
        })
        executor = HookExecutor()
        hooks = executor._resolve_hooks("test-agent", "pre")
        assert len(hooks) == 1
        assert hooks[0]["type"] == "context-inject"

    def test_agent_hooks_override_defaults(self, hook_env):
        """Agent-specific hooks override defaults."""
        write_registry(hook_env, {
            "defaults": {
                "hooks": {
                    "pre": [{"type": "context-inject", "config": {}}],
                },
            },
            "agents": {
                "test-agent": {
                    "description": "test",
                    "hooks": {
                        "pre": [{"type": "validate-preconditions", "config": {}}],
                    },
                },
            },
        })
        executor = HookExecutor()
        hooks = executor._resolve_hooks("test-agent", "pre")
        assert len(hooks) == 1
        assert hooks[0]["type"] == "validate-preconditions"

    def test_inherit_defaults_keyword(self, hook_env):
        """Agent hooks can inherit defaults at a specific position."""
        write_registry(hook_env, {
            "defaults": {
                "hooks": {
                    "pre": [{"type": "context-inject", "config": {}}],
                },
            },
            "agents": {
                "test-agent": {
                    "description": "test",
                    "hooks": {
                        "pre": [
                            {"inherit": "defaults"},
                            {"type": "validate-preconditions", "config": {}},
                        ],
                    },
                },
            },
        })
        executor = HookExecutor()
        hooks = executor._resolve_hooks("test-agent", "pre")
        assert len(hooks) == 2
        assert hooks[0]["type"] == "context-inject"
        assert hooks[1]["type"] == "validate-preconditions"

    def test_empty_hook_type_returns_empty(self, hook_env):
        """Unknown hook type returns empty list."""
        write_registry(hook_env, {
            "agents": {"test-agent": {"description": "test"}},
        })
        executor = HookExecutor()
        hooks = executor._resolve_hooks("test-agent", "nonexistent")
        assert hooks == []


class TestPreHooks:
    """Test pre-execution hook execution."""

    def test_pre_hooks_return_continue(self, hook_env):
        """Pre-hooks with no abort return should_continue=True."""
        write_registry(hook_env, {
            "agents": {"test-agent": {"description": "test"}},
        })
        executor = HookExecutor()
        should_continue, context = executor.execute_pre_hooks("test-agent", "Test task")
        assert should_continue is True

    def test_precondition_failure_aborts(self, hook_env):
        """Failed preconditions abort execution."""
        write_registry(hook_env, {
            "agents": {
                "test-agent": {
                    "description": "test",
                    "hooks": {
                        "pre": [{
                            "type": "validate-preconditions",
                            "config": {
                                "required_files": ["nonexistent-required-file.md"],
                                "abort_on_failure": True,
                            },
                        }],
                    },
                },
            },
        })
        executor = HookExecutor()
        should_continue, context = executor.execute_pre_hooks("test-agent", "Test")
        assert should_continue is False
        assert "missing" in context.lower()


class TestValidationHooks:
    """Test validation hook execution."""

    def test_output_exists_passes(self, hook_env, tmp_path):
        """Output-exists validation passes when files exist."""
        write_registry(hook_env, {
            "agents": {
                "test-agent": {
                    "description": "test",
                    "hooks": {
                        "validate": [{
                            "type": "output-exists",
                            "config": {"check_writes": True},
                        }],
                    },
                },
            },
        })
        # Create output file
        output = tmp_path / "output.md"
        output.write_text("Result content")

        executor = HookExecutor()
        passed, msg = executor.execute_validate_hooks("test-agent", {
            "outputs": {"files_created": [str(output)]},
        })
        assert passed is True

    def test_output_exists_fails_no_files(self, hook_env):
        """Output-exists validation fails when no files created."""
        write_registry(hook_env, {
            "agents": {
                "test-agent": {
                    "description": "test",
                    "hooks": {
                        "validate": [{
                            "type": "output-exists",
                            "config": {"check_writes": True},
                        }],
                    },
                },
            },
        })
        executor = HookExecutor()
        passed, msg = executor.execute_validate_hooks("test-agent", {
            "outputs": {"files_created": []},
        })
        assert passed is False

    def test_quality_gate_min_length(self, hook_env):
        """Quality gate rejects short output."""
        write_registry(hook_env, {
            "agents": {
                "test-agent": {
                    "description": "test",
                    "hooks": {
                        "validate": [{
                            "type": "quality-gate",
                            "config": {"min_output_length": 100},
                        }],
                    },
                },
            },
        })
        executor = HookExecutor()
        passed, msg = executor.execute_validate_hooks("test-agent", {
            "output_text": "too short",
        })
        assert passed is False

    def test_quality_gate_forbidden_patterns(self, hook_env):
        """Quality gate rejects forbidden patterns."""
        write_registry(hook_env, {
            "agents": {
                "test-agent": {
                    "description": "test",
                    "hooks": {
                        "validate": [{
                            "type": "quality-gate",
                            "config": {
                                "forbidden_patterns": ["TODO", "FIXME"],
                            },
                        }],
                    },
                },
            },
        })
        executor = HookExecutor()
        passed, msg = executor.execute_validate_hooks("test-agent", {
            "output_text": "This has a TODO item that needs fixing",
        })
        assert passed is False
        assert "TODO" in msg


class TestErrorHooks:
    """Test error hook execution."""

    def test_classify_transient_error(self, hook_env):
        """Transient errors are classified correctly."""
        write_registry(hook_env, {
            "agents": {
                "test-agent": {
                    "description": "test",
                    "hooks": {
                        "on_error": [{
                            "type": "classify-error",
                            "config": {
                                "transient_patterns": ["rate_limit", "timeout"],
                                "permanent_patterns": ["not_found", "invalid"],
                            },
                        }],
                    },
                },
            },
        })
        executor = HookExecutor()
        instructions = executor.execute_error_hooks("test-agent", Exception("rate_limit exceeded"))
        assert instructions["error_type"] == "transient"

    def test_classify_permanent_error(self, hook_env):
        """Permanent errors are classified correctly."""
        write_registry(hook_env, {
            "agents": {
                "test-agent": {
                    "description": "test",
                    "hooks": {
                        "on_error": [{
                            "type": "classify-error",
                            "config": {
                                "transient_patterns": ["rate_limit"],
                                "permanent_patterns": ["not_found"],
                            },
                        }],
                    },
                },
            },
        })
        executor = HookExecutor()
        instructions = executor.execute_error_hooks("test-agent", Exception("resource not_found"))
        assert instructions["error_type"] == "permanent"

    def test_classify_unknown_error(self, hook_env):
        """Unrecognized errors are classified as unknown."""
        write_registry(hook_env, {
            "agents": {
                "test-agent": {
                    "description": "test",
                    "hooks": {
                        "on_error": [{
                            "type": "classify-error",
                            "config": {
                                "transient_patterns": [],
                                "permanent_patterns": [],
                            },
                        }],
                    },
                },
            },
        })
        executor = HookExecutor()
        instructions = executor.execute_error_hooks("test-agent", Exception("weird error"))
        assert instructions["error_type"] == "unknown"


class TestRetrySchedule:
    """Test retry scheduling logic."""

    def test_retry_scheduled_for_transient(self, hook_env):
        """Transient errors get retry scheduled."""
        write_registry(hook_env, {
            "agents": {
                "test-agent": {
                    "description": "test",
                    "hooks": {
                        "on_error": [
                            {
                                "type": "classify-error",
                                "config": {"transient_patterns": ["timeout"]},
                            },
                            {
                                "type": "retry-schedule",
                                "config": {
                                    "max_retries": 3,
                                    "backoff": [60, 300, 900],
                                    "only_transient": True,
                                },
                            },
                        ],
                    },
                },
            },
        })
        executor = HookExecutor()
        instructions = executor.execute_error_hooks("test-agent", Exception("connection timeout"))
        assert instructions["retry"] is True
        assert instructions["retry_delay"] == 60

    def test_no_retry_for_permanent(self, hook_env):
        """Permanent errors don't get retried."""
        write_registry(hook_env, {
            "agents": {
                "test-agent": {
                    "description": "test",
                    "hooks": {
                        "on_error": [
                            {
                                "type": "classify-error",
                                "config": {"permanent_patterns": ["invalid"]},
                            },
                            {
                                "type": "retry-schedule",
                                "config": {"only_transient": True},
                            },
                        ],
                    },
                },
            },
        })
        executor = HookExecutor()
        instructions = executor.execute_error_hooks("test-agent", Exception("invalid input"))
        assert instructions["retry"] is False


class TestExtractSection:
    """Test markdown section extraction."""

    def test_extracts_h2_section(self, hook_env):
        """H2 sections are extracted correctly."""
        executor = HookExecutor()
        content = "## Summary\n\nThis is the summary.\n\n## Details\n\nMore info."
        result = executor._extract_section(content, "Summary")
        assert result is not None
        assert "This is the summary." in result
        assert "More info" not in result

    def test_extracts_h3_section(self, hook_env):
        """H3 sections are extracted correctly."""
        executor = HookExecutor()
        content = "## Parent\n\n### Agent Context\n\nContext content here.\n\n### Other\n\nOther stuff."
        result = executor._extract_section(content, "Agent Context")
        assert result is not None
        assert "Context content here" in result

    def test_missing_section_returns_none(self, hook_env):
        """Missing section returns None."""
        executor = HookExecutor()
        content = "## Summary\n\nContent here."
        result = executor._extract_section(content, "Nonexistent Section")
        assert result is None


class TestSpaceDiscovery:
    """Test discover-spaces hook."""

    def test_discovers_spaces(self, hook_env):
        """Space directories are discovered."""
        (hook_env / "0-personal" / "org").mkdir(parents=True)
        (hook_env / "1-teamspace" / "org").mkdir(parents=True)
        (hook_env / "docs").mkdir()  # Not a space

        write_registry(hook_env, {
            "agents": {
                "test-agent": {
                    "description": "test",
                    "hooks": {
                        "pre": [{
                            "type": "discover-spaces",
                            "config": {"pattern": "[0-9]-*/"},
                        }],
                    },
                },
            },
        })
        executor = HookExecutor()
        result = executor._hook_discover_spaces({"pattern": "[0-9]-*/"})
        spaces = result.data.get("spaces", [])
        assert "0-personal" in spaces
        assert "1-teamspace" in spaces
        assert "docs" not in spaces

    def test_space_cache(self, hook_env):
        """Discovered spaces are cached."""
        (hook_env / "0-personal").mkdir()

        write_registry(hook_env, {"agents": {}})
        executor = HookExecutor()

        # First call discovers
        result1 = executor._hook_discover_spaces({"pattern": "[0-9]-*/", "cache_ttl_minutes": 60})
        assert "0-personal" in result1.data["spaces"]

        # Add new space
        (hook_env / "1-teamspace").mkdir()

        # Second call uses cache (won't see new space)
        result2 = executor._hook_discover_spaces({"pattern": "[0-9]-*/", "cache_ttl_minutes": 60})
        assert "1-teamspace" not in result2.data["spaces"]


class TestStatePersistence:
    """Test hook state save/load."""

    def test_state_round_trip(self, hook_env):
        """State survives save/load cycle."""
        write_registry(hook_env, {"agents": {}})
        executor = HookExecutor()
        executor.state["retry_counts"]["test-agent"] = {"task-1": 2}
        executor._save_state()

        executor2 = HookExecutor()
        assert executor2.state["retry_counts"]["test-agent"]["task-1"] == 2


class TestLogOwnershipGuardAuthorship:
    """The guard must judge what a machine WROTE, not what it is carrying.

    Merge-based sync (DIP-0046) carries other actors' commits into your history
    as themselves, so a push range legitimately contains foreign-authored
    commits. Rebase used to hide this by replaying everything under the pusher.
    On 2026-08-13 the guard blocked Miles's entire nightshift wrap-up over two
    commits Winston had authored against winston.jsonl — correctly attributed
    events, doing exactly what merge-based sync is for.
    """

    @staticmethod
    def _repo(tmp_path):
        import subprocess

        def g(*a, **kw):
            return subprocess.run(["git", *a], cwd=tmp_path, capture_output=True,
                                  text=True, **kw)

        g("init", "-q", "-b", "main")
        g("config", "user.email", "gregor+miles@datafund.io")
        g("config", "user.name", "Miles")
        (tmp_path / ".datacore" / "events").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".datacore" / "events" / "miles.jsonl").write_text('{"a":1}\n')
        g("add", "-A")
        g("commit", "-q", "-m", "base")
        g("branch", "-q", "base-ref")
        return g

    def _run_guard(self, tmp_path, rng):
        import subprocess
        from pathlib import Path
        guard = Path(__file__).resolve().parents[1] / "hooks" / "log_ownership_guard.py"
        env = {**os.environ, "DATACORE_ACTOR": "miles"}
        return subprocess.run(["python3", str(guard), rng], cwd=tmp_path,
                              capture_output=True, text=True, env=env)

    def test_foreign_authored_commit_in_range_is_allowed(self, tmp_path):
        """Winston's own commit, merged in, must not be blamed on Miles."""
        g = self._repo(tmp_path)
        g("checkout", "-q", "-b", "wside", "base-ref")
        (tmp_path / ".datacore" / "events" / "winston.jsonl").write_text('{"w":1}\n')
        g("add", "-A")
        g("-c", "user.email=gregor+winston@datafund.io",
          "-c", "user.name=Winston (CoS)", "commit", "-q", "-m", "cos: local autosave")
        g("checkout", "-q", "main")
        g("merge", "-q", "--no-edit", "wside", "-m", "Merge")

        r = self._run_guard(tmp_path, "base-ref..HEAD")
        assert r.returncode == 0, f"blocked an honest merge:\n{r.stdout}{r.stderr}"

    def test_locally_authored_foreign_log_write_is_refused(self, tmp_path):
        """The real violation still has to be caught."""
        g = self._repo(tmp_path)
        (tmp_path / ".datacore" / "events" / "winston.jsonl").write_text('{"forged":1}\n')
        g("add", "-A")
        g("commit", "-q", "-m", "miles writes winston's log")

        r = self._run_guard(tmp_path, "base-ref..HEAD")
        assert r.returncode == 1, "forgery was allowed through"
        assert "winston.jsonl" in (r.stdout + r.stderr)
