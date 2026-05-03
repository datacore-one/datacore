"""Agent event emitter — DIP-0027 / DIP-0028 compliant client for Datacore agents.

Used by:
  - DIP-0024 reactive hooks (Claude Code subagent lifecycle)
  - DIP-0011 nightshift task execution wrappers
  - DIP-0023 messaging state transitions (agent inbox state machine)
  - Anywhere an autonomous agent's activity needs to land in lens

Two layers:
  - `emit(...)` — generic helper, checks DIP-0027 disciplined-core constraints
                  client-side (fast feedback) and POSTs via lens_client.
  - Convenience wrappers (`emit_tick`, `emit_task_claimed`, etc.) — typed,
    self-documenting, harder to misuse.

Stdlib only. Import lens_client by path so this works from any space.

Typical usage from a DIP-0024 hook script:

    from agent_event import AgentEventEmitter
    em = AgentEventEmitter(agent_id="gtd-inbox-processor", runtime="claude-code")
    em.tick()
    em.task_claimed(task_id="org-20260503-foo")
    em.task_completed(task_id="org-20260503-foo", outcome="success", tokens_used=12000)

For peer agents (DIP-0023) running outside Claude Code, the agent_id should be
the ActivityPub-shaped actor identifier:

    em = AgentEventEmitter(agent_id="mr-data@plur-claw", runtime="openclaw")

The wire format is identical regardless of runtime — that's the point.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Import lens_client by path. This file lives at .datacore/lib/agent_event.py
# and lens_client lives at .datacore/modules/lens/lib/lens_client.py.
_HERE = Path(__file__).resolve().parent
_LENS_LIB = _HERE.parent / "modules" / "lens" / "lib"
if str(_LENS_LIB) not in sys.path:
    sys.path.insert(0, str(_LENS_LIB))

try:
    from lens_client import LensClient  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — lens module not installed
    LensClient = None  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)


# Mirror of lens.lib.schema.DISCIPLINED_CORE_TYPES for the agent.* namespace.
# Kept in sync manually — schema is the source of truth, this is a fast-fail
# client-side check so misuse surfaces at call site, not at server validation.
# See DIP-0027 §8 for the canonical specification.
_AGENT_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "agent.tick": (),
    "agent.session_started": ("session_id",),
    "agent.session_ended": ("session_id",),
    "agent.task_received": ("task_id", "sender"),
    "agent.task_claimed": ("task_id",),
    "agent.task_completed": ("task_id", "outcome"),
    "agent.task_failed": ("task_id", "error_class"),
    "agent.approval_requested": ("request_id", "action_class"),
    "agent.approval_resolved": ("request_id", "granted"),
    "agent.message_sent": ("recipient", "message_class"),
    "agent.message_received": ("sender", "message_class"),
    "agent.decision": ("decision_id", "branch"),
    "agent.escalated": ("task_id", "to"),
    "agent.error": ("error_class",),
}

VALID_TASK_OUTCOMES = ("success", "partial", "nochange")
VALID_USED_KINDS = ("cited", "paraphrased", "contradicted", "ignored")
VALID_RUNTIMES = ("claude-code", "openclaw", "hermes", "nightshift", "external")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EmitError(ValueError):
    """Raised when an emit call violates DIP-0027 schema constraints client-side."""


class AgentEventEmitter:
    """Stable, typed wrapper around lens_client for agent.* events.

    Constructor args:
        agent_id: Stable identifier for this agent. ActivityPub-shaped for
                  peer agents (DIP-0023) like 'mr-data@plur-claw'; plain
                  registry name for DIP-0016 subagents like 'gtd-inbox-processor'.
        runtime:  One of VALID_RUNTIMES — the runtime emitting the event.
        client:   Optional pre-built LensClient. If None, auto-discovers token
                  + port from ~/.datacore/lens/auth/ (the DIP-0028 contract).
        fail_soft: If True (default), capture failures log a warning and
                   return None. If False, propagate the underlying error.
                   DIP-0028 §C says capture is fail-soft — host application
                   must continue functioning if capture is down.
    """

    def __init__(
        self,
        agent_id: str,
        runtime: str,
        client: Any = None,
        fail_soft: bool = True,
    ) -> None:
        if runtime not in VALID_RUNTIMES:
            raise EmitError(
                f"runtime {runtime!r} not in {VALID_RUNTIMES}; "
                "register a new runtime in agent_event.py:VALID_RUNTIMES first"
            )
        if not agent_id:
            raise EmitError("agent_id required (stable identifier for this agent)")

        self.agent_id = agent_id
        self.runtime = runtime
        self.fail_soft = fail_soft

        if client is not None:
            self._client = client
        elif LensClient is not None:
            self._client = LensClient()
        else:  # pragma: no cover
            self._client = None

    # ------------------------------------------------------------------ generic

    def emit(
        self,
        event_type: str,
        metadata: dict[str, Any] | None = None,
        target_id: str | None = None,
    ) -> dict | None:
        """Emit an arbitrary `agent.*` event after client-side validation.

        Convenience wrappers below cover the canonical types — prefer them.
        """
        meta = dict(metadata or {})
        meta.setdefault("agent_id", self.agent_id)
        meta.setdefault("runtime", self.runtime)

        # Client-side disciplined-core validation: fail-fast at call site so
        # caller bugs don't masquerade as silent server validation_error rows.
        if event_type in _AGENT_REQUIRED_KEYS:
            missing = [k for k in _AGENT_REQUIRED_KEYS[event_type] if k not in meta]
            if missing:
                msg = f"event {event_type!r} missing required metadata: {missing}"
                if self.fail_soft:
                    logger.warning("agent_event: %s", msg)
                    return None
                raise EmitError(msg)
        elif event_type.startswith("agent."):
            msg = (
                f"unknown disciplined-core event_type {event_type!r}; "
                f"register in DIP-0027 §8 and lens schema before emitting"
            )
            if self.fail_soft:
                logger.warning("agent_event: %s", msg)
                return None
            raise EmitError(msg)

        if self._client is None:
            logger.warning("agent_event: lens client not initialised; skipping %s", event_type)
            return None

        return self._client.capture(
            event_type=event_type,
            actor="agent",
            surface="agent",
            target_id=target_id,
            metadata=meta,
        )

    # -------------------------------------------------------------- lifecycle

    def tick(self, **extra: Any) -> dict | None:
        """Heartbeat liveness — agent is alive at this `ts`. No required metadata."""
        return self.emit("agent.tick", metadata=extra)

    def session_started(self, session_id: str, **extra: Any) -> dict | None:
        return self.emit(
            "agent.session_started",
            metadata={"session_id": session_id, **extra},
        )

    def session_ended(
        self,
        session_id: str,
        duration_ms: int | None = None,
        tokens_used: int | None = None,
        outcome: str | None = None,
        **extra: Any,
    ) -> dict | None:
        meta: dict[str, Any] = {"session_id": session_id, **extra}
        if duration_ms is not None:
            meta["duration_ms"] = duration_ms
        if tokens_used is not None:
            meta["tokens_used"] = tokens_used
        if outcome is not None:
            meta["outcome"] = outcome
        return self.emit("agent.session_ended", metadata=meta)

    # ----------------------------------------------------------------- tasks

    def task_received(
        self,
        task_id: str,
        sender: str,
        trust_tier: str | None = None,
        **extra: Any,
    ) -> dict | None:
        meta: dict[str, Any] = {"task_id": task_id, "sender": sender, **extra}
        if trust_tier is not None:
            meta["trust_tier"] = trust_tier
        return self.emit("agent.task_received", target_id=task_id, metadata=meta)

    def task_claimed(self, task_id: str, **extra: Any) -> dict | None:
        return self.emit(
            "agent.task_claimed",
            target_id=task_id,
            metadata={"task_id": task_id, **extra},
        )

    def task_completed(
        self,
        task_id: str,
        outcome: str,
        tokens_used: int | None = None,
        cost_usd: float | None = None,
        **extra: Any,
    ) -> dict | None:
        if outcome not in VALID_TASK_OUTCOMES:
            raise EmitError(f"outcome must be one of {VALID_TASK_OUTCOMES}, got {outcome!r}")
        meta: dict[str, Any] = {"task_id": task_id, "outcome": outcome, **extra}
        if tokens_used is not None:
            meta["tokens_used"] = tokens_used
        if cost_usd is not None:
            meta["cost_usd"] = cost_usd
        return self.emit("agent.task_completed", target_id=task_id, metadata=meta)

    def task_failed(
        self,
        task_id: str,
        error_class: str,
        recoverable: bool | None = None,
        **extra: Any,
    ) -> dict | None:
        meta: dict[str, Any] = {"task_id": task_id, "error_class": error_class, **extra}
        if recoverable is not None:
            meta["recoverable"] = recoverable
        return self.emit("agent.task_failed", target_id=task_id, metadata=meta)

    # ------------------------------------------------------------- approvals

    def approval_requested(
        self,
        request_id: str,
        action_class: str,
        risk_tier: str | None = None,
        **extra: Any,
    ) -> dict | None:
        meta: dict[str, Any] = {
            "request_id": request_id,
            "action_class": action_class,
            **extra,
        }
        if risk_tier is not None:
            meta["risk_tier"] = risk_tier
        return self.emit("agent.approval_requested", target_id=request_id, metadata=meta)

    def approval_resolved(
        self,
        request_id: str,
        granted: bool,
        decision_latency_ms: int | None = None,
        **extra: Any,
    ) -> dict | None:
        meta: dict[str, Any] = {
            "request_id": request_id,
            "granted": granted,
            **extra,
        }
        if decision_latency_ms is not None:
            meta["decision_latency_ms"] = decision_latency_ms
        return self.emit("agent.approval_resolved", target_id=request_id, metadata=meta)

    # -------------------------------------------------------------- messaging

    def message_sent(
        self,
        recipient: str,
        message_class: str,
        message_id: str | None = None,
        **extra: Any,
    ) -> dict | None:
        return self.emit(
            "agent.message_sent",
            target_id=message_id,
            metadata={"recipient": recipient, "message_class": message_class, **extra},
        )

    def message_received(
        self,
        sender: str,
        message_class: str,
        message_id: str | None = None,
        **extra: Any,
    ) -> dict | None:
        return self.emit(
            "agent.message_received",
            target_id=message_id,
            metadata={"sender": sender, "message_class": message_class, **extra},
        )

    # ---------------------------------------------------------------- reasoning

    def decision(
        self,
        decision_id: str,
        branch: str,
        **extra: Any,
    ) -> dict | None:
        return self.emit(
            "agent.decision",
            target_id=decision_id,
            metadata={"decision_id": decision_id, "branch": branch, **extra},
        )

    def escalated(self, task_id: str, to: str, **extra: Any) -> dict | None:
        return self.emit(
            "agent.escalated",
            target_id=task_id,
            metadata={"task_id": task_id, "to": to, **extra},
        )

    # ------------------------------------------------------------------ error

    def error(self, error_class: str, **extra: Any) -> dict | None:
        return self.emit("agent.error", metadata={"error_class": error_class, **extra})

    # --------------------------------------------------- engram (PLUR analytics)

    def engram_queried(
        self,
        query_hash: str,
        mode: str,
        results_count: int | None = None,
        top_engram_ids: list[str] | None = None,
        scope: str | None = None,
        domain: str | None = None,
        **extra: Any,
    ) -> dict | None:
        """Note: engram.queried lives in the engram.* namespace, not agent.*.
        It's a sibling event commonly emitted by agents and is colocated here
        for convenience.
        """
        meta: dict[str, Any] = {
            "query_hash": query_hash,
            "mode": mode,
            "agent_id": self.agent_id,
            "runtime": self.runtime,
            **extra,
        }
        if results_count is not None:
            meta["results_count"] = results_count
        if top_engram_ids is not None:
            meta["top_engram_ids"] = top_engram_ids
        if scope is not None:
            meta["scope"] = scope
        if domain is not None:
            meta["domain"] = domain
        if self._client is None:
            return None
        return self._client.capture(
            event_type="engram.queried",
            actor="agent",
            surface="engram",
            metadata=meta,
        )

    def engram_used(
        self,
        engram_id: str,
        used_kind: str,
        derived_from: str | None = None,
        **extra: Any,
    ) -> dict | None:
        if used_kind not in VALID_USED_KINDS:
            raise EmitError(f"used_kind must be one of {VALID_USED_KINDS}, got {used_kind!r}")
        meta: dict[str, Any] = {
            "engram_id": engram_id,
            "used_kind": used_kind,
            "agent_id": self.agent_id,
            "runtime": self.runtime,
            **extra,
        }
        if derived_from is not None:
            meta["derived_from"] = derived_from
        if self._client is None:
            return None
        return self._client.capture(
            event_type="engram.used",
            actor="agent",
            surface="engram",
            target_id=engram_id,
            metadata=meta,
        )


# ----------------------------------------------------------------- module-level

_GLOBAL: AgentEventEmitter | None = None


def get_emitter(
    agent_id: str | None = None,
    runtime: str | None = None,
) -> AgentEventEmitter:
    """Return a process-wide emitter, lazily constructed.

    Useful for hook scripts where instantiating per-call would be wasteful.
    On first call, agent_id and runtime are required. Subsequent calls return
    the cached emitter regardless of args (warn if mismatch).
    """
    global _GLOBAL
    if _GLOBAL is None:
        if agent_id is None or runtime is None:
            raise EmitError(
                "first get_emitter() call must provide agent_id and runtime"
            )
        _GLOBAL = AgentEventEmitter(agent_id=agent_id, runtime=runtime)
    elif (agent_id and agent_id != _GLOBAL.agent_id) or (
        runtime and runtime != _GLOBAL.runtime
    ):
        logger.warning(
            "agent_event: get_emitter called with different identity "
            "(was %s/%s, now %s/%s); returning existing emitter",
            _GLOBAL.agent_id, _GLOBAL.runtime, agent_id, runtime,
        )
    return _GLOBAL
