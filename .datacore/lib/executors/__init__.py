"""executors -- pluggable model/harness adapters with live shadow accounting.

Importing this package registers every built-in adapter (claude-code,
hermes, api) via their `@register` decorators. None of their modules
perform import-time side effects that require an external binary or SDK to
be present: the `anthropic` import in `api.py` is deferred to `_invoke`
(run time), and missing `claude`/`hermes` binaries are only discovered
inside `_invoke` as well -- so `import executors` always succeeds
regardless of what's installed on the machine.

Public surface: `ExecResult`, `Executor`, `get_executor`, `register`,
`registered_executors`.
"""

from __future__ import annotations

from .base import (
    ESTIMATE_CENTS_PER_MILLION_TOKENS,
    ExecResult,
    Executor,
    estimate_cost_cents,
    get_executor,
    register,
    registered_executors,
)
from . import api, claude_code, hermes, openclaw, openrouter  # noqa: F401 -- import registers each adapter

__all__ = [
    "ESTIMATE_CENTS_PER_MILLION_TOKENS",
    "ExecResult",
    "Executor",
    "estimate_cost_cents",
    "get_executor",
    "register",
    "registered_executors",
]
